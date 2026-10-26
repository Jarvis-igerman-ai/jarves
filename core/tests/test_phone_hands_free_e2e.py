"""Hands-free Drive voice in a REAL headless Chromium with a fake microphone (V6 #219, client side): start, a spoken turn,
a lost audio route, a hidden page, a refused wake lock. Fake speech-to-text; no real phone, no car, no network."""
import subprocess
from pathlib import Path

import pytest
from test_mobile_phone_e2e import _drive_setup, _grant, browser, new_page, phone, ui_pair, until  # noqa: F401

from jarves import mobile, phone_hands_free as hf

JS = Path(mobile.__file__).parent / "dashboard_static" / "mobile" / "hands_free.js"


def test_the_script_parses_and_uses_no_unsafe_dom():
    src = JS.read_text(encoding="utf-8")
    assert "innerHTML" not in src and "eval(" not in src and "setAttribute(\"style\"" not in src
    assert "background" in src.lower()                                   # the honest scope is stated in the file itself
    try:
        subprocess.run(["node", "--check", str(JS)], check=True, capture_output=True, timeout=30)
    except FileNotFoundError:
        pytest.skip("node is not installed")


def _gone(page):
    """The page closes everything first and tells the computer afterwards: wait for that last call."""
    for _ in range(50):
        if hf._sessions == {}:
            return
        page.wait_for_timeout(100)
    assert hf._sessions == {}


def _setup(p, said):
    _drive_setup(p)
    hf.clear()
    p.features.set_beta(True, by="cli")
    p.features.set("phone_hands_free", True, by="cli")
    p.d.mobile.bridge.voice_transcribe = lambda wav, dev: said.pop(0) if said else ""


def _open(browser, p):
    page = new_page(browser)
    ui_pair(page, p)
    _grant(p, page, "drive")
    page.tap("[data-tab=drive]")
    page.wait_for_selector("#hfBtn")
    return page


def test_start_hear_speak_then_a_lost_car_route_ends_it_truthfully(browser, phone):
    p = phone(tls=True)
    said = ["what time is it"] * 50
    _setup(p, said)
    page = _open(browser, p)
    assert "never listens in the background" in page.inner_text("#hfRoot")
    page.evaluate("() => { window.JarvesHandsFree.cfg.silenceMs = 300; window.JarvesHandsFree.cfg.threshold = 0.004; }")        # the fake mic beeps once a second (a loud chunk, then a decaying tail of 0.03 .. 0.001): shorten the quiet window, count the tail so the beep is two loud chunks on any runner
    page.tap("#hfBtn")
    until(page, "document.querySelector('.hf-state') && document.querySelector('.hf-state').dataset.state !== 'idle'")
    until(page, "!!document.querySelector('#hfRoot').textContent.includes('echo: what time is it')", timeout=30000)        # the fake beep was heard, one turn ran
    assert p.fake.chats and p.fake.chats[0] == "what time is it"
    page.evaluate("""() => { navigator.mediaDevices.enumerateDevices = async () => [];
                              navigator.mediaDevices.dispatchEvent(new Event('devicechange')); }""")       # Bluetooth car audio drops
    until(page, "document.querySelector('#hfRoot').textContent.includes('car audio disconnected')")
    assert "microphone is closed" in page.inner_text("#hfRoot") and page.locator("#hfBtn").inner_text() == "Start Drive voice"
    _gone(page)
    assert page.errors == []


def test_hidden_page_and_a_refused_wake_lock(browser, phone):
    p = phone(tls=True)
    _setup(p, [])
    page = _open(browser, p)
    page.evaluate("() => { navigator.wakeLock = { request: () => Promise.reject(new Error('denied')) }; }")
    page.tap("#hfBtn")
    until(page, "document.querySelector('#hfRoot').textContent.includes('did not allow keeping the screen on')")
    assert "listening" in page.inner_text(".hf-state").lower()
    page.evaluate("() => { Object.defineProperty(document, 'hidden', { value: true, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); }")
    until(page, "document.querySelector('#hfRoot').textContent.includes('left the screen')")
    _gone(page)
    assert page.errors == []


def test_leaving_the_drive_tab_ends_the_session(browser, phone):
    p = phone(tls=True)
    _setup(p, [])
    page = _open(browser, p)
    page.tap("#hfBtn")
    until(page, "document.querySelector('.hf-state') && document.querySelector('.hf-state').dataset.state === 'listening'")
    assert len(hf._sessions) == 1
    page.tap("[data-tab=home]")
    until(page, "true")
    _gone(page)



@pytest.mark.parametrize("when", ["start_call", "mic_prompt"])
def test_leaving_while_starting_never_keeps_the_mic(browser, phone, when):
    """The session ends while start() still awaits the computer or the microphone permission prompt (the owner switches
    tab): no microphone stays open afterwards and no wake lock is taken."""
    p = phone(tls=True)
    _setup(p, [])
    page = _open(browser, p)
    page.evaluate("""() => { const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = (c) => { window.__gum = true;
            return new Promise((ok) => setTimeout(() => real(c).then((st) => { window.__hfStream = st; ok(st); }), 1500)); };
        window.__locks = 0; Object.defineProperty(navigator, 'wakeLock', { configurable: true,
            value: { request: async () => { window.__locks++; return { release: async () => {}, onrelease: null }; } } }); }""")
    if when == "start_call":
        page.evaluate("() => { document.getElementById('hfBtn').click(); document.querySelector('[data-tab=home]').click(); }")
    else:
        page.tap("#hfBtn")
        until(page, "window.__gum === true")
        page.tap("[data-tab=home]")
    page.wait_for_timeout(2500)
    assert page.evaluate("() => !window.__hfStream || window.__hfStream.getTracks().every((x) => x.readyState === 'ended')")
    assert page.evaluate("() => window.__locks") == 0
    _gone(page)
    assert page.errors == []
