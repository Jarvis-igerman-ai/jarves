"""#477 in a real headless browser against the real dashboard server: the moment the approvals tab goes hidden it
sends a ?hidden=1 heartbeat. Before, the last visible beat kept the marker fresh for only 15 s while the first hidden
beat came from the 20 s interval, which a throttled background tab stretches to ~1/min: up to ~45 s with nobody
"looking", so an approval could be refused as no_channel. A tab that is really CLOSED or navigated away also turns
hidden while it unloads; it must NOT send that beat, or a closed dashboard counts as a place to answer for 90 s.

Clock-free and handler-only: setInterval is stubbed before the page loads (the 2 s / 20 s polls can never stand in for
the handler), requests are recorded on the browser CONTEXT (so a departing page's requests are kept), and "no beat was
sent" is proven by ordering, not by sleeping: the page's next fetch (a sentinel) is awaited, and a beat issued earlier
would have been recorded before it; the marker is compared with the load's own beat (baseline), never time.time().
Browser: the same launch path as the other *_browser.py tests (apc.launch_kwargs: Playwright Chromium or a local
Chrome/Edge, msedge on Windows); the CI core job installs the 'browser' extra. Missing = a failure, never a skip."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_dashboard import dash  # noqa: F401  (fixture)

from jarves import approvals as appr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

NO_POLL = "window.setInterval = () => 0;"
SET_HIDDEN = """(h) => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => h ? 'hidden' : 'visible'});
  document.dispatchEvent(new Event('visibilitychange')); }"""
# synthetic unload, for targeted handler coverage of both in-task orders: Chromium's real close fires pagehide first,
# then visibilitychange (hidden) (seen in this browser); the reverse order must be handled the same way
UNLOAD = """(pagehideFirst) => {
  const hide = () => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
    document.dispatchEvent(new Event('visibilitychange')); };
  const ph = () => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: false}));
  if (pagehideFirst) { ph(); hide(); } else { hide(); ph(); } }"""
# records the real unload events (console for a closed page, sessionStorage for the next same-origin document): the
# test proves the browser really ran its unload steps, and these listeners also make headless Chromium run the
# departing page's handlers the way a desktop tab close does (without them the old code's leak was not reproduced)
WATCH = """() => { const rec = (e) => { console.log(e);
    sessionStorage.setItem('unload', (sessionStorage.getItem('unload') || '') + e + ' '); };
  window.addEventListener('pagehide', () => rec('pagehide'));
  document.addEventListener('visibilitychange', () => rec('visibilitychange')); }"""
DRAIN = "() => new Promise(r => setTimeout(() => setTimeout(r, 0), 0))"     # let deferred (setTimeout 0) tasks run


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def tab(browser, dash, home):  # noqa: F811
    ctx = browser.new_context()
    urls: list[str] = []
    ctx.on("request", lambda r: urls.append(r.url))
    ctx.add_init_script(NO_POLL)
    p = ctx.new_page()
    p.goto(dash.url + "#/approvals")
    p.wait_for_function("() => document.getElementById('apPage') && !document.querySelector('#apPage .muted')",
                        timeout=30000)
    assert any("/api/approvals" in u for u in urls) and not _hidden_beats(urls)
    base = _marker_mtime(home)                               # written by the load's visible beat: the baseline
    try:
        yield ctx, p, urls, base
    finally:
        ctx.close()


def _hidden_beats(urls):
    return [u for u in urls if "/api/approvals?hidden=1" in u]


def _marker_mtime(home) -> float:
    return (home / "approvals" / "dashboard_seen").stat().st_mtime


def _unload_events(events) -> list[str]:
    return [e for e in events if e in ("pagehide", "visibilitychange")]


def test_tab_going_hidden_sends_a_hidden_beat_at_once(tab, home):
    _, p, _, base = tab
    with p.expect_request(lambda r: "/api/approvals?hidden=1" in r.url, timeout=5000) as beat:
        p.evaluate(SET_HIDDEN, True)
    beat.value.response()                                    # the server handled it: marker dated forward (90 s)
    assert _marker_mtime(home) - base > appr.DASHBOARD_FRESH_S     # baseline-relative: no wall-clock read
    with p.expect_request(lambda r: r.url.endswith("/api/approvals"), timeout=5000):    # shown again: catch up (#159)
        p.evaluate(SET_HIDDEN, False)


@pytest.mark.parametrize("pagehide_first", [True, False], ids=["pagehide_first", "visibility_first"])
def test_synthetic_unload_sends_no_hidden_beat(tab, pagehide_first):
    _, p, urls, _ = tab
    p.evaluate(UNLOAD, pagehide_first)
    p.evaluate(DRAIN)
    with p.expect_request(lambda r: "/api/health" in r.url, timeout=5000):        # sentinel: later than any beat
        p.evaluate("() => { fetch('/api/health').catch(() => {}); }")
    assert not _hidden_beats(urls)


@pytest.mark.parametrize("how", ["close", "navigate"])
def test_real_close_or_navigation_sends_no_hidden_beat(tab, home, dash, how):  # noqa: F811
    ctx, p, urls, base = tab
    order: list[str] = []
    p.on("console", lambda m: order.append(m.text))
    p.evaluate(WATCH)
    if how == "close":
        p.close(run_before_unload=False)                     # returns once the page is gone (lifecycle complete)
        assert _unload_events(order)[:2] == ["pagehide", "visibilitychange"]   # the real unload steps ran (Chromium's order)
    else:
        p.goto(dash.url.split("/?")[0] + "/api/health", wait_until="load")   # returns once the old document unloaded
        seen = p.evaluate("() => sessionStorage.getItem('unload') || ''").split()
        assert _unload_events(seen)[:2] == ["pagehide", "visibilitychange"]
    assert not _hidden_beats(urls)
    assert _marker_mtime(home) - base <= appr.DASHBOARD_FRESH_S   # server side: not dated into the 90 s hidden window
