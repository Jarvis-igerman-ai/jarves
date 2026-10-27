"""Browser live view B2 (#397): token, expiry, loopback-only, test-site-only, read-only, Stop. Fake driver and fake clock;
the end-to-end part serves a local test site, renders a real PNG of it in headless Edge/Chromium and shows it in the real
dashboard page. No public page, no network."""
from __future__ import annotations

import base64
import http.server
import json
import re
import sys
import threading
from pathlib import Path

import pytest
from test_dashboard import dash, req  # noqa: F401  (fixture + helper)

from jarves import browser_liveview as lv
from jarves import browser_pilot as bp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n-fake-frame"
LOCAL = "http://localhost:8123/test"


class Drv:
    def __init__(self, url=LOCAL):
        self.url, self.closed, self.shots = url, False, 0

    @property
    def current_url(self):
        return self.url

    def pin_hosts(self, m):
        pass

    def open(self, url):
        self.url = url

    def text(self):
        return "page"

    def screenshot(self):
        self.shots += 1
        return PNG

    def close(self):
        self.closed = True


def mk(tmp_path, url=LOCAL, on=True, **kw):
    t = [1000.0]
    d = Drv(url)
    s = bp.PilotSession(tmp_path, d, enabled=lambda: on, clock=lambda: t[0], resolver=lambda h: ["93.184.216.34"])
    v = lv.LiveView(s, enabled=lambda: on, clock=lambda: t[0], **kw)
    return v, s, d, t


def code(fn, *a):
    with pytest.raises(lv.LiveError) as e:
        fn(*a)
    return e.value.code


def test_switch_is_beta_and_off_by_default():
    from jarves import features as feat
    assert feat.CATALOG[lv.FEATURE].default_enabled is False and feat.stage_of(lv.FEATURE) == "beta"


def test_start_frame_stop_happy_path(tmp_path):
    v, s, d, t = mk(tmp_path)
    r = v.start()
    assert len(r["grant"]) >= 40 and r["expires_in_s"] == 300
    f = v.frame(r["grant"])
    assert base64.b64decode(f["png_b64"]) == PNG and f["seq"] == 1
    assert v.stop() == {"stopped": True} and d.closed and s.state == bp.STOPPED
    assert code(v.frame, r["grant"]) == "LIVEVIEW_TOKEN"


def test_off_refuses(tmp_path):
    v, *_ = mk(tmp_path, on=False)
    assert code(v.start) == "LIVEVIEW_OFF"


@pytest.mark.parametrize("url", ["https://example.com/", "http://10.0.0.5/", "ftp://localhost/", "http://evil.localhost:8123/"])
def test_test_site_only_at_start(tmp_path, url):
    v, _, d, _ = mk(tmp_path, url=url)
    assert code(v.start) == "BROWSER_PILOT_VIEW_TEST_SITE" and d.shots == 0


def test_leaving_the_test_site_ends_the_view(tmp_path):
    v, s, d, t = mk(tmp_path)
    tok = v.start()["grant"]
    d.url = "https://example.com/"
    t[0] += 1
    assert code(v.frame, tok) == "BROWSER_PILOT_VIEW_TEST_SITE"
    d.url = LOCAL
    assert code(v.frame, tok) == "LIVEVIEW_TOKEN"                  # the token is revoked: coming back does not reopen it


def test_wrong_token_and_expiry(tmp_path):
    v, _, _, t = mk(tmp_path, ttl_s=60)
    tok = v.start()["grant"]
    for bad in ("", "x" * 43, None, 5, tok + "a"):
        assert code(v.frame, bad) == "LIVEVIEW_TOKEN"
    t[0] += 61
    assert code(v.frame, tok) == "LIVEVIEW_EXPIRED"
    assert code(v.frame, tok) == "LIVEVIEW_TOKEN"


def test_new_start_revokes_the_old_token(tmp_path):
    v, *_ = mk(tmp_path)
    a = v.start()["grant"]
    b = v.start()["grant"]
    assert a != b and code(v.frame, a) == "LIVEVIEW_TOKEN" and v.frame(b)["seq"] == 1


def test_frames_are_rate_limited_without_new_screenshots(tmp_path):
    v, _, d, t = mk(tmp_path)
    tok = v.start()["grant"]
    n = d.shots
    v.frame(tok)
    v.frame(tok)
    assert d.shots == n + 1
    t[0] += 1
    assert v.frame(tok)["seq"] == 2


def test_capped_session_ends_the_view(tmp_path):
    v, s, _, t = mk(tmp_path, ttl_s=10_000)
    tok = v.start()["grant"]
    t[0] += bp.MAX_SECONDS + 1
    assert code(v.frame, tok) == "BROWSER_PILOT_CAP" and s.state == bp.STOPPED


def test_frame_costs_no_action_and_receipts_are_kind_only(tmp_path):
    v, s, _, t = mk(tmp_path)
    tok = v.start()["grant"]
    seq = s.seq
    for _ in range(5):
        t[0] += 1
        v.frame(tok)
    assert s.seq == seq
    v.stop()
    audit = (tmp_path / bp.AUDIT_FILE).read_text(encoding="utf-8")
    assert tok not in audit and "localhost" not in audit and "view:start" in audit and "view:stop" in audit


def test_token_never_in_logs(tmp_path, caplog):
    v, *_ = mk(tmp_path)
    with caplog.at_level("DEBUG"):
        tok = v.start()["grant"]
        v.frame(tok)
    assert tok not in caplog.text


def test_the_route_list_is_closed_and_the_agent_side_has_no_input_calls():
    seen = []
    lv.dashboard_routes(lambda m, rx, fn: seen.append((m, rx)))
    assert seen == [("POST", r"/api/browser/live/start$"), ("POST", r"/api/browser/live/frame$"), ("POST", r"/api/browser/live/stop$"),
                    ("POST", r"/api/browser/live/takeover$"), ("POST", r"/api/browser/live/return$"), ("POST", r"/api/browser/live/input$")]
    src = Path(lv.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\.(click|fill|type|press|keyboard|mouse|evaluate|goto)\(", src)


def test_stop_works_without_a_valid_grant_after_expiry(tmp_path):
    v, s, d, t = mk(tmp_path, ttl_s=60)
    tok = v.start()["grant"]
    t[0] += 61
    assert code(v.frame, tok) == "LIVEVIEW_EXPIRED"
    assert v.stop() == {"stopped": True} and s.state == bp.STOPPED and d.closed


def test_a_driver_crash_ends_the_view_with_fixed_words(tmp_path):
    v, s, d, t = mk(tmp_path)
    tok = v.start()["grant"]

    def boom():
        raise RuntimeError("Page.screenshot failed at https://secret.example/?k=1")
    d.screenshot = boom
    t[0] += 1
    with pytest.raises(lv.LiveError) as e:
        v.frame(tok)
    assert e.value.code == "LIVEVIEW_FRAME" and "secret.example" not in e.value.message
    assert code(v.frame, tok) == "LIVEVIEW_TOKEN"


def test_a_navigation_during_the_screenshot_drops_the_frame(tmp_path):
    v, s, d, t = mk(tmp_path)
    tok = v.start()["grant"]

    def shot():
        d.url = "https://bank.example/"
        return PNG
    d.screenshot = shot
    t[0] += 1
    assert code(v.frame, tok) == "BROWSER_PILOT_VIEW_TEST_SITE"
    assert code(v.frame, tok) == "LIVEVIEW_TOKEN"


def test_concurrent_frames_take_one_screenshot(tmp_path):
    import time as _t
    v, s, d, t = mk(tmp_path)
    tok = v.start()["grant"]
    n = d.shots
    slow = d.screenshot

    def shot():
        _t.sleep(0.05)
        return slow()
    d.screenshot = shot
    t[0] += 1
    out = []
    ths = [threading.Thread(target=lambda: out.append(v.frame(tok)["seq"])) for _ in range(6)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    assert d.shots == n + 1
    assert len(set(out)) == 1                                       # everyone got the same new frame


def test_routes_need_a_session(dash):  # noqa: F811
    lv.set_active(None)
    r, body = req(dash, "POST", "/api/browser/live/start", headers={"X-Jarves": "1"}, body={})
    assert r.status == 404 and json.loads(body)["code"] == "LIVEVIEW_NO_SESSION"


def test_a_non_local_principal_is_refused(tmp_path):
    seen = []
    lv.dashboard_routes(lambda m, rx, fn: seen.append(fn))

    class Ctx:
        principal = {"kind": "device", "id": "phone"}
    status, body = seen[0](Ctx(), None, {})
    assert status == 403 and body["code"] == "LIVEVIEW_LOCAL_ONLY"


# ---------------------------------------------------------------- end to end: real PNG of a local test site in the real dashboard
SITE = b"<!doctype html><title>t</title><body style=\"background:#fde\"><h1>LIVE TEST SITE</h1></body>"


class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(SITE)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


def test_live_view_in_the_real_dashboard(browser, dash, tmp_path):  # noqa: F811
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{srv.server_address[1]}/"
    try:
        shot = browser.new_page(viewport={"width": 480, "height": 240})
        shot.goto(url)
        png = shot.screenshot()
        shot.close()
        assert png[:4] == b"\x89PNG"
        drv = Drv(url)
        drv.screenshot = lambda: png
        s = bp.PilotSession(tmp_path, drv, enabled=lambda: True, resolver=lambda h: ["93.184.216.34"])
        lv.set_active(lv.LiveView(s, enabled=lambda: True))
        ctx = browser.new_context(viewport={"width": 1200, "height": 900})
        p = ctx.new_page()
        errs: list[str] = []
        p.on("pageerror", lambda e: errs.append(str(e)))

        def with_live(route):
            r = route.fetch()
            d = r.json()
            d["live_view"] = True
            route.fulfill(response=r, body=json.dumps(d), headers={**r.headers, "content-type": "application/json"})
        p.route(re.compile(r"/api/browser(\?.*)?$"), with_live)
        p.add_init_script("try{localStorage.setItem('jarves-lang','en')}catch(e){}")
        p.goto(dash.url + "#/browser")
        p.wait_for_selector("#live-view button", timeout=30000)
        p.click("#live-view button:has-text('Live view')")
        p.wait_for_function("() => { const i = document.querySelector('#live-view img'); return i && i.src.startsWith('data:image/png;base64,') && i.naturalWidth > 0; }", timeout=30000)
        assert "Read only" in p.inner_text("#live-view")
        p.click("#live-view button:has-text('Stop')")
        p.wait_for_function("() => document.querySelector('#live-view').innerText.includes('Stopped')", timeout=30000)
        assert s.state == bp.STOPPED and drv.closed
        assert errs == []
        ctx.close()
    finally:
        lv.set_active(None)
        srv.shutdown()
