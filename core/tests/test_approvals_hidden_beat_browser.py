"""#477 in a real headless browser against the real dashboard server: the moment the approvals tab goes hidden it
sends a ?hidden=1 heartbeat. Before, the last visible beat kept the marker fresh for only 15 s while the first hidden
beat came from the 20 s interval, which a throttled background tab stretches to ~1/min: up to ~45 s with nobody
"looking", so an approval could be refused as no_channel. A tab that is really CLOSED also turns
hidden while it unloads; it must NOT send that beat, or a closed dashboard counts as a place to answer for 90 s.

Clock-free and handler-only: setInterval is stubbed before the page loads (the 2 s / 20 s polls can never stand in for
the handler), requests are recorded on the browser CONTEXT (so a departing page's requests are kept), and "no beat was
sent" is proven by ordering, not by sleeping: the page's next fetch (a sentinel) is awaited, and a beat issued earlier
would have been recorded before it; the marker is compared with the load's own beat (baseline), never time.time().
Browser: the same launch path as the other *_browser.py tests (apc.launch_kwargs: Playwright Chromium or a local
Chrome/Edge, msedge on Windows); the CI core job installs the 'browser' extra. Missing = a failure, never a skip."""
from __future__ import annotations

import importlib.metadata
import shutil
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_dashboard import dash  # noqa: F401  (fixture)

from jarves import approvals as appr
from jarves import dashboard

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

NO_POLL = "window.setInterval = () => 0;"
# #1735: the load sends TWO visible beats (DOMContentLoaded and the page's own refresh); the page renders on one while
# the other may still be in flight and rewrite the marker AFTER a later hidden beat (CI: marker 0.09 s past the
# baseline instead of 75 s). Count the page's own /api/approvals GETs from the call (before the browser's cache lock
# can hold one back) until their response arrives; the server writes the marker before it answers.
INFLIGHT = """(() => { window.__apInflight = 0; const f = window.fetch;
  window.fetch = function (u, o) { const beat = /^\\/api\\/approvals(\\?|$)/.test(String(u)) && !(o && o.method);
    if (!beat) return f.apply(this, arguments);
    window.__apInflight++; const done = () => { window.__apInflight--; };
    return f.apply(this, arguments).then((r) => { done(); return r; }, (e) => { done(); throw e; }); }; })();"""
# regression repro: the FIRST load beat gets a distinct URL (?n=1, ignored by the server), so the browser cannot hold
# the second identical GET behind it and the server can hold the first one back (slow_first_beat)
TAG_FIRST = """(() => { let tagged = false; const f = window.fetch;
  window.fetch = function (u, o) { if (!tagged && String(u) === "/api/approvals") { tagged = true; u = u + "?n=1"; }
    return f.call(this, u, o); }; })();"""
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
# records the real unload events in the console: the
# test proves the browser really ran its unload steps, and these listeners also make headless Chromium run the
# departing page's handlers the way a desktop tab close does (without them the old code's leak was not
# reproduced in headless Chromium). Because this rests on browser behaviour, the leak guard below runs
# the same real close against the leaky variant and requires a hidden beat: a browser update that stops running the
# departing page's handlers fails that guard instead of silently passing the leaky code.
# The sentinel /api/health is diag/service.py's route; DASHBOARD_FRESH_S is approvals.py's 15 s.
WATCH = """() => { const rec = (e) => console.log(e);
  window.addEventListener('pagehide', () => rec('pagehide'));
  document.addEventListener('visibilitychange', () => rec('visibilitychange')); }"""
# the fixed (deferred, unload-aware) hide beat in approvals.js; the leak guard serves a copy that beats at once on
# every hide instead (the first #477 attempt, 703f433), which leaked a 90 s beat from a closing tab
LEAK_GUARD = "setTimeout(() => { if (document.hidden && !leaving) refresh(); }, 0);"
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


def _open(browser, dash, home, init: str = ""):  # noqa: F811
    ctx = browser.new_context()
    urls: list[str] = []
    ctx.on("request", lambda r: urls.append(r.url))
    scripts: list = []                                       # the approvals.js response the page itself ran
    ctx.on("response", lambda r: scripts.append(r) if urlsplit(r.url).path.endswith("/approvals.js") else None)
    ctx.add_init_script(NO_POLL)
    if init:
        ctx.add_init_script(init)                            # runs before INFLIGHT: INFLIGHT wraps it
    ctx.add_init_script(INFLIGHT)
    p = ctx.new_page()
    p.goto(dash.url + "#/approvals")
    p.wait_for_function("() => document.getElementById('apPage') && !document.querySelector('#apPage .muted')",
                        timeout=30000)
    assert any("/api/approvals" in u for u in urls) and not _hidden_beats(urls)
    p.wait_for_function("() => window.__apInflight === 0", timeout=30000)   # #1735: every load beat has landed
    base = _marker_mtime(home)                               # written by the load's visible beat: the baseline
    p.loaded_js = scripts[0].text() if scripts else ""
    return ctx, p, urls, base


@pytest.fixture
def tab(browser, dash, home):  # noqa: F811
    t = _open(browser, dash, home)
    try:
        yield t
    finally:
        t[0].close()


@pytest.fixture
def leaky_tab(browser, dash, home, tmp_path, monkeypatch):  # noqa: F811
    # the server serves a static copy whose approvals.js has the leaky hide beat (no request routing: a routed context
    # delays a departing page's requests, which would hide the leak)
    static = tmp_path / "static"
    shutil.copytree(dashboard.STATIC, static)
    js = (static / "approvals.js").read_text(encoding="utf-8")
    assert LEAK_GUARD in js, "approvals.js changed: update LEAK_GUARD so the leak guard still bites"
    (static / "approvals.js").write_text(js.replace(LEAK_GUARD, "refresh();"), encoding="utf-8")
    monkeypatch.setattr(dashboard, "STATIC", static)
    t = _open(browser, dash, home)
    ran = t[1].loaded_js                                     # the body of the response the page executed, not a refetch
    assert ran and LEAK_GUARD not in ran and "refresh();" in ran, \
        "the page did not run the leaky approvals.js: the leak guard would test nothing"
    try:
        yield t
    finally:
        t[0].close()


def _base(dash) -> str:  # noqa: F811
    """scheme://host:port of the dashboard (its URL carries the sign-in token as a query string)."""
    u = urlsplit(dash.url)
    return f"{u.scheme}://{u.netloc}"


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


@pytest.fixture
def slow_first_beat(dash, monkeypatch):  # noqa: F811  (dash first: it registers the routes)
    """#1735 repro of the CI order: the server holds the first load beat (?n=1) until a hidden beat was handled, then
    lets it rewrite the marker before the hidden response returns. Each wait is bounded at 5 s: a fixed _open waits
    for that beat before any hidden beat exists, so it only costs the bound. Returns the handled ?n=1 count."""
    i = next(i for i, (m, rx, _) in enumerate(dashboard._ROUTES) if m == "GET" and rx.match("/api/approvals"))
    method, rx, handler = dashboard._ROUTES[i]
    seen = {"n": 0}
    hidden_done, first_done = threading.Event(), threading.Event()

    def slow(ctx, m, body, q=None):
        q = q or {}
        if (q.get("hidden") or [""])[0] == "1":
            out = handler(ctx, m, body, q)
            hidden_done.set()
            first_done.wait(5.0)
            return out
        if (q.get("n") or [""])[0] != "1":
            return handler(ctx, m, body, q)
        seen["n"] += 1
        hidden_done.wait(5.0)
        try:
            return handler(ctx, m, body, q)
        finally:
            first_done.set()
    routes = list(dashboard._ROUTES)
    routes[i] = (method, rx, slow)
    monkeypatch.setattr(dashboard, "_ROUTES", routes)
    return seen


def test_a_late_load_beat_cannot_overwrite_the_hidden_beat(slow_first_beat, browser, dash, home):  # noqa: F811
    # #1735: before the fix _open took the baseline while a load beat was still in flight; it then landed after the
    # hidden beat (test_tab_going_hidden_sends_a_hidden_beat_at_once:138 failed with the marker 0.09 s past the baseline)
    ctx, p, _, base = _open(browser, dash, home, TAG_FIRST)
    try:
        with p.expect_request(lambda r: "/api/approvals?hidden=1" in r.url, timeout=10000) as beat:
            p.evaluate(SET_HIDDEN, True)
        beat.value.response()
        assert slow_first_beat["n"] == 1                     # the repro really held a load beat
        assert _marker_mtime(home) - base > appr.DASHBOARD_FRESH_S
    finally:
        ctx.close()


@pytest.mark.parametrize("pagehide_first", [True, False], ids=["pagehide_first", "visibility_first"])
def test_synthetic_unload_sends_no_hidden_beat(tab, pagehide_first):
    # these synthetic orders are what pin the `leaving` check: on a real close the departing page's deferred
    # setTimeout never runs, so the real-close test only tells an immediate beat from a deferred one
    _, p, urls, _ = tab
    p.evaluate(UNLOAD, pagehide_first)
    p.evaluate(DRAIN)
    with p.expect_request(lambda r: "/api/health" in r.url, timeout=5000):        # sentinel: later than any beat
        p.evaluate("() => { fetch('/api/health').catch(() => {}); }")
    assert not _hidden_beats(urls)


def _real_close(p, dash):  # noqa: F811
    order: list[str] = []
    p.on("console", lambda m: order.append(m.text))
    p.evaluate(WATCH)
    p.close(run_before_unload=False)                         # returns once the page is gone (lifecycle complete)
    q = p.context.new_page()                                 # sentinel: a beat from the closed page is recorded first
    with q.expect_request(lambda r: "/api/health" in r.url, timeout=5000):
        q.goto(_base(dash) + "/api/health")
    return _unload_events(order)


def test_leak_guard_leaky_variant_is_caught_on_real_close(leaky_tab, dash):  # noqa: F811
    # guard for the browser-behaviour dependency: the real close must still let a hide beat out of the leaky variant.
    # (Same-tab navigation lets none out even from the leaky variant in this Chromium, so it is not leak coverage.)
    _, p, urls, _ = leaky_tab
    assert _real_close(p, dash)[:2] == ["pagehide", "visibilitychange"]   # both real unload events ran
    assert _hidden_beats(urls), (
        f"real close no longer runs the departing page's handlers (Playwright {_pw_version()}): "
        "most likely a browser/Playwright bump changed unload behaviour, so the close test is moot. "
        "Re-check the real-close repro on this version before changing approvals.js.")


def _pw_version() -> str:
    try:
        return importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def test_real_close_sends_no_hidden_beat(tab, home, dash):  # noqa: F811
    _, p, urls, base = tab
    assert _real_close(p, dash)[:2] == ["pagehide", "visibilitychange"]   # the real unload steps ran
    assert not _hidden_beats(urls)
    assert _marker_mtime(home) - base <= appr.DASHBOARD_FRESH_S   # server side: not dated into the 90 s hidden window
