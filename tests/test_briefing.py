"""Briefings and practice data: what players are told before a round, the same on screens, for
bots in every kind of round and offline; and recorded public markets to study."""

import asyncio
import csv

import pytest

from arena_kit import Bot, PUBLIC_KINDS, run_local
from arena_kit.cli import main as kit_main
from arena_kit.client import play
from arena_kit.markets import NEWS, briefing_of
from arena_kit.practice import record
from crowdbook_arena.games import briefing
from helpers import connect, recv_until


class Listener(Bot):
    """Does nothing; keeps what on_start told it."""

    def __init__(self):
        self.started = None

    def on_start(self, info):
        self.started = info


@pytest.mark.parametrize("kind", sorted(PUBLIC_KINDS))
def test_public_briefings_tell_the_rules_and_not_the_path(kind):
    k = PUBLIC_KINDS[kind]
    text = briefing_of(k, 5, 90)
    assert "1 minute 30 seconds" in text
    for rule in ("Who trades", "Fees", "0.2 tick", "100 lots", "Score"):
        assert rule in text
    # The same for every seed: nothing in it comes from the seed's path.
    assert briefing_of(k, 6, 90) == text
    assert ("Headlines:" in briefing_of(k, 5, 90, "crowd")) == (kind == "news")


def test_a_kind_without_a_briefing_is_briefed_with_its_description():
    class Plain:
        name, title, description = "plain", "Plain", "a plain market"

        def scenario(self, seed, duration_s):
            return ""

    class Broken(Plain):
        def briefing(self, seed, duration_s):
            raise RuntimeError("no")

    assert briefing_of(Plain(), 1, 60, "experts") == "a plain market"
    assert briefing(Broken(), 1, 60, None) == "a plain market"


def test_offline_bots_get_the_briefing():
    bot = Listener()
    run_local(bot, "news", seed=3, duration_s=20, news="crowd")
    assert bot.started["briefing"] == briefing_of(NEWS, 3, 20, "crowd")
    assert "about 30 seconds" in bot.started["briefing"]


@pytest.mark.parametrize("mode", ["lockstep", "live"])
def test_screens_and_bots_get_the_briefing(server, mode):
    game = server.new_game("alice")
    code = game["code"]
    bot = server.join(code, "listener", "bot")
    listener = Listener()

    async def go():
        ws = await connect(server, game["token"])
        task = asyncio.create_task(play(listener, server.url, bot["token"], rounds=1))
        await asyncio.sleep(0.3)
        spec = {"kind": mode, "market": "trending", "duration_s": 10}
        spec.update({"speed": 5} if mode == "live" else {"seed": 9})
        r = await asyncio.to_thread(server.post, f"/api/games/{code}/rounds", spec,
                                    game["token"])
        assert r.status_code == 201, r.text
        started = await recv_until(ws, lambda m: m["type"] == "round_started")
        await recv_until(ws, lambda m: m["type"] == "round_ended", timeout=40)
        await ws.close()
        await asyncio.wait_for(task, 40)
        return started

    started = asyncio.run(go())
    expected = briefing_of(PUBLIC_KINDS["trending"], 0, 10)
    assert started["round"]["config"]["briefing"] == expected
    assert listener.started["briefing"] == expected
    assert listener.started["mode"] == mode and listener.started["market"] == "trending"
    assert listener.started["duration_s"] == 10
    info = server.http.get(f"/api/games/{code}").json()
    assert "briefing" not in info["rounds"][0]["config"]
    round_ = server.http.get(f"/api/games/{code}/rounds/1").json()["round"]
    assert round_["config"]["briefing"] == expected


def test_the_lobby_previews_a_briefing(server):
    r = server.http.get("/api/kinds/news/briefing", params={"duration_s": 120, "news": "off"})
    assert r.status_code == 200
    assert r.json()["briefing"] == briefing_of(NEWS, 0, 120, "off")
    assert "none this round" in r.json()["briefing"]
    assert server.http.get("/api/kinds/nope/briefing").status_code == 404
    assert server.http.get("/api/kinds/calm/briefing?duration_s=x").status_code == 400
    assert server.http.get("/api/kinds/calm/briefing?news=soon").status_code == 400


def test_practice_data_is_the_same_for_a_seed():
    a, b = record("news", 4, 120), record("news", 4, 120)
    assert a == b
    assert a != record("news", 5, 120)
    assert len(a.ticks) == 4 * 119 + 1  # every quarter second after the first, warm-up second
    assert a.trades and a.headlines
    assert all(t.mid is None or t.bid < t.mid < t.ask for t in a.ticks)
    # The true value follows the path the news market draws for this seed.
    path = dict(NEWS._walk(4, 120)[0])
    assert a.ticks[-1].value == pytest.approx(path[120.0], abs=1e-3)
    with pytest.raises(KeyError):
        record("private-kind", 1, 60)


def test_practice_from_the_command_line(tmp_path, capsys):
    kit_main(["practice", "--market", "news", "--seeds", "1-2", "--duration", "30",
              "--out", str(tmp_path)])
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted(f"news-{s}-{f}" for s in (1, 2)
                           for f in ("book.csv", "trades.csv", "headlines.csv", "briefing.txt"))
    rows = list(csv.DictReader((tmp_path / "news-1-book.csv").open()))
    assert rows[0].keys() == {"time_s", "bid", "ask", "mid", "last_trade", "volume", "value"}
    assert float(rows[-1]["time_s"]) == 30
    first = (tmp_path / "news-1-book.csv").read_bytes()
    kit_main(["practice", "--market", "news", "--seeds", "1", "--duration", "30",
              "--out", str(tmp_path)])
    assert (tmp_path / "news-1-book.csv").read_bytes() == first
    assert "seed     2" in capsys.readouterr().out
