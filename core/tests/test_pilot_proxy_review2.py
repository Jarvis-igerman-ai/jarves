"""#658 second review round: a session that is switched off, paused or past its cap lets no NEW connection out, local tunnels end once
a public page was allowed, Stop closes client sockets, caps on rejected connections and total bytes, oversized upstream heads."""
from __future__ import annotations

import base64
import socket
import threading
import time

import pytest

from jarves import browser_pilot as bp
from jarves import pilot_proxy as pp
from test_pilot_proxy import Drv, Upstream, status  # noqa: F401


class Rig2:
    def __init__(self, tmp_path):
        self.up = Upstream()
        self.on = True
        self.t = [1000.0]
        self.dialed = []
        self.session = bp.PilotSession(tmp_path, Drv(), enabled=lambda: self.on, clock=lambda: self.t[0],
                                       resolver=lambda h: ["93.184.216.34"] if h == "example.com" else [])

        def connect(addr, port):
            self.dialed.append((addr, port))
            return socket.create_connection(("127.0.0.1", self.up.port), timeout=3)
        self.proxy = pp.PilotProxy(self.session, local_ports=[9999], connect=connect)
        info = self.proxy.start()
        self.auth = base64.b64encode(f"pilot:{info['password']}".encode()).decode()

    def raw(self, line: str, read_head_only=True) -> bytes:
        c = socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5)
        try:
            c.sendall((line + f"\r\nHost: x\r\nProxy-Authorization: Basic {self.auth}\r\n\r\n").encode())
            c.settimeout(5)
            out = b""
            while b"\r\n\r\n" not in out:
                d = c.recv(4096)
                if not d:
                    break
                out += d
            return out
        finally:
            c.close()

    def connect_(self, hp):
        return self.raw(f"CONNECT {hp} HTTP/1.1")

    def get(self, url):
        return self.raw(f"GET {url} HTTP/1.1")


@pytest.fixture
def r2(tmp_path):
    r = Rig2(tmp_path)
    yield r
    r.proxy.stop()


def test_a_switched_off_session_lets_no_new_connection_out(r2):
    assert status(r2.connect_("example.com:443")) == 200
    r2.on = False
    assert status(r2.connect_("example.com:443")) == 403
    assert status(r2.get("http://example.com/")) == 403
    assert status(r2.connect_("localhost:9999")) == 403
    assert len(r2.dialed) == 1


def test_a_session_past_its_time_cap_lets_no_new_connection_out(r2):
    assert status(r2.connect_("example.com:443")) == 200
    r2.t[0] += bp.MAX_SECONDS + 1                               # counted from the session start, not from when a tunnel opened
    assert status(r2.connect_("example.com:443")) == 403 and status(r2.get("http://example.com/")) == 403
    assert len(r2.dialed) == 1


def test_a_paused_session_is_blocked(r2):
    r2.session.pause()
    assert status(r2.connect_("example.com:443")) == 403 and status(r2.connect_("localhost:9999")) == 403
    r2.session.resume()
    assert status(r2.connect_("example.com:443")) == 200


def test_open_tunnels_die_when_the_session_is_switched_off(r2):
    c = socket.create_connection(("127.0.0.1", r2.proxy.port), timeout=5)
    c.sendall(f"CONNECT example.com:443 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {r2.auth}\r\n\r\n".encode())
    assert c.recv(200).startswith(b"HTTP/1.1 200")
    r2.on = False
    c.settimeout(5)
    end = time.time() + 5
    closed = False
    while time.time() < end and not closed:
        try:
            closed = c.recv(10) == b""
        except OSError:
            closed = True
    assert closed
    c.close()


def test_a_local_tunnel_ends_once_a_public_page_was_allowed(r2):
    c = socket.create_connection(("127.0.0.1", r2.proxy.port), timeout=5)
    c.sendall(f"CONNECT localhost:9999 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {r2.auth}\r\n\r\n".encode())
    assert c.recv(200).startswith(b"HTTP/1.1 200")
    assert status(r2.connect_("example.com:443")) == 200
    c.settimeout(5)
    end = time.time() + 5
    closed = False
    while time.time() < end and not closed:
        try:
            closed = c.recv(10) == b""
        except OSError:
            closed = True
    assert closed
    c.close()


def test_stop_closes_client_sockets_even_when_a_relay_is_idle(r2):
    c = socket.create_connection(("127.0.0.1", r2.proxy.port), timeout=5)
    c.sendall(f"CONNECT example.com:443 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {r2.auth}\r\n\r\n".encode())
    assert c.recv(200).startswith(b"HTTP/1.1 200")
    t0 = time.time()
    r2.proxy.stop()
    assert time.time() - t0 < 3
    c.settimeout(3)
    try:
        assert c.recv(10) == b""
    except OSError:
        pass
    c.close()
    assert not r2.proxy._conns


def test_the_total_byte_cap_ends_a_connection(r2, monkeypatch):
    monkeypatch.setattr(pp, "TOTAL_BYTES", 10)
    out = r2.get("http://example.com/")
    assert out == b""                                           # the first chunk already exceeds the cap: nothing is forwarded
    assert r2.proxy._total > 10


def test_rejected_connections_cannot_spawn_unlimited_threads(r2, monkeypatch):
    monkeypatch.setattr(r2.proxy, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(r2.proxy, "_busy_slots", threading.BoundedSemaphore(2))
    held = socket.create_connection(("127.0.0.1", r2.proxy.port), timeout=5)
    held.sendall(f"CONNECT example.com:443 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {r2.auth}\r\n\r\n".encode())
    assert held.recv(200).startswith(b"HTTP/1.1 200")
    before = threading.active_count()
    flood = [socket.create_connection(("127.0.0.1", r2.proxy.port), timeout=5) for _ in range(30)]
    time.sleep(0.3)
    assert threading.active_count() - before <= 4              # at most the busy slots (+ slack), not one thread per rejected socket
    for f in flood:
        f.close()
    held.close()


def test_an_oversized_upstream_head_is_refused_not_truncated(tmp_path):
    big = socket.socket()
    big.bind(("127.0.0.1", 0))
    big.listen(1)

    def serve():
        c, _ = big.accept()
        c.recv(4096)
        try:
            c.sendall(b"HTTP/1.1 200 OK\r\nX: " + b"a" * (pp.MAX_HEADER_BYTES + 100) + b"\r\n\r\nbody")
        except OSError:
            pass
        c.close()
    threading.Thread(target=serve, daemon=True).start()
    r = Rig2(tmp_path)
    try:
        r.proxy._connect = lambda a, p: socket.create_connection(("127.0.0.1", big.getsockname()[1]), timeout=3)
        out = r.get("http://example.com/")
        assert b"body" not in out and not out.startswith(b"HTTP/1.1 200 OK\r\nX: a")
    finally:
        r.proxy.stop()
        big.close()


def test_public_stays_closed_after_the_owner_used_the_page(r2):
    r2.session.human_takes_over()
    r2.session.human_returns()
    r2.session.resume()
    assert r2.session.after_human
    assert status(r2.connect_("example.com:443")) == 403          # a public script in that page could read what was typed
    assert status(r2.connect_("localhost:9999")) == 200
