"""SW-V3 integration: open EVERY dashboard page (all nav entries of index.html) in a real headless browser in
English, Hebrew and Arabic, and fail on JavaScript errors, console errors, failed API calls, an empty page,
a wrong html lang/dir, or horizontal page scroll. Throw-away data dir with demo data, 127.0.0.1 only, no network,
no sound, never a paid call.

    uv run python tools/v3_all_pages_check.py [--channel msedge] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

LANGS = {"en": "ltr", "he": "rtl", "ar": "rtl"}
STATIC = ROOT / "src" / "jarves" / "dashboard_static"

CHECK_JS = r"""
() => {
  const r = document.documentElement, main = document.getElementById('main') || document.querySelector('main');
  return { lang: r.lang, dir: r.dir, scrollW: r.scrollWidth, innerW: innerWidth,
           mainText: main ? main.innerText.trim().length : -1,
           active: (document.querySelector('#nav a.active, #nav a[aria-current="page"]') || {}).dataset?.page || null };
}
"""


def nav_pages() -> list[str]:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return list(dict.fromkeys(re.findall(r'data-page="([a-z0-9_-]+)"', html)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="msedge")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    pages = nav_pages()
    rows, failed = [], []
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        os.environ["JARVES_HOME"] = str(home)
        os.environ.pop("OPENAI_API_KEY", None)
        import v3_dashboard_screens
        v3_dashboard_screens.fill(home)
        from jarves import dashboard_auth
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        dashboard_auth.RATE_PER_MIN = 100_000
        d = Dashboard(home, Config()).start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, **({"channel": a.channel} if a.channel else {}))
                for lang, want in LANGS.items():
                    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="he-IL")
                    ctx.add_init_script(f"try {{ localStorage.setItem('jarves-lang', '{lang}'); }} catch (e) {{}}")
                    p = ctx.new_page()
                    errs: list[str] = []
                    p.on("pageerror", lambda e, errs=errs: errs.append(f"pageerror: {e}"))
                    p.on("console", lambda m, errs=errs: errs.append(f"console.{m.type}: {m.text}")
                         if m.type == "error" else None)
                    p.on("response", lambda r, errs=errs: errs.append(f"HTTP {r.status} {r.url}")
                         if "/api/" in r.url and r.status >= 500 else None)
                    for name in pages:
                        errs.clear()
                        p.goto(d.url + "#/" + name)
                        p.wait_for_selector("#langSlot select", timeout=30000)
                        p.wait_for_load_state("networkidle", timeout=30000)
                        p.wait_for_timeout(700)
                        c = p.evaluate(CHECK_JS)
                        probs = list(errs)
                        if c["lang"] != lang or c["dir"] != want:
                            probs.append(f"lang/dir {c['lang']}/{c['dir']}")
                        if c["scrollW"] > c["innerW"] + 1:
                            probs.append(f"horizontal scroll {c['scrollW']}>{c['innerW']}")
                        if c["mainText"] < 20:
                            probs.append(f"empty page (main text {c['mainText']} chars)")
                        rows.append({"page": name, "lang": lang, **c, "problems": probs})
                        if probs:
                            failed.append((name, lang, probs))
                        print(("ok   " if not probs else "FAIL ") + f"{name} [{lang}]" + (f" {probs}" if probs else ""),
                              flush=True)
                    ctx.close()
                browser.close()
        finally:
            d.stop()
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(rows)} page loads ({len(pages)} pages x {len(LANGS)} languages), {len(failed)} with problems")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
