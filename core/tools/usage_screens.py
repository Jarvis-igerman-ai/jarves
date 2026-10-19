"""Headless screenshots of the dashboard's Usage and Backup pages and the usage strip (SW-V2 M5). A throw-away data
dir gets synthetic usage (JARVES counters, one plan-limit error, an API budget) and two real backups; the real
dashboard serves it on 127.0.0.1 and headless Edge/Chrome captures it. No network, no sound, no paid call.

    python tools/usage_screens.py [--out ../docs/reviews/swv2_m5]

Automatic captures, not a hands-on UI check. One browser process at a time (RAM).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from dashboard_screens import BROWSERS, fill  # noqa: E402

PAGES = [("usage", "#/usage", "light"), ("usage", "#/usage", "dark"), ("backup", "#/backup", "light"),
         ("strip_conversations", "#/conversations", "light")]


def fill_m5(home: Path) -> None:
    from jarves import backup, usage
    from jarves.memory import MemoryStore
    from jarves.store import Store
    m = MemoryStore(home / "memory.db")
    for kind, n in (("ai_requests", 151), ("voice_seconds", 41 * 60)):
        m._x("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) ON CONFLICT(day, kind) DO UPDATE SET "
             "count = excluded.count", (date.today().isoformat(), kind, n))
    m.close()
    st = Store(home / "store.db")
    st.ingest({"ts": time.time() - 300, "level": "WARNING", "component": "chatgpt", "code": usage.PLAN_LIMIT_CODE,
               "msg": "ChatGPT plan request failed"})
    st.commit()
    st.close()
    usage.set_settings(home, {"api_monthly_budget_usd": 5})
    usage.Monitor(home).check_alerts()
    backup.create(home, "auto")
    backup.create(home, "manual")
    backup.restore_test(backup.backup_dir(home) / backup.list_backups(home)[0]["name"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv2_m5"))
    ap.add_argument("--browser", default=next((b for b in BROWSERS if Path(b).exists()), ""))
    a = ap.parse_args()
    if not a.browser:
        print("no headless browser found (Edge/Chrome)")
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        os.environ["JARVES_HOME"] = str(home)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENAI_ADMIN_KEY", None)
        fill(home)
        fill_m5(home)
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        d = Dashboard(home, Config()).start()
        try:
            for name, frag, theme in PAGES:
                png = out / f"m5_{name}_{theme}.png"
                cmd = [a.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                       f"--user-data-dir={Path(tmp) / 'browser'}", "--window-size=1400,1100",
                       "--virtual-time-budget=6000", f"--screenshot={png}"]
                cmd.append("--force-dark-mode" if theme == "dark" else "--blink-settings=preferredColorScheme=1")
                cmd.append(d.url + frag)
                subprocess.run(cmd, check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(("ok   " if png.exists() else "FAIL ") + str(png))
        finally:
            d.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
