"""End to end: a game, a live round with a scripted trader and the starter bot, and its results."""

import asyncio
import json
import sys
from pathlib import Path

import websockets

from arena_kit.client import play, ws_url
from helpers import recv_until

sys.path.insert(0, str(Path(__file__).parents[1] / "kit" / "examples"))
from starter_bot import StarterBot  # noqa: E402


def test_live_round_end_to_end(server):
    game = server.new_game("alice")
    code, host_token = game["code"], game["token"]
    bob = server.join(code, "bob")
    bot = server.join(code, "starter", kind="bot")

    async def scenario():
        bot_task = asyncio.create_task(play(StarterBot(), server.url, bot["token"], rounds=1))
        async with websockets.connect(ws_url(server.url)) as ws:
            await ws.send(json.dumps({"type": "auth", "token": bob["token"]}))
            welcome = await recv_until(ws, lambda m: m["type"] == "welcome")
            assert welcome["player"]["name"] == "bob"
            await asyncio.sleep(0.3)
            r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                        {"kind": "live", "market": "calm", "duration_s": 30,
                                         "speed": 15}, host_token)
            assert r.status_code == 201, r.text
            await recv_until(ws, lambda m: m["type"] == "live" and m["m"]["type"] == "start")
            await ws.send(json.dumps({"type": "new", "side": "buy", "order_type": "market",
                                      "quantity": 3}))
            fill = await recv_until(ws, lambda m: m["type"] == "live"
                                    and m["m"]["type"] == "filled")
            assert fill["m"]["quantity"] >= 1
            ended = await recv_until(ws, lambda m: m["type"] == "round_ended")
        bot_results = await asyncio.wait_for(bot_task, 20)
        return ended, bot_results

    ended, bot_results = asyncio.run(scenario())
    names = [r["name"] for r in ended["results"]]
    assert sorted(names) == ["alice", "bob", "starter"]
    bob_row = next(r for r in ended["results"] if r["name"] == "bob")
    assert bob_row["details"]["position"] >= 1
    starter = next(r for r in ended["results"] if r["name"] == "starter")
    assert bot_results[0]["result"]["player_id"] == starter["player_id"]
    assert starter["details"]["traded"] > 0
    # The round is stored, with the same results, and a new one can start.
    r = server.http.get(f"/api/games/{code}/rounds/1").json()
    assert r["round"]["status"] == "finished"
    assert [x["name"] for x in r["results"]] == names
    info = server.http.get(f"/api/games/{code}").json()
    assert info["current"] is None and len(info["rounds"]) == 1
