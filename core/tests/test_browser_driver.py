"""The REAL pilot driver (Playwright, headless Edge/Chromium) against a local test site, through the real proxy: everything the earlier
PRs only proved with fakes. Missing Playwright or browser = a failure, never a skip (CI installs both)."""
from __future__ import annotations

import http.server
import threading
import time

import pytest

from jarves import browser_driver as bd
from jarves import browser_liveview as bl
from jarves import browser_pilot as bp

SECRET = "hunter2-Correct-Horse-9"


class Hits:
    def __init__(self):
        self.paths: list[str] = []


def make_site(other_port: int):
    page = f"""<!doctype html><html><head><title>t</title></head><body>
<h1>PILOT TEST SITE</h1>
<input id="u" autofocus><div id="echo">nothing typed</div><div id="net">net: waiting</div><div id="ws">ws: waiting</div>
<img src="http://example.com/x.png" alt="public image">
<script>
document.getElementById('u').addEventListener('input', e => {{ document.getElementById('echo').textContent = 'you typed: ' + e.target.value; }});
fetch('http://127.0.0.1:{other_port}/secret').then(r => r.text()).then(t => {{ document.getElementById('net').textContent = 'net: reached ' + t; }})
  .catch(() => {{ document.getElementById('net').textContent = 'net: blocked'; }});
try {{ const w = new WebSocket('ws://127.0.0.1:{other_port}/ws'); w.onopen = () => {{ document.getElementById('ws').textContent = 'ws: open'; }};
  w.onclose = () => {{ document.getElementById('ws').textContent = 'ws: closed'; }}; w.onerror = () => {{ document.getElementById('ws').textContent = 'ws: closed'; }}; }}
catch (e) {{ document.getElementById('ws').textContent = 'ws: closed'; }}
</script></body></html>""".encode()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *a):
            pass
    return H


def make_other(hits: Hits):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.paths.append(self.path)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"SECRET-LOCAL-SERVICE")

        def log_message(self, *a):
            pass
    return H


class Feats:
    def active(self, fid):
        return fid in ("browser_pilot", "browser_liveview", "browser_takeover")


@pytest.fixture
def world(tmp_path):
    hits = Hits()
    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_other(hits))
    site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_site(other.server_address[1]))
    for s in (other, site):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    svc = bd.PilotService(tmp_path, lambda: Feats())
    try:
        yield svc, f"http://localhost:{site.server_address[1]}/", hits
    finally:
        svc.stop()
        bl.set_active(None)
        for s in (other, site):
            s.shutdown()


def wait_text(session, needle, timeout=15):
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        last = session.read()["untrusted_text"]
        if needle in last:
            return last
        time.sleep(1.0)
    raise AssertionError(f"{needle!r} not in {last!r}")


def test_a_real_browser_reads_the_test_page_only_through_the_proxy_and_cannot_reach_other_local_services(world):
    svc, url, hits = world
    assert svc.start(url) == {"started": True, "state": bp.AGENT}
    s = svc.session
    text = wait_text(s, "PILOT TEST SITE")
    assert svc.driver.proxy.connections >= 1                           # the browser really used the proxy for loopback (bypass-list contract)
    wait_text(s, "net: blocked")                                        # fetch to a second local port: refused
    wait_text(s, "ws: closed")                                          # a WebSocket to it: refused
    time.sleep(0.5)
    assert hits.paths == []                                             # the other local service never saw a request
    assert "SECRET-LOCAL-SERVICE" not in text
    assert s.read()["untrusted_text"]                                   # still alive and reading


def test_the_live_view_shows_a_real_picture_and_the_owner_can_type_without_the_agent_ever_seeing_it(world):
    svc, url, _ = world
    svc.start(url)
    s = svc.session
    view = bl.ACTIVE
    wait_text(s, "PILOT TEST SITE")
    g = view.start()["grant"]
    frame = view.frame(g)
    import base64
    assert base64.b64decode(frame["png_b64"])[:4] == b"\x89PNG"
    view.takeover(g)
    with pytest.raises(bp.PilotError):
        s.read()                                                        # the agent is locked out
    view.human(g, "scroll", {"dy": 0})
    view.human(g, "type", {"text": SECRET})
    view.give_back(g)
    view.give_back(g, True)
    out = wait_text(s, "you typed")
    assert SECRET not in out and "hidden" in out                        # the page echoed it; the agent got the marker instead


def test_stop_closes_the_browser_and_the_proxy(world):
    svc, url, _ = world
    svc.start(url)
    d, proxy = svc.driver, svc.driver.proxy
    wait_text(svc.session, "PILOT TEST SITE")
    assert svc.stop() == {"stopped": True}
    d._t.join(10)
    assert not d._t.is_alive() and proxy._stop.is_set() and bl.ACTIVE is None


@pytest.mark.parametrize("url", ["https://example.com/", "http://example.com/", "http://10.0.0.1:80/", "file:///etc/passwd",
                                 "http://localhost/", "http://user@localhost:8000/x"])
def test_a_pilot_starts_only_on_a_local_test_page_with_a_port(tmp_path, url):
    svc = bd.PilotService(tmp_path, lambda: Feats())
    with pytest.raises(bp.PilotError) as e:
        svc.start(url)
    assert e.value.code in ("PILOT_URL",) or url.startswith("http://user")


def test_the_switch_must_be_on(tmp_path):
    class Off:
        def active(self, fid):
            return False
    svc = bd.PilotService(tmp_path, lambda: Off())
    with pytest.raises(bp.PilotError) as e:
        svc.start("http://localhost:8123/")
    assert e.value.code == "BROWSER_PILOT_OFF"


def test_the_start_route_is_local_only_and_refuses_a_public_address(tmp_path):
    from types import SimpleNamespace as NS
    seen = []
    bd.dashboard_routes(lambda m, rx, fn: seen.append((rx, fn)))
    start = next(fn for rx, fn in seen if rx.endswith("start$"))
    assert start(NS(principal={"kind": "device", "id": "phone"}, home=tmp_path), None, {"url": "http://localhost:1/"})[0] == 403
    status, body = start(NS(principal={"kind": "local"}, home=tmp_path, cfg=None), None, {"url": "https://example.com/"})
    assert status == 409 and body["code"] == "PILOT_URL"
