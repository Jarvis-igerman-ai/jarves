"""#1514: the Learned preferences page, in a real headless browser against the real dashboard server, posts the
wording it showed with Confirm, and on a 409 PREF_CONFLICT reloads the list (showing the new words) instead of an
error. /api/prefs is faked at the HTTP edge. Missing Playwright or browser = a failure, never a skip."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from test_agents_pr_card_browser import goto_retry
from test_dashboard import dash  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

OLD, NEW = "short answers in the morning", "long answers in the morning"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


def test_confirm_posts_the_shown_words_and_a_conflict_reloads_the_new_words(browser, dash):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    p = ctx.new_page()
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    shown = {"text": OLD}                 # what GET /api/prefs lists; the conflicting POST rewords it

    def prefs(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "in_prompt": False, "prompt_text": "", "items": [], "declined": [], "topics": ["voice", "other"],
            "proposed": [{"id": 7, "text": shown["text"], "topic": "voice", "state": "proposed",
                          "evidence": "I said so", "stale": False}]}))

    def op(route):
        shown["text"] = NEW
        route.fulfill(status=409, content_type="application/json", body=json.dumps(
            {"error": "reworded since you looked", "code": "PREF_CONFLICT"}))
    p.route(re.compile(r"/api/prefs/op$"), op)
    p.route(re.compile(r"/api/prefs(\?.*)?$"), prefs)
    p.add_init_script("try{localStorage.setItem('jarves-lang','en')}catch(e){}")
    goto_retry(p, dash.url + "#/preferences")
    p.get_by_text(OLD).wait_for(timeout=30000)
    with p.expect_request(lambda r: r.method == "POST" and r.url.endswith("/api/prefs/op"), timeout=30000) as req:
        p.get_by_role("button", name="Confirm, remember it").click(timeout=30000)
    assert json.loads(req.value.post_data) == {"op": "confirm", "id": 7, "text": OLD}
    p.get_by_text(NEW).wait_for(timeout=30000)          # reloaded with the new words, still to be confirmed
    p.get_by_text("The wording changed meanwhile: check it again and confirm").wait_for(timeout=30000)
    assert p.get_by_text("Failed:").count() == 0 and p.get_by_role("button", name="Confirm, remember it").count() == 1
    assert errs == []
    ctx.close()
