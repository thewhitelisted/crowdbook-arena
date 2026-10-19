"""Tokens: who may connect, start and end rounds, and send orders, and from which seat."""

import asyncio
import json

import pytest
import websockets

from arena_kit.client import ws_url
from crowdbook_arena import app as app_module
from crowdbook_arena.live import RequestError, check_request
from helpers import connect, is_live, recv_until, start_round


def test_bad_and_missing_tokens_are_refused(server):
    async def go():
        async with websockets.connect(ws_url(server.url)) as ws:
            await ws.send(json.dumps({"type": "auth", "token": "nope"}))
            msg = json.loads(await ws.recv())
            assert msg["type"] == "error" and msg["fatal"]
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await ws.recv()
            assert closed.value.rcvd.code == 4401
        async with websockets.connect(ws_url(server.url)) as ws:
            await ws.send(json.dumps({"type": "new", "side": "buy"}))
            msg = json.loads(await ws.recv())
            assert msg["type"] == "error" and msg["fatal"]
    asyncio.run(go())


def test_auth_must_come_in_time(server, monkeypatch):
    monkeypatch.setattr(app_module, "AUTH_TIMEOUT_S", 0.2)

    async def go():
        async with websockets.connect(ws_url(server.url)) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            assert msg == {"type": "error", "message": "no auth message in time", "fatal": True}
    asyncio.run(go())


def test_only_the_host_runs_rounds(server):
    game = server.new_game("alice")
    bob = server.join(game["code"], "bob")
    other = server.new_game("carol")
    body = {"kind": "live", "market": "calm", "duration_s": 30}
    path = f"/api/games/{game['code']}/rounds"
    assert server.post(path, body).status_code == 401
    assert server.post(path, body, "made-up").status_code == 401
    assert server.post(path, body, bob["token"]).status_code == 403
    # The host of another game is not the host of this one.
    assert server.post(path, body, other["token"]).status_code == 403
    assert server.post(f"/api/games/{game['code']}/rounds/stop", {}, bob["token"]).status_code == 403
    assert server.post(path, {**body, "market": "nope"}, game["token"]).status_code == 400
    assert server.post(path, {**body, "duration_s": 10**6}, game["token"]).status_code == 400


def test_joining_checks_names_and_codes(server):
    game = server.new_game("alice")
    code = game["code"]
    assert server.post(f"/api/games/{code}/join", {"name": "Alice"}).status_code == 409
    assert server.post(f"/api/games/{code}/join", {"name": ""}).status_code == 400
    assert server.post(f"/api/games/{code}/join", {"name": "<script>"}).status_code == 400
    assert server.post("/api/games/ZZZZZZ/join", {"name": "x"}).status_code == 404
    assert server.post(f"/api/games/{code.lower()}/join", {"name": "dan"}).status_code == 201
    info = server.http.get(f"/api/games/{code}").json()
    assert "token" not in json.dumps(info)


@pytest.mark.parametrize("msg", [
    {"type": "hello", "protocol": 1, "seat": "p1"},
    {"type": "new", "side": "buy", "order_type": "market", "quantity": 1, "seat": "p2"},
    {"type": "new", "side": "buy", "order_type": "market", "quantity": 1, "token": "x"},
    {"type": "new", "side": "buy", "order_type": "limit", "quantity": 1},
    {"type": "new", "side": "buy", "order_type": "market", "quantity": 0},
    {"type": "new", "side": "buy", "order_type": "market", "quantity": True},
    {"type": "new", "side": "buy", "order_type": "market", "quantity": 1.5},
    {"type": "new", "side": "up", "order_type": "market", "quantity": 1},
    {"type": "new", "side": "buy", "order_type": "market", "quantity": 1, "price": 5},
    {"type": "cancel"},
    {"type": "modify", "id": 1, "price": 3},
    ["new"],
])
def test_requests_are_checked(msg):
    with pytest.raises(RequestError):
        check_request(msg)


def test_a_player_trades_only_from_their_own_seat(server):
    game = server.new_game("alice")
    code = game["code"]
    bob = server.join(code, "bob")

    async def go():
        a = await connect(server, game["token"])
        b = await connect(server, bob["token"])
        await start_round(server, code, game["token"], duration_s=60, speed=1)
        await recv_until(b, is_live("start"))
        # Bob tries to send from alice's seat by naming it; it is refused, not sent.
        await b.send(json.dumps({"type": "new", "seat": "p1", "side": "buy",
                                 "order_type": "market", "quantity": 1}))
        err = await recv_until(b, lambda m: m["type"] == "error")
        assert "unknown fields" in err["message"]
        # An order bob does send is his: his screen hears of it, alice's does not.
        await b.send(json.dumps({"type": "new", "id": 7, "side": "buy", "order_type": "limit",
                                 "price": 9000, "quantity": 2}))
        acc = await recv_until(b, is_live("accepted"))
        assert acc["m"]["id"] == 7
        # Bob cannot cancel by alice's id space either: ids are per seat.
        await a.send(json.dumps({"type": "cancel", "id": 7}))
        rej = await recv_until(a, is_live("rejected"))
        assert rej["m"]["reason"] == "unknown-order-id"
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {}, game["token"])
        assert r.status_code == 200
        ended = await recv_until(b, lambda m: m["type"] == "round_ended")
        await a.close()
        await b.close()
        return ended
    ended = asyncio.run(go())
    assert {r["name"] for r in ended["results"]} == {"alice", "bob"}
