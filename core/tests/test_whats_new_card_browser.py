"""'What changed since your version' card (#167) in a real headless browser against the real dashboard server, plus
the real HTTP gate. The card API is faked at the HTTP edge in the browser test (page.route). Missing Playwright = a
failure, never a skip."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from test_dashboard import dash, req  # noqa: F401  (fixture + helper)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

CARD = {"from": "4.5.1", "to": "4.6.0", "versions": ["4.6.0", "4.5.2"], "more": 3,
        "items": [{"version": "4.6.0", "kind": "Added", "scope": "", "text": "Phone pairing by QR code."},
                  {"version": "4.5.2", "kind": "Fixed", "scope": "music", "text": "Music resumes after a call."}]}


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


def test_api_is_gated_by_the_switch(dash):  # noqa: F811
    r, body = req(dash, "GET", "/api/home/whats-new")
    assert r.status == 200 and json.loads(body) == {"enabled": False, "card": None}
    r, _ = req(dash, "POST", "/api/home/whats-new", body={"action": "dismiss"})
    assert r.status == 403


@pytest.mark.parametrize("lang,title,close", [("en", "What changed since your version", "Got it, close"),
                                              ("he", "מה השתנה מאז הגרסה שלך", "הבנתי, לסגור")])
def test_card_renders_and_dismiss_removes_it(browser, dash, lang, title, close):  # noqa: F811
    state = {"card": CARD, "posts": []}

    def handle(route):
        r = route.request
        if r.method == "POST":
            state["posts"].append(json.loads(r.post_data or "{}"))
            state["card"] = None
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"dismissed": "4.6.0"}))
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"enabled": True, "card": state["card"]}))

    ctx = browser.new_context(viewport={"width": 390, "height": 900})          # phone width: no horizontal scroll
    p = ctx.new_page()
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.route(re.compile(r"/api/home/whats-new(\?.*)?$"), handle)
    p.add_init_script(f"try{{localStorage.setItem('jarves-lang','{lang}')}}catch(e){{}}")
    p.goto(dash.url + "#/home")
    p.wait_for_selector('[data-whats-new="4.6.0"]', timeout=30000)
    text = p.inner_text('[data-whats-new="4.6.0"]')
    assert title in text and "4.5.1 → 4.6.0" in text
    assert "Phone pairing by QR code." in text and "Music resumes after a call." in text
    assert p.locator('[data-whats-new] a[href="#/releasenotes?version=4.6.0"]').count() == 1
    assert p.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    p.click(f'[data-whats-new] button:has-text("{close}")')
    p.wait_for_function("() => !document.querySelector('[data-whats-new]')", timeout=30000)
    assert state["posts"] == [{"action": "dismiss"}]
    assert errs == []
    ctx.close()
