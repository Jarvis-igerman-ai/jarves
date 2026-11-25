"""#206 H2 (review R1 of #1680): the dashboard's real "Confirm, remember it" button, in a real headless browser against
the real dashboard server and preference store. It replaces a source-text check: the click must send the words the page
shows, so a plain confirm works and a reword made after the page loaded is refused (409) and stays proposed.
Missing Playwright or browser = a failure, never a skip (CI installs both)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_dashboard import dash  # noqa: F401  (fixture)

from jarves import preferences as prefs_mod
from jarves.userdata import open_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

OLD, NEW = "short answers in the morning", "always cc reports to someone else"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


def _open(browser, dash, home):  # noqa: F811
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(OLD, "I like short answers in the morning", by="voice")["item"]["id"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 800})
    page = ctx.new_page()
    posts: list = []
    page.on("request", lambda r: posts.append(r.post_data_json) if r.url.endswith("/api/prefs/op") else None)
    page.add_init_script("try{localStorage.setItem('jarves-lang','en')}catch(e){}")
    page.goto(dash.url + "#/preferences")
    page.get_by_role("button", name="Confirm, remember it").wait_for(timeout=30000)
    assert OLD in page.inner_text("main")
    return ctx, page, p, pid, posts


def _click_confirm(page):
    with page.expect_response(lambda r: r.url.endswith("/api/prefs/op"), timeout=30000) as resp:
        page.get_by_role("button", name="Confirm, remember it").click()
    return resp.value.status


def test_dashboard_confirm_button_sends_the_shown_words_and_remembers(browser, dash, home):  # noqa: F811
    ctx, page, p, pid, posts = _open(browser, dash, home)
    try:
        assert _click_confirm(page) == 200 and posts == [{"op": "confirm", "id": pid, "text": OLD}]
        assert p.for_prompt() == [OLD]
    finally:
        ctx.close()


def test_dashboard_confirm_button_refuses_a_reword_made_after_the_page_loaded(browser, dash, home):  # noqa: F811
    ctx, page, p, pid, posts = _open(browser, dash, home)
    try:
        p.edit(pid, NEW, by="voice", repropose=True)               # the model rewords it after the page showed OLD
        assert _click_confirm(page) == 409 and posts == [{"op": "confirm", "id": pid, "text": OLD}]
        assert p.get(pid)["state"] == "proposed" and p.get(pid)["text"] == NEW and p.for_prompt() == []
        # review R1 of #1766: the page reloads the list, shows the NEW words and says why; a second click is a fresh
        # decision on the words now shown, not another 409
        page.wait_for_function(f"() => document.querySelector('main').innerText.includes({NEW!r})", timeout=30000)
        assert page.locator("main b").all_inner_texts() == [NEW]           # the preference shown is the new wording
        assert "The wording changed meanwhile" in page.inner_text("main")
        assert _click_confirm(page) == 200 and posts[-1] == {"op": "confirm", "id": pid, "text": NEW}
        assert p.for_prompt() == [NEW]
    finally:
        ctx.close()
