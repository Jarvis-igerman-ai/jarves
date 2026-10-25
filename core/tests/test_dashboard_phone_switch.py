"""#356 / #357: the printed and opened link matches the scheme the port speaks, `jarvis dashboard` reopens a running
dashboard instead of starting a second one, the wrong-token page explains itself without revealing the key, and the
Phone page can turn phone access on without the CLI. Real HTTP/TLS on loopback; no browser is opened."""
import argparse
import http.client
import json
import ssl

import pytest

from jarves import cli, dashboard, features as feat, mobile, runmarks, tailnet
from jarves.config import Config

from conftest import free_port


def _get(port, path, *, tls=False, headers=None, cookie=None):
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if cookie:
        h["Cookie"] = cookie
    if tls:
        c = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=ssl._create_unverified_context())
    else:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def _pc(d, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    c.request(method, path, body=json.dumps(body if body is not None else {}),
              headers={"Host": f"127.0.0.1:{d.port}", "Cookie": f"{dashboard.COOKIE}={d.token}", "X-Jarves": "1",
                       "Content-Type": "application/json"})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data) if data[:1] == b"{" else data


@pytest.fixture
def opened(monkeypatch):
    got = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: got.append(url) or True)
    return got


def test_url_scheme_is_https_when_tls_is_on(home):
    cert, key = mobile.make_selfsigned(home, "127.0.0.1")
    d = dashboard.Dashboard(home, Config(), free_port(), tls=(cert, key)).start()
    try:
        assert d.url.startswith("https://127.0.0.1:")
        r, _ = _get(d.port, "/?t=" + d.token, tls=True)                  # what the printed link really does
        assert r.status == 302
    finally:
        d.stop()


def test_cli_dashboard_reuses_the_running_one(home, opened, monkeypatch, capsys):
    d = dashboard.Dashboard(home, Config(), free_port()).start()
    try:
        def boom(*a, **k):
            raise AssertionError("a second dashboard was started")
        monkeypatch.setattr(dashboard, "Dashboard", boom)
        a = argparse.Namespace(port=0, no_open=False, page="", lan=False, mobile=False, tailnet_only=False,
                               tls_selfsigned=False)
        assert cli.cmd_dashboard(a, Config(), home) == 0
        assert opened == [d.url]
        assert d.url in capsys.readouterr().out
    finally:
        monkeypatch.undo()
        d.stop()
    assert runmarks.read_live(home) is None                                # stopped: nothing to reuse


def test_wrong_token_page_explains_and_reopens_without_revealing_the_key(home, opened):
    d = dashboard.Dashboard(home, Config(), free_port()).start()
    try:
        r, body = _get(d.port, "/?t=wrong")
        text = body.decode()
        assert r.status == 403 and "text/html" in r.getheader("Content-Type")
        assert "Open the current dashboard" in text and "פתח את לוח הבקרה הנוכחי" in text
        assert d.token not in text and not opened
        r, _ = _get(d.port, "/?t=wrong&reopen=1")                           # no same-origin proof: refused
        assert r.status == 403 and not opened
        r, body = _get(d.port, "/?t=wrong&reopen=1", headers={"Sec-Fetch-Site": "same-origin"})
        assert r.status == 200 and opened == [d.url] and d.token.encode() not in body
        r, body = _get(d.port, "/api/overview")                              # no cookie at all: same helpful page
        assert r.status == 403 and b"Open the current dashboard" in body and d.token.encode() not in body
    finally:
        d.stop()


def test_phone_switch_turns_phone_access_on_live(home, monkeypatch):
    monkeypatch.setattr(tailnet, "detect", lambda *a, **k: {})
    monkeypatch.setattr(dashboard, "lan_ip", lambda: "127.0.0.1")
    d = dashboard.Dashboard(home, Config(), free_port()).start()
    try:
        assert _pc(d, "GET", "/api/mobile")[1]["enabled"] is False
        assert _pc(d, "POST", "/api/mobile/code")[0] == 409                  # off by default
        assert _pc(d, "POST", "/api/mobile/enable")[0] == 400                # high risk: needs the explicit confirm
        st, info = _pc(d, "POST", "/api/mobile/enable", {"confirm": True})
        assert st == 200 and info["enabled"] and info["tls"] and info["port"] != d.port
        assert feat.Features(Config(), home).enabled("mobile_access")
        st, code = _pc(d, "POST", "/api/mobile/code")
        assert st == 200 and len(code["code"]) == 8 and code["qr"] and code["url"].startswith("https://")
        r, _ = _get(info["port"], "/m/", tls=True)                           # the phone listener speaks TLS
        assert r.status == 200
        r, _ = _get(d.port, "/")                                             # the desktop link is unchanged
        assert r.status == 403
        assert runmarks.read_live(home)["mobile"] is True
    finally:
        d.stop()


def test_phone_switch_says_the_next_step_when_it_cannot(home, monkeypatch):
    d = dashboard.Dashboard(home, Config(), free_port()).start()
    try:
        d.ctx.dashboard = None
        st, err = _pc(d, "POST", "/api/mobile/enable", {"confirm": True})
        assert st == 409 and "jarvis dashboard --mobile --tls-selfsigned" in err["next"]
    finally:
        d.stop()
