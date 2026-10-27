"""Home setup cards (#457) in a real headless browser against the real dashboard server, plus the real HTTP gate.
The cards API is faked at the HTTP edge in the browser test (page.route). Missing Playwright = a failure, never a skip."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from test_dashboard import dash, req  # noqa: F401  (fixture + helper)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

CARD = {"id": "telegram", "title_he": "טלגרם", "title_en": "Telegram", "detail_he": "לא מחובר", "detail_en": "not connected",
        "steps_he": "שלבים", "steps_en": "1. Open BotFather", "guide": "telegram", "failed": 0}


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
    r, body = req(dash, "GET", "/api/home/setup-cards")
    assert r.status == 200 and json.loads(body) == {"enabled": False, "cards": []}
    r, _ = req(dash, "POST", "/api/home/setup-cards", body={"id": "telegram", "answer": "later"})
    assert r.status == 403


def test_card_renders_and_later_removes_it(browser, dash):  # noqa: F811
    state = {"cards": [CARD], "posts": []}

    def handle(route):
        r = route.request
        if r.method == "POST":
            body = json.loads(r.post_data or "{}")
            state["posts"].append(body)
            state["cards"] = []
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": body["id"], "answer": "later", "hidden": True}))
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"enabled": True, "cards": state["cards"]}))

    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    p = ctx.new_page()
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.route(re.compile(r"/api/home/setup-cards(\?.*)?$"), handle)
    p.add_init_script("try{localStorage.setItem('jarves-lang','en')}catch(e){}")
    p.goto(dash.url + "#/home")
    p.wait_for_selector('[data-setup-card="telegram"]', timeout=30000)
    text = p.inner_text('[data-setup-card="telegram"]')
    assert "Telegram" in text and "BotFather" in text
    assert p.locator('[data-setup-card="telegram"] button[data-answer]').count() == 3
    p.click('[data-setup-card="telegram"] button[data-answer="later"]')
    p.wait_for_function("() => !document.querySelector('[data-setup-card]')", timeout=30000)
    assert state["posts"] == [{"id": "telegram", "answer": "later"}]
    assert errs == []
    ctx.close()
