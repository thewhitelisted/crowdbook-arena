"""The trading screen in a real browser: a host and a second tab play a live round."""

import pytest

playwright = pytest.importorskip("playwright.sync_api")


SHOTS = __import__("pathlib").Path(__import__("tempfile").gettempdir())


def human_click(page, locator, hold_s=0.2):
    # The cell is redrawn every update, so it can be replaced while it is measured: try again.
    for _ in range(50):
        box = locator.bounding_box()
        if box:
            break
        page.wait_for_timeout(20)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(hold_s * 1000)
    page.mouse.up()


def test_two_tabs_play_a_live_round(server):
    errors = []
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        a = ctx.new_page()
        a.on("pageerror", lambda e: errors.append(str(e)))
        a.goto(server.url)
        a.fill("#create input[name=host_name]", "alice")
        a.click("#create button")
        a.wait_for_url("**/g/*")
        code = a.url.rsplit("/", 1)[1]

        # A second tab of the same browser joins as someone else.
        b = ctx.new_page()
        b.on("pageerror", lambda e: errors.append(str(e)))
        b.goto(f"{server.url}/g/{code}")
        b.fill("#join input[name=name]", "bob")
        b.click("#join button")
        a.wait_for_function("document.querySelectorAll('#players li').length === 2")
        b.wait_for_function("document.getElementById('me').textContent.includes('bob')")
        assert "you are bob" in b.text_content("#me")
        assert "you are alice (host)" in a.text_content("#me")
        assert b.is_hidden("#host-controls")

        a.fill("#duration", "40")
        a.fill("#speed", "4")
        a.click("#start-round")
        b.wait_for_selector("#trading:not(.hidden)")
        b.wait_for_function(
            "document.querySelectorAll('#ladder td.bidq:not(:empty)').length > 3")
        assert "/ 0:40" in b.text_content("#clock-text")
        assert "100 lots" in b.text_content("#limits")

        # A bid from the ladder, clicked as a person clicks: the button held down for a fifth of
        # a second, across several updates of the book.
        human_click(b, b.locator("#ladder td.bidq:not(:empty)").nth(3))
        b.wait_for_function("document.querySelectorAll('#orders tr').length >= 1",
                            timeout=3000)
        # Clicking the yellow order on the ladder cancels it, clicked the same way.
        human_click(b, b.locator("#ladder td.mine:not(:empty)").first)
        b.wait_for_function("document.querySelectorAll('#orders tr').length === 0",
                            timeout=3000)
        human_click(b, b.locator("#ladder td.bidq:not(:empty)").nth(3))
        b.wait_for_function("document.querySelectorAll('#orders tr').length >= 1",
                            timeout=3000)
        b.fill("#qty", "3")
        b.click("#buy-market")
        b.wait_for_function("document.getElementById('position').textContent !== '0'")
        assert b.locator("#tape tr").count() > 0
        # The chart draws the price, and the buy as a green marker.
        b.wait_for_timeout(300)
        colours = b.evaluate("""() => {
            const c = document.getElementById('chart');
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let line = 0, green = 0;
            for (let i = 0; i < d.length; i += 4) {
                if (d[i + 3] < 200) continue;
                if (d[i + 2] > 200 && d[i] < 120) line++;                    // the accent blue
                if (d[i + 1] > 150 && d[i] < 100 && d[i + 2] < 160) green++; // the bid green
            }
            return {line, green};
        }""")
        assert colours["line"] > 20 and colours["green"] > 5, colours
        # The hover crosshair shows the time and price under the pointer.
        box = b.locator("#chart").bounding_box()
        b.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
        b.wait_for_selector("#chart-tip:not(.hidden)")
        assert ":" in b.text_content("#chart-tip")
        b.mouse.move(0, 0)
        b.wait_for_selector("#chart-tip.hidden", state="attached")
        b.screenshot(path=str(SHOTS / "trading.png"))

        # Someone who joins mid-round watches the market without a seat.
        c = ctx.new_page()
        c.on("pageerror", lambda e: errors.append(str(e)))
        c.goto(f"{server.url}/g/{code}")
        c.fill("#join input[name=name]", "carol")
        c.click("#join button")
        c.wait_for_selector("#trading:not(.hidden)")
        c.wait_for_function(
            "document.querySelectorAll('#ladder td.bidq:not(:empty)').length > 3")
        assert "watching" in c.text_content("#spectating")
        assert c.locator("#tape tr").count() > 0
        # The Cancel button in the order list, held the same way.
        human_click(b, b.locator("#orders button").first)
        b.wait_for_function("document.querySelectorAll('#orders tr').length === 0",
                            timeout=3000)
        human_click(b, b.locator("#ladder td.bidq:not(:empty)").nth(3))
        b.wait_for_function("document.querySelectorAll('#orders tr').length >= 1",
                            timeout=3000)
        b.click("#cancel-all")
        b.wait_for_function("document.querySelectorAll('#orders tr').length === 0")

        # The host ends the round; both tabs show the results.
        a.once("dialog", lambda d: d.accept())
        a.click("#stop-round")
        for page in (a, b):
            page.wait_for_selector("#results:not(.hidden)", timeout=15000)
        rows = b.locator("#results-body tr")
        assert rows.count() == 2
        assert "bob" in b.text_content("#results-body")
        assert b.is_hidden("#trading")
        browser.close()
    assert errors == []


def test_a_challenge_in_the_browser(server):
    errors = []
    game = server.new_game("alice")
    code = game["code"]
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        b = ctx.new_page()
        b.on("pageerror", lambda e: errors.append(str(e)))
        b.goto(f"{server.url}/g/{code}")
        b.fill("#join input[name=name]", "bob")
        b.click("#join button")
        b.wait_for_selector("#lobby:not(.hidden)")
        r = server.post(f"/api/games/{code}/rounds", {"kind": "challenge",
                                                       "challenge": "conversion",
                                                       "duration_s": 600}, game["token"])
        assert r.status_code == 201
        b.wait_for_selector("#challenge:not(.hidden)")
        assert b.locator("#challenge-data table tbody tr").count() == 5
        steps = b.locator(".ans-step")
        steps.nth(0).select_option("Pearls")
        steps.nth(1).select_option("Shells")
        b.click("#challenge-form button")
        b.wait_for_function("document.getElementById('challenge-saved').textContent"
                            ".includes('Pearls')")
        server.post(f"/api/games/{code}/rounds/stop", {}, game["token"])
        b.wait_for_selector("#results:not(.hidden)")
        solution = b.text_content("#solution")
        assert "The best answer" in solution and "You started with 1000 Shells" in solution
        assert "Your answer, Shells → Pearls → Shells, ends with" in solution
        assert "Pearls" in b.text_content("#results-body")
        assert b.is_hidden("#challenge")
        b.wait_for_selector("#leaderboard:not(.hidden)")
        b.click("#board-tabs button[data-board=challenge]")
        assert "bob" in b.text_content("#board-body")
        browser.close()
    assert errors == []


def test_news_in_the_browser(server):
    from test_news import Newsy

    server.app.state.arena.kinds["newsy"] = Newsy()
    errors = []
    game = server.new_game("alice")
    code = game["code"]
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        a = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        a.on("pageerror", lambda e: errors.append(str(e)))
        a.goto(server.url)
        a.evaluate(f"sessionStorage.setItem('arena:{code}', '{game['token']}')")
        a.goto(f"{server.url}/g/{code}")
        a.wait_for_selector("#host-controls:not(.hidden)")
        a.select_option("#market", "newsy")
        assert a.is_visible("#news-delay")
        a.select_option("#news-delay", "instant")
        a.fill("#duration", "20")
        a.fill("#speed", "4")
        a.click("#start-round")
        a.wait_for_selector("#news-panel:not(.hidden)")
        a.wait_for_function("document.getElementById('news').textContent.includes('Good news')",
                            timeout=10000)
        a.screenshot(path=str(SHOTS / "news.png"))
        browser.close()
    assert errors == []


def test_the_chart_fills_its_width_early_in_a_long_round(server):
    errors = []
    game = server.new_game("alice")
    code = game["code"]
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        a = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        a.on("pageerror", lambda e: errors.append(str(e)))
        a.goto(server.url)
        a.evaluate(f"sessionStorage.setItem('arena:{code}', '{game['token']}')")
        a.goto(f"{server.url}/g/{code}")
        a.wait_for_selector("#host-controls:not(.hidden)")
        a.fill("#duration", "3600")
        a.fill("#speed", "4")
        a.click("#start-round")
        a.wait_for_selector("#trading:not(.hidden)")
        a.wait_for_timeout(2500)  # about ten seconds of a round an hour long
        span = a.evaluate("""() => {
            const c = document.getElementById('chart');
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let lo = c.width, hi = 0;
            for (let i = 0; i < d.length; i += 4) {
                if (d[i + 3] > 200 && d[i + 2] > 200 && d[i] < 120) {
                    const x = (i / 4) % c.width;
                    lo = Math.min(lo, x); hi = Math.max(hi, x);
                }
            }
            return (hi - lo) / c.width;
        }""")
        assert span > 0.75, span
        # A range button narrows the window, and All brings the whole round back.
        a.click("#chart-ranges button[data-range='30s']")
        a.click("#chart-ranges button[data-range='All']")
        assert "primary" in a.get_attribute("#chart-ranges button[data-range='All']", "class")
        a.screenshot(path=str(SHOTS / "chart.png"))
        r = server.http.get("/static/game.js")
        assert r.headers["cache-control"] == "no-cache"
        browser.close()
    assert errors == []
