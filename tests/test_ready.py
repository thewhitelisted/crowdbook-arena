"""The briefing before a live round: the market opens once every connected human has read it, or
when the host starts it; players who are not there are not waited for."""

import asyncio
import json

from helpers import connect, recv_until


def _info(server, code):
    return server.http.get(f"/api/games/{code}").json()


def _start(server, code, token, **spec):
    body = {"kind": "live", "market": "calm", "duration_s": 20, "speed": 4, **spec}
    r = server.post(f"/api/games/{code}/rounds", body, token)
    assert r.status_code == 201, r.text
    return r.json()


def _check(m):
    """The ready check in a game update, or {}."""
    return (m["type"] == "game" and m["game"]["ready_check"]) or {}


def _opened(m):
    return m["type"] == "live_snapshot"


def test_the_market_opens_when_everyone_connected_is_ready(server):
    game = server.new_game("alice")
    code = game["code"]
    bob = server.join(code, "bob")
    server.join(code, "a-bot", "bot")
    ids = {p["name"]: p["id"] for p in _info(server, code)["players"]}

    async def go():
        a = await connect(server, game["token"])
        b = await connect(server, bob["token"])
        await asyncio.to_thread(_start, server, code, game["token"])
        check = _info(server, code)["ready_check"]
        # Both people are asked; the bot, which gets the briefing in on_start, is not.
        assert check["asked"] == sorted([ids["alice"], ids["bob"]])
        assert check["waiting"] == check["asked"] and check["ready"] == []
        # Nothing trades before the market opens.
        await b.send(json.dumps({"type": "new", "side": "buy", "order_type": "market",
                                 "quantity": 1}))
        refused = await recv_until(b, lambda m: m["type"] == "error")
        assert "read the briefing" in refused["message"]

        await b.send(json.dumps({"type": "ready"}))
        update = await recv_until(a, lambda m: _check(m).get("ready"))
        assert _check(update)["waiting"] == [ids["alice"]]
        assert _info(server, code)["ready_check"] is not None

        await a.send(json.dumps({"type": "ready"}))
        await recv_until(a, _opened)
        await recv_until(b, _opened)
        assert _info(server, code)["ready_check"] is None
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {},
                                game["token"])
        await recv_until(a, lambda m: m["type"] == "round_ended")
        for ws in (a, b):
            await ws.close()

    asyncio.run(go())


def test_players_who_are_not_there_are_not_waited_for(server):
    game = server.new_game("alice")
    code = game["code"]
    bob = server.join(code, "bob")
    carol = server.join(code, "carol")
    ids = {p["name"]: p["id"] for p in _info(server, code)["players"]}

    async def go():
        a = await connect(server, game["token"])
        b = await connect(server, bob["token"])
        await asyncio.to_thread(_start, server, code, game["token"])
        # Carol is offline: asked, but not waited for.
        check = _info(server, code)["ready_check"]
        assert ids["carol"] in check["asked"]
        assert check["waiting"] == sorted([ids["alice"], ids["bob"]])
        await a.send(json.dumps({"type": "ready"}))
        # Carol comes back during the briefing: now she must read it too.
        c = await connect(server, carol["token"])
        await recv_until(a, lambda m: ids["carol"] in _check(m).get("waiting", []))
        await c.send(json.dumps({"type": "ready"}))
        await recv_until(a, lambda m: _check(m).get("waiting") == [ids["bob"]])
        # Bob leaves without reading it: the market opens without him.
        await b.close()
        await recv_until(a, _opened)
        await recv_until(c, _opened)
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {},
                                game["token"])
        await recv_until(a, lambda m: m["type"] == "round_ended")
        await a.close()
        await c.close()

    asyncio.run(go())


def test_the_host_can_start_the_market_or_end_the_round(server):
    game = server.new_game("alice")
    code = game["code"]
    bob = server.join(code, "bob")

    async def go():
        b = await connect(server, bob["token"])
        await asyncio.to_thread(_start, server, code, game["token"])
        assert _info(server, code)["ready_check"]["waiting"] == [bob["player"]["id"]]
        # Only the host may skip the wait.
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/begin", {},
                                    bob["token"])
        assert r.status_code == 403
        # Ending the round before the market opens leaves no results.
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {},
                                    game["token"])
        assert r.status_code == 200
        info = _info(server, code)
        assert info["current"] is None and info["ready_check"] is None
        assert info["rounds"][0]["status"] == "failed"

        await asyncio.to_thread(_start, server, code, game["token"])
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/begin", {},
                                    game["token"])
        assert r.status_code == 200, r.text
        await recv_until(b, _opened)
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/begin", {},
                                    game["token"])
        assert r.status_code == 409  # already open
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {},
                                game["token"])
        await recv_until(b, lambda m: m["type"] == "round_ended")
        await b.close()

    asyncio.run(go())


def test_a_live_round_with_nobody_to_wait_for_opens_at_once(server):
    game = server.new_game("alice")
    code = game["code"]
    _start(server, code, game["token"], duration_s=5, speed=5)
    info = _info(server, code)
    assert info["ready_check"] is None and info["current"]["status"] == "running"
