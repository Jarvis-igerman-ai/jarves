"""V5 phone app files (PWA), the QR encoder and the Core adapter. No browser: the files are served over real HTTP and
checked structurally (CSP-safe, EN+HE catalogs match, every referenced element exists)."""
import asyncio
import http.client
import io
import json
import re
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from jarves import dashboard, mobile, qrmini
from jarves.config import Config

HOST = "192.168.50.7"
MOBILE_DIR = Path(mobile.__file__).parent / "dashboard_static" / "mobile"


def get(d, path, host=None):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    c.request("GET", path, headers={"Host": host or f"{HOST}:{d.port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


@pytest.fixture
def srv(home):
    from conftest import free_port
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST).start()
    yield d
    d.stop()


def test_pwa_files_are_served_without_a_login(srv):
    for name, ctype in mobile.STATIC_FILES.items():
        r, body = get(srv, "/m/" + name)
        assert r.status == 200 and r.getheader("Content-Type") == ctype and body, name
        assert "default-src 'self'" in r.getheader("Content-Security-Policy")
        assert r.getheader("Cache-Control") == "no-store"
    r, _ = get(srv, "/m")
    assert r.status == 302 and r.getheader("Location") == "/m/"
    assert get(srv, "/m/")[1] == (MOBILE_DIR / "index.html").read_bytes()
    assert get(srv, "/m/sw.js")[0].getheader("Service-Worker-Allowed") == "/m/"


def test_only_the_whitelisted_files_are_served(srv):
    for bad in ("/m/../dashboard.py", "/m/%2e%2e/app.js", "/m/secret.txt", "/m/index.html/x", "/m/../../mobile.py"):
        assert get(srv, bad)[0].status in (404, 403), bad


def raw_get(d, target: bytes) -> int:
    """Send a raw request line (http.client refuses NUL/backslash/odd targets) and return the status code."""
    import socket
    with socket.create_connection(("127.0.0.1", d.port), timeout=10) as s:
        s.sendall(b"GET " + target + b" HTTP/1.1\r\nHost: " + f"{HOST}:{d.port}".encode() + b"\r\nConnection: close\r\n\r\n")
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    first = data.split(b"\r\n", 1)[0].split()
    return int(first[1]) if len(first) > 1 else 0


TRAVERSAL = [b"/m/../dashboard.py", b"/m/..%2fdashboard.py", b"/m/%2e%2e/mobile.py", b"/m/%2e%2e%2fmobile.py",
             b"/m/%252e%252e%252fmobile.py", b"/m/..%5cmobile.py", b"/m/..\\mobile.py", b"/m/%5c..%5cmobile.py",
             b"/m//etc/passwd", b"/m/C:/Windows/win.ini", b"/m/C:%5cWindows%5cwin.ini", b"/m/%00index.html",
             b"/m/index.html%00", b"/m/index.html%00.js", b"/m/./index.html", b"/m/mobile/index.html",
             b"/m/../mobile/index.html", b"/m/INDEX.HTML", b"/m/index.html/", b"/m/app.js?x=../../a",
             b"/m/unknown.js", b"/m/.git/config", b"/m/~/.ssh/id_rsa", b"/m/" + b"a" * 4000]


def test_traversal_and_unknown_names_never_reach_the_filesystem(srv, monkeypatch):
    """CodeQL #6/#7: whatever the phone sends after /m/, only a fixed STATIC_FILES key is ever opened; every encoded,
    absolute, backslash, NUL, case-variant or unknown name is a 404 and never touches the disk."""
    touched, rec = [], [True]
    for meth in ("is_file", "read_bytes", "exists", "stat", "resolve", "open"):
        real = getattr(Path, meth)
        monkeypatch.setattr(Path, meth, lambda self, *a, _r=real, **k: (rec[0] and touched.append(str(self)),
                                                                          _r(self, *a, **k))[1])
    try:
        for target in TRAVERSAL:
            st = raw_get(srv, target)
            if target == b"/m/app.js?x=../../a":
                assert st == 200, target                     # the query string is not part of the file name
            else:
                assert st in (400, 404), (target, st)
    finally:
        rec[0] = False
    bad = ("..", "passwd", "win.ini", "unknown.js", ".git", "INDEX.HTML", ".ssh", "\x00", "%", "aaaa", "mobile.py",
           "dashboard.py")
    assert not [p for p in touched if any(b in p for b in bad)], touched   # no attacker-chosen name reaches the disk


def test_static_file_returns_a_path_built_from_the_whitelist_key_only():
    for name in mobile.STATIC_FILES:
        f = mobile._static_file(name)
        assert f is not None and f.name == name and f.is_file()
    for bad in ("../mobile.py", "..\\mobile.py", "/etc/passwd", "C:\\Windows\\win.ini", "index.html\x00", "",
                "INDEX.HTML", "mobile/index.html", "%2e%2e/mobile.py", "index.html/"):
        assert mobile._static_file(bad) is None, bad


def test_a_symlinked_static_file_pointing_outside_is_refused(tmp_path, monkeypatch):
    """Containment: even a whitelisted name is refused when the file on disk resolves outside the static folders."""
    import os
    static = tmp_path / "dashboard_static" / "mobile"
    static.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("FAKE-SECRET-not-a-real-credential", encoding="utf-8")
    links = (static / "index.html", tmp_path / "dashboard_static" / "chatview.js")
    try:
        for link in links:
            os.symlink(outside, link)
    except (OSError, NotImplementedError):
        # Windows without symlink rights: simulate the link by making resolve() of those paths land outside
        for link in links:
            link.write_text("placeholder", encoding="utf-8")
        real_resolve = Path.resolve
        monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: (real_resolve(outside) if self.name in
                                                                      ("index.html", "chatview.js") else
                                                                      real_resolve(self, *a, **k)))
    (static / "app.js").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(mobile, "STATIC_DIR", static)
    assert mobile._static_file("index.html") is None and mobile._static_file("chatview.js") is None
    assert mobile._static_file("app.js") == (static / "app.js").resolve()


def test_index_has_no_inline_script_or_style():
    html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "style=" not in html and "onclick=" not in html
    assert 'rel="manifest"' in html and "viewport" in html
    man = json.loads((MOBILE_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert man["scope"] == "/m/" and man["start_url"] == "/m/" and man["display"] == "standalone"


def catalog(src, lang):
    body = re.search(rf"\n    {lang}: \{{(.*?)(?=\n    (?:he|en): \{{|\n  \}};)", src, re.S).group(1)
    return set(re.findall(r"\b([a-z_]+):\s*\"", body))


def test_english_and_hebrew_catalogs_match_and_cover_the_page():
    js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
    en, he = catalog(js, "en"), catalog(js, "he")
    assert en == he and len(en) > 30
    html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'data-(?:t|ph)="([a-z_]+)"', html)) | set(re.findall(r'\bt\("([a-z_]+)"\)', js))
    assert used <= en, used - en
    assert re.search(r"[֐-׿]", js) and 'dir = lang === "he" ? "rtl"' in js
    for el_id in set(re.findall(r"\$\(\"([A-Za-z]+)\"\)", js)):
        assert f'id="{el_id}"' in html, el_id


def test_the_app_never_uses_innerhtml_or_puts_the_token_in_a_url():
    js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "eval(" not in js and "document.write" not in js
    assert js.count("Authorization") == 1 and "?token" not in js and "token=" not in js
    sw = (MOBILE_DIR / "sw.js").read_text(encoding="utf-8")
    assert "fetch(e.request)" in sw and "/api/m" not in sw           # the worker never caches API answers


def test_dashboard_phone_page_is_wired():
    idx = (Path(mobile.__file__).parent / "dashboard_static" / "index.html").read_text(encoding="utf-8")
    assert 'href="#/phone"' in idx and "mobile_admin.js" in idx
    js = (Path(mobile.__file__).parent / "dashboard_static" / "mobile_admin.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "PAGES" in js


# ---------------------------------------------------------------- QR
def test_qr_structure():
    m = qrmini.matrix("http://192.168.50.7:8766/m/#c=12345678")
    n = len(m)
    assert n == 29 and all(len(r) == n for r in m)                       # version 3
    for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):                      # three finder patterns
        for dy in range(7):
            for dx in range(7):
                edge = max(abs(dx - 3), abs(dy - 3))
                assert m[oy + dy][ox + dx] == (edge != 2), (ox, oy, dx, dy)
    assert all(m[6][i] == (i % 2 == 0) for i in range(8, n - 8))         # timing pattern
    assert m[n - 8][8] is True                                           # dark module
    assert m == qrmini.matrix("http://192.168.50.7:8766/m/#c=12345678")  # deterministic
    # format information: 15 bits, BCH-valid, read from the first copy
    bits = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
    val = sum(int(b) << i for i, b in enumerate(bits)) ^ 0x5412
    data = val >> 10
    r = data
    for _ in range(10):
        r = (r << 1) ^ ((r >> 9) * 0x537)
    assert (data << 10 | r) == val and data >> 3 == 1                    # ECC level L


def test_qr_sizes_and_limits():
    assert len(qrmini.matrix("a")) == 21
    assert len(qrmini.matrix("x" * qrmini.MAX_BYTES)) == 41
    with pytest.raises(ValueError):
        qrmini.matrix("x" * (qrmini.MAX_BYTES + 1))
    rows = qrmini.rows("https://pc.local:8766/m/#c=87654321")
    assert set("".join(rows)) <= {"0", "1"} and len(rows) == len(rows[0])


def test_qr_reed_solomon_known_vector():
    # the classic "HELLO WORLD" 1-M example codewords and its 10 error-correction codewords
    ec = qrmini._rs([32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17], 10)
    assert ec == [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]


# ---------------------------------------------------------------- Core adapter
def wav_bytes(rate=8000, seconds=1.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.sin(np.linspace(0, 200, int(rate * seconds))) * 8000).astype("<i2").tobytes())
    return buf.getvalue()


def test_core_bridge_uses_the_brain_and_stt_but_not_the_speaker():
    seen = {"respond": [], "stt": [], "observed": []}

    class Brain:
        async def respond(self, text, *, spoken=True):
            seen["respond"].append((text, spoken))
            return SimpleNamespace(text="answer to " + text, ok=True)

    class STT:
        async def transcribe(self, audio):
            seen["stt"].append((audio.dtype, len(audio)))
            return " hello " if len(audio) else ""

    async def respond_remote(text):
        reply = await Brain().respond(text, spoken=False)
        seen["observed"] += ["user", "assistant"]
        return reply

    core = SimpleNamespace(
        stt=STT(), respond_remote=respond_remote,
        health=SimpleNamespace(core="running", components_view=lambda: {"audio": {"status": "ok", "detail": "/home/x/secret"}}))
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        b = mobile.core_bridge(core, loop)
        assert b.chat("hi", "dev1") == {"reply": "answer to hi", "ok": True}
        assert seen["respond"] == [("hi", False)] and seen["observed"] == ["user", "assistant"]
        out = b.voice(wav_bytes(8000, 1.0), "dev1")
        assert out == {"transcript": "hello", "reply": "answer to hello", "ok": True}
        assert seen["stt"][0] == (np.dtype("float32"), 16000)            # resampled to the 16 kHz the STT expects
        assert b.status() == {"core": "running", "components": {"audio": "ok"}}   # no detail text leaves the PC
        assert b.voice_available() is True
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(10)
        loop.close()


def test_phone_turn_is_logged_as_a_typed_exchange_in_the_desktop_history(monkeypatch):
    """V5-G: Core.respond_remote logs the same REPLY record a Windows typed turn does, so the History store puts the
    phone exchange into the shared (typed) conversation; the spoken-approval path is never used."""
    from jarves import app as core_app
    from jarves.store import Store
    records, observed = [], []

    class Brain:
        async def respond(self, text, *, spoken=True):
            assert spoken is False
            return SimpleNamespace(text="reply to " + text, ok=True, source="ai", tool=None, code=None)

    fake_self = SimpleNamespace(brain=Brain(), live=None,
                                curator=SimpleNamespace(observe=lambda *a, **k: observed.append(a[0])),
                                answer_words=lambda *a, **k: pytest.fail("phone text must not answer approvals"))
    monkeypatch.setattr(core_app.logs, "new_cid", lambda *a, **k: None)
    monkeypatch.setattr(core_app.logs, "info", lambda comp, code, msg, **f: records.append({"code": code, "ts": 1000.0, **f}))
    reply = asyncio.run(core_app.Core.respond_remote(fake_self, "from the phone"))
    assert reply.text == "reply to from the phone" and observed == ["user", "assistant"]
    st = Store(":memory:")
    for r in records:
        st.ingest(r)
    sid = st.sessions()[0]["id"]
    turns = [(t["role"], t["text"]) for t in st.rows("SELECT role, text FROM turns WHERE session_id = ? ORDER BY id", (sid,))]
    assert ("user", "from the phone") in turns and ("assistant", "reply to from the phone") in turns


def test_busy_message_exists_in_both_phone_languages():
    js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
    assert js.count("busy:") == 2


async def _noop():
    return None


def test_core_starts_the_phone_server_at_boot_only_when_the_switch_is_on(tmp_path, monkeypatch):
    """V5-I: Core.start_mobile -> ensure_dashboard only when `mobile_access` is ON (default OFF = nothing starts)."""
    from jarves import app as core_app
    calls = []

    async def ensure():
        calls.append(1)
        fake_self._dashboard = SimpleNamespace(port=1234)
    for on, expect in ((False, 0), (True, 1)):
        calls.clear()
        fake_self = SimpleNamespace(features=SimpleNamespace(enabled=lambda name, _on=on: _on and name == "mobile_access"),
                                    ensure_dashboard=ensure, _dashboard=None)
        asyncio.run(core_app.Core.start_mobile(fake_self))
        assert len(calls) == expect
    # a failing start never blocks the Core
    async def boom():
        raise OSError("no network")
    fake_self = SimpleNamespace(features=SimpleNamespace(enabled=lambda name: True), ensure_dashboard=boom, _dashboard=None)
    asyncio.run(core_app.Core.start_mobile(fake_self))


@pytest.mark.parametrize("exc,status", [(FileNotFoundError, 404), (PermissionError, 500), (OSError, 500)])
def test_a_read_error_gives_a_clean_error_without_a_path(srv, monkeypatch, exc, status):
    """The file can vanish or be unreadable between the check and the read: no traceback, no server path in the reply."""
    secret_path = "C:/very/private/install/dir/index.html"

    def boom(self, *a, **k):
        raise exc(5 if exc is OSError else 2, "boom", secret_path)
    monkeypatch.setattr(Path, "read_bytes", boom)
    r, body = get(srv, "/m/index.html")
    assert r.status == status
    assert b"private" not in body and b"install" not in body and b"index.html" not in body and b"boom" not in body


def test_manifest_meets_the_install_criteria():
    """#510: Chrome needs PNG icons at 192 and 512 (plus a maskable one), standalone, a start_url inside the scope."""
    import json
    import re
    import struct
    m = json.loads((MOBILE_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert m["display"] == "standalone" and m["start_url"].startswith(m["scope"]) and m["scope"] == "/m/"
    seen = {(i["sizes"], i["purpose"]) for i in m["icons"] if i["type"] == "image/png"}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= seen
    for i in m["icons"]:
        if i["type"] == "image/png":
            name = i["src"].rsplit("/", 1)[1]
            assert name in mobile.STATIC_FILES
            data = (MOBILE_DIR / name).read_bytes()
            w, h = struct.unpack(">II", data[16:24])
            assert data[:8] == b"\x89PNG\r\n\x1a\n" and f"{w}x{h}" == i["sizes"]
    sw = (MOBILE_DIR / "sw.js").read_text(encoding="utf-8")
    assert "addEventListener(\"fetch\"" in sw and "/m/icon-192.png" in sw
    app = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
    assert "serviceWorker.register" in app and "beforeinstallprompt" in app
    head, he_part = app.split("\n    he: {", 1)
    for lang in (head.split("    en: {", 1)[1], he_part.split("\n  };", 1)[0]):      # a repeated key silently replaced the first text
        keys = re.findall(r"(?:^|[\s,{])([a-z_]+): \"", lang)
        assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1]
