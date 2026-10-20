"""Headless screenshots of the REAL dashboard pages of SW-V2 WP-M4 (Features matrix, Identity & voice, Devices,
Calendar): a throw-away data dir; no network, no sound, no listener beyond 127.0.0.1 (the Devices page is shown in
LAN mode by flipping the access object only, so no Windows firewall prompt).

    uv run python tools/m4_dashboard_screens.py [--out ../docs/reviews/swv2_m4] [--browser PATH]

Automatic captures, not a hands-on UI check.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))


def fill(home: Path) -> None:
    from jarves.config import Config
    from jarves.features import Features
    from jarves.identity import Identity
    from jarves.memory import MemoryStore
    from jarves.voice import wake_train as wt
    mem = MemoryStore(home / "memory.db")
    ident = Identity(home, Config(), mem)
    ident.set_names("JARVES", "ג'ארוויס", by="dashboard")
    ident.choose_voice("live:vesper")
    rng = np.random.default_rng(0)
    x = rng.standard_normal((40, wt.WINDOWS, wt.EMB)).astype(np.float32)
    path = home / "wake" / "hey_jarves.npz"
    wt.save_head(path, wt.train_head(x, np.array([0, 1] * 20), epochs=2), {"phrase": "hey jarves", "threshold": 0.9})
    path.with_suffix(".json").write_text('{"phrase": "hey jarves", "threshold": 0.9, "eval": {"recall": 1.0, '
                                         '"false_wakes": 0, "hours": 0.337}, "created": "2026-10-19T21:10:00", '
                                         '"evidence": "synthetic (local TTS voices + generated noise)"}',
                                         encoding="utf-8")
    Features(Config(), home).set("dashboard_lan", True, by="dashboard")
    mem.close()


def main() -> int:
    from dashboard_screens import BROWSERS
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv2_m4"))
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
        fill(home)
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        d = Dashboard(home, Config()).start()
        d.access.lan, d.access.lan_host = True, "192.168.1.50"       # display only: still bound to 127.0.0.1
        d.access.pair(d.access.new_code()["code"], "Galaxy S25", "192.168.1.23")
        try:
            for name, frag, theme, w, h in (("features", "#/features", "light", 1700, 3000),
                                            ("features_not_ready_pi", "#/features/not_ready_on_pi", "dark", 1500, 2100),
                                            ("features_phone", "#/features/needs_attention", "light", 520, 2400),
                                            ("identity", "#/identity", "light", 1400, 2300),
                                            ("devices", "#/devices", "light", 1400, 1000),
                                            ("calendar", "#/calendar", "light", 1400, 700)):
                png = out / f"m4_{name}_{theme}.png"
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
