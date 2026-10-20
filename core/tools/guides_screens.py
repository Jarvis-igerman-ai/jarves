"""Headless screenshots of the dashboard Guides (SW-V2 WP-L): the index, three sample guides, the Home checklist and
the Features page links. A throw-away data dir (no key, no Google, Pear pointed at a closed port, one backup) is
served by the real dashboard on 127.0.0.1 and headless Edge/Chrome captures it. No network, no sound, no paid call.

    python tools/guides_screens.py [--out ../docs/reviews/swv2_guides]

Automatic captures, not a hands-on UI check. One browser process at a time (RAM).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from dashboard_screens import BROWSERS  # noqa: E402

PAGES = [("index_he", "#/guides", "light", 1500), ("index_en", "#/guides?lang=en", "dark", 1500),
         ("guide_music_he", "#/guide/music", "light", 1700), ("guide_google_calendar_he", "#/guide/google_calendar",
                                                               "light", 1700),
         ("guide_camera_en", "#/guide/camera?lang=en", "dark", 1700), ("guide_api_budget_he", "#/guide/api_budget",
                                                                      "light", 1300),
         ("home_checklist", "#/home", "light", 1100), ("features_links", "#/features", "light", 1100)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv2_guides"))
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
        for k in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY"):
            os.environ.pop(k, None)
        from jarves import backup
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        backup.create(home, "manual")
        cfg = Config()
        cfg.music.pear_url = "http://127.0.0.1:9"           # nothing listens: Music shows "setup needed"
        d = Dashboard(home, cfg).start()
        try:
            for name, frag, theme, height in PAGES:
                png = out / f"guides_{name}.png"
                cmd = [a.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                       f"--user-data-dir={Path(tmp) / 'browser'}", f"--window-size=1400,{height}",
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
