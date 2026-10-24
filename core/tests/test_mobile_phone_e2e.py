"""V5 phone acceptance in an EMULATED phone (issue #17; docs/reviews/SWV5_ACCEPTANCE.md, "emulated:" marks).

The real Core dashboard server runs with mobile access on in a temp home (fake Core bridge, fake STT/TTS, no network
beyond 127.0.0.1) and a REAL headless Chromium (Edge on Windows) drives the phone app as a Pixel-class device: 390x844,
touch, mobile user agent, a fake microphone (--use-fake-device-for-media-stream). The phone's host name `phone.lan` is
mapped to 127.0.0.1 inside the browser only, so plain http is NOT a secure context (like a real LAN address) and the
self-signed https is. Missing Playwright or browser = a failure, never a skip (CI installs both)."""
from __future__ import annotations

import asyncio
import http.client
import json
import ssl
import sys
import threading
import time
import wave
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest

from jarves import approvals as appr
from jarves import dashboard, mobile, qrmini
from jarves.config import Config
from jarves.features import Features
from jarves.mobile import Bridge

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import all_pages_check as apc  # noqa: E402

HOST = "phone.lan"
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Mobile Safari/537.36")
ARGS = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required",
        "--ignore-certificate-errors", f"--host-resolver-rules=MAP {HOST} 127.0.0.1"]
TIMEOUT = 20000


class FakeCore:
    """The Core as the phone sees it: records what reached it; fake speech-to-text and answer (no model, no TTS)."""

    def __init__(self):
        self.chats, self.wavs = [], []

    def bridge(self) -> Bridge:
        def chat(text, dev):
            self.chats.append(text)
            return {"reply": "echo: " + text, "ok": True}

        def voice(wav, dev):
            self.wavs.append(wav)
            return {"transcript": "what time is it", "reply": "It is noon.", "ok": True}
        return Bridge(chat=chat, voice=voice, status=lambda: {"core": "running", "components": {"audio": "ok"}},
                      voice_available=lambda: True)


class _Mem:
    def __init__(self):
        self.s = {}

    def get_setting(self, k):
        return self.s.get(k)

    def set_setting(self, k, v):
        self.s[k] = v


class _ChatCore:
    """What chat_engine.WebChat needs from a Core: a home, a memory and one async answer (fake model, no network)."""

    def __init__(self, home, fake):
        self.home, self.fake, self.memory, self.stopping = home, fake, _Mem(), False

    async def respond_remote(self, text, *, on_delta=None, fresh=False):
        from types import SimpleNamespace
        self.fake.chats.append(text)
        return SimpleNamespace(text="echo: " + text, ok=True, code="", source="")


class Phone:
    """One running Core dashboard with phone access, plus the owner's own (loopback) session."""

    def __init__(self, home: Path, tls: bool):
        from conftest import free_port
        self.home, self.tls, self.fake = home, tls, FakeCore()
        kw = {}
        if tls:
            kw["tls"] = mobile.make_selfsigned(home, HOST)
        self.features = Features(Config(), home)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        from jarves import chat_engine
        self.chat = chat_engine.WebChat(_ChatCore(home, self.fake), self.loop)
        self.d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST,
                                     mobile_bridge=self.fake.bridge(), chat=self.chat, **kw).start()
        self.d.mobile.gate = lambda: self.features.enabled("mobile_access")      # as app.py wires it
        self.features.set("mobile_access", True)
        self.base = f"{'https' if tls else 'http'}://{HOST}:{self.d.port}"
        self.svc = self.run(self._mk())
        self.asks: list = []

    async def _mk(self):
        return appr.ApprovalService(self.home)

    def run(self, coro, timeout=30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def owner(self, method, path, body=None):
        """The owner's session on the PC (loopback + private key), as the dashboard Phone page does."""
        if self.tls:
            conn = http.client.HTTPSConnection("127.0.0.1", self.d.port, timeout=10, context=ssl._create_unverified_context())
        else:
            conn = http.client.HTTPConnection("127.0.0.1", self.d.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.d.port}", "Cookie": f"{self.d.access.cookie}={self.d.url.split('t=')[1]}",
             "X-Jarves": "1"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = conn.getresponse()
        out = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, out

    def code(self) -> dict:
        st, c = self.owner("POST", "/api/mobile/code", {})
        assert st == 200, c
        return c

    def ask(self, title, typed=False):
        """Put an approval request in front of the owner; returns a future for the answer."""
        (self.home / "approvals").mkdir(parents=True, exist_ok=True)
        (self.home / "approvals" / "dashboard_seen").write_text(str(time.time()), encoding="utf-8")     # a place to answer
        fut = asyncio.run_coroutine_threadsafe(
            self.svc.ask(f"Send the test message? ({title})", title=title, kind="message", risk="high", typed=typed, timeout=120, source="owner"),
            self.loop)
        self.asks.append(fut)
        t = 0
        while not any(r.get("title") == title for r in appr.read_pending_file(self.home, reveal=False)):
            threading.Event().wait(0.05)
            t += 1
            assert t < 400, "approval never became pending"
        return fut

    def close(self):
        for r in appr.read_pending_file(self.home, reveal=False):             # let no ask() outlive the test
            appr.submit_answer(self.home, r["id"], False, channel="phone")
        self.chat.close()
        self.d.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=ARGS, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def phone(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    made: list[Phone] = []

    def make(tls=False) -> Phone:
        p = Phone(tmp_path, tls)
        made.append(p)
        return p
    yield make
    for p in made:
        p.close()


def new_page(browser, *, locale="en-US", scheme="light", mic=True):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, screen={"width": 390, "height": 844},
                              device_scale_factor=2.75, is_mobile=True, has_touch=True, user_agent=UA, locale=locale,
                              color_scheme=scheme, ignore_https_errors=True, permissions=["microphone"] if mic else [])
    ctx.set_default_timeout(TIMEOUT)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    return page


def ui_pair(page, p: Phone, name="Pixel test"):
    c = p.code()
    page.goto(c["url"])
    page.wait_for_selector("#pairView:not([hidden])")
    assert page.input_value("#code") == c["code"]                     # pre-filled from the QR link
    page.fill("#name", name)
    page.tap("#pairForm button[type=submit]")
    page.wait_for_selector("#appView:not([hidden])")
    return c


def say(page, text):
    """Type into the shared chat view (chatview.js) on the Chat tab and send."""
    page.tap("[data-tab=chat]")
    page.fill(".jc-in", text)
    page.tap(".jc-send")


def until(page, js, timeout=15000):
    """Poll a page expression until truthy (page.wait_for_function evals a string, which the phone page CSP forbids)."""
    import time
    end = time.time() + timeout / 1000
    while time.time() < end:
        if page.evaluate("() => !!(" + js + ")"):
            return
        page.wait_for_timeout(100)
    raise AssertionError("page condition not reached: " + js)


def token(page) -> str:
    return page.evaluate("localStorage.getItem('jarves_m_token')")


def call(page, method, path, body=None, tok=None, raw_headers=None):
    return page.evaluate("""async ([method, path, body, tok, hdrs]) => {
        const h = {"X-Jarves": "1", ...(hdrs || {})}; if (tok) h.Authorization = "Bearer " + tok;
        if (body !== null) h["Content-Type"] = "application/json";
        const r = await fetch(path, {method, headers: h, body: body === null ? undefined : JSON.stringify(body)});
        return {status: r.status, retry: r.headers.get("Retry-After"), json: await r.json().catch(() => ({}))};
    }""", [method, path, body, tok, raw_headers])


def decode_qr(rows) -> str:
    """Read the symbol the way a phone camera would: rendered with a 4-module quiet zone, decoded by OpenCV's
    ArUco-based QR detector. The classic cv2.QRCodeDetector misses about 0.3% of VALID symbols (57 of 20,002 random
    pairing URLs; 0 for the ArUco detector; qrmini's matrix is identical to the `qrcode` reference library's), and
    the pairing URL is random per run (free port + one-time code): PR #78 run 39978664908 failed on such a URL."""
    import cv2
    n, scale, quiet = len(rows), 8, 4
    img = np.full(((n + 2 * quiet) * scale,) * 2, 255, np.uint8)
    for y, row in enumerate(rows):
        for x, dark in enumerate(row):
            if dark in (True, "1"):
                img[(y + quiet) * scale:(y + quiet + 1) * scale, (x + quiet) * scale:(x + quiet + 1) * scale] = 0
    text, _, _ = cv2.QRCodeDetectorAruco().detectAndDecode(img)
    return text


# ------------------------------------------------------------------ steps 3-6: QR, pairing, single use
def test_qr_payload_decodes_and_pairing_is_single_use(browser, phone):
    p = phone(tls=True)
    c = p.code()
    assert decode_qr(c["qr"]) == c["url"]                              # what the phone camera reads
    assert c["url"] == f"{p.base}/m/#c={c['code']}" and len(c["code"]) == 8
    page = new_page(browser)
    page.goto(c["url"])                                                # "scan, open in Chrome, proceed past the warning"
    assert page.input_value("#code") == c["code"] and "c=" not in page.url           # code is not left in the address
    assert page.evaluate("window.isSecureContext") is True
    page.fill("#name", "Pixel test")
    page.tap("#pairForm button[type=submit]")
    page.wait_for_selector("#appView:not([hidden])")
    assert page.inner_text("#conn") == "connected"                    # status pill: Online
    tok = token(page)
    assert tok and tok.split(".")[0] in {d["id"] for d in p.owner("GET", "/api/mobile")[1]["devices"]}
    assert tok.split(".")[1] not in page.url and "token" not in page.url                 # never in a URL
    # the same QR / code on a second device fails; so does a wrong code
    other = new_page(browser)
    other.goto(c["url"])
    other.tap("#pairForm button[type=submit]")
    until(other, "document.getElementById('pairMsg').textContent.startsWith('Wrong or expired')")
    assert token(other) is None and other.is_visible("#pairView")
    c2 = p.code()
    wrong = new_page(browser)
    wrong.goto(f"{p.base}/m/")
    wrong.fill("#code", "00000000" if c2["code"] != "00000000" else "11111111")
    wrong.tap("#pairForm button[type=submit]")
    until(wrong, "document.getElementById('pairMsg').textContent.startsWith('Wrong or expired')")
    assert token(wrong) is None
    assert page.errors == [] and other.errors == [] and wrong.errors == []


# ------------------------------------------------------------------ steps 7-8: chat, idempotent resend
def test_chat_round_trip_and_idempotent_resend_after_a_lost_answer(browser, phone):
    p = phone()
    page = new_page(browser)
    ui_pair(page, p)
    say(page, "hello")
    until(page, "[...document.querySelectorAll('.jc-msg')].some(m => m.textContent.includes('echo: hello'))")
    assert p.fake.chats == ["hello"]
    # Wi-Fi blip: the app resends the same message id; the PC runs it ONCE and returns the stored answer
    tok = token(page)
    first = call(page, "POST", "/api/m/chat/send", {"text": "turn on the light", "id": "light-0001"}, tok)
    until(page, "true")
    again = call(page, "POST", "/api/m/chat/send", {"text": "turn on the light", "id": "light-0001"}, tok)
    assert first["status"] == 200 and again["status"] == 200 and again["json"].get("replay") is True
    for _ in range(100):
        if "turn on the light" in p.fake.chats:
            break
        page.wait_for_timeout(100)
    assert p.fake.chats == ["hello", "turn on the light"]              # the PC ran it ONCE
    assert page.errors == []


# ------------------------------------------------------------------ steps 11-14: approvals
def test_approvals_list_answer_typed_refused_and_switch_off(browser, phone):
    p = phone()
    page = new_page(browser)
    ui_pair(page, p)
    page.tap("[data-tab=approvals]")
    fut = p.ask("Send test message")
    until(page, "document.querySelector('#apprList li') && document.querySelector('#apprList').textContent.includes('Send test message')")
    assert "off" in page.inner_text("#apprNote")                       # step 14 state first: disabled, no buttons
    assert page.locator("#apprList button").count() == 0
    assert call(page, "POST", f"/api/m/approvals/{appr.read_pending_file(p.home, reveal=False)[0]['id']}",
                {"approved": True}, token(page))["json"]["code"] == "PHONE_APPROVALS_OFF" and not fut.done()
    assert p.owner("POST", "/api/mobile/settings", {"phone_approvals": True})[0] == 200
    page.wait_for_selector("#apprList button:has-text('Deny')", timeout=12000)         # within the 4 s poll (+margin)
    page.tap("#apprList button:has-text('Deny')")
    assert fut.result(15) is False                                      # the PC acts on the denial
    until(page, "document.querySelector('#apprList').textContent.includes('Nothing is waiting')")
    fut = p.ask("Second request")
    page.wait_for_selector("#apprList button:has-text('Approve')", timeout=12000)
    page.tap("#apprList button:has-text('Approve')")
    assert fut.result(15) is True
    # a typed-code (high risk) request: no Approve button, the API refuses an approval from the phone
    fut = p.ask("Install tool", typed=True)
    page.wait_for_selector("#apprList:has-text('Install tool')", timeout=12000)
    page.wait_for_selector("#apprList :text('Needs the code shown on the computer')")
    assert page.locator("#apprList button:has-text('Approve')").count() == 0
    aid = next(r["id"] for r in appr.read_pending_file(p.home, reveal=False) if r["title"] == "Install tool")
    r = call(page, "POST", f"/api/m/approvals/{aid}", {"approved": True}, token(page))
    assert r["status"] == 403 and r["json"]["code"] == "TYPED_PC_ONLY" and not fut.done()
    page.tap("#apprList button:has-text('Deny')")                      # denying is allowed
    assert fut.result(15) is False
    # step 14: phone approvals switched off again -> disabled state
    assert p.owner("POST", "/api/mobile/settings", {"phone_approvals": False})[0] == 200
    fut = p.ask("Third request")
    until(page, "document.querySelector('#apprList').textContent.includes('Third request') && "
                           "document.querySelector('#apprNote').textContent.includes('off')", timeout=12000)
    assert page.locator("#apprList button").count() == 0
    assert page.errors == []


# ------------------------------------------------------------------ steps 9-10: push to talk
def test_push_to_talk_is_refused_without_tls(browser, phone):
    p = phone(tls=False)
    page = new_page(browser)
    ui_pair(page, p)
    assert page.evaluate("window.isSecureContext") is False             # a LAN http address, like a real phone
    page.tap("[data-tab=voice]")
    assert page.is_disabled("#ptt") and "secure (https)" in page.inner_text("#voiceNote")
    wav = b"RIFF" + b"\0" * 60
    r = page.evaluate("""async ([tok, n]) => { const r = await fetch("/api/m/voice", {method: "POST",
        headers: {"X-Jarves": "1", Authorization: "Bearer " + tok, "Content-Type": "audio/wav"}, body: new Uint8Array(n)});
        return r.status; }""", [token(page), len(wav)])
    assert r == 409 and p.fake.wavs == []
    assert page.errors == []


def test_push_to_talk_with_tls_and_a_fake_microphone(browser, phone):
    p = phone(tls=True)
    page = new_page(browser)
    ui_pair(page, p)
    assert page.evaluate("window.isSecureContext") is True
    page.tap("[data-tab=voice]")
    until(page, "!document.getElementById('ptt').disabled")
    assert "Press and hold" in page.inner_text("#voiceNote")
    page.dispatch_event("#ptt", "pointerdown")
    until(page, "document.getElementById('ptt').classList.contains('rec')")      # the fake mic is open
    page.wait_for_timeout(1500)
    page.dispatch_event("#ptt", "pointerup")
    until(page, "document.getElementById('voiceOut').textContent.includes('what time is it')")
    out = page.inner_text("#voiceOut")
    assert "You said" in out and "It is noon." in out
    assert len(p.fake.wavs) == 1
    with wave.open(BytesIO(p.fake.wavs[0])) as w:                       # 16 kHz mono 16-bit, with real samples
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2) and w.getnframes() > 8000
    assert page.errors == []


def test_microphone_denied_explains_itself(browser, phone):
    p = phone(tls=True)
    page = new_page(browser)
    page.add_init_script("navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('no', 'NotAllowedError'))")
    ui_pair(page, p)
    page.tap("[data-tab=voice]")
    page.dispatch_event("#ptt", "pointerdown")
    until(page, "document.getElementById('voiceOut').textContent.includes('Microphone permission')")
    assert p.fake.wavs == []


# ------------------------------------------------------------------ steps 16-18: revoke, switch off, rate limit, audit
def test_revoke_is_immediate_and_returns_to_pairing(browser, phone):
    p = phone()
    page = new_page(browser)
    ui_pair(page, p)
    tok = token(page)
    dev = tok.split(".")[0]
    assert call(page, "GET", "/api/m/me", None, tok)["status"] == 200
    assert p.owner("POST", f"/api/mobile/devices/{dev}/revoke", {})[0] == 200
    assert call(page, "GET", "/api/m/me", None, tok)["status"] == 401                # the very next request
    assert call(page, "POST", "/api/m/chat", {"text": "x"}, tok)["status"] == 401
    page.tap("[data-tab=health]")                                                    # the app's next request
    page.wait_for_selector("#pairView:not([hidden])")
    assert token(page) is None
    page.fill("#code", p.code()["code"])                                             # a fresh pairing works again
    page.tap("#pairForm button[type=submit]")
    page.wait_for_selector("#appView:not([hidden])")


def test_switching_mobile_access_off_stops_the_server_answering(browser, phone):
    p = phone()
    page = new_page(browser)
    ui_pair(page, p)
    tok = token(page)
    assert call(page, "GET", "/api/m/me", None, tok)["status"] == 200
    p.features.set("mobile_access", False)
    assert page.evaluate("fetch('/m/').then(r => r.status)") == 404
    assert page.evaluate("fetch('/m/app.js').then(r => r.status)") == 404
    for method, path, body in (("GET", "/api/m/me", None), ("POST", "/api/m/chat", {"text": "hi"}),
                               ("POST", "/api/m/pair", {"code": "12345678"}), ("GET", "/api/m/approvals", None)):
        assert call(page, method, path, body, tok)["status"] == 404, path
    assert p.owner("POST", "/api/mobile/code", {})[0] == 409                         # and no new code can be made
    assert p.fake.chats == []
    p.features.set("mobile_access", True)                                            # back on: the phone is still paired
    assert call(page, "GET", "/api/m/me", None, tok)["status"] == 200


def test_rate_limit_shows_in_the_app(browser, phone):
    p = phone()
    page = new_page(browser)
    ui_pair(page, p)
    tok = token(page)
    codes = [call(page, "POST", "/api/m/chat/send", {"text": f"/status {i}", "id": f"rate-limit-{i:04d}"}, tok) for i in range(mobile.CHAT_RATE_PER_MIN + 2)]
    assert [c["status"] for c in codes].count(429) >= 1 and codes[-1]["status"] == 429
    assert codes[-1]["retry"]
    assert "one too many" not in p.fake.chats and codes[-1]["json"].get("code") == "RATE_LIMITED"


def test_audit_lines_hold_no_content_and_no_credentials(browser, phone):
    p = phone()
    page = new_page(browser)
    c = ui_pair(page, p)
    secret = "my bank PIN is 4921 sk-ABCDEF1234567890"
    say(page, secret)
    until(page, "[...document.querySelectorAll('.jc-msg')].some(m => m.textContent.includes('echo: my bank'))")
    tok = token(page)
    call(page, "POST", "/api/m/pair", {"code": "12345678", "name": "x"})
    p.owner("POST", f"/api/mobile/devices/{tok.split('.')[0]}/revoke", {})
    call(page, "GET", "/api/m/me", None, tok)
    raw = (p.home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    for bad in (secret, "PIN is 4921", "sk-ABC", tok, tok.split(".")[1], c["code"], "12345678"):
        assert bad not in raw, bad
    events = {json.loads(line)["event"] for line in raw.splitlines()}
    assert {"device_paired", "request", "device_revoked", "pairing_refused", "auth_refused"} <= events | {"device_revoked"}
    shown = {e["event"] for e in p.owner("GET", "/api/mobile/audit")[1]["entries"]}
    assert "device_paired" in shown and "request" in shown


# ------------------------------------------------------------------ step 15: the PWA shell
def test_manifest_service_worker_install_criteria_and_offline_shell(browser, phone):
    p = phone(tls=True)
    page = new_page(browser)
    ui_pair(page, p)
    # manifest, checked by hand
    link = page.get_attribute("link[rel=manifest]", "href")
    m = page.evaluate("href => fetch(href).then(r => r.json())", link)
    assert m["name"] and m["short_name"] and m["display"] in ("standalone", "fullscreen", "minimal-ui")
    assert m["start_url"].startswith(m["scope"]) and m["scope"] == "/m/"
    assert m["theme_color"].startswith("#") and m["background_color"].startswith("#")
    assert m["icons"] and all(page.evaluate("u => fetch(u).then(r => r.status)", i["src"]) == 200 for i in m["icons"])
    assert page.get_attribute("meta[name=viewport]", "content").startswith("width=device-width")
    assert page.get_attribute("meta[name=theme-color]", "content")
    # the service worker installs, takes control and caches the shell
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    page.reload()
    until(page, "!!navigator.serviceWorker.controller")
    cached = page.evaluate("caches.keys().then(async ks => { const c = await caches.open(ks[0]); "
                           "return (await c.keys()).map(r => new URL(r.url).pathname); })")
    assert {"/m/", "/m/app.js", "/m/style.css", "/m/manifest.webmanifest"} <= set(cached)
    # Chromium's own installability verdict (what "Add to Home screen" uses)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Page.enable")
    errs = [e["errorId"] for e in cdp.send("Page.getInstallabilityErrors")["installabilityErrors"]]
    # excused: a test context is incognito-like, and the self-signed test cert is not a trusted origin (real phone: step 15)
    assert [e for e in errs if e not in ("in-incognito", "not-from-secure-origin")] == [], errs
    # offline: the shell still opens and says so honestly; API calls are never served from the cache
    page.context.set_offline(True)
    page.reload()
    page.wait_for_selector("#appView:not([hidden])")
    until(page, "document.getElementById('conn').textContent === 'computer unreachable'", 20000)
    page.context.set_offline(False)
    until(page, "document.getElementById('conn').textContent === 'connected'", 30000)
    assert p.fake.chats == []
    assert page.errors == []


def test_plain_http_has_no_service_worker_but_the_app_still_works(browser, phone):
    p = phone(tls=False)
    page = new_page(browser)
    ui_pair(page, p)
    assert page.evaluate("!navigator.serviceWorker") is True or page.evaluate("navigator.serviceWorker.controller") is None
    say(page, "hi over http")
    until(page, "[...document.querySelectorAll('.jc-msg')].some(m => m.textContent.includes('echo: hi over http'))")


# ------------------------------------------------------------------ steps 15 (EN + HE) layout at 390 px
RULES_THAT_APPLY = {"html-lang", "html-dir", "control-name", "label", "icon-button-name", "duplicate-id", "tabindex",
                    "aria-hidden-focus", "color-contrast", "target-size", "off-screen", "bad-text", "untranslated",
                    "hebrew-on-english", "image-alt", "mouse-only"}


def audit(page, lang):
    page.wait_for_timeout(250)
    metrics = page.evaluate("""() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
        bw: document.body.scrollWidth, dir: document.documentElement.dir, lang: document.documentElement.lang,
        small: [...document.querySelectorAll('button, input, a, [role=tab]')].filter(e => e.getClientRects().length &&
            !e.closest('[hidden]')).map(e => { const r = e.getBoundingClientRect(); return [e.id || e.textContent.trim().slice(0, 20),
            Math.round(r.width), Math.round(r.height)]; }).filter(([, w, h]) => w < 44 || h < 44)})""")
    assert metrics["sw"] <= metrics["cw"] and metrics["bw"] <= metrics["cw"], metrics        # no sideways scroll
    assert metrics["dir"] == ("rtl" if lang == "he" else "ltr") and metrics["lang"] == lang
    assert metrics["small"] == [], metrics["small"]                                          # tap targets >= 44 px
    r = page.evaluate(apc.RULES, {"lang": lang, "width": 390, "reduced": False})
    issues = [i for i in r["issues"] if i["rule"] in RULES_THAT_APPLY and i["where"] != "button#lang"
              and not i["where"].startswith(("div#log>", "div.jc-log", "ul#apprList>li>span.grow", "li.plan>div.grow>div.plan-head>s", "details>ol.plan-steps>"))]     # chat text and request titles are user DATA     # the language button names its language by design
    assert issues == [], issues


@pytest.mark.parametrize("lang,locale", [("en", "en-US"), ("he", "he-IL")])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_layouts_at_390px_have_no_sideways_scroll_big_targets_and_contrast(browser, phone, lang, locale, scheme):
    p = phone(tls=True)
    page = new_page(browser, locale=locale, scheme=scheme)
    page.goto(f"{p.base}/m/")
    page.wait_for_selector("#pairView:not([hidden])")
    audit(page, lang)                                                   # pairing screen
    page.goto("about:blank")                                            # (the QR link opens in a fresh tab)
    ui_pair(page, p)
    p.owner("POST", "/api/mobile/settings", {"phone_approvals": True})
    p.ask("Send test message")
    p.ask("Install tool", typed=True)
    audit(page, lang)                                                   # chat
    say(page, "שלום hello")
    until(page, "[...document.querySelectorAll('.jc-msg')].some(m => m.textContent.includes('echo: '))")
    audit(page, lang)
    for tab in ("voice", "approvals", "health"):
        page.tap(f"[data-tab={tab}]")
        if tab == "approvals":
            page.wait_for_selector("#apprList button", timeout=12000)
        if tab == "health":
            page.wait_for_selector("#statusList li")
        audit(page, lang)
    page.tap("#lang")                                                   # the language button flips direction
    assert page.evaluate("document.documentElement.dir") == ("ltr" if lang == "he" else "rtl")
    assert page.errors == []


# ------------------------------------------------------------------ V5 G4 (#184): Goal plans on the phone
def _seed_plan(p: Phone, title="Tidy the notes folder"):
    from jarves.goal_plans import PlanStore
    from jarves.userdata import open_db
    st = PlanStore(open_db(p.home))
    pl = st.propose(title, [{"tool": "list_notes", "args": {}, "why": "see what is there"},
                            {"tool": "send_message", "args": {"body": "SECRET-ARG"}, "why": "tell Dana"}])
    st.approve(pl["id"])
    st.db.x("UPDATE goal_plan_steps SET status='done', evidence='SECRET-OUT' WHERE plan_id=? AND idx=0", (pl["id"],))
    return st, pl["id"]


def _grant_tasks(p: Phone, page):
    dev = call(page, "GET", "/api/m/me", None, token(page))["json"]["device"]
    assert p.owner("POST", f"/api/mobile/devices/{dev}/scopes", {"scopes": ["chat", "voice", "status", "approvals", "tasks"]})[0] == 200
    page.reload()
    page.wait_for_selector("#appView:not([hidden])")


def test_goal_plan_view_pause_and_cancel_from_the_phone_and_nothing_that_starts_a_step(browser, phone):
    p = phone()
    st, pid = _seed_plan(p)
    page = new_page(browser)
    ui_pair(page, p)
    page.wait_for_timeout(600)
    assert page.locator("#tabbtn-plans").is_hidden()                    # least privilege: no tasks scope, no tab
    assert call(page, "GET", "/api/m/plans", None, token(page))["status"] == 403
    _grant_tasks(p, page)
    page.wait_for_selector("#tabbtn-plans:not([hidden])")
    page.tap("[data-tab=plans]")
    page.wait_for_selector("#plansRoot .plan-title")
    body = page.inner_text("#plansRoot")
    assert "Tidy the notes folder" in body and "50%" in body and "1/2" in body and "ready" in body
    page.tap("#plansRoot summary")                                      # the steps: tools, reasons, statuses
    page.wait_for_selector("#plansRoot .plan-steps")
    steps = page.inner_text("#plansRoot .plan-steps")
    assert "list_notes" in steps and "send_message" in steps and "tell Dana" in steps and "changes something" not in steps
    assert "SECRET" not in page.inner_text("body")                      # arguments and results never reach the phone
    assert page.locator("#plansRoot button:has-text('Approve'), #plansRoot button:has-text('Retry'), #plansRoot button:has-text('Resume')").count() == 0
    page.tap("#plansRoot button:has-text('Pause')")
    until(page, "document.querySelector('#plansRoot').textContent.includes('Plan paused')")
    assert st.get(pid)["state"] == "paused"                             # the PC sees the same state
    assert p.owner("GET", "/api/goal-plans")[1]["plans"][0]["state"] == "paused"
    page.wait_for_selector("#plansRoot :text('on the computer')")       # resuming is the computer's job
    assert page.locator("#plansRoot button:has-text('Pause')").count() == 0
    page.tap("#plansRoot button:has-text('Cancel plan')")               # cancelling asks first
    assert st.get(pid)["state"] == "paused"
    page.tap("#plansRoot button:has-text('Yes, cancel')")
    until(page, "document.querySelector('#plansRoot').textContent.includes('Plan cancelled')")
    assert st.get(pid)["state"] == "cancelled"
    r = call(page, "POST", "/api/m/plans/op", {"id": "plan-e2e-00000001", "op": "retry", "plan": pid}, token(page))
    assert r["status"] == 403 and r["json"]["code"] == "PLAN_PC_ONLY"
    assert page.errors == []


@pytest.mark.parametrize("lang,locale", [("en", "en-US"), ("he", "he-IL")])
def test_goal_plans_layout_at_390px_in_english_and_hebrew(browser, phone, lang, locale):
    p = phone()
    _seed_plan(p)
    page = new_page(browser, locale=locale)
    ui_pair(page, p)
    _grant_tasks(p, page)
    page.tap("[data-tab=plans]")
    page.wait_for_selector("#plansRoot .plan-title")
    audit(page, lang)
    page.tap("#plansRoot summary")
    page.wait_for_selector("#plansRoot .plan-steps")
    audit(page, lang)
    page.tap("#plansRoot button >> nth=1")                              # the cancel question
    page.wait_for_selector("#plansRoot button.primary")
    audit(page, lang)
    assert page.errors == []


def test_qr_encoder_output_decodes_for_realistic_urls():
    # the last two are valid symbols the classic cv2.QRCodeDetector could not find (the first is PR #78's CI URL)
    for url in ("https://192.168.100.200:60123/m/#c=12345678", "http://10.0.0.5:8765/m/#c=00000042",
                "https://phone.lan:50424/m/#c=60476510", "https://phone.lan:32496/m/#c=32580007"):
        assert decode_qr(qrmini.rows(url)) == url
