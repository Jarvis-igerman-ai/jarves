"""#1557 (review R1 of #1625): the privacy switch on the dashboard shows the EN/HE note when the scrub of older text
was only partial, in a real headless browser against the real dashboard; only the switch's answer is faked.
Missing Playwright or browser = a failure, never a skip (CI installs both)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from test_dashboard import dash  # noqa: F401  (fixture)

from jarves import privacy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


def _switch(browser, dash, partial, lang, then=None):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    p = ctx.new_page()
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    state = {"privacy": False}

    def api(route):
        if route.request.method == "POST":
            state["privacy"] = True
            scrub = {"ledger": 0, "plan_steps": 0, "partial": ["memory.db"] if partial else []}
            body = {**state, "saved_in_config": True, "config_error": "", **privacy._scrub_answer(scrub)}
        else:
            body = {**state, "banner_en": "" if state["privacy"] else "dev mode", "banner_he": ""}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    p.route(re.compile(r"/api/privacy$"), api)
    p.add_init_script(f"try{{localStorage.setItem('jarves-lang','{lang}')}}catch(e){{}}")
    p.goto(dash.url + "#/home")
    p.wait_for_selector("#privacyBtn", timeout=30000)
    p.click("#privacyBtn")
    p.wait_for_selector('#privacyBtn[aria-pressed="true"]', timeout=30000)   # the top bar was drawn again
    note = p.locator("#privacyScrubNote")
    text = note.inner_text() if note.count() else ""
    if then:                                                  # the owner picks another language afterwards
        p.select_option("#langSlot select", then)
        p.wait_for_selector(f'html[lang="{then}"]', timeout=30000)
        try:                                                  # the top bar is drawn again with Hebrew text
            p.wait_for_selector('#privacyScrubNote:text-matches("[\u05d0-\u05ea]")', timeout=10000)
        except Exception:                                     # playwright TimeoutError: report what is shown
            pass
        note = p.locator("#privacyScrubNote")
        text = note.inner_text() if note.count() else ""
    ctx.close()
    assert errs == []
    return text


def test_a_partial_scrub_shows_the_note_in_english_and_hebrew(browser, dash):  # noqa: F811
    assert _switch(browser, dash, True, "en") == privacy.SCRUB_PARTIAL_NOTE
    assert _switch(browser, dash, True, "he") == privacy.SCRUB_PARTIAL_NOTE_HE


def test_the_note_follows_a_later_language_change(browser, dash):  # noqa: F811
    assert _switch(browser, dash, True, "en", then="he") == privacy.SCRUB_PARTIAL_NOTE_HE   # #1625 R6


def test_a_full_scrub_shows_no_note(browser, dash):  # noqa: F811
    assert _switch(browser, dash, False, "en") == ""
