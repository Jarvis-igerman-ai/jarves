"""SW-V3 P0.2: headless screenshots of the REAL redesigned dashboard in Hebrew (default) and English, desktop and
phone widths, light and dark. A throw-away data dir is filled with the same demo data as dashboard_screens.py and
m4_dashboard_screens.py (19 Oct fixture conversation, typed chat, task, memory, wake model, LAN pairing); no network,
no sound, bound to 127.0.0.1. One browser process at a time (RAM).

    uv run python tools/v3_dashboard_screens.py [--out ../docs/reviews/swv3_p02] [--browser PATH]
    uv run python tools/v3_dashboard_screens.py --serve 900     # print the URL and serve for 900 s (interactive review)

Automatic captures, not a hands-on UI check.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

DESKTOP, PHONE = (1440, 1000), (500, 1500)   # headless Edge will not go narrower than ~500 px; 390 px: Playwright pw_*.png
SHOTS = [  # name, fragment, lang, theme, size
    ("home", "#/home", "he", "light", DESKTOP), ("home", "#/home", "en", "light", DESKTOP),
    ("home", "#/home", "he", "dark", DESKTOP), ("home_phone", "#/home", "he", "light", PHONE),
    ("home_phone", "#/home", "en", "dark", PHONE), ("conversations", "#/conversations", "he", "light", DESKTOP),
    ("transcript", "#/session/{sid}", "he", "light", DESKTOP), ("costs", "#/costs", "he", "light", DESKTOP),
    ("features", "#/features", "he", "light", DESKTOP), ("features_phone", "#/features/not_ready_on_pi", "he", "light", PHONE),
    ("runtime", "#/runtime", "he", "dark", DESKTOP), ("usage", "#/usage", "en", "light", DESKTOP),
    ("settings", "#/settings", "he", "light", DESKTOP), ("logs", "#/logs", "he", "dark", DESKTOP),
    ("identity", "#/identity", "he", "light", DESKTOP), ("devices", "#/devices", "en", "light", DESKTOP),
]


def fill(home: Path) -> str:
    import dashboard_screens
    import m4_dashboard_screens
    sid = dashboard_screens.fill(home)
    m4_dashboard_screens.fill(home)
    return sid


def main() -> int:
    from dashboard_screens import BROWSERS
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv3_p02"))
    ap.add_argument("--browser", default=next((b for b in BROWSERS if Path(b).exists()), ""))
    ap.add_argument("--serve", type=int, default=0, help="serve for N seconds instead of capturing")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        os.environ["JARVES_HOME"] = str(home)
        os.environ.pop("OPENAI_API_KEY", None)           # never a paid call from here
        sid = fill(home)
        from jarves import dashboard_auth
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        dashboard_auth.RATE_PER_MIN = 100_000      # 16 fresh browser loads in two minutes are not an attack
        d = Dashboard(home, Config()).start()
        try:
            if a.serve:
                print(f"URL {d.url}\nSID {sid}", flush=True)
                time.sleep(a.serve)
                return 0
            if not a.browser:
                print("no headless browser found (Edge/Chrome)")
                return 2
            out = Path(a.out)
            out.mkdir(parents=True, exist_ok=True)
            for name, frag, lang, theme, (w, h) in SHOTS:
                png = out / f"p02_{name}_{lang}_{theme}.png"
                frag = frag.format(sid=sid)
                frag += ("&" if "?" in frag else "?") + "lang=" + lang
                cmd = [a.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                       f"--user-data-dir={Path(tmp) / 'browser'}", f"--window-size={w},{h}",
                       "--virtual-time-budget=6000", f"--screenshot={png}",
                       "--force-dark-mode" if theme == "dark" else "--blink-settings=preferredColorScheme=1",
                       d.url + frag]
                subprocess.run(cmd, check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(("ok   " if png.exists() else "FAIL ") + str(png))
        finally:
            d.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
