"""The trading screen in a real browser: a host and a second tab play a live round."""

import pytest

playwright = pytest.importorskip("playwright.sync_api")


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

        # A bid from the ladder, then a market buy from the ticket.
        b.locator("#ladder td.bidq:not(:empty)").nth(3).click()
        b.wait_for_function("document.querySelectorAll('#orders tr').length >= 1")
        b.fill("#qty", "3")
        b.click("#buy-market")
        b.wait_for_function("document.getElementById('position').textContent !== '0'")
        assert b.locator("#tape tr").count() > 0

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
