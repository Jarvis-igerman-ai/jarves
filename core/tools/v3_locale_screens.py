"""SW-V3 P0.2c: real-browser (Playwright, headless Chromium/Edge) screenshots of the multi-language dashboard in
English (default) and Hebrew at desktop (1440 px) and phone (390 px) widths, plus automatic layout checks.

A throw-away data dir is filled with the same demo data as v3_dashboard_screens.py; no network, no sound, bound to
127.0.0.1. For every capture the page is checked for: html lang/dir of the locale, no horizontal page scroll, the
drawer / search box on the correct side (inline-start) and LTR islands (code, numbers) staying LTR. The findings go
to <out>/checks.json; the run fails (exit 1) when a check fails.

    uv run python tools/v3_locale_screens.py [--out ../docs/reviews/swv3_p02c] [--channel msedge]

Automatic captures, not a hands-on UI check.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

DESKTOP, PHONE = (1440, 1000), (390, 844)
LANGS = {"en": "ltr", "he": "rtl"}
PAGES = [("home", "#/home"), ("conversations", "#/conversations"), ("features", "#/features"), ("usage", "#/usage"),
         ("logs", "#/logs"), ("guides", "#/guides"), ("settings", "#/settings")]
PHONE_PAGES = [("home", "#/home"), ("features", "#/features"), ("conversations", "#/conversations")]

CHECK_JS = r"""
() => {
  const r = document.documentElement, side = document.getElementById('side'), search = document.getElementById('gsearch');
  const box = (e) => e ? e.getBoundingClientRect() : null;
  const codes = Array.from(document.querySelectorAll('main code, main .mono')).slice(0, 20)
    .map((e) => getComputedStyle(e).direction);
  return { lang: r.lang, dir: r.dir, scrollW: r.scrollWidth, innerW: innerWidth,
           side: box(side), search: box(search), picker: !!document.querySelector('#langSlot select.lang-pick'),
           pickerValue: (document.querySelector('#langSlot select') || {}).value || null,
           codesLtr: codes.every((d) => d === 'ltr'), nCodes: codes.length,
           title: document.title };
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv3_p02c"))
    ap.add_argument("--channel", default="", help="Playwright browser channel (msedge, chrome); default bundled Chromium")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    checks, failed = [], []
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        os.environ["JARVES_HOME"] = str(home)
        os.environ.pop("OPENAI_API_KEY", None)           # never a paid call from here
        import v3_dashboard_screens
        v3_dashboard_screens.fill(home)
        from jarves import dashboard_auth
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        dashboard_auth.RATE_PER_MIN = 100_000
        d = Dashboard(home, Config()).start()
        try:
            with sync_playwright() as pw:
                kw = {"channel": a.channel} if a.channel else {}
                browser = pw.chromium.launch(headless=True, **kw)

                def page_for(size, lang=None):
                    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, locale="he-IL",
                                              device_scale_factor=1)          # a Hebrew browser must still get English
                    if lang:
                        ctx.add_init_script(f"try {{ localStorage.setItem('jarves-lang', '{lang}'); }} catch (e) {{}}")
                    return ctx, ctx.new_page()

                def shot(p, name, lang, want_dir, size, extra=None):
                    p.wait_for_selector("#langSlot select", timeout=30000)     # the shell has run (slow machine)
                    p.wait_for_load_state("networkidle", timeout=30000)
                    p.wait_for_timeout(900)
                    c = p.evaluate(CHECK_JS)
                    c.update({"name": name, "lang_wanted": lang, "width": size[0]})
                    problems = []
                    if c["lang"] != lang or c["dir"] != want_dir:
                        problems.append(f"html lang/dir {c['lang']}/{c['dir']} != {lang}/{want_dir}")
                    if c["scrollW"] > c["innerW"] + 1:
                        problems.append(f"horizontal page scroll: {c['scrollW']} > {c['innerW']}")
                    if not c["picker"] or c["pickerValue"] != lang:
                        problems.append(f"language picker missing or shows {c['pickerValue']}")
                    if not c["codesLtr"]:
                        problems.append("a code/mono island is not LTR")
                    if extra:
                        problems += extra(c)
                    c["problems"] = problems
                    checks.append(c)
                    if problems:
                        failed.append((name, problems))
                    p.screenshot(path=str(out / f"{name}.png"), full_page=False)
                    print(("ok   " if not problems else "FAIL ") + name + ("" if not problems else f" {problems}"), flush=True)

                # 1. first load with nothing saved: English, even in a Hebrew browser
                ctx, p = page_for(DESKTOP)
                p.goto(d.url + "#/home")
                shot(p, "default_first_load_en_desktop", "en", "ltr", DESKTOP)
                # 2. the picker switches and the choice is remembered over a reload
                p.select_option("#langSlot select", "he")
                p.wait_for_timeout(600)
                p.reload()
                shot(p, "picker_he_after_reload_desktop", "he", "rtl", DESKTOP)
                ctx.close()

                for lang, want in LANGS.items():
                    ctx, p = page_for(DESKTOP, lang)
                    for name, frag in PAGES:
                        p.goto(d.url + frag)
                        shot(p, f"{name}_{lang}_desktop", lang, want, DESKTOP)
                    # the search results open on the inline-start side under the search box
                    p.goto(d.url + "#/home")
                    p.wait_for_timeout(500)
                    p.fill("#gsearch", "cal")
                    p.wait_for_timeout(600)

                    def search_side(c, want=want):
                        b = p.evaluate("(() => { const s = document.getElementById('gsearch').getBoundingClientRect(),"
                                       " g = document.getElementById('gresults').getBoundingClientRect();"
                                       " return {sl: s.left, sr: s.right, gl: g.left, gr: g.right, hidden: document.getElementById('gresults').hidden}; })()")
                        if b["hidden"]:
                            return ["search results did not open"]
                        ok = abs(b["gl"] - b["sl"]) < 2 if want == "ltr" else abs(b["gr"] - b["sr"]) < 2
                        return [] if ok else [f"search results not aligned to inline-start: {b}"]
                    shot(p, f"search_{lang}_desktop", lang, want, DESKTOP, search_side)
                    ctx.close()

                    ctx, p = page_for(PHONE, lang)
                    for name, frag in PHONE_PAGES:
                        p.goto(d.url + frag)
                        shot(p, f"{name}_{lang}_390", lang, want, PHONE)
                    p.goto(d.url + "#/home")
                    p.wait_for_timeout(500)
                    p.click("#menuBtn")
                    p.wait_for_timeout(500)

                    def drawer_side(c, want=want):
                        s = c["side"]
                        if not s:
                            return ["no drawer"]
                        ok = s["left"] <= 1 if want == "ltr" else abs(s["right"] - c["innerW"]) <= 1
                        return [] if ok else [f"drawer on the wrong side: left={s['left']} right={s['right']}"]
                    shot(p, f"drawer_{lang}_390", lang, want, PHONE, drawer_side)
                    ctx.close()
                browser.close()
        finally:
            d.stop()
    (out / "checks.json").write_text(json.dumps(checks, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(checks)} captures, {len(failed)} with problems")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
