"""Headless screenshots of the REAL dashboard (no network, no sound): a throw-away data dir is filled from the
19 Oct session fixture plus a few synthetic records (a typed chat, a background task, memory facts, a workspace
edit, log files, a config.toml), the dashboard serves it on 127.0.0.1, and headless Edge/Chrome captures each page.

    uv run python tools/dashboard_screens.py [--out ../docs/reviews/swv2_dashboard] [--browser PATH]

Automatic captures, not a hands-on UI check. One browser process at a time (RAM).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURE = ROOT / "tests" / "fixtures" / "session_2026-10-19.jsonl"
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", "/usr/bin/chromium", "/usr/bin/google-chrome"]
PAGES = [("conversations", "#/conversations", "light"), ("transcript", "#/session/{sid}", "light"),
         ("transcript", "#/session/{sid}", "dark"), ("logs", "#/logs", "light"), ("logs_conversation", "#/logs?conv={sid}", "dark"),
         ("costs", "#/costs", "light"), ("tasks", "#/tasks", "light"), ("memory", "#/memory", "light"),
         ("workspace", "#/workspace/USER.md", "light"), ("settings", "#/settings", "light"), ("raw", "#/raw/{sid}", "light"),
         ("issues", "#/issues", "light")]


def fill(home: Path) -> str:
    from jarves import diagnose
    from jarves.config import Config
    from jarves.memory import MemoryStore
    from jarves.store import Store
    from jarves.workspace import Workspace
    st = Store(home / "store.db", tier="priority", backend_model="gpt-6-luna")
    diagnose.run(st, Config(), st.ingest_log(FIXTURE))
    t = time.time() - 1800
    for i, (u, a) in enumerate([("מה השעה?", "השעה שתיים וחצי."), ("תודה", "בכיף!")]):
        st.ingest({"ts": t + i * 30, "code": "AI_REPLY", "msg": "agent replied", "usage_in": 2400, "usage_out": 60,
                   "model": "gpt-6-luna", "session": "typed001"})
        st.ingest({"ts": t + i * 30 + 0.2, "code": "REPLY", "msg": "reply via ai", "ms": 1400, "text": a, "utterance": u,
                   "typed": True, "session": "typed001"})
    st.commit()
    sid = st.rows("SELECT id FROM sessions WHERE kind = 'live' LIMIT 1")[0]["id"]
    st.close()
    (home / "logs").mkdir(exist_ok=True)
    shutil.copy(FIXTURE, home / "logs" / "jarves.log")
    (home / "tasks.jsonl").write_text(json.dumps({
        "id": "a1b2", "description": "השוואה בין אייפון 18 לגלקסי S27", "status": "done", "created": t - 600,
        "finished": t - 583, "duration_s": 17.9, "attempts": 1, "queries": ["iPhone 18 vs Galaxy S27 review"],
        "result": "לאפל יש את האייפון שמונה עשרה פרו עם סוללה גדולה יותר; לסמסונג מסך בהיר יותר.", "announced": True,
        "usage": {"input_tokens": 5200, "output_tokens": 410}}, ensure_ascii=False) + "\n", encoding="utf-8")
    mem = MemoryStore(home / "memory.db")
    mem.add("הצבע האהוב על המשתמש: כחול")
    mem.add("שם המשתמש: דנה", source="auto", session="20261019-171402-ab12cd", confidence=0.9, kind="identity")
    mem.add("לכלב של המשתמש קוראים רקס", source="auto", session="20261019-171402-ab12cd", confidence=0.85, kind="relation")
    mem.close()
    ws = Workspace(home / "workspace")
    ws.write("USER.md", ws.read("USER.md").rstrip() + "\n- קוראים לי דנה; תפנה אליי בלשון נקבה.\n")
    (home / "config.toml").write_text("[voice]\nidle_disconnect_s = 180\n", encoding="utf-8")
    return sid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv2_dashboard"))
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
        os.environ.pop("OPENAI_API_KEY", None)          # never a paid call from here
        sid = fill(home)
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        d = Dashboard(home, Config()).start()
        try:
            for name, frag, theme in PAGES:
                png = out / f"wph_{name}_{theme}.png"
                cmd = [a.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                       f"--user-data-dir={Path(tmp) / 'browser'}", "--window-size=1400,1000",
                       "--virtual-time-budget=6000", f"--screenshot={png}"]
                # prefers-color-scheme for the page (the OS theme must not decide): 0 = dark, 1 = light
                cmd.append("--force-dark-mode" if theme == "dark" else "--blink-settings=preferredColorScheme=1")
                cmd.append(d.url + frag.format(sid=sid))
                subprocess.run(cmd, check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(("ok   " if png.exists() else "FAIL ") + str(png))
        finally:
            d.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
