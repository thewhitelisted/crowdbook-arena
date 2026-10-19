"""Leaderboards across rounds."""

import asyncio
import json

import pytest

from crowdbook_arena.leaderboard import leaderboards, rank_points
from helpers import connect, recv_until


def test_rank_points():
    assert rank_points({1: 5.0}) == {1: 100.0}
    assert rank_points({1: 5.0, 2: -1.0, 3: 2.0}) == {1: 100.0, 3: 50.0, 2: 0.0}
    tied = rank_points({1: 3.0, 2: 3.0, 3: 1.0, 4: 0.0})
    assert tied[1] == tied[2] == pytest.approx((100 + 200 / 3) / 2)
    assert tied[4] == 0.0


def test_boards_by_kind_and_overall():
    players = [{"id": i, "name": n, "kind": k} for i, n, k in
               [(1, "ann", "human"), (2, "bot", "bot"), (3, "cy", "human")]]
    rounds = [{"id": 10, "kind": "live", "status": "finished"},
              {"id": 11, "kind": "lockstep", "status": "finished"},
              {"id": 12, "kind": "challenge", "status": "finished"},
              {"id": 13, "kind": "live", "status": "failed"}]
    results = [{"round_id": 10, "player_id": 1, "score": 50.0},
               {"round_id": 10, "player_id": 2, "score": 10.0},
               {"round_id": 10, "player_id": 3, "score": -5.0},
               {"round_id": 11, "player_id": 2, "score": 3.0},
               {"round_id": 12, "player_id": 1, "score": 80.0},
               {"round_id": 12, "player_id": 3, "score": -20.0},
               {"round_id": 13, "player_id": 3, "score": 999.0}]
    b = leaderboards(rounds, results, players)
    assert [(r["name"], r["points"]) for r in b["live"]["rows"]] == [
        ("ann", 100), ("bot", 50), ("cy", 0)]
    assert [(r["name"], r["points"]) for r in b["lockstep"]["rows"]] == [("bot", 100)]
    assert [(r["name"], r["points"]) for r in b["challenge"]["rows"]] == [
        ("ann", 80), ("cy", -20)]
    overall = {r["name"]: (r["points"], r["rounds"]) for r in b["overall"]["rows"]}
    assert overall == {"ann": (180, 2), "bot": (150, 2), "cy": (-20, 2)}


def test_the_leaderboard_follows_rounds(server):
    game = server.new_game("alice")
    code = game["code"]
    bob = server.join(code, "bob")

    async def go():
        a = await connect(server, game["token"])
        b = await connect(server, bob["token"])
        board = await recv_until(b, lambda m: m["type"] == "leaderboard")
        assert board["boards"]["overall"]["rows"] == []
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                    {"kind": "challenge", "challenge": "kelly", "seed": 2,
                                     "duration_s": 600}, game["token"])
        assert r.status_code == 201
        await b.send(json.dumps({"type": "challenge_answer", "answer": {"fraction": 0.0}}))
        await recv_until(b, lambda m: m["type"] == "challenge_saved")
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {}, game["token"])
        await recv_until(a, lambda m: m["type"] == "round_ended")
        board = await recv_until(a, lambda m: m["type"] == "leaderboard")
        await a.close()
        await b.close()
        return board

    board = asyncio.run(go())
    rows = board["boards"]["challenge"]["rows"]
    assert {r["name"] for r in rows} == {"alice", "bob"}
    assert server.http.get(f"/api/games/{code}/leaderboard").json() == board["boards"]
