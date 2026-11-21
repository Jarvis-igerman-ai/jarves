"""JAR-368 origin pin in real headless Chromium: playwright_capture against a loopback fixture dashboard plus an
off-origin listener that must never receive a request. Missing Playwright = a failure, never a skip."""
from __future__ import annotations

import http.server
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402
import post_install_demos as pid  # noqa: E402

APP = (b"<html><body><img src='a.png'><script>fetch('/seen?' + location.pathname); fetch('/off').catch(()=>0);"
       b"try { new WebSocket('ws://localhost:%d/ws') } catch (e) {}</script>"
       b"<img src='http://localhost:%d/img.png'></body></html>")


def _server(host, routes, log):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            log.append(self.path)
            status, headers, body = routes(self.path)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    s = http.server.ThreadingHTTPServer((host, 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


@pytest.fixture(scope="module")
def sites():
    evil_log, dash_log = [], []
    evil = _server("localhost", lambda p: (200, {"Content-Type": "text/plain"}, b"evil"), evil_log)
    ep = evil.server_address[1]
    redirects = {"/": "/app/", "/bs": f"/\\localhost:{ep}/x", "/chain": "/hop2", "/hop2": f"http://localhost:{ep}/y",
                 "/off": f"http://localhost:{ep}/z"}

    def dash_routes(p):
        if p in redirects:
            return 302, {"Location": redirects[p]}, b""
        if p == "/app/":
            return 200, {"Content-Type": "text/html"}, APP % (ep, ep)
        return 200, {"Content-Type": "text/plain"}, b"ok"
    dash = _server("127.0.0.1", dash_routes, dash_log)
    yield f"http://127.0.0.1:{dash.server_address[1]}", dash_log, evil_log
    evil.shutdown()
    dash.shutdown()


def _capture(base, page, tmp_path):
    launch = apc.launch_kwargs("msedge" if sys.platform == "win32" else "")
    img, vid = tmp_path / "demo.png", tmp_path / "demo.webm"
    pid.playwright_capture(base, launch)(page, img, vid)
    return img, vid


def test_redirected_page_loads_at_final_url_and_off_origin_gets_nothing(sites, tmp_path):
    base, dash_log, evil_log = sites
    img, vid = _capture(base, "/", tmp_path)
    assert img.stat().st_size > 0 and vid.stat().st_size > 0
    assert "/seen?/app/" in dash_log            # document.location is the final URL, not the pre-redirect one
    assert "/app/a.png" in dash_log and "/a.png" not in dash_log   # relative assets resolve against it
    assert "/off" in dash_log and evil_log == []   # sub-resource redirect, image and WebSocket off-origin: refused


@pytest.mark.parametrize("page", ["/bs", "/chain"])
def test_ambiguous_or_off_origin_redirect_never_reaches_other_origin(sites, tmp_path, page):
    base, _dash_log, evil_log = sites
    with pytest.raises(ValueError, match="DEMO_URL_REFUSED"):
        _capture(base, page, tmp_path)
    assert evil_log == []
