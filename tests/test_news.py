"""News: headlines for jumps in the true value, the same for screens, live bots, lockstep bots
and offline tests."""

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from arena_kit import Bot, News, Quote, SteppedMarket, run_local
from arena_kit.client import play
from arena_kit.markets import CALM, NEWS, Jump, engine_has_value_path, headline, headlines
from helpers import connect, is_live, recv_until

S = 10**9


@dataclass(frozen=True)
class Newsy:
    """The calm market, with two jumps announced: a test kind, whatever crowdbook supports."""

    name: str = "newsy"
    title: str = "Newsy"
    description: str = "calm, with headlines"

    def scenario(self, seed, duration_s):
        return CALM.scenario(seed, duration_s)

    def jumps(self, seed, duration_s):
        return [Jump(3 * S, 8.4), Jump(6 * S + 250_000_000, -3.4)]


class NewsTrader(Bot):
    """Buys 5 on good news, sells 5 on bad, and notes what it heard."""

    def __init__(self):
        self.heard = []

    def on_turn(self, state):
        for n in state.news:
            self.heard.append((n.time, n.headline, state.time))
        take = sum(5 if n.change > 0 else -5 for n in state.news)
        return Quote(take=take) if take else None


def test_headlines():
    assert headline(8.4) == "Good news: value up about 8"
    assert headline(-3.4) == "Bad news: value down about 3"
    assert headline(0.3) == "Minor news: the value barely moves"
    k = Newsy()
    assert [n.time for n in headlines(k, 1, 60, "instant")] == [3 * S, 6_250_000_000]
    assert [n.time for n in headlines(k, 1, 60, "experts")] == [5 * S, 8_250_000_000]
    assert headlines(k, 1, 60, "crowd")[0].time == 33 * S
    assert headlines(k, 1, 20, "crowd") == []  # after the round's end
    assert headlines(k, 1, 60, "off") == [] and headlines(CALM, 1, 60) == []
    with pytest.raises(ValueError):
        headlines(k, 1, 60, "soon")


def test_offline_news_comes_at_the_first_turn_after_it_is_out():
    m = SteppedMarket(CALM.scenario(2, 20), 2, headlines(Newsy(), 2, 20, "experts"))
    seen = []
    while not m.done:
        st = m.state()
        seen += [(n.headline, st.time) for n in st.news]
        m.step(None)
    assert [h for h, _ in seen] == ["Good news: value up about 8", "Bad news: value down about 3"]
    assert 5 * S <= seen[0][1] < 6 * S + 10**8 and 8_250_000_000 <= seen[1][1] < 9_300_000_000


def test_news_market_draws_its_jumps_when_crowdbook_can_take_them():
    if engine_has_value_path():
        assert NEWS.jumps(3, 600) and "path = [" in NEWS.scenario(3, 60)
    else:
        assert NEWS.jumps(3, 600) == [] and "jump_rate" in NEWS.scenario(3, 60)


@pytest.fixture
def newsy(server):
    server.app.state.arena.kinds["newsy"] = Newsy()
    return server


def test_lockstep_news_matches_offline(newsy):
    server = newsy
    game = server.new_game("alice")
    code = game["code"]
    bot = server.join(code, "reader", "bot")
    trader = NewsTrader()

    async def go():
        task = asyncio.create_task(play(trader, server.url, bot["token"], rounds=1))
        await asyncio.sleep(0.3)
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                    {"kind": "lockstep", "market": "newsy", "duration_s": 30,
                                     "seed": 7, "news": "instant"}, game["token"])
        assert r.status_code == 201, r.text
        return await asyncio.wait_for(task, 60)

    (res,) = asyncio.run(go())
    offline = NewsTrader()
    local = run_local(offline, Newsy(), seed=7, duration_s=30, news="instant")
    assert trader.heard == offline.heard and len(offline.heard) == 2
    assert res["result"]["score"] == pytest.approx(local.score)


def test_live_news_reaches_screens_and_bots(newsy):
    server = newsy
    game = server.new_game("alice")
    code = game["code"]
    bot = server.join(code, "reader", "bot")
    trader = NewsTrader()

    async def go():
        ws = await connect(server, game["token"])
        task = asyncio.create_task(play(trader, server.url, bot["token"], rounds=1))
        await asyncio.sleep(0.3)
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                    {"kind": "live", "market": "newsy", "duration_s": 12,
                                     "speed": 4, "news": "experts"}, game["token"])
        assert r.status_code == 201, r.text
        first = await recv_until(ws, lambda m: m["type"] == "news")
        second = await recv_until(ws, lambda m: m["type"] == "news")
        await recv_until(ws, lambda m: m["type"] == "round_ended")
        await ws.close()
        await asyncio.wait_for(task, 30)
        return first, second

    first, second = asyncio.run(go())
    assert first["headline"] == "Good news: value up about 8" and first["time"] == 5 * S
    assert second["change"] == -3
    assert [h for _, h, _ in trader.heard] == [first["headline"], second["headline"]]
    info = server.http.get(f"/api/games/{code}").json()
    assert info["rounds"][0]["config"]["news"] == "experts"


def test_news_setting_is_checked(newsy):
    game = newsy.new_game("alice")
    r = newsy.post(f"/api/games/{game['code']}/rounds",
                   {"kind": "live", "market": "newsy", "news": "soon"}, game["token"])
    assert r.status_code == 400
