"""The #658 review fixes of the pilot proxy: local port allow-list, public-then-local, no client->server pipe over plain HTTP, tunnels
re-checked, deadlines, odd inputs, launch contract. Same rig as test_pilot_proxy.py (real sockets, fake upstreams)."""
from __future__ import annotations

import base64
import socket
import threading
import time

import pytest
from test_pilot_proxy import Rig, rig, status  # noqa: F401  (fixture + helpers)

from jarves import pilot_proxy as pp


def test_only_the_listed_local_ports_are_reachable(rig):  # noqa: F811
    for port in (8765, 11434, 5037, 5432, 80, 443):                         # dashboard, Ollama, adb, Postgres ...
        assert status(rig.connect(f"localhost:{port}")) == 403
        assert status(rig.get(f"http://127.0.0.1:{port}/")) == 403
    assert rig.dialed == []
    assert status(rig.connect("[::1]:9999")) == 200 and rig.dialed == [("::1", 9999)]


def test_no_local_access_after_a_public_page_was_allowed(rig):  # noqa: F811
    assert status(rig.connect("localhost:9999")) == 200
    assert status(rig.connect("example.com:443")) == 200
    assert status(rig.connect("localhost:9999")) == 403                      # a public page must not reach local services
    assert status(rig.get("http://127.0.0.1:8123/")) == 403


def test_plain_http_only_on_80_and_tunnels_only_on_443(rig):  # noqa: F811
    assert status(rig.get("http://example.com:443/")) == 403
    assert status(rig.connect("example.com:80")) == 403
    assert rig.dialed == []


def test_plain_http_is_not_a_pipe_from_the_client(rig):  # noqa: F811
    c = socket.create_connection(("127.0.0.1", rig.proxy.port), timeout=5)
    c.sendall(f"GET http://example.com/ HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {rig.auth}\r\n\r\n".encode())
    c.settimeout(5)
    first = c.recv(4096)
    assert first.startswith(b"HTTP/1.1 200") and b"Connection: close" in first
    try:                                                                       # a second request on the reused socket goes nowhere
        c.sendall(b"POST http://bank.example/x HTTP/1.1\r\nHost: bank.example\r\nContent-Length: 0\r\n\r\n")
        rest = c.recv(4096)
    except OSError:
        rest = b""
    assert rest == b""
    assert len(rig.up.got) == 1 and len(rig.dialed) == 1
    c.close()


def test_a_get_with_a_body_or_odd_framing_is_refused(rig):  # noqa: F811
    for extra in ("Content-Length: 5", "Transfer-Encoding: chunked", "Expect: 100-continue"):
        raw = f"GET http://example.com/ HTTP/1.1\r\nHost: x\r\n{extra}\r\n\r\n".encode()
        assert status(rig.ask(raw)) == 400
    assert rig.dialed == []


def test_the_server_is_told_to_close_and_framing_headers_are_not_forwarded(rig):  # noqa: F811
    out = rig.get("http://example.com/")
    assert b"Connection: close" in out
    sent = rig.up.got[-1].lower()
    assert b"connection: close" in sent and b"content-length" not in sent and b"keep-alive" not in sent


def test_an_open_public_tunnel_dies_when_the_owner_takes_over(rig):  # noqa: F811
    c = socket.create_connection(("127.0.0.1", rig.proxy.port), timeout=5)
    c.sendall(f"CONNECT example.com:443 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {rig.auth}\r\n\r\n".encode())
    assert c.recv(200).startswith(b"HTTP/1.1 200")
    rig.session.human_takes_over()
    c.settimeout(5)
    deadline = time.time() + 5
    closed = False
    while time.time() < deadline and not closed:
        try:
            closed = c.recv(10) == b""
        except OSError:
            closed = True
    assert closed
    c.close()


def test_a_slow_header_is_cut_off_by_a_total_deadline(rig, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pp, "HEAD_DEADLINE_S", 0.6)
    c = socket.create_connection(("127.0.0.1", rig.proxy.port), timeout=5)
    c.sendall(b"CONNECT ")
    time.sleep(0.4)
    c.sendall(b"exam")
    time.sleep(0.4)
    c.settimeout(3)
    got = b""
    try:
        got = c.recv(200)
    except OSError:
        pass
    assert got.startswith(b"HTTP/1.1 408") or got == b""
    c.close()


@pytest.mark.parametrize("raw", [b"CONNECT example.com:\xb2 HTTP/1.1\r\nHost: x\r\n\r\n",
                                 b"GET http://example.com:99999/ HTTP/1.1\r\nHost: x\r\n\r\n",
                                 b"GET http://example.com/ HTTP/1.1\r\nHost: x\nX: y\r\n\r\n",
                                 b"GET http://example.com/ HTTP/1.1\r\nHost: x\r\nX: a\x00b\r\n\r\n"])
def test_odd_inputs_get_a_clean_answer_not_a_dead_thread(rig, raw):  # noqa: F811
    assert status(rig.ask(raw)) in (400, 403, 407) and rig.dialed == []


def test_non_ascii_credentials_are_just_wrong(rig):  # noqa: F811
    bad = base64.b64encode("pilot:éé".encode()).decode()
    raw = f"CONNECT example.com:443 HTTP/1.1\r\nHost: x\r\nProxy-Authorization: Basic {bad}\r\n\r\n".encode()
    assert status(rig.ask(raw, auth=False, read_until_close=False)) == 407


def test_the_driver_contract_launch_args(rig):  # noqa: F811
    args = rig.proxy.launch_args()
    assert f"--proxy-server=http://127.0.0.1:{rig.proxy.port}" in args
    assert "--proxy-bypass-list=<-loopback>" in args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args


def test_start_twice_is_refused(rig):  # noqa: F811
    with pytest.raises(RuntimeError):
        rig.proxy.start()


def test_a_name_is_resolved_once_even_from_many_threads(tmp_path):
    r = Rig(tmp_path)
    try:
        ths = [threading.Thread(target=lambda: r.connect("example.com:443")) for _ in range(8)]
        [t.start() for t in ths]
        [t.join(10) for t in ths]
        assert r.calls.count("example.com") == 1
    finally:
        r.close()


def test_the_proxy_never_calls_the_page_driver(tmp_path):
    r = Rig(tmp_path)
    try:
        def boom(m):
            raise AssertionError("driver touched from a proxy thread")
        r.session.driver.pin_hosts = boom
        assert status(r.connect("example.com:443")) == 200
    finally:
        r.close()
