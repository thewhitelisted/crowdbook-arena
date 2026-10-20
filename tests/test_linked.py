"""Pairs and baskets: several linked assets in one round, each with its own book, traded from one
bankroll, the same offline, in lockstep and live."""

import asyncio
import json
import statistics

import pytest

from arena_kit import Bot, Quote, run_local
from arena_kit.client import play
from arena_kit.markets import BASKET, PAIR, is_linked
from arena_kit.stepped import SteppedPortfolio
from helpers import connect, recv_until

SEED = 31


class PairTrader(Bot):
    """Leans against the gap between Lanterns and four fifths of Driftwood, and quotes both."""

    def __init__(self):
        self.started = None
        self.seen = []

    def on_start(self, info):
        self.started = info

    def on_turn(self, p):
        self.seen.append((p.time, {s: a.time for s, a in p.assets.items()}))
        d, lan = p.assets["DRIFT"], p.assets["LANTERN"]
        if not (d.mid and lan.mid):
            return None
        gap = lan.mid - (8000 + 0.8 * (d.mid - 10000))
        want = max(-30, min(30, round(-gap)))
        out = {"DRIFT": Quote(bid=(d.bid[0], 2), ask=(d.ask[0], 2))}
        if abs(want - lan.position) >= 3:
            out["LANTERN"] = Quote(take=want - lan.position)
        return out


def test_linked_values_move_together():
    v = PAIR.values(SEED, 300)
    d, lan = v["DRIFT"], v["LANTERN"]
    dd = [b - a for a, b in zip(d[::40], d[40::40])]
    dl = [b - a for a, b in zip(lan[::40], lan[40::40])]
    assert statistics.correlation(dd, dl) > 0.6
    b = BASKET.values(SEED, 300)
    premium = [c - (x + y) for x, y, c in zip(b["COCO"], b["MANGO"], b["CRATE"])]
    assert max(abs(x) for x in premium) < 60  # the crate stays near its contents
    assert PAIR.values(SEED, 300) == v
    assert is_linked(PAIR) and set(PAIR.scenarios(SEED, 30)) == {"DRIFT", "LANTERN"}


def test_offline_turns_never_see_one_market_ahead_of_another():
    bot = PairTrader()
    a = run_local(bot, "pair", seed=SEED, duration_s=60)
    b = run_local(PairTrader(), "pair", seed=SEED, duration_s=60)
    assert a.score == b.score and a.positions == b.positions
    assert bot.started["assets"] == {"DRIFT": "Driftwood", "LANTERN": "Lanterns"}
    times = [t for t, _ in bot.seen]
    assert times == sorted(times)
    assert all(at <= t for t, assets in bot.seen for at in assets.values())


def test_one_bankroll_carries_every_asset():
    class Greedy(Bot):
        def on_turn(self, p):
            return {s: Quote(take=20) for s in p.assets}

    # $5,000 buys 50 crates on their own; with coconuts and mangoes bought too, far fewer.
    r = run_local(Greedy(), "basket", seed=SEED, duration_s=30, bankroll=5_000)
    held = r.positions
    cost = held["COCO"] * 40 + held["MANGO"] * 60 + held["CRATE"] * 100
    assert held["COCO"] > 0 and held["CRATE"] > 0
    assert cost <= 5_000 * 1.03  # a little drift in prices, no more
    m = SteppedPortfolio(BASKET.scenarios(SEED, 30), SEED, 5_000 * 100)
    assert m.state().room("CRATE", "buy", 10000) == 50


def test_lockstep_pair_scores_what_it_scores_offline(server):
    game = server.new_game("alice")
    code = game["code"]
    bot = server.join(code, "pairs", "bot")
    trader = PairTrader()

    async def go():
        task = asyncio.create_task(play(trader, server.url, bot["token"], rounds=1))
        await asyncio.sleep(0.3)
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                    {"kind": "lockstep", "market": "pair", "duration_s": 30,
                                     "seed": SEED}, game["token"])
        assert r.status_code == 201, r.text
        return await asyncio.wait_for(task, 60)

    (res,) = asyncio.run(go())
    offline = run_local(PairTrader(), "pair", seed=SEED, duration_s=30)
    row = res["result"]
    assert row["score"] == pytest.approx(offline.score, abs=1e-6)
    assert row["details"]["positions"] == offline.positions
    assert row["details"]["timeouts"] == 0 and row["details"]["invalid"] == 0
    assert trader.started["assets"] == {"DRIFT": "Driftwood", "LANTERN": "Lanterns"}
    assert trader.started["mode"] == "lockstep"


def test_a_live_pair_round(server):
    r = server.post("/api/games", {"host_name": "alice", "bankroll": 5000})
    game = r.json()
    code = game["code"]
    bot = server.join(code, "pairs", "bot")
    trader = PairTrader()
    events = []

    async def go():
        ws = await connect(server, game["token"])
        task = asyncio.create_task(play(trader, server.url, bot["token"], rounds=1,
                                        on_event=events.append))
        await asyncio.sleep(0.3)
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                    {"kind": "live", "market": "pair", "duration_s": 20,
                                     "speed": 4}, game["token"])
        assert r.status_code == 201, r.text
        await ws.send(json.dumps({"type": "ready"}))
        snap = await recv_until(ws, lambda m: m["type"] == "live_snapshot")
        assert set(snap["state"]["assets"]) == {"DRIFT", "LANTERN"}
        await recv_until(ws, lambda m: m["type"] == "live" and m.get("asset") == "LANTERN"
                         and m["m"]["type"] == "depth")
        # An order must name its asset.
        await ws.send(json.dumps({"type": "new", "side": "buy", "order_type": "market",
                                  "quantity": 3}))
        refused = await recv_until(ws, lambda m: m["type"] == "error")
        assert "asset" in refused["message"]
        await ws.send(json.dumps({"type": "new", "side": "buy", "order_type": "market",
                                  "quantity": 3, "asset": "LANTERN"}))
        fill = await recv_until(ws, lambda m: m["type"] == "live" and m["m"]["type"] == "filled")
        assert fill["asset"] == "LANTERN"
        # One bankroll of $5,000: with Lanterns tying up some of it, 60 lots of Driftwood at
        # $90 are more than it carries.
        for _ in range(3):
            await ws.send(json.dumps({"type": "new", "side": "buy", "order_type": "limit",
                                      "time_in_force": "post-only", "price": 9000,
                                      "quantity": 20, "asset": "DRIFT"}))
        refused = await recv_until(ws, lambda m: m["type"] == "error"
                                   and "buying power" in m["message"])
        assert refused["request"]["asset"] == "DRIFT"
        ended = await recv_until(ws, lambda m: m["type"] == "round_ended", timeout=60)
        await ws.close()
        await asyncio.wait_for(task, 30)
        return ended

    ended = asyncio.run(go())
    rows = {r["name"]: r for r in ended["results"]}
    assert rows["alice"]["details"]["positions"]["LANTERN"] >= 1
    assert set(rows["pairs"]["details"]["positions"]) == {"DRIFT", "LANTERN"}
    assert trader.started["assets"] == {"DRIFT": "Driftwood", "LANTERN": "Lanterns"}
    sent = [m for m in events if m["type"] == "live" and m["m"]["type"] == "accepted"]
    assert {m["asset"] for m in sent} >= {"DRIFT"}  # the bot quoted Driftwood from its seat
