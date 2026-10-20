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
        self.started = None

    def on_start(self, info):
        self.started = info

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


def test_the_news_market_follows_the_arenas_path():
    assert engine_has_value_path()  # crowdbook 0.14 or later, as pinned
    path, jumps = NEWS._walk(3, 60)
    assert jumps and NEWS.jumps(3, 60) == jumps and "path = [" in NEWS.scenario(3, 60)
    # The engine's true value is the path, so a headline tells of a jump players really meet.
    m = SteppedMarket(NEWS.scenario(3, 60), 3)
    while not m.done:
        t = m.state().time
        assert abs(float(m._info["value"][0]) - path[int(t / 250_000_000 + 1e-9)][1]) < 1e-3
        m.step(None)


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
    # A kind without a briefing of its own is briefed with its description, and the news.
    brief = ("calm, with headlines\n\nHeadlines: each jump in the fair value is told to you "
             "the moment")
    assert trader.started["briefing"].startswith(brief)
    assert trader.started["briefing"] == offline.started["briefing"]
    assert trader.started["market"] == "newsy" and trader.started["news"] == "instant"
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
        await ws.send(json.dumps({"type": "ready"}))
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
    assert trader.started["mode"] == "live" and "about 2 seconds" in trader.started["briefing"]
    info = server.http.get(f"/api/games/{code}").json()
    assert info["rounds"][0]["config"]["news"] == "experts"


def test_news_setting_is_checked(newsy):
    game = newsy.new_game("alice")
    r = newsy.post(f"/api/games/{game['code']}/rounds",
                   {"kind": "live", "market": "newsy", "news": "soon"}, game["token"])
    assert r.status_code == 400


def test_the_news_market_end_to_end(server):
    """Real headlines in the news market: lockstep equals offline, and live screens get them on
    the market's clock, with the experts."""
    game = server.new_game("alice")
    code = game["code"]
    bot = server.join(code, "reader", "bot")
    trader = NewsTrader()
    expected = headlines(NEWS, 5, 120, "instant")
    assert len(expected) >= 1

    async def lockstep():
        task = asyncio.create_task(play(trader, server.url, bot["token"], rounds=1))
        await asyncio.sleep(0.3)
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                    {"kind": "lockstep", "market": "news", "duration_s": 120,
                                     "seed": 5, "news": "instant"}, game["token"])
        assert r.status_code == 201, r.text
        return await asyncio.wait_for(task, 60)

    (res,) = asyncio.run(lockstep())
    offline = NewsTrader()
    local = run_local(offline, "news", seed=5, duration_s=120, news="instant")
    assert [h for _, h, _ in trader.heard] == [n.headline for n in expected]
    assert trader.heard == offline.heard
    assert res["result"]["score"] == pytest.approx(local.score)

    async def live():
        ws = await connect(server, game["token"])
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds",
                                    {"kind": "live", "market": "news", "duration_s": 60,
                                     "seed": 3, "speed": 15}, game["token"])
        assert r.status_code == 201, r.text
        await ws.send(json.dumps({"type": "ready"}))
        first = await recv_until(ws, lambda m: m["type"] == "news", timeout=30)
        await asyncio.to_thread(server.post, f"/api/games/{code}/rounds/stop", {},
                                game["token"])
        await recv_until(ws, lambda m: m["type"] == "round_ended")
        await ws.close()
        return first

    first = live()
    first = asyncio.run(first)
    jump = NEWS.jumps(3, 60)[0]
    assert first["time"] == jump.time + 2 * S  # with the experts, the default
    assert first["headline"] == headline(jump.change)


def test_the_bankroll_holds_in_the_news_market():
    class AllIn(Bot):
        def __init__(self):
            self.biggest = 0

        def on_turn(self, state):
            self.biggest = max(self.biggest, abs(state.position))
            take = sum(20 if n.change > 0 else -20 for n in state.news)
            return Quote(take=take) if take else None

    bot = AllIn()
    run_local(bot, "news", seed=3, duration_s=300, news="instant", bankroll=300)
    assert bot.biggest <= 3
