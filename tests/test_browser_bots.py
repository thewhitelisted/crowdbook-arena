"""Bots pasted into the game page, run in the player's own tab with Pyodide: they play from their
own seat, score in lockstep exactly what they score offline, and show their errors."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from arena_kit import browser, run_local

try:
    from playwright import sync_api as playwright
except ImportError:
    playwright = None
needs_browser = pytest.mark.skipif(playwright is None, reason="the browser group is not installed")

EXAMPLES = Path(__file__).parents[1] / "kit" / "examples"
sys.path.insert(0, str(EXAMPLES))
from starter_bot import StarterBot  # noqa: E402

SEED = 777


def test_the_kit_for_browsers_is_pure_python(server, tmp_path):
    kit = server.http.get("/api/kit").json()
    assert kit["pyodide"].startswith("https://cdn.jsdelivr.net/pyodide/v")
    assert set(kit["files"]) == set(browser.PACKAGE_FILES)
    # The modules a tab gets work with the standard library alone: no numpy, crowdbook or
    # websockets, which a browser does not have.
    pkg = tmp_path / "arena_kit"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(kit["init"])
    for name, text in kit["files"].items():
        (pkg / name).write_text(text)
    script = (
        "import sys, json, asyncio\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from arena_kit import browser\n"
        "reports, sent = [], []\n"
        "r = browser.start(open(sys.argv[2]).read(), sent.append, reports.append)\n"
        "print(json.dumps([r is not None, sorted(m for m in ('numpy', 'websockets', "
        "'crowdbook_learn') if m in sys.modules)]))\n")
    out = subprocess.run([sys.executable, "-I", "-c", script, str(tmp_path),
                          str(EXAMPLES / "starter_bot.py")],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [True, []]


def _turn(turn=1):
    state = {"time": 1, "time_left": 50 * 10**9, "position": 0, "cash": 0.0, "fees": 0.0,
             "bid": [100, 5], "ask": [102, 5], "last_trade": None, "bids": [[100, 5]],
             "asks": [[102, 5]], "my_bid": [99, 2], "my_ask": None, "max_position": 100,
             "max_order_quantity": 20, "reference_price": 100}
    return json.dumps({"type": "turn", "round": 1, "turn": turn, "state": state})


def test_the_runner_reports_errors_and_keeps_quotes_on_a_bad_turn():
    import asyncio

    reports, sent = [], []
    assert browser.start("def broken(:\n", sent.append, reports.append) is None
    assert "syntax error on line 1" in json.loads(reports[-1])["message"]
    assert browser.start("x = 1\n", sent.append, reports.append) is None
    assert "exactly one Bot subclass" in json.loads(reports[-1])["message"]

    source = ("from arena_kit import Bot\n"
              "class Boom(Bot):\n"
              "    def on_turn(self, state):\n"
              "        raise ValueError('boom')\n")
    runner = browser.start(source, sent.append, reports.append)
    asyncio.run(runner.handle(_turn()))
    # The turn's answer leaves the resting quotes as they were.
    assert json.loads(sent[-1]) == {"type": "orders", "turn": 1, "bid": [99, 2], "ask": None,
                                    "take": 0, "take_price": None}
    error = json.loads(reports[-1])
    assert error["type"] == "error" and "ValueError: boom" in error["trace"]


def _open(page, server, game):
    page.goto(server.url)
    page.evaluate(f"sessionStorage.setItem('arena:{game['code']}', '{game['token']}')")
    page.goto(f"{server.url}/g/{game['code']}")
    page.wait_for_selector("#bot-panel:not(.hidden)")


def _phase(page, text, timeout=60000):
    page.wait_for_function(
        f"document.getElementById('bot-phase').textContent.includes({json.dumps(text)})",
        timeout=timeout)


@needs_browser
def test_a_pasted_bot_plays_live_and_lockstep_rounds(bot_server):
    server = bot_server
    game = server.new_game("alice")
    code = game["code"]
    errors = []
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, server, game)
        assert page.input_value("#bot-name") == "alice's bot"
        page.fill("#bot-code", (EXAMPLES / "starter_bot.py").read_text())
        page.click("#bot-deploy")
        _phase(page, "waiting for rounds")
        players = server.http.get(f"/api/games/{code}").json()["players"]
        assert [(x["name"], x["kind"], x["online"]) for x in players] == [
            ("alice", "human", True), ("alice's bot", "bot", True)]

        # A live round: the bot quotes from its own seat, shown in its panel.
        r = server.post(f"/api/games/{code}/rounds",
                        {"kind": "live", "market": "calm", "duration_s": 20, "speed": 4},
                        game["token"])
        assert r.status_code == 201, r.text
        # Alice reads the briefing; her bot, which gets it in on_start, is not asked.
        page.wait_for_selector("#ready-gate:not(.hidden)")
        page.click("#ready-button")
        page.wait_for_function("document.getElementById('bot-quotes').textContent.includes('@')",
                               timeout=30000)
        _phase(page, "round ended")
        live = server.http.get(f"/api/games/{code}/rounds/1").json()["results"]
        assert {x["name"] for x in live} == {"alice", "alice's bot"}

        # A reload carries on as the same bot player.
        page.reload()
        page.wait_for_selector("#bot-panel:not(.hidden)")
        _phase(page, "waiting for rounds")
        info = server.http.get(f"/api/games/{code}").json()
        assert len(info["players"]) == 2
        balance = next(x["balance"] for x in info["players"] if x["kind"] == "bot")

        # A lockstep round scores what `arena-kit test` scores for the same file and seed.
        r = server.post(f"/api/games/{code}/rounds",
                        {"kind": "lockstep", "market": "calm", "duration_s": 30, "seed": SEED},
                        game["token"])
        assert r.status_code == 201, r.text
        _phase(page, "round ended", timeout=90000)
        page.screenshot(path=str(Path(__import__("tempfile").gettempdir()) / "bot.png"))
        b.close()
    (row,) = server.http.get(f"/api/games/{code}/rounds/2").json()["results"]
    offline = run_local(StarterBot(), "calm", seed=SEED, duration_s=30, bankroll=balance / 100)
    assert row["details"]["timeouts"] == 0
    assert row["score"] == pytest.approx(offline.score, abs=1e-6)
    assert row["details"]["turns"] == offline.turns
    assert errors == []


@needs_browser
def test_a_bots_errors_and_output_show_in_its_panel(bot_server):
    server = bot_server
    game = server.new_game("alice")
    code = game["code"]
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        _open(page, server, game)
        page.fill("#bot-code", "def broken(:\n")
        page.click("#bot-deploy")
        page.wait_for_function("document.getElementById('bot-error').textContent"
                               ".includes('syntax error on line 1')", timeout=60000)
        # A file that does not load never joins the game.
        assert len(server.http.get(f"/api/games/{code}").json()["players"]) == 1

        page.fill("#bot-code", "from arena_kit import Bot\n"
                               "class Boom(Bot):\n"
                               "    def on_start(self, info):\n"
                               "        print('hello from', info['market'])\n"
                               "    def on_turn(self, state):\n"
                               "        raise ValueError('boom')\n")
        page.click("#bot-deploy")
        _phase(page, "waiting for rounds")
        r = server.post(f"/api/games/{code}/rounds",
                        {"kind": "lockstep", "market": "calm", "duration_s": 6, "seed": 1},
                        game["token"])
        assert r.status_code == 201, r.text
        _phase(page, "round ended")
        output = page.inner_text("#bot-output")
        assert "hello from calm" in output and "ValueError: boom" in output
        assert "on_turn raised" in page.inner_text("#bot-error")
        page.click("#bot-stop")
        b.close()
    (row,) = server.http.get(f"/api/games/{code}/rounds/1").json()["results"]
    assert row["score"] == 0 and row["details"]["timeouts"] == 0
