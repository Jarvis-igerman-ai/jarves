"""V7 B4 (#399): the phone app's Browser tab in a REAL headless browser (Edge on Windows), against the real phone server.

The pilot is a FAKE page driver (no Playwright page, no network): a LiveView over a PilotSession whose driver returns a real
PNG and records the owner's input. Checks: the tab is hidden without the switch or the `browser` scope; Watch shows the
picture; Take over -> type + key + tap reach the driver; the grant and the typed text never appear in the page; give back
pauses the assistant and continuing needs a confirmed second step; Stop closes the session; the tab fits 390 and 360 px in
English (LTR, light) and Hebrew (RTL, dark) with 44 px targets. Set JARVES_B4_SHOTS=<folder> to save screenshots."""
from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

import pytest
from test_mobile_phone_e2e import PHONE_FIT, _grant, browser, call, go, new_page, phone, token, ui_pair, until  # noqa: F401

from jarves import browser_liveview as lv
from jarves import browser_pilot as bp
from jarves import mobile_browser  # noqa: F401  (registers the browser_phone_view switch)

LOCAL = "http://localhost:8123/login"
SECRET = "hunter2-Correct-Horse-9"
W, H = 640, 400
SWITCHES = ("browser", "browser_pilot", "browser_liveview", "browser_takeover", "browser_phone_view")


def _png(w=W, h=H) -> bytes:
    """A real PNG (the <img> must decode it: a tap is scaled to its natural size): white page, a blue 'button' in the middle."""
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for x in range(w):
            inside = w // 3 <= x < 2 * w // 3 and h // 3 <= y < 2 * h // 3
            rows += b"\x2f\x5b\xea" if inside else b"\xff\xff\xff"

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


PNG = _png()


class Drv:
    def __init__(self):
        self.url, self.closed, self.inputs = LOCAL, False, []

    @property
    def current_url(self):
        return self.url

    def pin_hosts(self, m):
        pass

    def open(self, url):
        self.url = url

    def text(self):
        return "Login page"

    def screenshot(self):
        return PNG

    def human_input(self, kind, data):
        self.inputs.append((kind, dict(data)))

    def close(self):
        self.closed = True


class Pilot:
    """The one active live view the phone server uses (browser_liveview.ACTIVE), over a fake session; records each grant."""

    def __init__(self, home: Path, features):
        self.drv = Drv()
        on = lambda: features.active(lv.FEATURE)                                     # noqa: E731
        self.s = bp.PilotSession(home, self.drv, enabled=lambda: True, resolver=lambda h: ["93.184.216.34"])
        self.v = lv.LiveView(self.s, enabled=on, takeover_enabled=lambda: features.active(lv.TAKEOVER))
        self.grants, self.frames = [], 0
        start, frame = self.v.start, self.v.frame

        def _start():
            out = start()
            self.grants.append(out["grant"])
            return out

        def _frame(g):
            self.frames += 1
            return frame(g)
        self.v.start, self.v.frame = _start, _frame


def _switches(p, on=True):
    p.features.set_beta(True)
    for fid in SWITCHES:
        p.features.set(fid, on)


@pytest.fixture
def pilot(monkeypatch):
    made = []

    def make(p) -> Pilot:
        pl = Pilot(p.home, p.features)
        monkeypatch.setattr(lv, "ACTIVE", pl.v)
        made.append(pl)
        return pl
    yield make
    monkeypatch.setattr(lv, "ACTIVE", None)


def _shot(page, name):
    d = os.environ.get("JARVES_B4_SHOTS")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"{name}.png"), full_page=True)


def _fits(page, lang):
    m = page.evaluate(PHONE_FIT)
    assert m["sw"] <= m["cw"] and m["bw"] <= m["cw"], m                # no sideways scroll
    assert m["outside"] == [], m["outside"]                             # nothing sticks out of the screen
    assert m["small"] == [], m["small"]                                 # every control >= 44 px
    assert m["dir"] == ("rtl" if lang == "he" else "ltr") and m["lang"] == lang
    return m


def _open_tab(page, p):
    _grant(p, page, "browser")
    page.wait_for_selector("#tabbtn-browser:not([hidden])", state="attached")
    go(page, "browser")
    page.wait_for_selector("#browserRoot .bv-card")


def _state(page):
    return page.inner_text("#browserRoot .bv-state")


# ------------------------------------------------------------------ least privilege: the tab is hidden until both say yes
def test_browser_tab_is_hidden_without_the_switch_or_the_scope(browser, phone, pilot):
    p = phone(tls=True)
    pilot(p)
    page = new_page(browser)
    ui_pair(page, p)
    page.wait_for_timeout(600)
    assert page.get_attribute("#tabbtn-browser", "hidden") is not None        # no scope: no tab, and the server refuses
    assert call(page, "GET", "/api/m/browser", None, token(page))["status"] == 403
    _grant(p, page, "browser")                                                # the scope, but the phone view switch is off
    page.wait_for_timeout(600)
    assert page.get_attribute("#tabbtn-browser", "hidden") is not None
    assert call(page, "GET", "/api/m/browser", None, token(page))["json"]["enabled"] is False
    assert call(page, "POST", "/api/m/browser/start", {}, token(page))["json"]["code"] == "PHONE_BROWSER_OFF"
    _switches(p)
    page.reload()
    page.wait_for_selector("#tabbtn-browser:not([hidden])", state="attached")
    _switches(p, False)                                                       # switched off again: the tab goes away by itself
    until(page, "document.getElementById('tabbtn-browser').hidden", 15000)
    assert page.errors == []


# ------------------------------------------------------------------ the whole flow, and what must never reach the page
def test_watch_take_over_type_key_tap_give_back_continue_and_stop(browser, phone, pilot):
    p = phone(tls=True)
    _switches(p)
    page = new_page(browser)
    ui_pair(page, p)
    _open_tab(page, p)
    # no pilot on the computer yet: the phone says where to start it and starts nothing itself
    assert "Start the browser pilot on the computer" in page.inner_text("#browserRoot")
    page.tap("#browserRoot button:has-text('Watch')")
    until(page, "document.querySelector('#browserRoot .bv-msg').textContent.includes('on the computer')")
    pl = pilot(p)
    page.tap("#browserRoot button:has-text('Watch')")
    until(page, "(() => { const i = document.querySelector('#browserRoot .bv-img'); return i && !i.hidden && i.naturalWidth === %d; })()" % W)
    assert page.get_attribute("#browserRoot .bv-img", "src").startswith("data:image/png;base64,")
    assert _state(page) == "The assistant is working" and pl.grants
    # take over: the owner's controls open; type (password box), a key and a tap reach the driver and nowhere else
    page.tap("#browserRoot button:has-text('Take over')")
    page.wait_for_selector("#browserRoot .bv-ctl:not([hidden])")
    assert pl.s.state == bp.HUMAN and _state(page) == "You are in control"
    assert page.get_attribute("#bvType", "type") == "password"
    page.fill("#bvType", SECRET)
    page.tap("#browserRoot .bv-send")
    until(page, "document.querySelector('#browserRoot .bv-msg').textContent === 'Sent to the page.'")
    assert page.input_value("#bvType") == ""                                  # cleared at once
    page.tap("#browserRoot .bv-key[aria-label='Press Enter on the page']")
    page.tap("#browserRoot button:has-text('Scroll down')")
    box = page.locator("#browserRoot .bv-img").bounding_box()
    page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    until(page, "true")
    for _ in range(50):
        if any(k == "click" for k, _ in pl.drv.inputs):
            break
        page.wait_for_timeout(100)
    kinds = [k for k, _ in pl.drv.inputs]
    assert kinds == ["type", "key", "scroll", "click"], pl.drv.inputs
    assert pl.drv.inputs[0][1] == {"text": SECRET} and pl.drv.inputs[1][1] == {"key": "Enter"} and pl.drv.inputs[2][1] == {"dy": 400}
    x, y = pl.drv.inputs[3][1]["x"], pl.drv.inputs[3][1]["y"]
    assert abs(x - W // 2) <= 3 and abs(y - H // 2) <= 3                      # scaled to the picture's natural size
    html = page.evaluate("document.documentElement.outerHTML") + page.inner_text("body")
    assert SECRET not in html and all(g not in html for g in pl.grants)      # the grant and the typed text never reach the page
    assert page.evaluate("JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage}) + location.href").count(pl.grants[-1]) == 0
    page.tap("#browserRoot .bv-show")                                         # "Show what I type" and back
    assert page.get_attribute("#bvType", "type") == "text"
    page.tap("#browserRoot .bv-show")
    assert page.get_attribute("#bvType", "type") == "password"
    # give back: the assistant is PAUSED; continuing is a separate step the owner confirms
    page.tap("#browserRoot .bv-give")
    page.wait_for_selector("#browserRoot .bv-cont:not([hidden])")
    assert pl.s.state == bp.PAUSED and page.is_hidden("#browserRoot .bv-ctl") and _state(page) == "The assistant is paused"
    page.once("dialog", lambda d: d.dismiss())                                # "no": nothing changes
    page.tap("#browserRoot .bv-cont")
    page.wait_for_timeout(300)
    assert pl.s.state == bp.PAUSED
    page.once("dialog", lambda d: d.accept())
    page.tap("#browserRoot .bv-cont")
    until(page, "document.querySelector('#browserRoot .bv-msg').textContent.includes('never saw what you typed')")
    assert pl.s.state == bp.AGENT and page.is_hidden("#browserRoot .bv-cont")
    # polling stops while another tab is open
    go(page, "chat")
    page.wait_for_timeout(400)
    n = pl.frames
    page.wait_for_timeout(2500)
    assert pl.frames == n
    go(page, "browser")
    until(page, "document.querySelector('#browserRoot .bv-img') && !document.querySelector('#browserRoot .bv-img').hidden")
    page.tap("#browserRoot .bv-stop")
    until(page, "document.querySelector('#browserRoot .bv-msg').textContent.startsWith('Stopped')")
    assert pl.s.state == bp.STOPPED and pl.drv.closed and page.is_hidden("#browserRoot .bv-img")
    assert page.errors == []


# ------------------------------------------------------------------ fits a phone: 390 and 360 px, EN light and HE RTL dark
@pytest.mark.parametrize("lang,locale,scheme", [("en", "en-US", "light"), ("he", "he-IL", "dark")])
def test_browser_tab_fits_390_and_360_px_in_english_and_hebrew(browser, phone, pilot, lang, locale, scheme):
    p = phone(tls=True)
    _switches(p)
    page = new_page(browser, locale=locale, scheme=scheme)
    ui_pair(page, p)
    _open_tab(page, p)
    assert page.inner_text("#tabbtn-browser") == ("דפדפן" if lang == "he" else "Browser")
    for w, h in ((390, 844), (360, 740)):
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(250)
        _fits(page, lang)
        _shot(page, f"{lang}-{w}-1-no-pilot")
    pl = pilot(p)
    page.tap("#browserRoot .bv-acts button.primary:visible")                  # Watch
    until(page, "(() => { const i = document.querySelector('#browserRoot .bv-img'); return i && !i.hidden && i.naturalWidth > 0; })()")
    steps = [("2-watching", None), ("3-in-control", "#browserRoot .bv-acts button.primary:visible"), ("4-paused", "#browserRoot .bv-give")]
    for name, tap in steps:
        if tap:
            page.set_viewport_size({"width": 390, "height": 844})
            page.tap(tap)
            page.wait_for_timeout(300)
        for w, h in ((390, 844), (360, 740)):
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(250)
            _fits(page, lang)
            _shot(page, f"{lang}-{w}-{name}")
        if name == "3-in-control":
            assert pl.s.state == bp.HUMAN and page.is_visible("#bvType")
            if lang == "he":                                                  # Hebrew strings, no English left on the controls
                txt = page.inner_text("#browserRoot")
                assert "השליטה אצלכם" in txt and "Take over" not in txt and "Give back" not in txt
    assert pl.s.state == bp.PAUSED
    assert page.errors == []
