"""What happens to a seat whose player goes away and comes back, and to a round that fails."""

import asyncio
import json

from helpers import connect, is_live, recv_until, start_round


def test_disconnect_cancels_orders_and_reconnect_restores_the_seat(server):
    game = server.new_game("alice")
    code = game["code"]
    bob = server.join(code, "bob")

    async def go():
        a = await connect(server, game["token"])
        b = await connect(server, bob["token"])
        await start_round(server, code, game["token"], duration_s=120, speed=1)
        await recv_until(b, is_live("start"))
        await b.send(json.dumps({"type": "new", "side": "buy", "order_type": "market",
                                 "quantity": 2}))
        await recv_until(b, lambda m: m["type"] == "live" and m["m"]["type"] == "filled"
                         and m["m"]["leaves"] == 0)
        await b.send(json.dumps({"type": "new", "side": "buy", "order_type": "limit",
                                 "price": 9000, "quantity": 4}))
        acc = await recv_until(b, is_live("accepted"))
        await b.close()
        # The resting bid is cancelled once bob has no connection left.
        await asyncio.sleep(0.5)
        b = await connect(server, bob["token"])
        snap = await recv_until(b, lambda m: m["type"] == "live_snapshot")
        assert snap["seated"]
        state = snap["state"]
        assert state["orders"] == [], state["orders"]
        assert state["position"] >= 1
        assert acc["m"]["id"] >= 1
        # Bob trades again from the same seat.
        await b.send(json.dumps({"type": "new", "side": "sell", "order_type": "market",
                                 "quantity": 1}))
        await recv_until(b, is_live("filled"))
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {}, game["token"])
        ended = await recv_until(a, lambda m: m["type"] == "round_ended")
        await a.close()
        await b.close()
        return ended, state["position"]

    ended, held = asyncio.run(go())
    bob_row = next(r for r in ended["results"] if r["name"] == "bob")
    assert bob_row["details"]["position"] == held - 1


def test_a_second_tab_of_the_same_player_shares_the_seat(server):
    game = server.new_game("alice")
    code = game["code"]

    async def go():
        t1 = await connect(server, game["token"])
        t2 = await connect(server, game["token"])
        await start_round(server, code, game["token"], duration_s=60, speed=1)
        await recv_until(t1, is_live("start"))
        await t1.send(json.dumps({"type": "new", "side": "buy", "order_type": "limit",
                                  "price": 9000, "quantity": 1}))
        acc = await recv_until(t2, is_live("accepted"))
        await t1.close()  # one tab closes: the other still watches, so the order stays
        await asyncio.sleep(0.3)
        await t2.send(json.dumps({"type": "cancel", "id": acc["m"]["id"]}))
        cancelled = await recv_until(t2, is_live("cancelled"))
        assert cancelled["m"]["reason"] == "requested"
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {}, game["token"])
        await recv_until(t2, lambda m: m["type"] == "round_ended")
        await t2.close()
    asyncio.run(go())


def test_a_market_that_does_not_start_fails_the_round(tmp_path):
    from conftest import Server

    s = Server(tmp_path)
    try:
        s.app.state.arena.settings = s.app.state.arena.settings.__class__(
            **{**s.app.state.arena.settings.__dict__, "crowdbook": "/bin/false"})
        game = s.new_game("alice")
        r = s.post(f"/api/games/{game['code']}/rounds",
                   {"kind": "live", "market": "calm", "duration_s": 30}, game["token"])
        assert r.status_code == 500
        info = s.http.get(f"/api/games/{game['code']}").json()
        assert info["current"] is None
        assert info["rounds"][0]["status"] == "failed"
    finally:
        s.stop()


def test_a_late_joiner_watches_without_a_seat(server):
    game = server.new_game("alice")
    code = game["code"]

    async def go():
        a = await connect(server, game["token"])
        await start_round(server, code, game["token"], duration_s=60, speed=1)
        await recv_until(a, is_live("start"))
        late = server.join(code, "late")
        w = await connect(server, late["token"])
        snap = await recv_until(w, lambda m: m["type"] == "live_snapshot")
        assert not snap["seated"]
        await recv_until(w, lambda m: m["type"] == "live" and m["m"]["type"] in ("trade", "clock"))
        await w.send(json.dumps({"type": "new", "side": "buy", "order_type": "market",
                                 "quantity": 1}))
        err = await recv_until(w, lambda m: m["type"] == "error")
        assert "no seat" in err["message"]
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {}, game["token"])
        ended = await recv_until(w, lambda m: m["type"] == "round_ended")
        await a.close()
        await w.close()
        return ended
    ended = asyncio.run(go())
    assert [r["name"] for r in ended["results"]] == ["alice"]
