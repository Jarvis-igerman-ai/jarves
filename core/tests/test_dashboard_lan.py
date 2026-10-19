"""Dashboard home-LAN mode (SW-V2 D02, WP-M4) over real HTTP: loopback stays the default; LAN needs the switch;
pairing code, device sessions, CSRF, spoofed Host/Origin, least privilege, rate limit, audit, revocation."""
import http.client
import json

import pytest

from jarves import cli, dashboard, dashboard_auth
from jarves.config import Config
from jarves.dashboard_auth import Access, client_allowed
from jarves.features import Features

LAN = "192.168.50.7"


@pytest.fixture
def lan(home):
    d = dashboard.Dashboard(home, Config(), free_port(), lan=True, lan_host=LAN).start()
    yield d
    d.stop()


def free_port():
    from conftest import free_port as fp
    return fp()


def req(d, method, path, *, host=None, cookie=None, headers=None, body=None, admin=False):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=5)
    h = {"Host": host or f"{LAN}:{d.port}", **(headers or {})}
    if admin:
        cookie = f"{dashboard.COOKIE}={d.url.split('t=')[1]}"
    if cookie:
        h["Cookie"] = cookie
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def jget(d, path, **kw):
    r, data = req(d, "GET", path, **kw)
    return r.status, (json.loads(data) if data[:1] in (b"{", b"[") else data)


def pair(d, code, name="phone"):
    r, data = req(d, "POST", "/api/pair", headers={"X-Jarves": "1"}, body={"code": code, "name": name})
    ck = r.getheader("Set-Cookie") or ""
    return r.status, ck.split(";")[0] if ck else None, ck


def new_code(d) -> str:
    r, data = req(d, "POST", "/api/devices/pair-code", admin=True, host=f"127.0.0.1:{d.port}",
                  headers={"X-Jarves": "1"}, body={})
    assert r.status == 200, data
    return json.loads(data)["code"]


def test_loopback_is_the_default(home):
    d = dashboard.Dashboard(home, Config()).start()
    try:
        assert d.httpd.server_address[0] == "127.0.0.1" and d.lan_url is None and not d.access.lan
        assert not d.access.host_ok(f"{LAN}:80")                   # a LAN Host is refused when LAN mode is off
    finally:
        d.stop()


def test_lan_mode_binds_all_but_answers_only_the_home_network(lan, monkeypatch):
    assert lan.httpd.server_address[0] == "0.0.0.0" and lan.lan_url == f"http://{LAN}:{lan.port}/pair"
    for ip in ("192.168.1.20", "10.0.0.5", "172.16.3.4", "127.0.0.1", "::1", "fe80::1", "fd00::5", "169.254.1.1"):
        assert client_allowed(ip), ip
    for ip in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "100.64.0.1", "not-an-ip"):
        assert not client_allowed(ip), ip
    monkeypatch.setattr(dashboard, "client_allowed", lambda ip: False)    # e.g. a port-forward from the internet
    r, body = req(lan, "GET", "/pair")
    assert r.status == 403 and b"network" in body


def test_spoofed_host_and_origin_are_refused(lan):
    assert req(lan, "GET", "/pair", host="evil.example")[0].status == 403
    assert req(lan, "GET", "/pair", host=f"{LAN}.evil.example:{lan.port}")[0].status == 403
    assert req(lan, "GET", "/pair", host=f"{LAN}:{lan.port}")[0].status == 200
    for hdr in ({"Origin": "http://evil.example"}, {"Origin": f"http://{LAN}:1"}, {"Sec-Fetch-Site": "cross-site"}):
        r, _ = req(lan, "POST", "/api/diagnose", admin=True, host=f"127.0.0.1:{lan.port}",
                   headers={"X-Jarves": "1", **hdr}, body={})
        assert r.status == 403, hdr
    r, _ = req(lan, "POST", "/api/diagnose", admin=True, host=f"127.0.0.1:{lan.port}",
               headers={"X-Jarves": "1", "Origin": f"http://127.0.0.1:{lan.port}"}, body={})
    assert r.status == 200
    assert any(e["event"] == "refused_cross_origin" for e in lan.access.audit_tail())


def test_nothing_without_a_session_except_the_pairing_page(lan):
    assert req(lan, "GET", "/")[0].status == 403
    assert req(lan, "GET", "/api/features")[0].status == 403
    assert req(lan, "GET", "/pair")[0].status == 200 and b"Pair this phone" in req(lan, "GET", "/pair")[1]
    assert req(lan, "GET", "/static/app.js")[0].status == 403
    # the one-time computer token is only accepted from the computer itself
    st, _ = jget(lan, "/api/session", admin=True)
    assert st == 200


def test_pairing_device_session_csrf_privileges_and_revocation(lan, home):
    assert pair(lan, "00000000")[0] == 403                         # no code yet
    code = new_code(lan)
    st, cookie, raw = pair(lan, code, "Galaxy")
    assert st == 200 and cookie.startswith("jarves_dev=") and "HttpOnly" in raw and "SameSite=Strict" in raw
    assert pair(lan, code)[0] == 403                               # single use
    st, s = jget(lan, "/api/session", cookie=cookie)
    assert st == 200 and s["kind"] == "device" and s["name"] == "Galaxy" and len(s["csrf"]) > 16
    assert jget(lan, "/api/features", cookie=cookie)[0] == 200
    # writes: X-Jarves AND the device's CSRF token
    w = {"X-Jarves": "1"}
    assert req(lan, "POST", "/api/diagnose", cookie=cookie, headers=w, body={})[0].status == 403
    assert req(lan, "POST", "/api/diagnose", cookie=cookie, headers={**w, "X-CSRF-Token": "nope"}, body={})[0].status == 403
    assert req(lan, "POST", "/api/diagnose", cookie=cookie, headers={**w, "X-CSRF-Token": s["csrf"]}, body={})[0].status == 200
    # least privilege: no device management, audit or LAN switch from a phone
    csrf = {**w, "X-CSRF-Token": s["csrf"]}
    assert jget(lan, "/api/devices", cookie=cookie)[0] == 403
    assert jget(lan, "/api/audit", cookie=cookie)[0] == 403
    assert req(lan, "POST", "/api/devices/pair-code", cookie=cookie, headers=csrf, body={})[0].status == 403
    assert req(lan, "POST", "/api/features/dashboard_lan", cookie=cookie, headers=csrf,
               body={"enabled": False})[0].status == 403
    # the computer sees it; the secret is stored only as a hash
    st, devs = jget(lan, "/api/devices", admin=True, host=f"127.0.0.1:{lan.port}")
    assert st == 200 and devs["devices"][0]["name"] == "Galaxy" and "token_hash" not in devs["devices"][0]
    stored = (home / dashboard_auth.DEVICES_FILE).read_text(encoding="utf-8")
    assert cookie.split(".", 1)[1] not in stored
    # a phone may un-pair itself; afterwards nothing works
    r, _ = req(lan, "POST", f"/api/devices/{s['device']}/revoke", cookie=cookie, headers=csrf, body={})
    assert r.status == 200
    assert jget(lan, "/api/features", cookie=cookie)[0] == 403
    audit = (home / dashboard_auth.AUDIT_FILE).read_text(encoding="utf-8")
    for ev in ("pairing_code_created", "device_paired", "refused_csrf", "refused_privilege", "device_revoked", '"write"'):
        assert ev in audit, ev
    assert cookie.split("=", 1)[1] not in audit and code not in audit and s["csrf"] not in audit


def test_wrong_codes_are_limited_then_the_code_is_burnt(lan, monkeypatch):
    code = new_code(lan)
    for _ in range(dashboard_auth.CODE_TRIES_PER_IP):
        assert pair(lan, "12345678" if code != "12345678" else "87654321")[0] == 403
    assert pair(lan, code)[0] == 403                               # this address is locked out of this code
    a = Access(lan.access.home, "x", lan=True, lan_host=LAN)
    c = a.new_code()["code"]
    for i in range(dashboard_auth.CODE_TRIES_TOTAL):
        a.pair("wrong", "x", f"192.168.1.{i % 250 + 2}")
    assert a.pair(c, "x", "192.168.1.250") is None                 # 20 wrong codes in total: burnt


def test_expired_code_and_idle_device(home):
    t = [1000.0]
    a = Access(home, "admin", lan=True, lan_host=LAN, clock=lambda: t[0])
    c = a.new_code()["code"]
    t[0] += dashboard_auth.CODE_TTL_S + 1
    assert a.pair(c, "x", "192.168.1.5") is None
    c = a.new_code()["code"]
    got = a.pair(c, "x", "192.168.1.5")
    assert a.device_for(got["cookie"]) is not None
    t[0] += dashboard_auth.DEVICE_IDLE_S + 1
    assert a.device_for(got["cookie"]) is None


def test_rate_limit(lan, monkeypatch):
    monkeypatch.setattr(dashboard_auth, "RATE_PER_MIN", 5)
    codes = [req(lan, "GET", "/pair")[0].status for _ in range(8)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429


def test_device_cookie_is_ignored_when_lan_mode_is_off(home):
    a = Access(home, "admin", lan=True, lan_host=LAN)
    got = a.pair(a.new_code()["code"], "x", "192.168.1.5")
    off = Access(home, "admin", lan=False)
    assert off.principal({dashboard_auth.DEVICE_COOKIE: got["cookie"]}, "127.0.0.1") is None
    assert a.principal({dashboard_auth.DEVICE_COOKIE: got["cookie"]}, "192.168.1.5")["kind"] == "device"
    assert a.principal({"jarves_dash": "admin"}, "192.168.1.5") is None   # the computer token never from the LAN


def test_cli_lan_needs_the_switch(home, capsys):
    assert Features(Config(), home).enabled("dashboard_lan") is False
    assert cli.main(["dashboard", "--lan", "--no-open"]) == 1
    assert "dashboard_lan" in capsys.readouterr().err


def test_static_pages_and_phone_layout(lan):
    for f in ("m4.js", "m4.css", "pair.js"):
        r, body = req(lan, "GET", "/static/" + f, admin=True, host=f"127.0.0.1:{lan.port}")
        assert r.status == 200 and b".innerHTML" not in body
    css = req(lan, "GET", "/static/m4.css")[1].decode()
    assert "@media (max-width: 760px)" in css and "data-label" in css
    html = req(lan, "GET", "/", admin=True, host=f"127.0.0.1:{lan.port}")[1]
    assert b"/static/m4.js" in html and b'name="viewport"' in html
