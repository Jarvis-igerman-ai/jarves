"""Top-bar mic mode menu (Muted / Wake word only / Live) and the Stop / Start JARVES toggle, in a real headless browser
against the real dashboard server. The Core is faked at the HTTP edge (page.route on /api/runtime*), like the other
browser tests fake their API. Missing Playwright or browser = a failure, never a skip (CI installs both)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from test_dashboard import dash  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

PATHS = {"/api/runtime", "/api/runtime/stop", "/api/runtime/start"}


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


class FakeCore:
    def __init__(self):
        self.live, self.mic, self.posts, self.talk_ok = True, "wake_only", [], True

    def handle(self, route):
        from urllib.parse import urlparse
        req = route.request
        path = urlparse(req.url).path
        if path not in PATHS:
            return route.fallback()
        if req.method == "POST":
            body = json.loads(req.post_data or "{}")
            self.posts.append((path, body))
            if path == "/api/runtime/stop":
                self.live = False
                return route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
            if path == "/api/runtime/start":
                self.live = True
                return route.fulfill(status=200, content_type="application/json", body='{"ok": true, "starting": true}')
            applied = {}
            if "mic_muted" in body:
                self.mic = "muted" if body["mic_muted"] else ("wake_only" if self.mic == "muted" else self.mic)
            if body.get("talk") == "start":
                applied["talk"] = {"ok": self.talk_ok, "message": "voice mode is local, not live" if not self.talk_ok else "ok"}
                if self.talk_ok:
                    self.mic = "listening"
            elif body.get("talk") == "stop" and self.mic == "listening":
                self.mic = "wake_only"
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"applied": applied}))
        d = {"live": self.live, "runtime": {"core": {"state": "running" if self.live else "stopped"}}, "host": {"os": "Windows", "machine": "x"},
             "starting": False, "start_error": None, "autostart": {}, "platform_status": {},
             "privacy": {"mic": {"state": self.mic}, "camera": {"state": "disabled"}, "cloud_audio": {"state": "off"}} if self.live else None}
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(d))


def open_page(browser, dash, fake, *, width=1440, lang="en"):  # noqa: F811
    """Load the dashboard. A static script whose TCP connection is reset by the loaded Windows runner (WinError 10053,
    flake #488) leaves pages.js unloaded, and every later script then throws "PAGES is not defined" - a transport
    failure, not a product bug. A load in which any /static/ request FAILED is therefore discarded and repeated (at most
    3 loads); a page error on a load where every asset arrived still fails the test."""
    for attempt in range(3):
        ctx = browser.new_context(viewport={"width": width, "height": 800})
        p = ctx.new_page()
        errs: list[str] = []
        failed: list[str] = []
        p.on("pageerror", lambda e: errs.append(str(e)))
        p.on("requestfailed", lambda r: failed.append(r.url) if "/static/" in r.url else None)
        p.route(re.compile(r"/api/runtime(/stop|/start)?(\?.*)?$"), fake.handle)
        p.add_init_script(f"try{{localStorage.setItem('jarves-lang','{lang}')}}catch(e){{}}")
        p.goto(dash.url + "#/home")
        try:
            p.wait_for_selector("#micPill", timeout=30000)
            p.wait_for_function("() => !!document.querySelector('.schip-stop, .schip-start')", timeout=30000)
        except Exception:
            if not failed or attempt == 2:
                raise
        if not failed:
            return ctx, p, errs
        ctx.close()
        assert attempt < 2, f"static assets kept failing to load: {failed}"
    raise AssertionError("unreachable")


def pill_text(p):
    return p.inner_text("#micPill").strip()


def test_mic_pill_menu_switches_modes_and_shows_feedback(browser, dash):  # noqa: F811
    fake = FakeCore()
    ctx, p, errs = open_page(browser, dash, fake)
    assert pill_text(p) == "Wake word only"
    assert p.get_attribute("#micPill", "aria-haspopup") == "menu" and p.get_attribute("#micPill", "aria-expanded") == "false"
    p.click("#micPill")
    assert p.get_attribute("#micPill", "aria-expanded") == "true"
    assert p.locator("#micMenu [role=menuitemradio]").count() == 3
    assert p.get_attribute('#micMenu [data-mode="wake_only"]', "aria-checked") == "true"
    p.click('#micMenu [data-mode="muted"]')
    p.wait_for_function("() => document.getElementById('micPill').innerText.trim() === 'Muted'")
    assert fake.posts[-1] == ("/api/runtime", {"mic_muted": True})
    assert p.get_attribute('#micMenu [data-mode="muted"]', "aria-checked") == "true"
    assert "Microphone: Muted" in p.inner_text("#micMenu .mm-msg")
    p.click('#micMenu [data-mode="live"]')
    p.wait_for_function("() => document.getElementById('micPill').innerText.trim() === 'Listening'")
    assert fake.posts[-1] == ("/api/runtime", {"mic_muted": False, "talk": "start"})
    assert p.get_attribute('#micMenu [data-mode="live"]', "aria-checked") == "true"
    p.click('#micMenu [data-mode="wake_only"]')
    p.wait_for_function("() => document.getElementById('micPill').innerText.trim() === 'Wake word only'")
    assert fake.posts[-1] == ("/api/runtime", {"mic_muted": False, "talk": "stop"})
    assert errs == []
    ctx.close()


def test_mic_menu_keyboard_escape_and_outside_click(browser, dash):  # noqa: F811
    fake = FakeCore()
    ctx, p, errs = open_page(browser, dash, fake)
    p.focus("#micPill")
    p.keyboard.press("Enter")
    assert p.evaluate("document.activeElement.dataset.mode") == "wake_only"      # focus starts on the current mode
    p.keyboard.press("ArrowUp")
    assert p.evaluate("document.activeElement.dataset.mode") == "muted"
    p.keyboard.press("End")
    assert p.evaluate("document.activeElement.dataset.mode") == "live"
    p.keyboard.press("Home")
    p.keyboard.press("Enter")                                                     # activates "Muted" from the keyboard
    p.wait_for_function("() => document.getElementById('micPill').innerText.trim() === 'Muted'")
    p.keyboard.press("Escape")
    assert p.locator("#micMenu").count() == 0 and p.evaluate("document.activeElement.id") == "micPill"
    p.keyboard.press("ArrowDown")                                                 # the arrow also opens it
    assert p.locator("#micMenu").count() == 1
    p.mouse.click(5, 400)
    assert p.locator("#micMenu").count() == 0
    assert errs == []
    ctx.close()


def test_live_failure_is_shown_and_mode_unchanged(browser, dash):  # noqa: F811
    fake = FakeCore()
    fake.talk_ok = False
    ctx, p, errs = open_page(browser, dash, fake)
    p.click("#micPill")
    p.click('#micMenu [data-mode="live"]')
    p.wait_for_selector("#micMenu .mm-msg.err")
    assert "not live" in p.inner_text("#micMenu .mm-msg")
    assert pill_text(p) == "Wake word only"
    ctx.close()


def test_mic_menu_narrow_width_stays_on_screen_and_hebrew(browser, dash):  # noqa: F811
    fake = FakeCore()
    ctx, p, errs = open_page(browser, dash, fake, width=360, lang="he")
    assert pill_text(p) == "רק מילת הפעלה"
    p.click("#micPill")
    box = p.eval_on_selector("#micMenu", "e => { const r = e.getBoundingClientRect(); return [r.left, r.right, innerWidth]; }")
    assert box[0] >= 0 and box[1] <= box[2]
    assert p.locator("#micMenu .mm-item").nth(0).inner_text().startswith("✓") is False
    assert "מושתק" in p.inner_text('#micMenu [data-mode="muted"]')
    assert errs == []
    ctx.close()


def test_stop_confirm_then_start_returns(browser, dash):  # noqa: F811
    fake = FakeCore()
    ctx, p, errs = open_page(browser, dash, fake)
    assert p.locator(".schip-stop").inner_text().strip() == "Stop JARVES"
    assert p.locator(".schip-start:not(.schip-stop)").count() == 0
    p.once("dialog", lambda d: d.dismiss())                                       # cancel: nothing happens
    p.click(".schip-stop")
    assert not [x for x in fake.posts if x[0] == "/api/runtime/stop"] and fake.live
    dialogs: list[str] = []
    p.once("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    p.click(".schip-stop")
    p.wait_for_selector(".schip-start:not(.schip-stop)", timeout=15000)
    assert "Stop JARVES?" in dialogs[0]
    assert ("/api/runtime/stop", {}) in fake.posts and not fake.live
    assert p.locator(".schip-stop").count() == 0
    assert "Stopped" in p.inner_text("#statusChips")
    p.click(".schip-start")
    p.wait_for_selector(".schip-stop", timeout=15000)
    assert ("/api/runtime/start", {}) in fake.posts and "Running" in p.inner_text("#statusChips")
    assert errs == []
    ctx.close()


def test_stopped_core_mic_menu_is_disabled_with_a_note(browser, dash):  # noqa: F811
    fake = FakeCore()
    fake.live = False
    ctx, p, errs = open_page(browser, dash, fake)
    p.click("#micPill")
    assert p.locator("#micMenu .mm-item:disabled").count() == 3
    assert "stopped" in p.inner_text("#micMenu .mm-msg")
    ctx.close()


def test_stop_endpoint_is_owner_only():
    from types import SimpleNamespace

    from jarves.runtime import api_core_stop
    code, _ = api_core_stop(SimpleNamespace(principal={"kind": "phone"}, home=None, cfg=None), None, {})
    assert code == 403
