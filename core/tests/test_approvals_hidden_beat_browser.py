"""#477 in a real headless browser against the real dashboard server: the moment the approvals tab goes hidden it
sends a ?hidden=1 heartbeat. Before, the last visible beat kept the marker fresh for only 15 s while the first hidden
beat came from the 20 s interval, which a throttled background tab stretches to ~1/min: up to ~45 s with nobody
"looking", so an approval could be refused as no_channel. A tab that is CLOSED (or navigated away) also turns hidden
first; it must NOT send that beat, or a closed dashboard would count as a place to answer for 90 s.
setInterval is stubbed before the page loads, so the 2 s / 20 s polls can never stand in for the handler.
The approvals API is faked at the HTTP edge (page.route). Missing Playwright or browser = a failure, never a skip."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from test_dashboard import dash  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

NO_POLL = "window.setInterval = () => 0;"     # only event handlers may send a beat in these tests
SET_HIDDEN = """(h) => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => h ? 'hidden' : 'visible'});
  document.dispatchEvent(new Event('visibilitychange')); }"""
# what the browser does on close / navigation: visibilitychange (hidden) and pagehide in the same unload steps
CLOSE = """() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: false})); }"""


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


def _open(browser, dash, seen):  # noqa: F811
    def handle(route):
        seen.append(route.request.url)
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"pending": [], "history": []}))
    p = browser.new_page()
    p.add_init_script(NO_POLL)
    p.route(re.compile(r"/api/approvals(\?.*)?$"), handle)
    p.goto(dash.url + "#/approvals")
    p.wait_for_function("() => document.getElementById('apPage') && !document.querySelector('#apPage .muted')",
                        timeout=30000)
    assert seen and not any("hidden=1" in u for u in seen)
    return p


def test_tab_going_hidden_sends_a_hidden_beat_at_once(browser, dash):  # noqa: F811
    p = _open(browser, dash, [])
    with p.expect_request(lambda r: "/api/approvals?hidden=1" in r.url, timeout=1000):
        p.evaluate(SET_HIDDEN, True)
    with p.expect_request(lambda r: r.url.endswith("/api/approvals"), timeout=1000):    # shown again: catch up (#159)
        p.evaluate(SET_HIDDEN, False)


def test_closing_the_tab_sends_no_hidden_beat(browser, dash):  # noqa: F811
    seen: list[str] = []
    p = _open(browser, dash, seen)
    p.evaluate(CLOSE)
    p.wait_for_timeout(1000)
    assert not any("hidden=1" in u for u in seen)       # the marker lapses after 15 s, as for any closed dashboard
