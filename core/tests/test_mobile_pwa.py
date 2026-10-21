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

    core = SimpleNamespace(
        brain=Brain(), stt=STT(), curator=SimpleNamespace(observe=lambda *a, **k: seen["observed"].append(a[0])),
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
