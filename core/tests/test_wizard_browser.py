"""Setup and tour (#/setup, #152) in a REAL headless browser against a real dashboard on a throw-away DEMO home:
Works/Enter marks a topic done and it stays done after a reload, finished topics leave the queue for "Done (n)",
keys 1/2/3, Hebrew RTL at phone width, the a11y/locale DOM rules, and the first card's timing (a regression guard).
127.0.0.1 only, no network (netguard), never the owner's JARVES. Missing Playwright or browser = a failure, never a skip."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import all_pages_check as apc  # noqa: E402

FIRST_CARD_MS = 2000          # V4.5 workstream F: first actionable card within ~2 s of opening the page


@pytest.fixture(scope="module")
def site():
    from playwright.sync_api import sync_playwright
    keep = {k: os.environ.get(k) for k in ("JARVES_HOME", "OPENAI_API_KEY", "JARVES_TEST_OFFLINE", "JARVES_BETA_FEATURES")}
    with tempfile.TemporaryDirectory() as tmp:
        d, _sid = apc.start_dashboard(Path(tmp))
        try:
            with sync_playwright() as pw:
                b = pw.chromium.launch(headless=True, **apc.launch_kwargs(""))
                try:
                    yield d, b
                finally:
                    b.close()
        finally:
            d.stop()
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _real(errs: list[str]) -> list[str]:
    """Console/page errors without the Windows OS socket-buffer line (net::ERR_NO_BUFFER_SPACE, #695): a runner limit, not a
    page defect; core/tools/all_pages_check.py retries on it too. Anything else (pageerror, HTTP >= 400, other console
    errors) still fails."""
    return [e for e in errs if "ERR_NO_BUFFER_SPACE" not in e]


def _page(site, lang="en", width=1024):
    d, b = site
    ctx = b.new_context(viewport={"width": width, "height": 900}, locale="he-IL",
                        reduced_motion="reduce" if width <= 480 else "no-preference")
    ctx.add_init_script(f"try {{ localStorage.setItem('jarves-lang', '{lang}'); }} catch (e) {{}}")
    p = ctx.new_page()
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    p.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    p.on("response", lambda r: errs.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
    p.goto(d.url + "#/setup")
    p.wait_for_selector("#tour-works", timeout=30000)
    p.evaluate("() => api('/api/wizard/control', {method: 'POST', body: JSON.stringify({op: 'reset'})})")
    return ctx, p, errs


def _open(p):
    """A fresh load of #/setup (the state was reset): the card comes from the server, not from the page's memory."""
    p.evaluate("() => history.replaceState(null, '', '#/setup')")      # not the deep link of the last card
    p.reload()
    p.wait_for_selector("#tour-works", timeout=30000)
    p.wait_for_load_state("networkidle")


def _until(p, cond: str, timeout_s: float = 15.0) -> dict:
    """Poll GET /api/wizard until cond(d) is true (the answers are saved in the background)."""
    deadline = time.time() + timeout_s
    while True:
        d = _state(p)
        if p.evaluate(f"(d) => !!({cond})", d):
            return d
        if time.time() > deadline:
            raise AssertionError(f"never true: {cond}; left={d['left']} done={d['done']}")
        time.sleep(0.1)


def _state(p) -> dict:
    return p.evaluate("() => api('/api/wizard')")


def _title(p) -> str:
    return p.inner_text("#tour-title").strip()


def test_works_marks_done_and_it_survives_a_reload(site):
    ctx, p, errs = _page(site)
    try:
        _open(p)
        first = _state(p)["left"][0]
        t0 = _title(p)
        p.keyboard.press("Enter")                                        # focus starts on Works: Enter = Works
        p.wait_for_function("t => document.getElementById('tour-title').innerText.trim() !== t", arg=t0)
        st = _until(p, f"d.done.includes('{first}')")
        assert first in st["done"] and first not in st["left"] and st["finished"] == 1
        assert p.get_attribute(".tour-bar", "aria-valuenow") != "0"
        p.reload()
        p.wait_for_selector("#tour-works", timeout=15000)
        assert _title(p) != t0                                           # the finished topic is not shown again
        chips = p.eval_on_selector_all(".tour-chip", "els => els.map((e) => e.innerText)")
        assert t0 not in chips
        assert p.inner_text(".tour-done summary").strip() == "Done (1)"
        p.click(".tour-done summary")
        assert t0 in p.inner_text(".tour-done")
        assert _real(errs) == []
    finally:
        ctx.close()


def test_keys_later_and_failed_with_a_note(site):
    ctx, p, errs = _page(site)
    try:
        _open(p)
        st = _state(p)
        a, b = st["left"][0], st["left"][1]
        p.locator("body").press("3")                                     # Later: moves to the end of the queue
        _until(p, f"d.left[d.left.length - 1] === '{a}'")
        p.keyboard.press("2")                                            # Did not work: the note field opens
        p.wait_for_selector("#tour-note", state="visible")
        p.fill("#tour-note", "no sound at all")
        p.keyboard.press("Enter")
        st = _until(p, f"d.failed.includes('{b}')")                      # Failed (n): out of the to-do, not done
        row = next(t for t in st["topics"] if t["id"] == b)
        assert row["status"] == "fails" and b not in st["done"] and b not in st["left"]
        assert "(1)" in p.inner_text(".tour-failed summary")
        assert "#/issues" in p.get_attribute("#tour-msg a", "href")      # GitHub only through the review page
        p.keyboard.press("1")
        _until(p, "d.done.length === 1")
        assert _real(errs) == []
    finally:
        ctx.close()


def test_hebrew_rtl_phone_width_and_dom_rules(site):
    ctx, p, errs = _page(site, "he", 390)
    try:
        _open(p)
        p.click("#tour-works")
        p.wait_for_selector(".tour-done")
        p.mouse.move(1, 1)                                               # no hover colour in the contrast rule
        basic = p.evaluate("() => ({lang: document.documentElement.lang, dir: document.documentElement.dir, "
                           "sw: document.documentElement.scrollWidth, w: innerWidth})")
        assert basic["lang"] == "he" and basic["dir"] == "rtl" and basic["sw"] <= basic["w"] + 1
        assert any("֐" <= ch <= "׿" for ch in _title(p))
        for sel in ("#tour-works", "#tour-fails", "#tour-later"):
            assert p.locator(sel).bounding_box()["height"] >= 44
        p.evaluate("() => document.querySelectorAll('#main details:not([open])').forEach((d) => d.open = true)")
        p.wait_for_selector(".tour-more-body .tour-learn", timeout=15000)   # "More" loads lazily, only when opened
        r = p.evaluate(apc.RULES, {"lang": "he", "width": 390, "reduced": True})
        assert not r["issues"], json.dumps(r["issues"][:10], ensure_ascii=False)
        assert _real(errs) == []
    finally:
        ctx.close()


def test_english_dom_rules_and_first_card_timing(site):
    ctx, p, errs = _page(site, "en", 1440)
    try:
        times = []
        for _ in range(3):                                               # median of 3: one slow runner tick is not a regression
            p.evaluate("() => { location.hash = '#/guides'; }")
            p.wait_for_function("() => !document.getElementById('tour-works')")
            p.wait_for_load_state("networkidle")
            t = p.evaluate("""() => new Promise((done) => {
                const t0 = performance.now();
                const ob = new MutationObserver(() => { if (document.getElementById('tour-works')) { ob.disconnect(); done(performance.now() - t0); } });
                ob.observe(document.body, {childList: true, subtree: true});
                location.hash = '#/setup';
            })""")
            times.append(t)
        times.sort()
        assert times[1] < FIRST_CARD_MS, times
        p.wait_for_load_state("networkidle")
        p.mouse.move(1, 1)
        r = p.evaluate(apc.RULES, {"lang": "en", "width": 1440, "reduced": False})
        assert not r["issues"], json.dumps(r["issues"][:10], ensure_ascii=False)
        assert _real(errs) == []
    finally:
        ctx.close()


def test_real_drops_only_the_os_socket_buffer_line():
    assert _real(["console.error: Failed to load resource: net::ERR_NO_BUFFER_SPACE"]) == []
    other = ["pageerror: boom", "HTTP 500 http://x/api", "console.error: Failed to load resource: net::ERR_CONNECTION_RESET"]
    assert _real(other + ["console.error: net::ERR_NO_BUFFER_SPACE"]) == other
