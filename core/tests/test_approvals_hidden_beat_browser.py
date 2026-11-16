"""#477 in a real headless browser against the real dashboard server: the moment the approvals tab goes hidden it
sends a ?hidden=1 heartbeat, long before the 20 s hidden interval (which a throttled background tab stretches to
~1/min). The approvals API is faked at the HTTP edge (page.route). Missing Playwright = a failure, never a skip."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from test_dashboard import dash  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

HIDE = """() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
  document.dispatchEvent(new Event('visibilitychange')); }"""


def test_tab_going_hidden_sends_a_hidden_beat_at_once(dash):  # noqa: F811
    from playwright.sync_api import sync_playwright
    seen: list[str] = []

    def handle(route):
        seen.append(route.request.url)
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"pending": [], "history": []}))

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            p = b.new_page()
            p.route(re.compile(r"/api/approvals(\?.*)?$"), handle)
            p.goto(dash.url + "#/approvals")
            p.wait_for_function("() => document.getElementById('apPage') && !document.querySelector('#apPage .muted')",
                                timeout=30000)
            assert seen and not any("hidden=1" in u for u in seen)
            # 3 s < the 20 s hidden interval: only the visibilitychange handler can send this beat
            with p.expect_request(lambda r: "/api/approvals?hidden=1" in r.url, timeout=3000):
                p.evaluate(HIDE)
        finally:
            b.close()
