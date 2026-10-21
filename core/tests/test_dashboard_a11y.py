"""SW-V4 K (issue #17): the dashboard in a REAL headless browser (Edge on Windows, Chromium on Linux).

1. The DOM rules of tools/all_pages_rules.js catch what they claim (a planted page with one problem per rule).
2. Every dashboard page (navigation + detail views) on the DEMO profile, Hebrew at phone width and English at
   desktop width: 0 JavaScript/console errors, 0 HTTP >= 400, no sideways scroll, correct lang/dir, 0 accessibility
   rule hits (names, labels, contrast AA, keyboard reachability, target size, reduced motion) and 0 translation
   leftovers (English phrases on Hebrew pages, Hebrew on English pages; logs, code and user content excluded).
The full matrix (en/he x 1440/1024/390, light/dark, 3 sweeps, screenshots) is `python tools/all_pages_check.py`.
Missing Playwright or browser = a failure, never a skip (CI installs both)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import all_pages_check as apc  # noqa: E402

PLANTED = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><style>
body { background: #fff; color: #000; font: 14px sans-serif; } .low { color: #aaa; } .click { cursor: pointer; }
.wide { width: 2000px; } button.tiny { width: 10px; height: 10px; padding: 0; }</style></head><body>
<main id="main"><h1>כותרת</h1>
<button type="button"></button>
<input type="text">
<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<div id="dup">א</div><div id="dup">ב</div>
<p class="low">טקסט חלש מאוד על רקע לבן</p>
<div class="click">לחיצה בלי מקלדת</div>
<p>This sentence was never translated</p>
<div class="wide">רחב מדי</div>
<button type="button" class="tiny">✕</button>
<a href="#a">open</a><a href="#b">open</a><a href="#c">open</a>
<div tabindex="3">טאב חיובי</div>
<p>[object HTMLDivElement]</p>
</main></body></html>"""


def _browser(pw):
    return pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))


def test_rules_catch_each_planted_problem():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = _browser(pw)
        try:
            p = b.new_page(viewport={"width": 390, "height": 800})
            p.set_content(PLANTED)
            r = p.evaluate(apc.RULES, {"lang": "he", "width": 390, "reduced": True})
        finally:
            b.close()
    got = {i["rule"] for i in r["issues"]}
    for rule in ("control-name", "label", "image-alt", "duplicate-id", "color-contrast", "mouse-only", "untranslated",
                 "off-screen", "target-size", "icon-button-name", "identical-links", "tabindex", "bad-text"):
        assert rule in got, (rule, sorted(got))
    assert not any(i["rule"] == "html-dir" for i in r["issues"])


def test_rules_flag_hebrew_on_an_english_page_but_not_user_content():
    from playwright.sync_api import sync_playwright
    page = ('<!doctype html><html lang="en" dir="ltr"><body><main id="main"><h1>Title</h1><p>שלום עולם</p>'
            '<p dir="auto">מה שהמשתמש אמר</p><code>קוד</code></main></body></html>')
    with sync_playwright() as pw:
        b = _browser(pw)
        try:
            p = b.new_page()
            p.set_content(page)
            r = p.evaluate(apc.RULES, {"lang": "en", "width": 1440, "reduced": False})
        finally:
            b.close()
    he = [i["msg"] for i in r["issues"] if i["rule"] == "hebrew-on-english"]
    assert he == ["שלום עולם"]


def test_every_page_is_clean_in_a_real_browser(monkeypatch):
    from jarves import dashboard_auth
    monkeypatch.setenv("JARVES_HOME", "unused")            # start_dashboard points it at its temp home: restored after
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(dashboard_auth, "RATE_PER_MIN", dashboard_auth.RATE_PER_MIN)
    rows = apc.run(langs=["he"], widths=[390], quiet=True) + apc.run(langs=["en"], widths=[1440], quiet=True)
    assert len(rows) >= 2 * (len(apc.nav_pages()) + len(apc.EXTRA))
    bad = [(r["page"], r["lang"], r["width"], p["rule"], p["msg"][:120]) for r in rows for p in r["problems"]]
    assert not bad, f"{len(bad)} problems, e.g.:\n" + "\n".join(map(str, bad[:40]))


@pytest.mark.parametrize("lang,dir_", [("en", "ltr"), ("he", "rtl")])
def test_only_english_and_hebrew_are_swept(lang, dir_):
    assert apc.LANGS == {"en": "ltr", "he": "rtl"} and apc.LANGS[lang] == dir_
