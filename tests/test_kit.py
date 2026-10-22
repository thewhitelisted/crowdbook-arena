"""The kit: local markets, the bot API, and how a quote becomes orders in a live round."""

import asyncio
import sys
from pathlib import Path

import pytest

from arena_kit import Bot, PUBLIC_KINDS, Quote, State, SteppedMarket, run_local
from arena_kit.client import LiveDriver, ws_url
from arena_kit.markets import is_linked
from arena_kit.tracker import SeatTracker

sys.path.insert(0, str(Path(__file__).parents[1] / "kit" / "examples"))
from starter_bot import StarterBot  # noqa: E402


@pytest.mark.parametrize("kind", sorted(k for k, v in PUBLIC_KINDS.items() if not is_linked(v)))
def test_local_runs_are_deterministic(kind):
    a = run_local(StarterBot(), kind, seed=11, duration_s=60)
    b = run_local(StarterBot(), kind, seed=11, duration_s=60)
    assert a == b
    assert a.turns >= 59 and a.position == 0  # it got flat at the end


def test_a_bot_that_does_nothing_scores_nothing():
    r = run_local(Bot(), "calm", seed=3, duration_s=30)
    assert r.score == 0 and r.position == 0


def test_state_and_quotes_in_a_stepped_market():
    m = SteppedMarket(PUBLIC_KINDS["calm"].scenario(5, 30), 5)
    s = m.state()
    assert s.bid and s.ask and s.bid[0] < s.ask[0]
    assert s.bids[0] == s.bid and s.asks[0] == s.ask
    assert s.max_position == 100 and s.max_order_quantity == 20
    m.step(Quote(bid=(s.bid[0] - 2, 3), ask=(s.ask[0] + 2, 4)))
    s = m.state()
    assert s.my_bid is not None and s.my_bid[1] == 3
    assert s.my_ask is not None and s.my_ask[1] == 4
    m.step(None)  # leaves the quotes where they are
    s2 = m.state()
    assert (s2.my_bid is None or s2.my_bid[0] == s.my_bid[0])
    m.step(Quote(take=2))
    s3 = m.state()
    assert s3.position >= 1 and s3.fills and s3.fills[0].side == "buy"
    assert s3.my_bid is None  # a take cancels the quote on its side
    assert State.from_json(s3.to_json()) == s3


def test_quotes_take_whole_numbers_of_any_type_the_same_offline_and_live():
    import numpy as np
    q = Quote(bid=(np.int64(100), 2.0), ask=None, take=np.int32(-1)).checked()
    assert q == Quote((100, 2), None, -1) and type(q.bid[0]) is int
    for bad in (Quote(bid=(99.5, 1)), Quote(ask=(100, 0)), Quote(take=0.5)):
        with pytest.raises(ValueError):
            bad.checked()

    class Halves(Bot):
        def on_turn(self, state):
            return Quote(bid=(state.bid[0] - 0.5, 1))

    with pytest.raises(ValueError):
        run_local(Halves(), "calm", seed=1, duration_s=10)


def test_a_refused_order_does_not_block_its_side():
    sent = []

    async def send(msg):
        sent.append(msg)

    async def go():
        t = _tracker()
        d = LiveDriver(Bot(), t, send)
        await d.place(Quote(bid=(98, 3)))
        assert t.pending
        # The server refuses it (say the round has just ended): the kit forgets it.
        t.pending.pop(sent[0]["id"])
        sent.clear()
        await d.place(Quote(bid=(98, 3)))
        assert sent and sent[0]["type"] == "new"

    asyncio.run(go())


def test_quote_json_is_checked():
    assert Quote.from_json({"bid": [100, 2], "ask": None, "take": -1}) == Quote((100, 2), None, -1)
    for bad in ({"bid": [100]}, {"bid": [0, 1]}, {"take": 1.5}, {"take": "1"}, {"seat": 1}, []):
        with pytest.raises(ValueError):
            Quote.from_json(bad)


def _tracker():
    t = SeatTracker()
    t.apply({"type": "welcome", "protocol": 1, "seat": "p1", "started": True, "time": 0,
             "duration": 60_000_000_000, "reference_price": 10000, "max_price": 10**9,
             "depth_levels": 10, "maker_fee": -100, "taker_fee": 200, "auction_fee": 0,
             "fee_curve": "flat", "phase": "continuous", "latency": {},
             "account": {"initial_cash": 0, "initial_position": 0, "cash": 0, "position": 0,
                         "fees": 0, "max_position": 10, "max_order_quantity": 5},
             "orders": [], "challenge": None, "scoring": None})
    t.apply({"type": "depth", "time": 1, "bids": [{"price": 99, "quantity": 5, "orders": 1}],
             "asks": [{"price": 101, "quantity": 5, "orders": 1}]})
    return t


def test_live_driver_turns_quotes_into_orders():
    sent = []

    async def send(msg):
        sent.append(msg)

    async def go():
        t = _tracker()
        d = LiveDriver(Bot(), t, send)
        await d.place(Quote(bid=(98, 3), ask=(102, 50)))
        assert [(m["type"], m["side"], m["price"], m["quantity"], m["time_in_force"])
                for m in sent] == [("new", "buy", 98, 3, "post-only"),
                                   ("new", "sell", 102, 5, "post-only")]  # cut to the order limit
        # While they are in flight, nothing more is sent for those sides.
        sent.clear()
        await d.place(Quote(bid=(97, 3), ask=None))
        assert sent == []
        for m in ({"type": "accepted", "time": 2, "id": 1, "order_id": 9, "side": "buy",
                   "order_type": "limit", "price": 98, "quantity": 3},
                  {"type": "accepted", "time": 2, "id": 2, "order_id": 10, "side": "sell",
                   "order_type": "limit", "price": 102, "quantity": 5}):
            t.apply(m)
        await d.place(Quote(bid=(97, 3), ask=None))
        assert sent == [{"type": "modify", "id": 1, "price": 97, "quantity": 3},
                        {"type": "cancel", "id": 2}]
        sent.clear()
        await d.place(Quote(bid=(97, 3), ask=None))  # the same again: nothing to do
        assert sent == []
        await d.on_message({"type": "modified", "time": 3, "id": 1, "order_id": 9, "price": 97,
                            "quantity": 3})
        t.apply({"type": "modified", "time": 3, "id": 1, "order_id": 9, "price": 97,
                 "quantity": 3})
        await d.place(Quote(bid=(97, 3), ask=None))
        assert sent == []
        # A take cancels its side's quote and crosses at the best price on the other side.
        await d.place(Quote(take=4))
        assert sent[0] == {"type": "cancel", "id": 1}
        assert sent[1]["side"] == "buy" and sent[1]["price"] == 101 and sent[1]["quantity"] == 4
        assert sent[1]["time_in_force"] == "immediate-or-cancel"

    asyncio.run(go())


def test_ws_url():
    assert ws_url("http://localhost:8000") == "ws://localhost:8000/ws"
    assert ws_url("https://arena.example.com/") == "wss://arena.example.com/ws"
    assert ws_url("localhost:8000") == "ws://localhost:8000/ws"


def test_joining_a_plain_http_server_over_https_says_what_to_do(server, tmp_path, monkeypatch):
    from arena_kit.cli import main as kit_main

    monkeypatch.setenv("ARENA_KIT_HOME", str(tmp_path))
    code = server.new_game("host")["code"]
    https = server.url.replace("http://", "https://")
    bot = str(Path(__file__).parents[1] / "kit" / "examples" / "starter_bot.py")
    for extra in ([], ["--token", "anything"]):  # joining, and connecting with a token
        with pytest.raises(SystemExit) as e:
            kit_main(["join", bot, "--server", https, "--code", code, "--name", "b", *extra])
        assert f"use --server {server.url}" in str(e.value)
    # A server that is not there at all.
    with pytest.raises(SystemExit) as e:
        kit_main(["join", bot, "--server", "http://127.0.0.1:9", "--code", code, "--name", "b"])
    assert "cannot reach http://127.0.0.1:9" in str(e.value)
    # A token the server does not know: what the server said, not a network error.
    with pytest.raises(SystemExit) as e:
        kit_main(["join", bot, "--server", server.url, "--code", code, "--name", "b",
                  "--token", "not-a-token"])
    assert str(e.value).startswith("the server said:")
