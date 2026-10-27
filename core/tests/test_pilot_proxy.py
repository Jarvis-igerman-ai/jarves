"""The pilot's local forward proxy: default deny, vetted-address pinning, rebinding, redirects, auth, caps. Real sockets on 127.0.0.1
against fake upstreams; the 'public' addresses are mapped to local fake servers by an injected connect(). No browser, no network."""
from __future__ import annotations

import base64
import socket
import threading
import time

import pytest

from jarves import browser_pilot as bp
from jarves import pilot_proxy as pp

PUBLIC = "93.184.216.34"
OTHER = "93.184.216.35"


class Upstream:
    """A tiny HTTP server that answers every request with 200 and records what it received."""

    def __init__(self):
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(8)
        self.port = self.s.getsockname()[1]
        self.got: list[bytes] = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            data = b""
            c.settimeout(2)
            try:
                while b"\r\n\r\n" not in data:
                    chunk = c.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                self.got.append(data)
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            except OSError:
                pass
            finally:
                c.close()


class Drv:
    current_url = "http://localhost:1/"

    def pin_hosts(self, m):
        pass

    def open(self, u):
        pass

    def text(self):
        return ""

    def screenshot(self):
        return b""

    def close(self):
        self.closed = True


class Rig:
    def __init__(self, tmp_path, answers=None):
        self.up = Upstream()
        self.dialed: list[tuple[str, int]] = []
        self.calls: list[str] = []
        table = answers if answers is not None else {"example.com": [PUBLIC], "evil.example.com": ["10.0.0.9"]}

        def resolver(host):
            self.calls.append(host)
            return list(table.get(host, []))
        self.table = table
        self.session = bp.PilotSession(tmp_path, Drv(), enabled=lambda: True, resolver=resolver)

        def connect(addr, port):
            self.dialed.append((addr, port))
            return socket.create_connection(("127.0.0.1", self.up.port), timeout=3)
        self.proxy = pp.PilotProxy(self.session, connect=connect)
        info = self.proxy.start()
        self.auth = base64.b64encode(f"pilot:{info['password']}".encode()).decode()

    def ask(self, raw: bytes, *, auth=True, read_until_close=True) -> bytes:
        c = socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5)
        try:
            if auth:
                raw = raw.replace(b"\r\n\r\n", f"\r\nProxy-Authorization: Basic {self.auth}\r\n\r\n".encode(), 1)
            c.sendall(raw)
            out = b""
            c.settimeout(5)
            while True:
                try:
                    d = c.recv(4096)
                except OSError:
                    break
                if not d:
                    break
                out += d
                if not read_until_close and b"\r\n\r\n" in out:
                    break
            return out
        finally:
            c.close()

    def connect(self, hostport: str, **kw) -> bytes:
        return self.ask(f"CONNECT {hostport} HTTP/1.1\r\nHost: {hostport}\r\n\r\n".encode(), read_until_close=False, **kw)

    def get(self, url: str, **kw) -> bytes:
        return self.ask(f"GET {url} HTTP/1.1\r\nHost: x\r\n\r\n".encode(), **kw)

    def close(self):
        self.proxy.stop()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


def status(raw: bytes) -> int:
    return int(raw.split(b" ", 2)[1])


def test_it_listens_on_loopback_only_and_needs_the_secret(rig):
    assert rig.proxy._sock.getsockname()[0] == "127.0.0.1"
    assert status(rig.connect("example.com:443", auth=False)) == 407
    raw = b"CONNECT example.com:443 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic " + base64.b64encode(b"pilot:wrong") + b"\r\n\r\n"
    assert status(rig.ask(raw, auth=False, read_until_close=False)) == 407 and rig.dialed == []


def test_a_public_name_is_dialled_at_its_vetted_address_only(rig):
    assert status(rig.connect("example.com:443")) == 200
    assert rig.dialed == [(PUBLIC, 443)]


def test_dns_rebinding_cannot_move_the_connection(rig):
    assert status(rig.connect("example.com:443")) == 200
    rig.table["example.com"] = ["10.0.0.9"]                        # the name now answers with a private address
    assert status(rig.connect("example.com:443")) == 200
    assert rig.dialed == [(PUBLIC, 443), (PUBLIC, 443)]            # the pin from the first lookup is used, no second lookup
    assert rig.calls.count("example.com") == 1


def test_a_name_that_resolves_private_is_refused_and_never_dialled(rig):
    assert status(rig.connect("evil.example.com:443")) == 403 and rig.dialed == []


def test_an_unknown_name_is_refused(rig):
    assert status(rig.connect("nowhere.example:443")) == 403 and rig.dialed == []


@pytest.mark.parametrize("target", ["example.com:22", "example.com:8080", "example.com:0", "example.com:99999", "93.184.216.34:25",
                                    "10.0.0.1:80", "169.254.169.254:80", "[::ffff:0:a00:1]:80", "0x7f.1:80", "127.1:80"])
def test_non_web_ports_and_private_targets_are_refused(rig, target):
    assert status(rig.connect(target)) in (400, 403) and rig.dialed == []


def test_localhost_goes_to_loopback_without_any_dns(rig):
    assert status(rig.connect("localhost:9999")) == 200
    assert rig.dialed == [("127.0.0.1", 9999)] and "localhost" not in rig.calls
    assert status(rig.connect("127.0.0.1:8123")) == 200 and rig.dialed[-1] == ("127.0.0.1", 8123)


def test_plain_http_get_is_relayed_with_proxy_headers_stripped(rig):
    out = rig.get("http://example.com/a/b?q=1")
    assert status(out) == 200 and out.endswith(b"ok")
    got = rig.up.got[-1].decode("latin-1").lower()
    assert got.startswith("get /a/b?q=1 http/1.1") and "proxy-authorization" not in got and "host: example.com" in got
    assert rig.dialed == [(PUBLIC, 80)]


def test_only_get_and_head_over_plain_http(rig):
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        raw = f"{m} http://example.com/x HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n".encode()
        assert status(rig.ask(raw)) == 405
    assert rig.dialed == []


def test_a_redirect_target_is_a_new_request_and_is_vetted_again(rig):
    assert status(rig.get("http://example.com/")) == 200
    assert status(rig.get("http://evil.example.com/inside")) == 403          # the browser follows a 302 to a private name: stopped here
    assert rig.dialed == [(PUBLIC, 80)]


def test_credentials_in_the_url_and_odd_requests_are_refused(rig):
    assert status(rig.get("http://user:pw@example.com/")) == 400
    assert status(rig.get("/relative")) == 400
    assert status(rig.ask(b"GARBAGE\r\n\r\n")) == 400
    assert status(rig.ask(b"GET http://example.com/ HTTP/1.1\r\nbad header\r\n\r\n")) == 400
    assert rig.dialed == []


def test_oversized_headers_are_refused(rig):
    raw = b"GET http://example.com/ HTTP/1.1\r\nX: " + b"a" * (pp.MAX_HEADER_BYTES + 10) + b"\r\n\r\n"
    assert status(rig.ask(raw)) == 431 and rig.dialed == []


def test_while_the_owner_drives_only_loopback_passes_and_after_stop_nothing(rig):
    rig.session.human_takes_over()
    assert status(rig.connect("example.com:443")) == 403
    assert status(rig.connect("localhost:9999")) == 200
    rig.session.stop()
    assert status(rig.connect("localhost:9999")) == 403


def test_stop_closes_open_tunnels(rig):
    c = socket.create_connection(("127.0.0.1", rig.proxy.port), timeout=5)
    c.sendall(f"CONNECT example.com:443 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {rig.auth}\r\n\r\n".encode())
    assert c.recv(200).startswith(b"HTTP/1.1 200")
    rig.session.stop()
    c.settimeout(5)
    deadline = time.time() + 5
    closed = False
    while time.time() < deadline:
        try:
            if c.recv(10) == b"":
                closed = True
                break
        except OSError:
            closed = True
            break
    assert closed
    c.close()


def test_a_refused_connection_is_never_logged_with_its_address(rig, caplog):
    with caplog.at_level("DEBUG"):
        rig.connect("evil.example.com:443")
        rig.get("http://10.0.0.1/secret-path?k=abc")
    assert "evil.example.com" not in caplog.text and "secret-path" not in caplog.text and "10.0.0.1" not in caplog.text


def test_the_connection_cap(rig, monkeypatch):
    monkeypatch.setattr(rig.proxy, "_slots", threading.BoundedSemaphore(1))
    held = socket.create_connection(("127.0.0.1", rig.proxy.port), timeout=5)
    held.sendall(f"CONNECT example.com:443 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {rig.auth}\r\n\r\n".encode())
    assert held.recv(200).startswith(b"HTTP/1.1 200")
    assert status(rig.connect("example.com:443")) == 503
    held.close()


def test_the_dialled_address_must_itself_be_global(tmp_path):
    r = Rig(tmp_path, answers={"example.com": [PUBLIC]})
    try:
        r.session.pins["example.com"] = ["10.0.0.9"]               # even a bad pin can never be dialled
        assert status(r.connect("example.com:443")) == 403 and r.dialed == []
    finally:
        r.close()


def test_a_dead_upstream_is_a_502_not_a_hang(tmp_path):
    r = Rig(tmp_path)
    try:
        def boom(a, p):
            raise OSError("down")
        r.proxy._connect = boom
        assert status(r.connect("example.com:443")) == 502
    finally:
        r.close()
