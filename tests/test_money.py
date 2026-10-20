"""Bankrolls: buying power, the same for people and bots, live, in lockstep and offline, and
balances carried from round to round."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import update

from arena_kit import Bot, Quote, money, run_local
from arena_kit.client import play
from crowdbook_arena.db import players
from helpers import connect, is_live, recv_until, start_round

sys.path.insert(0, str(Path(__file__).parents[1] / "kit" / "examples"))
from starter_bot import StarterBot  # noqa: E402


def test_room_long_short_and_bust():
    # $500 of equity carries 5 lots at $100, either way.
    assert money.max_lots(50_000, 10_000) == 5
    assert money.room("buy", 0, 0, 50_000, 10_000) == 5
    assert money.room("buy", 3, 1, 50_000, 10_000) == 1
    assert money.room("sell", 3, 0, 50_000, 10_000) == 8  # sell 3 to close, 5 more short
    assert money.room("sell", -5, 0, 50_000, 10_000) == 0
    # Bust: nothing more either way, but a position can still be closed.
    assert money.max_lots(5_000, 10_000) == 0
    assert money.room("buy", 0, 0, 5_000, 10_000) == 0
    assert money.room("sell", 4, 0, 5_000, 10_000) == 4
    assert money.room("buy", -2, 0, 5_000, 10_000) == 2
    assert money.dollars(1_234_567) == "$12,345.67" and money.dollars(-50) == "-$0.50"


def test_bankroll_is_checked_and_shown(server):
    assert server.post("/api/games", {"host_name": "a", "bankroll": 5}).status_code == 400
    game = server.post("/api/games", {"host_name": "a", "bankroll": 2500}).json()
    info = server.http.get(f"/api/games/{game['code']}").json()
    assert info["bankroll"] == 250_000 and info["players"][0]["balance"] == 250_000
    default = server.new_game("b")
    assert server.http.get(f"/api/games/{default['code']}").json()["bankroll"] == 1_000_000


def test_live_orders_beyond_buying_power_are_refused(server):
    game = server.post("/api/games", {"host_name": "alice", "bankroll": 500}).json()
    code = game["code"]

    async def go():
        ws = await connect(server, game["token"])
        await start_round(server, code, game["token"], duration_s=60)
        await recv_until(ws, is_live("start"))
        await recv_until(ws, lambda m: m["type"] == "live" and m["m"]["type"] == "depth"
                         and m["m"]["bids"] and m["m"]["asks"])
        # $500 carries 5 lots at about $100: 6 is too many, long or short.
        for side, price in (("buy", 9_990), ("sell", 10_010)):
            await ws.send(json.dumps({"type": "new", "side": side, "order_type": "limit",
                                      "price": price, "quantity": 6}))
            err = await recv_until(ws, lambda m: m["type"] == "error")
            assert "not enough buying power" in err["message"], err
        # Open orders count: 3 bid, then 3 more is too many, 2 is fine.
        await ws.send(json.dumps({"type": "new", "side": "buy", "order_type": "limit",
                                  "price": 9_900, "quantity": 3}))
        await recv_until(ws, is_live("accepted"))
        await ws.send(json.dumps({"type": "new", "side": "buy", "order_type": "limit",
                                  "price": 9_900, "quantity": 3}))
        err = await recv_until(ws, lambda m: m["type"] == "error")
        assert "carries 2 more lots" in err["message"], err
        await ws.send(json.dumps({"type": "new", "side": "buy", "order_type": "limit",
                                  "price": 9_900, "quantity": 2}))
        await recv_until(ws, is_live("accepted"))
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {},
                                game["token"])
        await recv_until(ws, lambda m: m["type"] == "round_ended")
        await ws.close()
    asyncio.run(go())


def test_a_bust_player_can_watch_but_not_open_positions(server):
    game = server.new_game("alice")
    code = game["code"]
    bob = server.join(code, "bob")
    store = server.app.state.arena.store
    with store.engine.begin() as c:
        c.execute(update(players).where(players.c.id == bob["player"]["id"]).values(balance=5_000))
    info = server.http.get(f"/api/games/{code}").json()
    assert {p["name"]: p["bust"] for p in info["players"]} == {"alice": False, "bob": True}

    async def go():
        b = await connect(server, bob["token"])
        await start_round(server, code, game["token"], duration_s=60)
        snap = await recv_until(b, lambda m: m["type"] == "live_snapshot")
        assert snap["seated"] and snap["state"]["balance"] == 5_000
        await recv_until(b, is_live("start"))
        await b.send(json.dumps({"type": "new", "side": "buy", "order_type": "limit",
                                 "price": 9_000, "quantity": 1}))
        err = await recv_until(b, lambda m: m["type"] == "error")
        assert "you are bust" in err["message"]
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {},
                                game["token"])
        await recv_until(b, lambda m: m["type"] == "round_ended")
        await b.close()
    asyncio.run(go())


class Greedy(Bot):
    """Wants 20 lots each way: more than a $500 bankroll carries."""

    def __init__(self):
        self.biggest = 0

    def on_turn(self, state):
        self.biggest = max(self.biggest, abs(state.position))
        if state.bid is None or state.ask is None:
            return Quote()
        if state.time_left < 5_000_000_000:
            return Quote(take=-state.position)
        return Quote(bid=(state.bid[0], 20), ask=(state.ask[0], 20), take=20 if state.time < 3e9 else 0)


def test_offline_equals_lockstep_with_a_bankroll_and_balances_carry(server):
    game = server.post("/api/games", {"host_name": "alice", "bankroll": 500}).json()
    code = game["code"]
    bot = server.join(code, "greedy", "bot")
    pid = bot["player"]["id"]

    def run_round(seed):
        async def go():
            trader = Greedy()
            task = asyncio.create_task(play(trader, server.url, bot["token"], rounds=1))
            await asyncio.sleep(0.3)
            r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                        {"kind": "lockstep", "market": "calm", "seed": seed,
                                         "duration_s": 40}, game["token"])
            assert r.status_code == 201, r.text
            (res,) = await asyncio.wait_for(task, 60)
            return res, trader
        return asyncio.run(go())

    res1, t1 = run_round(11)
    offline = Greedy()
    local = run_local(offline, "calm", seed=11, duration_s=40, bankroll=500)
    assert res1["result"]["score"] == pytest.approx(local.score)
    assert t1.biggest <= 5 and offline.biggest == t1.biggest
    balance1 = 50_000 + local.score
    info = server.http.get(f"/api/games/{code}").json()
    assert next(p for p in info["players"] if p["id"] == pid)["balance"] == pytest.approx(balance1)

    # The next round starts from that balance, offline too.
    res2, _ = run_round(12)
    local2 = run_local(Greedy(), "calm", seed=12, duration_s=40, bankroll=balance1 / 100)
    assert res2["result"]["score"] == pytest.approx(local2.score)
    assert res2["result"]["details"]["start_balance"] == pytest.approx(balance1)
    info = server.http.get(f"/api/games/{code}").json()
    final = next(p for p in info["players"] if p["id"] == pid)["balance"]
    assert final == pytest.approx(balance1 + local2.score)
    board = server.http.get(f"/api/games/{code}/leaderboard").json()["overall"]
    assert board["by"] == "balance"
    assert next(r for r in board["rows"] if r["player_id"] == pid)["balance"] == pytest.approx(final)


def test_a_database_from_before_bankrolls_is_brought_up_to_date(tmp_path):
    import sqlite3

    from crowdbook_arena.db import Store

    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE games (id INTEGER PRIMARY KEY, code VARCHAR(12) NOT NULL UNIQUE,
            name VARCHAR(64) NOT NULL, created_at FLOAT NOT NULL, status VARCHAR(16) NOT NULL);
        CREATE TABLE players (id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL,
            name VARCHAR(32) NOT NULL, kind VARCHAR(8) NOT NULL,
            token_hash VARCHAR(64) NOT NULL UNIQUE, is_host INTEGER NOT NULL,
            joined_at FLOAT NOT NULL, UNIQUE(game_id, name));
        INSERT INTO games VALUES (1, 'ABCDEF', 'old', 0, 'open');
        INSERT INTO players VALUES (1, 1, 'alice', 'human', 'h', 1, 0);
    """)
    c.commit()
    c.close()
    store = Store(f"sqlite:///{path}")
    assert store.game(1)["bankroll"] == money.DEFAULT_BANKROLL
    assert store.players(1)[0]["balance"] == money.DEFAULT_BANKROLL
    Store(f"sqlite:///{path}")  # starting again changes nothing
    assert store.players(1)[0]["balance"] == money.DEFAULT_BANKROLL
