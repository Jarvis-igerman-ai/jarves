"""SW-V4 K (issue #17): open EVERY dashboard page in a real headless browser (Edge on Windows, Chromium on Linux)
in English and Hebrew at the given widths and fail on:

- JavaScript errors, console errors, HTTP >= 400, failed requests;
- a wrong html lang/dir, an empty page, horizontal page scroll, elements that stick out of the viewport;
- the DOM rules of tools/all_pages_rules.js: accessible names, alt, duplicate ids, positive tabindex, focusable
  inside aria-hidden, contrast AA, mouse-only controls, phone target size, identical link names, direction of
  Hebrew/English blocks, reduced motion, leftover "Loading" / "undefined" texts, and the translation inventory
  (English multi-word strings on a Hebrew page, Hebrew on an English page; logs, code, user content excluded);
- with --focus: Tab through the page, every reached control shows a focus ring.

Throw-away data dir with DEMO data only (synthetic conversations, timers, lists, approvals), 127.0.0.1 only, no
network, no sound, never a paid call. The browser is closed after each page group (RAM).

    python tools/all_pages_check.py [--channel msedge] [--widths 1440,390] [--langs en,he] [--themes light,dark]
        [--sweeps 3] [--pages home,timers] [--focus] [--shots ../docs/reviews/swv4_k] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

LANGS = {"en": "ltr", "he": "rtl"}
STATIC = ROOT / "src" / "jarves" / "dashboard_static"
RULES = (ROOT / "tools" / "all_pages_rules.js").read_text(encoding="utf-8")

BASIC_JS = r"""
() => {
  const r = document.documentElement, main = document.getElementById('main');
  return { lang: r.lang, dir: r.dir, scrollW: r.scrollWidth, innerW: innerWidth,
           mainText: main ? main.innerText.trim().length : -1,
           pages: typeof PAGES === 'undefined' ? [] : Object.keys(PAGES) };
}
"""
FOCUS_JS = r"""
() => {
  const e = document.activeElement;
  if (!e || e === document.body || e === document.documentElement) return null;
  const cs = getComputedStyle(e), r = e.getBoundingClientRect();
  const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== 'none');
  let w = e.tagName.toLowerCase(); if (e.id) w += '#' + e.id; else if (e.className && typeof e.className === 'string') w += '.' + e.className.split(' ')[0];
  return { ring, where: w, text: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 40), visible: r.width > 0 && r.height > 0 };
}
"""


BUILTIN = {"home", "runtime", "session", "costs", "issues", "memory", "skills", "workspace", "features", "settings",
           "conversations"}                         # pages app.js renders itself (the rest register in PAGES)


def nav_groups() -> list[tuple[str, list[str]]]:
    """[(group, [page, ...])] in the order of the side navigation (index.html is the registry)."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'<div class="nav-group" data-group="([a-z]+)">(.*?)</div>\s*(?=<div class="nav-group"|</nav>)', html, re.S):
        out.append((m.group(1), list(dict.fromkeys(re.findall(r'data-page="([a-z0-9_-]+)"', m.group(2))))))
    return out


def nav_pages() -> list[str]:
    return [p for _, ps in nav_groups() for p in ps]


# pages that are not in the navigation (detail views), filled in with the demo session id
EXTRA = ["session/{sid}", "context/{sid}", "raw/{sid}", "logs?conv={sid}", "workspace/USER.md", "guide/timers",
         "features/needs_attention"]


def seed_v4(home: Path) -> None:
    """DEMO rows for the V4 pages: timers (one ringing soon), lists (one item done), approvals (2 pending + history),
    notes. Synthetic, never real data."""
    from jarves import approvals
    from jarves.lists import Lists
    from jarves.notes import Notes
    from jarves.timers import Timers
    from jarves.userdata import open_db
    db = open_db(home)
    tm = Timers(db)
    tm.start_timer(600, "פסטה")
    tm.start_timer(5400, "laundry")
    tm.set_alarm("07:30", "wake up")
    ls = Lists(db)
    ls.add("shopping", ["milk", "eggs", "לחם"])
    ls.check("shopping", "milk")
    ls.add("packing", ["passport", "charger"])
    Notes(db).add("Wi-Fi for guests", "network: demo-guest", tags=["home"])
    now = time.time()
    rows = [{"id": "demo1", "title": "Send a Telegram message to Dana", "prompt": "Send to Dana: running 10 minutes late",
             "why": "you asked me to tell Dana", "risk": "medium", "kind": "send", "source": "voice",
             "details": {"recipient": "Dana", "channel": "telegram"}, "voice_ok": True, "created": now - 20,
             "expires": now + 3600, "state": "pending", "channel": "", "decided_at": 0, "shown": ["dashboard"],
             "typed": False, "seconds_left": 3600},
            {"id": "demo2", "title": "Run a command on this PC", "prompt": "winget upgrade --id Pear.Desktop",
             "why": "update the music player", "risk": "high", "kind": "install", "source": "voice",
             "details": {}, "voice_ok": False, "created": now - 5, "expires": now + 3600, "state": "pending",
             "channel": "", "decided_at": 0, "shown": ["dashboard"], "typed": False, "seconds_left": 3600}]
    adir = home / "approvals"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "pending.json").write_text(json.dumps({"pid": os.getpid(), "at": now, "pending": rows, "signed": True}),
                                       encoding="utf-8")
    for i, (title, decision, kind) in enumerate([("Add an event to the calendar", "approved", "calendar"),
                                                 ("Send an email to Avi", "denied", "send"),
                                                 ("Delete the list 'old'", "timeout", "tool")]):
        approvals.append_audit(adir / "audit.jsonl", {"ts": now - 3600 * (i + 1), "id": f"h{i}", "kind": kind,
                                                      "risk": "medium", "source": "voice", "title": title,
                                                      "decision": decision, "channel": "voice", "waited_s": 4.0,
                                                      "shown": ["voice"], "prompt": title, "details": {}})
    db.close()                                    # a leaked handle kept memory.db locked (temp dir not removable)


def start_dashboard(tmp: Path):
    home = tmp / "home"
    home.mkdir()
    os.environ["JARVES_HOME"] = str(home)
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["JARVES_TEST_OFFLINE"] = "1"      # updates/GitHub/OpenAI/Google stubbed: pages render deterministic content
    import netguard
    netguard.install()
    import v3_dashboard_screens
    sid = v3_dashboard_screens.fill(home)
    seed_v4(home)
    from jarves import dashboard_auth
    from jarves.config import Config
    from jarves.dashboard import Dashboard
    dashboard_auth.RATE_PER_MIN = 100_000
    return Dashboard(home, Config()).start(), sid


def launch_kwargs(channel: str) -> dict:
    if channel:
        return {"channel": channel}
    from jarves import browser
    c = browser.candidates()
    if not c:
        raise SystemExit("no Edge / Chrome / Chromium found")
    return c[0][1]


def check_page(p, url: str, frag: str, lang: str, width: int, *, reduced: bool, focus: bool, shots: Path | None,
               theme: str, errs: list) -> dict:
    errs.clear()
    p.goto(url + "#/" + frag)
    p.wait_for_selector("#langSlot select", timeout=90000)
    p.wait_for_load_state("networkidle", timeout=30000)
    p.wait_for_function("() => !/^\\s*(Loading…|טוען…)\\s*$/.test((document.getElementById('main')||{}).innerText||'')",
                        timeout=15000)
    # event-driven settle (a fixed 400 ms was too short on a loaded runner): resolved when #main's DOM was quiet for 300 ms
    p.evaluate("""() => new Promise((done) => {
        const m = document.getElementById('main') || document.body; let t;
        const fin = () => { ob.disconnect(); done(); };
        const ob = new MutationObserver(() => { clearTimeout(t); t = setTimeout(fin, 300); });
        ob.observe(m, {childList: true, subtree: true, characterData: true, attributes: true});
        t = setTimeout(fin, 300); setTimeout(fin, 10000);
    })""")
    c = p.evaluate(BASIC_JS)
    probs: list[dict] = [{"rule": "browser", "sev": "critical", "msg": e, "where": ""} for e in errs]
    if c["lang"] != lang or c["dir"] != LANGS[lang]:
        probs.append({"rule": "html-lang", "sev": "serious", "msg": f"lang/dir {c['lang']}/{c['dir']}", "where": ""})
    if c["scrollW"] > c["innerW"] + 1:
        probs.append({"rule": "page-scroll", "sev": "serious", "msg": f"horizontal scroll {c['scrollW']}>{c['innerW']}",
                      "where": ""})
    want = frag.split("?")[0].split("/")[0]
    if want not in c.pop("pages") and want not in BUILTIN:   # the router fell back to Conversations (a script failed)
        probs.append({"rule": "wrong-page", "sev": "critical", "msg": f"no page registered for {want}", "where": ""})
    if c["mainText"] < 20:
        probs.append({"rule": "empty", "sev": "serious", "msg": f"empty page ({c['mainText']} chars)", "where": ""})
    if shots:
        # a tall viewport instead of a full-page capture: fixed parts (side bar, usage strip) land where they belong
        h = min(p.evaluate("() => document.documentElement.scrollHeight"), 4200)
        name = re.sub(r"[^a-z0-9]+", "_", re.sub(r"[0-9a-f]{8}-\d{8}-\d{6}|\d{8}-\d{6}-\w+", "demo", frag.split("?")[0]).lower()).strip("_")
        p.set_viewport_size({"width": width, "height": max(h, 900)})
        p.wait_for_timeout(250)
        p.screenshot(path=str(shots / f"{name}_{lang}_{width}{'' if theme == 'light' else '_' + theme}.jpg"),
                     type="jpeg", quality=55)
        p.set_viewport_size({"width": width, "height": 900})
        p.wait_for_timeout(150)
    # closed <details> hold texts too: open them for the rules (translation, contrast, names)
    p.evaluate("() => document.querySelectorAll('#main details:not([open])').forEach((d) => d.open = true)")
    r = p.evaluate(RULES, {"lang": lang, "width": width, "reduced": reduced})
    probs += r["issues"]
    if focus:
        p.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
        p.keyboard.press("Escape")
        seen = set()
        for _ in range(80):
            p.keyboard.press("Tab")
            f = p.evaluate(FOCUS_JS)
            if not f:
                continue
            key = (f["where"], f["text"])
            if key in seen:
                break
            seen.add(key)
            if f["visible"] and not f["ring"]:
                probs.append({"rule": "focus-ring", "sev": "serious", "msg": f"no focus ring: {f['text']!r}", "where": f["where"]})
        r["stats"]["tabStops"] = len(seen)
    return {"page": frag, "lang": lang, "width": width, "theme": theme, **c, "stats": r["stats"], "problems": probs}


def run(*, langs, widths, themes=("light",), pages=None, channel="", focus=False, shots: Path | None = None,
        sweeps=1, quiet=False, json_path: Path | None = None) -> list[dict]:
    from playwright.sync_api import sync_playwright
    groups = nav_groups() + [("details", EXTRA)]
    if pages:
        groups = [(g, [p for p in ps if p.split("/")[0].split("?")[0] in pages or p in pages]) for g, ps in groups]
        groups = [(g, ps) for g, ps in groups if ps]
    rows: list[dict] = []
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        d, sid = start_dashboard(Path(tmp))
        try:
            with sync_playwright() as pw:
                for sweep in range(sweeps):
                    for group, gpages in groups:
                        browser = pw.chromium.launch(headless=True, **launch_kwargs(channel))
                        try:
                            for lang in langs:
                                for width in widths:
                                    for theme in themes:
                                        reduced = width <= 480
                                        ctx = browser.new_context(
                                            viewport={"width": width, "height": 900}, locale="he-IL",
                                            color_scheme=theme, reduced_motion="reduce" if reduced else "no-preference")
                                        ctx.add_init_script(f"try {{ localStorage.setItem('jarves-lang', '{lang}'); "
                                                            f"localStorage.setItem('jarves-theme', '{theme}'); }} catch (e) {{}}")
                                        p = ctx.new_page()
                                        errs: list[str] = []
                                        p.on("pageerror", lambda e, errs=errs: errs.append(f"pageerror: {e}"))
                                        p.on("console", lambda m, errs=errs: errs.append(
                                            f"console.{m.type}: {m.text} @ {m.location.get('url', '')}")
                                            if m.type == "error" else None)
                                        p.on("response", lambda r, errs=errs: errs.append(
                                            f"HTTP {r.status} {r.request.method} {r.url}") if r.status >= 400 else None)
                                        p.on("requestfailed", lambda r, errs=errs: errs.append(
                                            f"request failed {r.url}: {r.failure}")
                                            if "net::ERR_ABORTED" not in str(r.failure) else None)
                                        for frag in gpages:
                                            t0 = time.time()
                                            for attempt in (0, 1):
                                                row = check_page(p, d.url, frag.format(sid=sid), lang, width,
                                                                 reduced=reduced, focus=focus and width >= 1024,
                                                                 shots=shots if sweep == 0 else None, theme=theme,
                                                                 errs=errs)
                                                # Windows runners exhaust socket buffers under many page loads
                                                # (net::ERR_NO_BUFFER_SPACE): an OS limit, not a page defect.
                                                # Re-check once; a real defect still fails the second time.
                                                if attempt or not any("ERR_NO_BUFFER_SPACE" in x["msg"]
                                                                      for x in row["problems"]):
                                                    break
                                                time.sleep(1.0)
                                            row.update(group=group, sweep=sweep, ms=round((time.time() - t0) * 1000))
                                            rows.append(row)
                                            if not quiet:
                                                n = len(row["problems"])
                                                print(("ok   " if not n else f"FAIL ({n:3}) ") +
                                                      f"{group}/{frag.split('/')[0]} [{lang} {width} {theme}] {row['ms']} ms", flush=True)
                                        ctx.close()
                        finally:
                            browser.close()
                        if json_path:                  # partial results survive an interrupted sweep
                            json_path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        finally:
            d.stop()
    return rows


def summary(rows: list[dict]) -> dict:
    by_rule: dict[str, int] = {}
    for r in rows:
        for p in r["problems"]:
            by_rule[p["rule"]] = by_rule.get(p["rule"], 0) + 1
    return {"loads": len(rows), "failed_loads": sum(1 for r in rows if r["problems"]), "by_rule": by_rule}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="msedge" if sys.platform == "win32" else "")
    ap.add_argument("--langs", default="en,he")
    ap.add_argument("--widths", default="1440,390")
    ap.add_argument("--themes", default="light")
    ap.add_argument("--pages", default="")
    ap.add_argument("--sweeps", type=int, default=1)
    ap.add_argument("--focus", action="store_true")
    ap.add_argument("--shots", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    rows = run(langs=a.langs.split(","), widths=[int(w) for w in a.widths.split(",")], themes=a.themes.split(","),
               pages=set(a.pages.split(",")) if a.pages else None, channel=a.channel, focus=a.focus,
               shots=Path(a.shots) if a.shots else None, sweeps=a.sweeps, json_path=Path(a.json) if a.json else None)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    s = summary(rows)
    print(json.dumps(s, indent=1))
    return 1 if s["failed_loads"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
