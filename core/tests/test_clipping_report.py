"""JAR-79: report-only clipping / overlap / scroll-cue rules (docs/proposals/2026-11-09-ui-review.md).

The three rules of tools/all_pages_rules.js (opts.clipping, severity "info") are proven on a planted page, then the
phone app is swept (EN + HE, 390 and 360 px, fixed fake state: quiet hours forced, waiting approvals, no clock).
Hits are LISTED, never asserted: set JARVES_CLIPPING_REPORT=<file.md> to write them. Not blocking by design."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402
from test_mobile_phone_e2e import browser, go, new_page, phone, ui_pair, until  # noqa: E402,F401

PLANTED = """<!doctype html><html lang="en" dir="ltr"><head><meta charset="utf-8"><style>
body { font: 14px sans-serif; margin: 0; }
.cut { width: 60px; overflow: hidden; white-space: nowrap; }
.ell { width: 60px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.row { position: relative; height: 40px; } .row span { position: absolute; top: 0; left: 0; width: 100px; }
.row span + span { left: 40px; }
.strip { width: 100px; overflow-x: auto; white-space: nowrap; } .strip b { display: inline-block; width: 300px; }
</style></head><body><main id="main"><h1>T</h1>
<div class="cut" id="a">a very long unbroken label</div>
<div class="ell" id="b" title="a very long unbroken label">a very long unbroken label</div>
<div class="ell" id="c">a very long unbroken label</div>
<div class="row"><span>first overlapping</span><span>second overlapping</span></div>
<div class="strip" id="s"><b>wide</b></div>
</main></body></html>"""


def _evaluate(browser_, html, clipping=True):
    page = browser_.new_page(viewport={"width": 390, "height": 800})
    try:
        page.set_content(html)
        return page.evaluate(apc.RULES, {"lang": "en", "width": 390, "reduced": False, "clipping": clipping})
    finally:
        page.close()


def test_new_rules_report_planted_problems_and_are_off_by_default():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            on = _evaluate(b, PLANTED)
            off = _evaluate(b, PLANTED, clipping=False)
        finally:
            b.close()
    new = [i for i in on["issues"] if i["sev"] == "info"]
    by = {r: [i for i in new if i["rule"] == r] for r in ("clipped-text", "overlap", "scroll-cue")}
    assert any("no ellipsis" in i["msg"] for i in by["clipped-text"])              # #a
    assert any("no title" in i["msg"] for i in by["clipped-text"])                 # #c
    assert not any("div#b" in i["where"] or i["where"].endswith("#b") for i in by["clipped-text"])   # ellipsis + title is fine
    assert by["overlap"] and by["scroll-cue"]
    assert not [i for i in off["issues"] if i["rule"] in by]                      # nothing unless asked for


SCREENS = ("chat", "drawer", "voice", "approvals", "health")


@pytest.mark.parametrize("lang,locale", [("en", "en-US"), ("he", "he-IL")])
def test_phone_tabs_clipping_hits_are_listed_not_asserted(browser, phone, monkeypatch, lang, locale):
    from jarves import chat_engine
    monkeypatch.setattr(chat_engine.WebChat, "quiet", lambda self: True)          # quiet hours forced: no clock
    p = phone(tls=True)
    hits = []
    for width in (390, 360):
        page = new_page(browser, locale=locale)
        page.set_viewport_size({"width": width, "height": 800})
        ui_pair(page, p)
        p.owner("POST", "/api/mobile/settings", {"phone_approvals": True})
        p.ask("Send test message")
        p.ask("Install " + "averyveryveryverylongunbrokentoolname" * 2, typed=True)
        for screen in SCREENS:
            if screen == "drawer":
                page.tap("#menuBtn")
                page.wait_for_selector("#drawer:not([hidden])")
            else:
                go(page, screen)
                if screen == "approvals":
                    page.wait_for_selector("#apprList button", timeout=12000)
                if screen == "health":
                    page.wait_for_selector("#statusList li")
            page.wait_for_timeout(300)
            r = page.evaluate(apc.RULES, {"lang": lang, "width": width, "reduced": True, "clipping": True})
            hits += [(lang, width, screen, i["rule"], i["msg"], i["where"]) for i in r["issues"] if i["sev"] == "info"]
            if screen == "drawer":
                page.tap("#drawerClose")
        page.context.close()
    out = os.environ.get("JARVES_CLIPPING_REPORT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            for h in hits:
                f.write("\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in h) + "\n")
