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


@pytest.fixture(autouse=True)
def _ci_sandbox(monkeypatch):
    monkeypatch.setenv("JARVES_PILOT_NO_SANDBOX", "1")                 # only if the sandbox cannot start (Linux CI containers); local runs keep it ON


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
    proxy = svc.driver.proxy
    assert proxy.connections_local >= 1                                 # the browser really used the proxy for loopback (bypass-list contract)
    wait_text(s, "net: blocked")                                        # fetch to a second local port: refused
    wait_text(s, "ws: closed")                                          # a WebSocket to it: refused
    time.sleep(0.5)
    assert proxy.denials.count("local port refused") >= 2               # the REASON is the port rule: the fetch AND the WebSocket
    assert proxy.connections_public == 0 and not proxy._public_seen    # nothing public at all: not the <img>, not the browser's own calls
    assert s.local_only
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
    assert e.value.code == "PILOT_URL"


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


@pytest.mark.parametrize("port,dash", [(11434, 0), (5037, 0), (9222, 0), (5432, 0), (8765, 8765)])
def test_service_ports_and_the_dashboard_port_are_refused(tmp_path, port, dash):
    svc = bd.PilotService(tmp_path, lambda: Feats(), dashboard_port=dash)
    with pytest.raises(bp.PilotError) as e:
        svc.start(f"http://localhost:{port}/")
    assert e.value.code == "PILOT_URL_PORT"


def test_the_launch_keeps_the_sandbox_and_forbids_direct_dns_quic_and_prediction():
    seen = {}

    class Chromium:
        def launch(self, **kw):
            seen.update(kw)
            return object()

    class FakePW:
        chromium = Chromium()

    class FakeProxy:
        credentials = {"server": "127.0.0.1:1", "username": "pilot", "password": "x"}

        def launch_args(self):
            return ["--proxy-server=http://127.0.0.1:1", "--proxy-bypass-list=<-loopback>",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
    d = bd.PlaywrightDriver.__new__(bd.PlaywrightDriver)
    d._pw, d.proxy, d._launch = FakePW(), FakeProxy(), {}
    d._launch_browser(True)
    assert seen["chromium_sandbox"] is True and seen["proxy"]["bypass"] == "<-loopback>"
    for a in bd.EXTRA_ARGS + ["--proxy-bypass-list=<-loopback>"]:
        assert a in seen["args"]
    assert not any(a.startswith("--proxy-server") for a in seen["args"])       # the proxy (with its password) goes through Playwright's option


def test_the_loopback_bypass_override_variable_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK", "1")
    with pytest.raises(bd.DriverError):
        bd.PlaywrightDriver(object(), lambda: None)


def _pid_alive(pid: int) -> bool:
    import subprocess
    import sys
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        import os
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                                            # a killed child nobody reaped yet is a zombie: dead
        with open(f"/proc/{pid}/stat", encoding="ascii") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def test_a_hung_page_cannot_keep_the_browser_alive_after_stop(world, monkeypatch):
    svc, url, _ = world
    svc.start(url)
    d = svc.driver
    wait_text(svc.session, "PILOT TEST SITE")
    pid = d._node_pid
    assert pid and _pid_alive(pid)
    monkeypatch.setattr(bd, "CLOSE_WAIT_S", 1.0)
    threading.Thread(target=lambda: _swallow(d._job, lambda: time.sleep(40), 1.0), daemon=True).start()
    time.sleep(0.3)
    t0 = time.time()
    svc.stop()
    assert time.time() - t0 < 10
    end = time.time() + 10
    while time.time() < end and _pid_alive(pid):
        time.sleep(0.3)
    assert not _pid_alive(pid)                                          # the abandoned driver's whole process tree was killed


def _swallow(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass


def test_a_job_that_missed_its_deadline_never_runs_late(world):
    svc, url, _ = world
    svc.start(url)
    d = svc.driver
    ran = []
    threading.Thread(target=lambda: _swallow(d._job, lambda: time.sleep(1.5), 5.0), daemon=True).start()
    time.sleep(0.2)
    with pytest.raises(bd.DriverError):
        d._job(lambda: ran.append(1), 0.3)                              # queued behind the slow job, gives up after 0.3 s
    time.sleep(2.5)
    assert ran == []                                                    # the worker skipped it instead of running it late


def test_a_stop_during_the_start_wins(tmp_path):
    started, release = threading.Event(), threading.Event()
    svc = bd.PilotService(tmp_path, lambda: Feats())

    class SlowDriver:
        current_url = "http://localhost:8123/"

        def __init__(self, proxy, ref):
            started.set()
            release.wait(10)
            self.proxy = proxy

        def pin_hosts(self, m):
            pass

        def open(self, u):
            pass

        def text(self):
            return ""

        def screenshot(self):
            return b""

        def human_input(self, k, d):
            pass

        def close(self):
            self.proxy.stop()
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("e", _catch(lambda: svc.start("http://localhost:8123/", driver_factory=SlowDriver))))
    th.start()
    assert started.wait(5)
    t0 = time.time()
    assert svc.stop() == {"stopped": True} and time.time() - t0 < 2      # Stop is not stuck behind the launch
    release.set()
    th.join(10)
    assert out["e"] == "PILOT_SUPERSEDED" and bl.ACTIVE is None


def _catch(fn):
    try:
        fn()
    except bp.PilotError as e:
        return e.code
