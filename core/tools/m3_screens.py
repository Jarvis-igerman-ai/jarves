"""Headless screenshots of the WP-M3 dashboard pages (Jobs, Reports, Notes, Files, Alerts, Autonomy) on synthetic
data in a throw-away data dir: no network, no model calls, no sound. Same capture method as dashboard_screens.py.

    uv run python tools/m3_screens.py [--out ../docs/reviews/swv2_m3] [--browser PATH]

Automatic captures, not a hands-on UI check.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dashboard_screens import BROWSERS  # noqa: E402

PAGES = [("jobs", "#/jobs", "light"), ("jobs", "#/jobs", "dark"), ("reports", "#/reports/{rid}", "light"),
         ("notes", "#/notes", "light"), ("files", "#/files", "light"), ("alerts", "#/alerts", "light"),
         ("autonomy", "#/autonomy", "light")]


def fill(home: Path) -> int:
    from jarves.alerts import AlertEngine
    from jarves.autonomy import Autonomy
    from jarves.config import Config
    from jarves.notes import Notes
    from jarves.sandbox import Sandbox
    from jarves.scheduler import Scheduler
    from jarves.tasks import LookupResult
    from jarves.userdata import open_db

    db = open_db(home)
    now = time.time()
    clock = [now - 2 * 86400]

    async def research(task, budget):
        budget.reserve("ai_requests")
        return LookupResult("שלוש ידיעות AI מהבוקר: מודל קוד חדש, תקנות בינה מלאכותית באירופה, ושבב חדש לרובוטים "
                            "ביתיים.", queries=["AI news this morning", "EU AI act update"])

    alerts = AlertEngine(db, clock=lambda: clock[0])
    s = Scheduler(db, research=research, alerts=alerts, clock=lambda: clock[0])
    daily = s.preview("כל בוקר ב-8", "סיכום חדשות AI של הבוקר", name="דוח AI בוקר")
    s.approve(daily["id"], by="voice")
    rem = s.preview("כל יום חמישי ב-18:00", "להוציא את הפח", action="reminder", name="פח")
    s.approve(rem["id"], by="dashboard")
    s.pause(rem["id"], by="dashboard")
    s.preview("בימי חול ב-7:15", "מזג אוויר ופקקים לדרך לעבודה", name="בוקר עבודה")

    async def runs():
        for days_ago, late in ((1, False), (0, True)):
            clock[0] = now - days_ago * 86400
            s.db.x("UPDATE jobs SET next_run=? WHERE id=?", (clock[0] - (3 * 3600 if late else 30), daily["id"]))
            await s.tick()
            await asyncio.gather(*[t for st in s._running.values() for t in st])
    asyncio.run(runs())
    rid = s.db.row("SELECT MAX(id) id FROM reports")["id"]

    notes = Notes(db)
    notes.add("קניות לשבת", "חלב, ביצים, חלה, ענבים", ["בית", "קניות"])
    notes.add("רעיונות לרובוט", "מצב לילה שקט; תזכורת לשתות מים כל שעתיים בימי עבודה.", ["jarves", "ideas"])
    sb = Sandbox(home / "files", db, home=home)
    sb.write_text("reports/ai-morning.md", "# דוח AI\n\nשלוש ידיעות מהבוקר.")
    notes.export(sb)
    shared = home.parent / "Shared Documents"
    shared.mkdir()
    sb.request_grant(str(shared), "read", by="voice", reason="לקרוא את רשימת המתכונים")

    alerts.clock = lambda: now
    alerts.raise_("scheduler", "הדוח מוכן: דוח AI בוקר", "אפשר לבקש ממני להקריא אותו.", priority="normal",
                  dedupe_key="x1")
    alerts.raise_("scheduler", "הדוח מוכן: דוח AI בוקר", "duplicate", priority="normal", dedupe_key="x1")
    a = Autonomy(db, cfg=Config(), home=home, alerts=alerts)
    a.sync([{"fingerprint": "false_wake", "kind": "wake", "severity": "low", "count": 4, "status": "open",
             "title": "False wake", "evidence": "{}"}])
    db.close()
    return rid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv2_m3"))
    ap.add_argument("--browser", default=next((b for b in BROWSERS if Path(b).exists()), ""))
    a = ap.parse_args()
    if not a.browser:
        print("no headless browser found (Edge/Chrome)")
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        os.environ["JARVES_HOME"] = str(home)
        os.environ.pop("OPENAI_API_KEY", None)
        for k in ("LOCALAPPDATA", "APPDATA"):           # the temp dir lives there; grants under it are refused
            os.environ.pop(k, None)
        rid = fill(home)
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        d = Dashboard(home, Config()).start()
        try:
            for name, frag, theme in PAGES:
                png = out / f"m3_{name}_{theme}.png"
                cmd = [a.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                       f"--user-data-dir={Path(tmp) / 'browser'}", "--window-size=1400,1000",
                       "--virtual-time-budget=6000", f"--screenshot={png}",
                       "--force-dark-mode" if theme == "dark" else "--blink-settings=preferredColorScheme=1",
                       d.url + frag.format(rid=rid)]
                subprocess.run(cmd, check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(("ok   " if png.exists() else "FAIL ") + str(png))
        finally:
            d.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
