"""Headless screenshots of the WP-K pages (Home, Connections, the privacy bar) on the REAL dashboard: a throw-away
data dir (dashboard_screens.fill + a fake Core health snapshot + fake Google files), no network, no Google login.

    uv run python tools/connections_screens.py [--out ../docs/reviews/swv2_connections] [--browser PATH]

States captured: Google not connected, connected (check passed, days until the weekly reconnect), expired
(needs reconnect + how to fix), and privacy ON. Automatic captures, not a hands-on UI check.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
CLIENT = {"installed": {"client_id": "123-demo.apps.googleusercontent.com", "client_secret": "demo"}}


def shoot(browser: str, url: str, png: Path, theme: str, tmp: Path) -> None:
    cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
           f"--user-data-dir={tmp / 'browser'}", "--window-size=1400,1200", "--virtual-time-budget=6000",
           f"--screenshot={png}", "--force-dark-mode" if theme == "dark" else "--blink-settings=preferredColorScheme=1",
           url]
    subprocess.run(cmd, check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(("ok   " if png.exists() else "FAIL ") + str(png))


def main() -> int:
    from dashboard_screens import BROWSERS, fill
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv2_connections"))
    ap.add_argument("--browser", default=next((b for b in BROWSERS if Path(b).exists()), ""))
    a = ap.parse_args()
    if not a.browser:
        print("no headless browser found (Edge/Chrome)")
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        home = tmp / "home"
        home.mkdir()
        os.environ["JARVES_HOME"] = str(home)
        os.environ.pop("OPENAI_API_KEY", None)
        fill(home)
        from jarves import google_setup, logs
        from jarves.chatgpt_auth import _write_private
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        from jarves.health import Health
        h = Health(home / "health.json")
        h.mark("ready")
        h.set("ai", "ok", "GPT-Live + gpt-6-luna reachable")
        logs.setup(home / "logs", "INFO", console=False, privacy=False)
        (home / "config.toml").write_text("[log]\nprivacy = false\n", encoding="utf-8")
        cfg = Config()
        d = Dashboard(home, cfg, config_path=home / "config.toml").start()
        url = d.url
        try:
            google_setup.mark_step(home, "project")
            google_setup.mark_step(home, "calendar_api")
            shoot(a.browser, url + "#/home", out / "wpk_home_light.png", "light", tmp)
            shoot(a.browser, url + "#/connections", out / "wpk_connections_not_connected_light.png", "light", tmp)
            gdir = home / "google"
            _write_private(gdir / "client.json", CLIENT)
            from jarves.google_auth import ALL_SCOPES
            _write_private(gdir / "token.json", {"access_token": "x", "refresh_token": "y", "scopes": list(ALL_SCOPES),
                                                 "expires_at": time.time() + 3000,
                                                 "connected_at": time.time() - 2.4 * 86400})
            st = google_setup.load_state(home)
            st["steps"] = {k: time.time() for k in google_setup.STEP_IDS}
            st["check"] = {"at": time.time(), "calendar": "ok", "gmail": "ok", "error": ""}
            google_setup.save_state(home, st)
            shoot(a.browser, url + "#/connections", out / "wpk_connections_connected_light.png", "light", tmp)
            shoot(a.browser, url + "#/home", out / "wpk_home_connected_dark.png", "dark", tmp)
            _write_private(gdir / "token.json", {"needs_reconnect": True, "scopes": list(ALL_SCOPES),
                                                 "last_error": "GOOGLE_AUTH_EXPIRED", "last_error_at": time.time()})
            shoot(a.browser, url + "#/connections", out / "wpk_connections_reconnect_dark.png", "dark", tmp)
            from jarves import privacy
            privacy.set_privacy(d.ctx, True)
            shoot(a.browser, url + "#/home", out / "wpk_home_privacy_on_light.png", "light", tmp)
        finally:
            d.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
