"""V5 phone access over real HTTP (no browser): default OFF, pairing (one-time code), hashed per-device tokens,
revocation, scopes, rate limits, audit without content, chat / voice / status / approvals through a fake Core bridge."""
import asyncio
import http.client
import json
import ssl
import struct

import pytest

from jarves import approvals as appr
from jarves import dashboard, mobile
from jarves.config import Config
from jarves.mobile import Bridge, MobileAccess

HOST = "192.168.50.7"


class FakeCore:
    """Records what reached the Core; answers deterministically."""

    def __init__(self):
        self.chats, self.voices = [], []

    def bridge(self, voice=True) -> Bridge:
        def chat(text, dev):
            self.chats.append((text, dev))
            return {"reply": "echo: " + text, "ok": True}

        def vo(wav, dev):
            self.voices.append((len(wav), dev))
            return {"transcript": "hello there", "reply": "hi", "ok": True}
        return Bridge(chat=chat, voice=vo, status=lambda: {"core": "running", "components": {"audio": "ok"}},
                      voice_available=lambda: voice)


def free_port():
    from conftest import free_port as fp
    return fp()


@pytest.fixture
def fake():
    return FakeCore()


@pytest.fixture
def srv(home, fake):
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST,
                            mobile_bridge=fake.bridge()).start()
    yield d
    d.stop()


def req(d, method, path, *, token=None, body=None, raw=None, headers=None, ctype="application/json", tls=False):
    if tls:
        conn = http.client.HTTPSConnection("127.0.0.1", d.port, timeout=10, context=ssl._create_unverified_context())
    else:
        conn = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    h = {"Host": f"{HOST}:{d.port}", **(headers or {})}
    if method != "GET":
        h.setdefault("X-Jarves", "1")
    if token:
        h["Authorization"] = "Bearer " + token
    data = raw
    if body is not None:
        data = json.dumps(body)
    if data is not None:
        h["Content-Type"] = ctype
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    out = r.read()
    conn.close()
    try:
        return r.status, json.loads(out), r
    except ValueError:
        return r.status, out, r


def admin(d, method, path, body=None):
    """The owner's own session on this computer (loopback + private key)."""
    conn = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    h = {"Host": f"127.0.0.1:{d.port}", "Cookie": f"{d.access.cookie}={d.url.split('t=')[1]}", "X-Jarves": "1"}
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = conn.getresponse()
    out = r.read()
    conn.close()
    return r.status, json.loads(out)


def pair(d, name="Galaxy"):
    st, code = admin(d, "POST", "/api/mobile/code", {})
    assert st == 200, code
    st, got, _ = req(d, "POST", "/api/m/pair", body={"code": code["code"], "name": name})
    assert st == 200, got
    return got["token"], got["device"], code


# ---------------------------------------------------------------- default OFF
def test_default_is_off_and_local_only(home):
    d = dashboard.Dashboard(home, Config(), free_port()).start()
    try:
        assert d.httpd.server_address[0] == "127.0.0.1"
        assert d.mobile.enabled is False
        for method, path in (("GET", "/m/"), ("GET", "/m/app.js"), ("POST", "/api/m/pair"), ("GET", "/api/m/status")):
            st, _, _ = req(d, method, path, body={} if method == "POST" else None,
                           headers={"Host": f"127.0.0.1:{d.port}"})
            assert st == 404, (method, path)
        st, body = admin(d, "POST", "/api/mobile/code", {})
        assert st == 409                                   # no code can be made while the phone mode is off
    finally:
        d.stop()


def test_mobile_mode_listens_on_the_network_but_only_for_phone_paths(srv):
    assert srv.httpd.server_address[0] == "0.0.0.0"
    n = dict(lan=False, mobile_on=True)
    assert mobile.network_ok("192.168.1.20", path="/m/", **n) and mobile.network_ok("192.168.1.20", path="/api/m/chat", **n)
    assert not mobile.network_ok("192.168.1.20", path="/api/sessions", **n)         # the dashboard stays PC-only
    assert not mobile.network_ok("192.168.1.20", path="/", **n)
    assert not mobile.network_ok("8.8.8.8", path="/m/", **n)                         # never the internet
    assert not mobile.network_ok("2001:4860::1", path="/api/m/chat", **n)
    assert mobile.network_ok("127.0.0.1", path="/api/sessions", **n)                 # the owner's own PC
    assert not mobile.network_ok("192.168.1.20", path="/m/", lan=False, mobile_on=False)   # off = nothing


def test_phone_token_opens_nothing_of_the_dashboard(srv):
    tok, dev, _ = pair(srv)
    for path in ("/api/sessions", "/api/devices", "/api/audit", "/api/mobile", "/api/files", "/api/settings"):
        st, _, _ = req(srv, "GET", path, token=tok)
        assert st == 403, path                              # no dashboard cookie: refused


# ---------------------------------------------------------------- pairing
def test_pairing_code_is_single_use_and_tokens_are_hashed(srv, home):
    st, code = admin(srv, "POST", "/api/mobile/code", {})
    assert st == 200 and len(code["code"]) == 8 and code["url"].endswith("#c=" + code["code"]) and code["qr"]
    st, got, _ = req(srv, "POST", "/api/m/pair", body={"code": code["code"], "name": "  My   phone "})
    assert st == 200 and got["token"].startswith(got["device"] + ".")
    st, _, _ = req(srv, "POST", "/api/m/pair", body={"code": code["code"]})
    assert st == 403                                         # the same code never works twice
    stored = (home / mobile.DEVICES_FILE).read_text(encoding="utf-8")
    assert got["token"].split(".", 1)[1] not in stored and code["code"] not in stored
    assert '"name": "My phone"' in stored
    assert req(srv, "GET", "/api/m/me", token=got["token"])[1]["name"] == "My phone"


def test_wrong_codes_burn_the_code(srv):
    st, code = admin(srv, "POST", "/api/mobile/code", {})
    for _ in range(mobile.CODE_TRIES_PER_IP):
        assert req(srv, "POST", "/api/m/pair", body={"code": "00000000"})[0] == 403
    # the real code no longer works from the same address after 5 misses
    assert req(srv, "POST", "/api/m/pair", body={"code": code["code"]})[0] == 403


def test_code_expires(home):
    now = [1000.0]
    a = MobileAccess(home, clock=lambda: now[0])
    c = a.new_code()
    now[0] += mobile.CODE_TTL_S + 1
    assert a.pair(c["code"], "x", "10.0.0.2") is None
    c = a.new_code()
    assert a.pair(c["code"], "x", "10.0.0.2")["token"]


def test_requests_need_the_x_jarves_header_and_same_origin(srv):
    st, code = admin(srv, "POST", "/api/mobile/code", {})
    st, _, _ = req(srv, "POST", "/api/m/pair", body={"code": code["code"]}, headers={"X-Jarves": "0"})
    assert st == 403
    st, _, _ = req(srv, "POST", "/api/m/pair", body={"code": code["code"]}, headers={"Origin": "http://evil.example"})
    assert st == 403
    st, _, _ = req(srv, "POST", "/api/m/pair", body={"code": code["code"]}, headers={"Sec-Fetch-Site": "cross-site"})
    assert st == 403
    st, _, _ = req(srv, "POST", "/api/m/pair", body={"code": code["code"]}, headers={"Origin": f"http://{HOST}:{srv.port}"})
    assert st == 200                                         # the PWA's own origin


def test_spoofed_host_is_refused(srv):
    st, _, _ = req(srv, "GET", "/m/", headers={"Host": "evil.example"})
    assert st == 403


# ---------------------------------------------------------------- auth, revocation, scopes
def test_no_token_bad_token_and_revocation(srv):
    assert req(srv, "GET", "/api/m/status")[0] == 401
    assert req(srv, "GET", "/api/m/status", token="abc.def")[0] == 401
    tok, dev, _ = pair(srv)
    assert req(srv, "GET", "/api/m/status", token=tok)[0] == 200
    st, _ = admin(srv, "POST", f"/api/mobile/devices/{dev}/revoke", {})
    assert st == 200
    assert req(srv, "GET", "/api/m/status", token=tok)[0] == 401          # at once
    assert admin(srv, "GET", "/api/mobile")[1]["devices"][0]["revoked"]
    stored = json.loads((srv.ctx.home / mobile.DEVICES_FILE).read_text(encoding="utf-8"))
    assert stored["devices"][0]["token_hash"] == ""


def test_phone_can_unpair_itself(srv):
    tok, dev, _ = pair(srv)
    assert req(srv, "POST", "/api/m/unpair", token=tok, body={})[0] == 200
    assert req(srv, "GET", "/api/m/me", token=tok)[0] == 401


def test_two_phones_are_independent(srv):
    t1, d1, _ = pair(srv, "one")
    t2, d2, _ = pair(srv, "two")
    admin(srv, "POST", f"/api/mobile/devices/{d1}/revoke", {})
    assert req(srv, "GET", "/api/m/me", token=t1)[0] == 401 and req(srv, "GET", "/api/m/me", token=t2)[0] == 200


def test_switching_from_phone_a_to_phone_b_rejects_a_for_good(srv, home):
    """#428: pair A, switch to B (revoke A, pair B): A is rejected at once, cannot re-pair with its used code, and stays
    rejected after the phone server restarts; B works."""
    ta, da, code_a = pair(srv, "phone A")
    assert req(srv, "GET", "/api/m/me", token=ta)[0] == 200
    assert admin(srv, "POST", f"/api/mobile/devices/{da}/revoke", {})[0] == 200
    tb, db, _ = pair(srv, "phone B")
    assert req(srv, "GET", "/api/m/me", token=ta)[0] == 401 and req(srv, "GET", "/api/m/me", token=tb)[0] == 200
    assert req(srv, "POST", "/api/m/pair", body={"code": code_a["code"], "name": "phone A"})[0] != 200   # the code was single use
    assert req(srv, "GET", "/api/m/me", token=ta)[0] == 401                                              # still rejected
    srv.stop()
    d2 = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=FakeCore().bridge()).start()
    try:
        assert req(d2, "GET", "/api/m/me", token=ta)[0] == 401 and req(d2, "GET", "/api/m/me", token=tb)[0] == 200
    finally:
        d2.stop()


def test_per_device_scopes(srv, fake):
    tok, dev, _ = pair(srv)
    st, _ = admin(srv, "POST", f"/api/mobile/devices/{dev}/scopes", {"scopes": ["status"]})
    assert st == 200
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "hi"})[0] == 403
    assert req(srv, "GET", "/api/m/approvals", token=tok)[0] == 403
    assert req(srv, "GET", "/api/m/status", token=tok)[0] == 200
    assert fake.chats == []


def test_idle_token_expires(home):
    now = [1000.0]
    a = MobileAccess(home, clock=lambda: now[0])
    tok = a.pair(a.new_code()["code"], "x", "10.0.0.2")["token"]
    assert a.authenticate(tok)
    now[0] += mobile.DEVICE_IDLE_S + 5
    assert a.authenticate(tok) is None


# ---------------------------------------------------------------- chat, status, voice
def test_chat_reaches_the_core_and_text_is_never_logged(srv, fake, home):
    tok, dev, _ = pair(srv)
    secret_text = "my-very-private-sentence-4711"
    st, body, _ = req(srv, "POST", "/api/m/chat", token=tok, body={"text": secret_text})
    assert st == 200 and body == {"reply": "echo: " + secret_text, "ok": True}
    assert fake.chats == [(secret_text, dev)]
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "  "})[0] == 400
    st, body, _ = req(srv, "POST", "/api/m/chat", token=tok, raw=b"x" * 9000)
    assert st == 413
    audit = (home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    assert secret_text not in audit and tok.split(".", 1)[1] not in audit and '"chars": "' in audit
    assert '"event": "request"' in audit and "device_paired" in audit


def test_chat_long_text_is_cut(srv, fake):
    tok, _, _ = pair(srv)
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "a" * 5000})[0] == 200
    assert len(fake.chats[0][0]) == mobile.MAX_CHAT_CHARS


def test_status_and_me(srv):
    tok, _, _ = pair(srv)
    st, body, _ = req(srv, "GET", "/api/m/status", token=tok)
    assert st == 200 and body["core"] == "running" and body["components"] == {"audio": "ok"} and body["tls"] is False
    me = req(srv, "GET", "/api/m/me", token=tok)[1]
    assert me["phone_approvals"] is False and me["voice"] is False       # plain HTTP: no voice


def test_voice_is_refused_without_tls(srv, fake):
    tok, _, _ = pair(srv)
    wav = b"RIFF" + b"\0" * 100
    st, body, _ = req(srv, "POST", "/api/m/voice", token=tok, raw=wav, ctype="audio/wav")
    assert st == 409 and fake.voices == []


def test_voice_over_tls_end_to_end(home, fake):
    cert, key = mobile.make_selfsigned(home, HOST)
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=fake.bridge(),
                            tls=(cert, key)).start()
    try:
        assert d.mobile.scheme == "https" and key.stat().st_size > 0
        st, code = admin_tls(d)
        st, got, _ = req(d, "POST", "/api/m/pair", body={"code": code["code"]}, tls=True)
        assert st == 200 and code["url"].startswith("https://")
        tok = got["token"]
        assert req(d, "GET", "/api/m/me", token=tok, tls=True)[1]["voice"] is True
        wav = b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"\0" * 64
        st, body, _ = req(d, "POST", "/api/m/voice", token=tok, raw=wav, ctype="audio/wav", tls=True)
        assert st == 200 and body == {"transcript": "hello there", "reply": "hi", "ok": True}
        assert fake.voices and fake.voices[0][1] == got["device"]
        assert req(d, "POST", "/api/m/voice", token=tok, raw=b"not a wav", ctype="audio/wav", tls=True)[0] == 415
        assert req(d, "POST", "/api/m/voice", token=tok, raw=b"RIFF",
                   headers={"Content-Length": str(mobile.MAX_AUDIO_BYTES + 10)}, ctype="audio/wav", tls=True)[0] == 413
    finally:
        d.stop()


def admin_tls(d):
    conn = http.client.HTTPSConnection("127.0.0.1", d.port, timeout=10, context=ssl._create_unverified_context())
    conn.request("POST", "/api/mobile/code", body="{}", headers={
        "Host": f"127.0.0.1:{d.port}", "Cookie": f"{d.access.cookie}={d.url.split('t=')[1]}", "X-Jarves": "1"})
    r = conn.getresponse()
    out = json.loads(r.read())
    conn.close()
    return r.status, out


def test_core_failure_is_an_honest_error(home):
    def boom(text, dev):
        raise RuntimeError("secret internal detail")
    b = Bridge(chat=boom, voice=boom, status=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=b).start()
    try:
        tok, _, _ = pair(d)
        st, body, _ = req(d, "POST", "/api/m/chat", token=tok, body={"text": "hi"})
        assert st == 502 and "secret" not in json.dumps(body)
        st, body, _ = req(d, "GET", "/api/m/status", token=tok)
        assert st == 200 and body["core"] == "unreachable"
    finally:
        d.stop()


# ---------------------------------------------------------------- rate limits
def test_chat_rate_limit_and_bad_token_lockout(home, fake):
    now = [5000.0]
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=fake.bridge()).start()
    d.mobile.clock = lambda: now[0]
    try:
        tok, _, _ = pair(d)
        codes = [req(d, "POST", "/api/m/chat", token=tok, body={"text": "x"})[0] for _ in range(mobile.CHAT_RATE_PER_MIN + 3)]
        assert codes.count(200) == mobile.CHAT_RATE_PER_MIN and codes[-1] == 429
        st, _, r = req(d, "POST", "/api/m/chat", token=tok, body={"text": "x"})
        assert st == 429 and r.getheader("Retry-After")
        now[0] += 61                                          # a minute later it works again
        assert req(d, "POST", "/api/m/chat", token=tok, body={"text": "x"})[0] == 200
        for _ in range(mobile.AUTH_FAILS_PER_MIN):
            assert req(d, "GET", "/api/m/status", token="a.b")[0] == 401
        assert req(d, "GET", "/api/m/status", token="a.b")[0] == 429     # locked out ...
        assert req(d, "GET", "/api/m/status", token=tok)[0] == 429       # ... for this address, valid token or not
        now[0] += 61
        assert req(d, "GET", "/api/m/status", token=tok)[0] == 200
    finally:
        d.stop()


def test_address_rate_limit(home, fake):
    now = [9000.0]
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=fake.bridge()).start()
    d.mobile.clock = lambda: now[0]
    try:
        codes = [req(d, "POST", "/api/m/pair", body={"code": "1"})[0] for _ in range(mobile.IP_RATE_PER_MIN + 2)]
        assert codes[-1] == 429
    finally:
        d.stop()


# ---------------------------------------------------------------- approvals
def dashboard_open(home):
    (home / "approvals").mkdir(parents=True, exist_ok=True)
    (home / "approvals" / "dashboard_seen").write_text("1", encoding="utf-8")


async def next_request(svc):
    ev, box = asyncio.Event(), []
    svc.on_ask.append(lambda a: (box.append(a), ev.set()))
    await asyncio.wait_for(ev.wait(), 30)
    svc.on_ask.clear()
    return box[0]


async def test_approvals_stay_on_the_pc_unless_the_owner_enables_phone_approvals(srv, home):
    tok, dev, _ = pair(srv)
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Send the email?", title="Send email", kind="message", risk="high"))
    a = await next_request(svc)
    st, view, _ = await asyncio.to_thread(req, srv, "GET", "/api/m/approvals", token=tok)
    assert st == 200 and [p["id"] for p in view["pending"]] == [a.id] and view["phone_approvals"] is False
    assert view["pending"][0]["title"] == "Send email" and "details" not in view["pending"][0]
    st, body, _ = await asyncio.to_thread(req, srv, "POST", f"/api/m/approvals/{a.id}", token=tok, body={"approved": True})
    assert st == 403 and body["code"] == "PHONE_APPROVALS_OFF"
    assert not task.done() and svc.has_pending()                       # nothing was approved
    st, body = await asyncio.to_thread(admin, srv, "POST", "/api/mobile/settings", {"phone_approvals": True})
    assert st == 200 and body["phone_approvals"] is True
    st, body, _ = await asyncio.to_thread(req, srv, "POST", f"/api/m/approvals/{a.id}", token=tok, body={"approved": True})
    assert st == 200
    assert await asyncio.wait_for(task, 30) is True
    assert appr.read_history(home, 5)[0]["channel"] == "phone"
    assert admin(srv, "POST", "/api/mobile/settings", {"phone_approvals": "yes"})[0] == 400


async def test_phone_can_deny_and_typed_code_approvals_are_pc_only(srv, home):
    tok, dev, _ = pair(srv)
    admin(srv, "POST", "/api/mobile/settings", {"phone_approvals": True})
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Install Git?", typed=True, title="Install"))
    a = await next_request(svc)
    st, view, _ = await asyncio.to_thread(req, srv, "GET", "/api/m/approvals", token=tok)
    assert view["pending"][0]["typed"] is True and "typed_code" not in json.dumps(view)
    st, body, _ = await asyncio.to_thread(req, srv, "POST", f"/api/m/approvals/{a.id}", token=tok, body={"approved": True})
    assert st == 403 and body["code"] == "TYPED_PC_ONLY" and not task.done()
    st, body, _ = await asyncio.to_thread(req, srv, "POST", f"/api/m/approvals/{a.id}", token=tok, body={"approved": False})
    assert st == 200 and await asyncio.wait_for(task, 30) is False


async def test_bad_approval_requests(srv, home):
    tok, _, _ = pair(srv)
    admin(srv, "POST", "/api/mobile/settings", {"phone_approvals": True})
    assert req(srv, "POST", "/api/m/approvals/0123456789abcdef", token=tok, body={"approved": "yes"})[0] == 400
    st, body, _ = req(srv, "POST", "/api/m/approvals/0123456789abcdef", token=tok, body={"approved": True})
    assert st == 409                                                     # nothing waiting under that id
    assert req(srv, "POST", "/api/m/approvals/not-an-id", token=tok, body={"approved": True})[0] == 404


# ---------------------------------------------------------------- owner side
def test_audit_and_owner_views(srv):
    tok, dev, _ = pair(srv)
    req(srv, "GET", "/api/m/status", token="bad.token")
    srv.mobile.revoke(dev, by="local")
    ev = {e["event"] for e in admin(srv, "GET", "/api/mobile/audit")[1]["entries"]}
    assert {"mobile_mode_started", "pairing_code_created", "device_paired", "auth_refused", "device_revoked"} <= ev
    info = admin(srv, "GET", "/api/mobile")[1]
    assert info["enabled"] and info["devices"][0]["id"] == dev and "token_hash" not in json.dumps(info)
    assert srv.mobile.pair_url("123").startswith(f"http://{HOST}:{srv.port}/m/#c=")


def test_selfsigned_certificate_is_private(home):
    cert, key = mobile.make_selfsigned(home, "192.168.1.5")
    assert cert.read_text().startswith("-----BEGIN CERTIFICATE")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))


# ---------------------------------------------------------------- security review fixes (docs/reviews/SWV5_SECURITY_REVIEW.md)
def test_sr1_switching_the_feature_off_stops_serving_at_once(home, fake):
    on = [True]
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=fake.bridge()).start()
    d.mobile.gate = lambda: on[0]
    try:
        tok, _, _ = pair(d)
        assert req(d, "GET", "/api/m/status", token=tok)[0] == 200
        on[0] = False
        assert req(d, "GET", "/api/m/status", token=tok)[0] == 404
        assert req(d, "GET", "/m/")[0] == 404
        assert admin(d, "POST", "/api/mobile/code", {})[0] == 409
        assert mobile.network_ok("192.168.1.9", lan=False, mobile_on=d.mobile.enabled, path="/m/") is False
        on[0] = True
        assert req(d, "GET", "/api/m/status", token=tok)[0] == 200          # tokens survive a switch cycle
        d.mobile.gate = lambda: 1 / 0                                          # an unreadable switch fails closed
        assert req(d, "GET", "/api/m/status", token=tok)[0] == 404
    finally:
        d.stop()


def test_sr2_unauthenticated_floods_cannot_rotate_the_audit_log(home, fake):
    now = [100.0]
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=fake.bridge()).start()
    d.mobile.clock = lambda: now[0]
    try:
        tok, dev, _ = pair(d)
        for _ in range(60):
            req(d, "POST", "/api/m/pair", body={"code": "00000000"})
        for _ in range(20):
            req(d, "GET", "/api/m/me", token="bad.token")
            now[0] += 0.001
        entries = d.mobile.audit_tail(1000)
        assert len([e for e in entries if e["event"] == "pairing_refused"]) <= 5
        assert len([e for e in entries if e["event"] == "auth_refused"]) <= 5
        assert any(e["event"] == "device_paired" for e in entries)            # the real history is intact
        now[0] += 61
        req(d, "GET", "/api/m/me", token="bad.token2")
        assert any(e.get("suppressed") for e in d.mobile.audit_tail(1000) if e["event"] == "auth_refused")
    finally:
        d.stop()


def test_sr3_secret_files_are_created_owner_only(home):
    import os
    import stat
    m = MobileAccess(home)
    m.pair(m.new_code()["code"], "p", "10.0.0.2")
    assert m.path.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(m.path.stat().st_mode) == 0o600
    created = []
    real = os.open
    os.open = lambda path, flags, mode=0o777, **k: (created.append((str(path), mode)), real(path, flags, mode, **k))[1]
    try:
        mobile.make_selfsigned(home, "192.168.1.5")
    finally:
        os.open = real
    assert any(p.endswith("key.pem") and mode == 0o600 for p, mode in created)   # never created world-readable


def test_sr4_one_turn_per_phone_at_a_time(home):
    import threading
    gate, entered = threading.Event(), threading.Event()
    calls = []

    def slow(text, dev):
        calls.append(text)
        entered.set()
        gate.wait(20)
        return {"reply": "done", "ok": True}
    br = Bridge(chat=slow, voice=lambda w, d: {}, status=lambda: {"core": "x", "components": {}})
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=br).start()
    try:
        tok, _, _ = pair(d)
        first = []
        t = threading.Thread(target=lambda: first.append(req(d, "POST", "/api/m/chat", token=tok, body={"text": "one"})))
        t.start()
        assert entered.wait(20)
        st, body, _ = req(d, "POST", "/api/m/chat", token=tok, body={"text": "one"})      # the double tap
        assert st == 409 and body["code"] == "BUSY"
        gate.set()
        t.join(20)
        assert first[0][0] == 200 and calls == ["one"]                    # the message ran exactly once
        assert req(d, "POST", "/api/m/chat", token=tok, body={"text": "two"})[0] == 200   # free again
    finally:
        gate.set()
        d.stop()


async def test_sr5_typed_approval_cannot_be_approved_from_the_phone_without_the_code(srv, home):
    """The in-process service enforces the typed code itself; the phone path never sends one."""
    pair(srv)
    admin(srv, "POST", "/api/mobile/settings", {"phone_approvals": True})
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Install Git?", typed=True, title="Install"))
    a = await next_request(svc)
    ok, why = await asyncio.to_thread(appr.submit_answer, home, a.id, True, channel="phone")   # no pending row involved
    assert ok is False and not task.done()
    ok, why = await asyncio.to_thread(appr.submit_answer, home, a.id, False, channel="phone")
    assert ok and await asyncio.wait_for(task, 30) is False


def test_sr6_audit_never_holds_content_or_credentials(srv, fake, home):
    tok, dev, code = pair(srv)
    secret_msg = "my bank PIN is 4921 sk-ABCDEF1234567890"
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": secret_msg})[0] == 200
    req(srv, "POST", "/api/m/pair", body={"code": "12345678", "name": "x"})
    raw = (home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    assert secret_msg not in raw and "4921" not in raw and tok not in raw and tok.split(".")[1] not in raw
    assert code["code"] not in raw and "12345678" not in raw


def test_sr7_revocation_is_immediate(srv):
    tok, dev, _ = pair(srv)
    assert req(srv, "GET", "/api/m/me", token=tok)[0] == 200
    assert admin(srv, "POST", f"/api/mobile/devices/{dev}/revoke", {})[0] == 200
    assert req(srv, "GET", "/api/m/me", token=tok)[0] == 401
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "x"})[0] == 401


def test_chat_message_ids_make_a_resend_idempotent(srv, fake):
    tok, dev, _ = pair(srv)
    st, a, _ = req(srv, "POST", "/api/m/chat", token=tok, body={"text": "turn on the light", "id": "c0ffee-0001-abcd"})
    assert st == 200 and a["reply"] == "echo: turn on the light" and "replay" not in a
    # the phone lost the answer (Wi-Fi blip) and resends the same id: stored answer, the Core does not run it again
    st, b, _ = req(srv, "POST", "/api/m/chat", token=tok, body={"text": "turn on the light", "id": "c0ffee-0001-abcd"})
    assert st == 200 and b["reply"] == a["reply"] and b["replay"] is True
    assert fake.chats == [("turn on the light", dev)]
    # a new id is a new message; no id keeps the old behaviour
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "again", "id": "c0ffee-0002-abcd"})[0] == 200
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "again"})[0] == 200
    assert len(fake.chats) == 3
    # malformed ids are refused
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "x", "id": "short"})[0] == 400
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "x", "id": 12345678})[0] == 400
    assert len(fake.chats) == 3


def test_chat_message_ids_are_per_phone_and_expire(home):
    now = [1000.0]
    a = MobileAccess(home, clock=lambda: now[0])
    a.remember("dev-a", "msg-id-0001", {"reply": "r", "ok": True})
    assert a.replay("dev-a", "msg-id-0001") == {"reply": "r", "ok": True}
    assert a.replay("dev-b", "msg-id-0001") is None                    # another phone cannot read it
    now[0] += mobile.REPLY_TTL_S + 1
    assert a.replay("dev-a", "msg-id-0001") is None
    for i in range(mobile.REPLY_KEEP + 20):
        a.remember("dev-a", f"msg-id-{i:06d}", {"reply": "x", "ok": True})
    assert len(a._replies) <= mobile.REPLY_KEEP


def test_removing_every_scope_does_not_restore_the_defaults(srv, home):
    """V5 sec F7: an empty scope list means no scopes (fail closed), not chat/voice/status/approvals again."""
    tok, dev, _ = pair(srv)
    assert admin(srv, "POST", f"/api/mobile/devices/{dev}/scopes", {"scopes": []})[0] == 200
    assert MobileAccess(home).authenticate(tok)["scopes"] == []
    assert req(srv, "GET", "/api/m/status", token=tok)[0] == 403
    assert req(srv, "POST", "/api/m/chat", token=tok, body={"text": "hi"})[0] == 403
    assert req(srv, "GET", "/api/m/approvals", token=tok)[0] == 403

async def test_phone_cannot_approve_a_pc_only_plan_approval(srv, home):
    """V5 sec F1: with phone approvals ON a paired phone can neither approve a goal plan nor a plan step."""
    tok, dev, _ = pair(srv)
    admin(srv, "POST", "/api/mobile/settings", {"phone_approvals": True})
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    for title in ("goal_plan", "goal_plan_step"):
        task = asyncio.create_task(svc.ask(f"Run? ({title})", title=title, risk="medium", pc_only=True, timeout=60))
        a = await next_request(svc)
        st, view, _ = await asyncio.to_thread(req, srv, "GET", "/api/m/approvals", token=tok)
        assert view["pending"][0]["pc_only"] is True
        st, body, _ = await asyncio.to_thread(req, srv, "POST", f"/api/m/approvals/{a.id}", token=tok, body={"approved": True})
        assert st == 403 and body["code"] == "PC_ONLY" and not task.done()
        ok, why = svc.answer(a.id, True, channel="phone")           # also refused inside the service itself
        assert not ok and "computer" in why and not task.done()
        st, body, _ = await asyncio.to_thread(req, srv, "POST", f"/api/m/approvals/{a.id}", token=tok, body={"approved": False})
        assert st == 200 and await asyncio.wait_for(task, 30) is False     # a phone may still say no


async def test_pc_only_approval_is_still_approvable_on_the_computer(home):
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Run this plan?", title="goal_plan", pc_only=True, timeout=60))
    a = await next_request(svc)
    assert svc.answer(a.id, True, channel="dashboard") == (True, "")
    assert await asyncio.wait_for(task, 30) is True


def test_network_help_absent_when_phone_access_is_off(home, fake):
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=fake.bridge()).start()
    try:
        assert admin(d, "GET", "/api/mobile")[1]["network"]["firewall"]["profile"] == "private"
        d.mobile.gate = lambda: False
        assert admin(d, "GET", "/api/mobile")[1]["network"] is None
    finally:
        d.stop()
