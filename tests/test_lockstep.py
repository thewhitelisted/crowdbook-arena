"""Lockstep rounds: the market waits for each bot, a bot's score is what it would score offline,
and slow, silent or absent bots cannot hold anyone up."""

import asyncio
import json
import sys
import time
from pathlib import Path

from arena_kit import Bot, Quote, run_local
from arena_kit.client import play
from helpers import connect, recv_until

sys.path.insert(0, str(Path(__file__).parents[1] / "kit" / "examples"))
from starter_bot import StarterBot  # noqa: E402

SEED, DURATION = 4242, 60


class Sleepy(StarterBot):
    """The starter bot, too slow to answer its third turn in time."""

    def __init__(self, sleep_s=0.0, skip_turn=3):
        super().__init__()
        self.n = 0
        self.sleep_s = sleep_s
        self.skip_turn = skip_turn

    def on_turn(self, state):
        self.n += 1
        if self.n == self.skip_turn:
            if self.sleep_s:
                time.sleep(self.sleep_s)
                return Quote(take=5)  # arrives too late to count
            return None
        return super().on_turn(state)


def start(server, code, token, **spec):
    body = {"kind": "lockstep", "market": "calm", "duration_s": DURATION, "seed": SEED, **spec}
    r = server.post(f"/api/games/{code}/rounds", body, token)
    assert r.status_code == 201, r.text
    return r.json()


def test_lockstep_scores_match_offline_and_timeouts_count_as_no_answer(server):
    game = server.new_game("alice")
    code = game["code"]
    fast = server.join(code, "fast", "bot")
    slow = server.join(code, "slow", "bot")
    absent = server.join(code, "absent", "bot")

    async def go():
        # Each bot on its own thread, as on its own machine: the slow one holds up only itself.
        tasks = [asyncio.create_task(asyncio.to_thread(
                     asyncio.run, play(StarterBot(), server.url, fast["token"], rounds=1))),
                 asyncio.create_task(asyncio.to_thread(
                     asyncio.run, play(Sleepy(sleep_s=0.6), server.url, slow["token"],
                                       rounds=1)))]
        await asyncio.sleep(0.5)
        # Turn 3 times out at 0.4 s; turn 4, sent then, is answered at 0.6 s, in time.
        await asyncio.to_thread(start, server, code, game["token"], turn_timeout_ms=400)
        return await asyncio.wait_for(asyncio.gather(*tasks), 60)

    (fast_res,), (slow_res,) = asyncio.run(go())
    rows = {r["name"]: r for r in fast_res["results"]}
    assert set(rows) == {"fast", "slow", "absent"}  # bots only: the human host has no seat

    offline = run_local(StarterBot(), "calm", seed=SEED, duration_s=DURATION)
    assert abs(rows["fast"]["score"] - offline.score) < 1e-6
    assert rows["fast"]["details"]["turns"] == offline.turns
    assert rows["fast"]["details"]["timeouts"] == 0

    # The slow bot's late third answer counted as no answer at all.
    offline_slow = run_local(Sleepy(sleep_s=0), "calm", seed=SEED, duration_s=DURATION)
    assert rows["slow"]["details"]["timeouts"] == 1
    assert abs(rows["slow"]["score"] - offline_slow.score) < 1e-6

    # A bot that never connected stood aside every turn.
    assert rows["absent"]["score"] == 0
    assert rows["absent"]["details"]["absent"] == rows["absent"]["details"]["turns"]

    stored = server.http.get(f"/api/games/{code}/rounds/1").json()
    assert stored["round"]["kind"] == "lockstep" and stored["round"]["status"] == "finished"


def test_a_bot_that_drops_out_does_not_hold_up_the_round(server):
    game = server.new_game("alice")
    code = game["code"]
    flaky = server.join(code, "flaky", "bot")

    async def go():
        ws = await connect(server, flaky["token"])
        watcher = await connect(server, game["token"])
        await asyncio.to_thread(start, server, code, game["token"], turn_timeout_ms=60_000,
                                duration_s=600)
        turn = await recv_until(ws, lambda m: m["type"] == "turn")
        assert turn["turn"] == 1 and turn["state"]["time_left"] > 0
        # A bad answer is refused and counted; the turn still waits for a good one.
        await ws.send(json.dumps({"type": "orders", "turn": 1, "bid": [1]}))
        await ws.send(json.dumps({"type": "orders", "turn": 1, "bid": None, "ask": None,
                                  "take": 0}))
        turn2 = await recv_until(ws, lambda m: m["type"] == "turn")
        assert turn2["turn"] == 2
        # An answer for the wrong turn is refused.
        await ws.send(json.dumps({"type": "orders", "turn": 1}))
        err = await recv_until(ws, lambda m: m["type"] == "error")
        assert "not your turn" in err["message"]
        # The bot goes, mid-turn, with a minute-long timeout: the round finishes anyway.
        t0 = time.time()
        await ws.close()
        ended = await recv_until(watcher, lambda m: m["type"] == "round_ended", timeout=30)
        assert time.time() - t0 < 10
        await watcher.close()
        return ended

    ended = asyncio.run(go())
    row = ended["results"][0]
    assert row["name"] == "flaky" and row["details"]["ended"]
    assert row["details"]["absent"] >= row["details"]["turns"] - 3


def test_the_host_can_end_a_lockstep_round(server):
    game = server.new_game("alice")
    code = game["code"]
    bot = server.join(code, "waiter", "bot")

    async def go():
        ws = await connect(server, bot["token"])
        host = await connect(server, game["token"])
        await asyncio.to_thread(start, server, code, game["token"], turn_timeout_ms=60_000,
                                duration_s=600)
        await recv_until(ws, lambda m: m["type"] == "turn")
        progress = await recv_until(host, lambda m: m["type"] == "lockstep_progress")
        assert progress["duration"] == 600 * 10**9 and progress["done"] == []
        assert "turns" not in progress
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {},
                                    game["token"])
        assert r.status_code == 200
        ended = await recv_until(host, lambda m: m["type"] == "round_ended")
        await ws.close()
        await host.close()
        return ended

    ended = asyncio.run(go())
    assert ended["results"][0]["details"]["ended"] is False


def test_lockstep_needs_bots(server):
    game = server.new_game("alice")
    server.join(game["code"], "bob")
    r = server.post(f"/api/games/{game['code']}/rounds", {"kind": "lockstep"}, game["token"])
    assert r.status_code == 400 and "bots" in r.json()["error"]


class Recorder(StarterBot):
    """The starter bot, noting when it saw each moment of its market."""

    def __init__(self, log, name, delay=0.0):
        self.log, self.name, self.delay = log, name, delay

    def on_turn(self, state):
        self.log.append((time.monotonic(), self.name, state.time))
        if self.delay:
            time.sleep(self.delay)
        return super().on_turn(state)


def test_no_bot_sees_its_market_ahead_of_the_others(server):
    game = server.new_game("alice")
    code = game["code"]
    fast = server.join(code, "fast", "bot")
    slow = server.join(code, "slow", "bot")
    log = []

    async def go():
        tasks = [asyncio.create_task(asyncio.to_thread(
                     asyncio.run, play(Recorder(log, "fast"), server.url, fast["token"],
                                       rounds=1))),
                 asyncio.create_task(asyncio.to_thread(
                     asyncio.run, play(Recorder(log, "slow", delay=0.05), server.url,
                                       slow["token"], rounds=1)))]
        await asyncio.sleep(0.5)
        await asyncio.to_thread(start, server, code, game["token"], turn_timeout_ms=2000,
                                duration_s=15)
        return await asyncio.wait_for(asyncio.gather(*tasks), 60)

    asyncio.run(go())
    log.sort()
    latest = {"fast": 0, "slow": 0}
    worst = 0
    for _, name, t in log:
        latest[name] = t
        if all(latest.values()):
            worst = max(worst, abs(latest["fast"] - latest["slow"]))
    # Without the shared clock the fast bot would finish while the slow one was at second 2.
    assert worst <= 2 * 10**9, worst


def test_a_bot_that_drops_and_comes_back_carries_on(server):
    game = server.new_game("alice")
    code = game["code"]
    bot = server.join(code, "comeback", "bot")

    async def go():
        ws = await connect(server, bot["token"])
        watcher = await connect(server, game["token"])
        await asyncio.to_thread(start, server, code, game["token"], turn_timeout_ms=2000,
                                duration_s=20)
        for _ in range(3):
            turn = await recv_until(ws, lambda m: m["type"] == "turn")
            await ws.send(json.dumps({"type": "orders", "turn": turn["turn"], "bid": None,
                                      "ask": None, "take": 0}))
        await ws.close()
        await asyncio.sleep(0.5)
        ws = await connect(server, bot["token"])
        turn = await recv_until(ws, lambda m: m["type"] == "turn")
        assert turn["state"]["time"] < 6 * 10**9  # it waited, rather than playing on without us
        await ws.send(json.dumps({"type": "orders", "turn": turn["turn"], "bid": None,
                                  "ask": None, "take": 0}))
        while True:
            msg = await recv_until(ws, lambda m: m["type"] in ("turn", "round_ended"))
            if msg["type"] == "round_ended":
                break
            await ws.send(json.dumps({"type": "orders", "turn": msg["turn"], "bid": None,
                                      "ask": None, "take": 0}))
        await ws.close()
        await watcher.close()
        return msg

    ended = asyncio.run(go())
    row = ended["results"][0]
    # At most the turn in flight as it dropped went unanswered, and the turn after it, if the
    # bot was not back in time.
    assert row["details"]["ended"]
    assert row["details"]["absent"] + row["details"]["timeouts"] <= 2


def test_stopping_the_server_fails_a_round_under_way(tmp_path):
    from conftest import Server
    from crowdbook_arena.db import Store

    s = Server(tmp_path)
    game = s.new_game("alice")
    bot = s.join(game["code"], "waiter", "bot")

    async def go():
        ws = await connect(s, bot["token"])
        await asyncio.to_thread(start, s, game["code"], game["token"], turn_timeout_ms=60_000,
                                duration_s=600)
        await recv_until(ws, lambda m: m["type"] == "turn")
        await ws.close()
    asyncio.run(go())
    s.stop()
    store = Store(f"sqlite:///{tmp_path}/arena.db")
    gid = store.game_by_code(game["code"])["id"]
    rnd = store.rounds(gid)[0]
    assert rnd["status"] == "failed" and store.results(rnd["id"]) == []
