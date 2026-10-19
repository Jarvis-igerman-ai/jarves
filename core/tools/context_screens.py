"""Headless screenshots of the dashboard's Context page (no network, no sound, no paid call).

A throw-away data dir gets (a) a copy of a REAL captured conversation if --real HOME is given (store.db, memory.db,
workspace of e.g. a tools/silent_live.py run) and (b) a synthetic offline conversation that exercises every part
of the page: clock note, tool result, background-task note, workspace change, facts learned. Headless Edge/Chrome
captures each view.

    python tools/context_screens.py [--real DIR] [--out ../docs/reviews/swv2_context] [--browser PATH]
"""
from __future__ import annotations

import argparse
import asyncio
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
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", "/usr/bin/chromium", "/usr/bin/google-chrome"]


async def synthetic(home: Path) -> str:
    """An offline conversation through the real LiveVoice + capture hooks (fake connection, no network)."""
    from jarves import context_trace as ct
    from jarves import features, logs
    from jarves import workspace as wsp
    from jarves.config import Config
    from jarves.memory import MemoryStore
    from jarves.skills import SkillRegistry
    from jarves.skills.builtin import ClockSkill, MemorySkill
    from jarves.store import Store
    from jarves.voice.live import LiveVoice, clock_note
    sys.path.insert(0, str(ROOT / "tests"))
    from test_context_trace import FakeConn
    logs.set_privacy(False)
    mem = MemoryStore(home / "memory.db")
    mem.add("הצבע האהוב עליי כחול")
    mem.add("שם המשתמש: דנה", source="auto", session="20261019-171402-ab12cd", confidence=0.9)
    ws = wsp.Workspace(home / "workspace")
    ws.write("USER.md", "- קוראים לי דנה; תפנה אליי בלשון נקבה.\n- גרה בחיפה.", by="user")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    reg.register(MemorySkill(mem))
    feats = features.Features(Config(), home)
    lv = LiveVoice(reg, lambda: "You are JARVES, the backend.\nFacts the user asked you to remember:\n" + "\n".join(
        f"- {m['text']}" for m in mem.list(20)), on_end=None)
    wsp.attach_live(lv, ws, mem)
    features.attach_live(lv, feats)
    ct.attach_live(lv, home=home, cfg=Config(), memory=mem, workspace=ws, registry=reg, feats=feats)
    t0 = time.time() - 120
    lv.conn, lv.running = FakeConn(), True
    lv.session_config()
    st = Store(home / "store.db")

    def log(code, dt, **f):
        st.ingest({"ts": t0 + dt, "code": code, "session": logs._SESSION, **f})
    tr = lv._ctx_trace
    tr.started = t0 - 1
    ct.recorder(home).put("UPDATE context_traces SET started = ? WHERE id = ?", (t0 - 1, tr.id))
    log("LIVE_STARTED", 0, msg="GPT-Live session started (gpt-live-1 -> gpt-6-luna)")
    log("LIVE_USER_SAID", 4, text="מה השעה?")
    tr.event("clock", "Clock note", clock_note(), ts=t0 + 1)
    log("LIVE_JARVES_SAID", 6, text="השעה עשר וחצי בלילה.", wait_s=0.8)
    log("LIVE_USER_SAID", 12, text="תזכור שיש לי כלב בשם רקס")
    log("LIVE_TOOL_CALL", 13, msg="backend called remember", tool="remember")
    tr.calls["c1"] = ("remember", json.dumps({"text": "יש לי כלב בשם רקס"}))
    mem.add("יש לי כלב בשם רקס")
    tr.event("tool_result", "Tool result: remember (ok)", json.dumps(
        {"ok": True, "message": "זכרתי: יש לי כלב בשם רקס", "code": "ok"}, ensure_ascii=False),
        data={"tool": "remember", "ok": True, "code": "ok", "args": '{"text": "יש לי כלב בשם רקס"}'}, ts=t0 + 13.4)
    st.ingest({"ts": t0 + 13.4, "code": "TOOL_RESULT", "tool": "remember", "ok": True, "msg": "remember -> ok",
               "session": logs._SESSION})
    log("LIVE_JARVES_SAID", 15, text="רשמתי לי שיש לך כלב בשם רקס.", wait_s=1.6)
    ws.write("USER.md", "- קוראים לי דנה; תפנה אליי בלשון נקבה.\n- גרה בתל אביב.", by="user")
    tr.event("workspace", "Workspace changed: USER.md", "[workspace update: USER.md changed. From now on follow this]\n"
             + ws.section("front"), block=ct.WS_NOTE_BLOCK, ts=t0 + 40)
    log("LIVE_USER_SAID", 50, text="איפה אני גרה?")
    log("LIVE_JARVES_SAID", 52, text="את גרה בתל אביב.", wait_s=0.9)
    tr.event("task", "Background-task result announced", "[task] The research finished. Result: מחיר המניה עלה בשלושה "
             "אחוזים היום.", data={"task": "a1b2"}, ts=t0 + 70)
    log("LIVE_JARVES_SAID", 72, text="אגב, המחקר הסתיים: המניה עלתה בשלושה אחוזים היום.")
    await lv.on_end("user")
    log("VOICE_ENDED", 90, reason="user", voice_seconds=90)
    st.commit()
    ct.recorder(home).flush()
    sid = [s["id"] for s in st.sessions() if s["started"] >= t0 - 1][0]
    st.close()
    mem.close()
    return sid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "reviews" / "swv2_context"))
    ap.add_argument("--real", help="data dir of a real captured conversation (copied, never changed)")
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
        real_sid = None
        if a.real:
            for p in Path(a.real).glob("store.db*"):
                shutil.copy(p, home / p.name)
            from jarves.store import Store
            st = Store(home / "store.db")
            real_sid = next((s["id"] for s in st.sessions() if s["kind"] == "live"), None)
            st.close()
        os.environ["JARVES_HOME"] = str(home)
        os.environ.pop("OPENAI_API_KEY", None)                 # never a paid call from here
        sid = asyncio.run(synthetic(home))
        from jarves import context_trace as ct
        ct.close_all()
        from jarves.config import Config
        from jarves.dashboard import Dashboard
        d = Dashboard(home, Config()).start()
        d.access.rate_ok = lambda ip: True        # many page loads in a row from one script (screenshots only)
        pages =[("transcript", f"#/session/{sid}", "light", 1000), ("blocks", f"#/context/{sid}", "light", 1500),
                 ("blocks_search", f"#/context/{sid}?q=" + "רקס", "dark", 1300),
                 ("timeline", f"#/context/{sid}?tab=timeline", "light", 1500),
                 ("diff", f"#/context/{sid}?tab=diff", "dark", 1400), ("why", f"#/context/{sid}?tab=why&turn=4", "light", 1500),
                 ("tools", f"#/context/{sid}?tab=tools", "light", 1100), ("memory", f"#/context/{sid}?tab=memory", "dark", 900),
                 ("workspace_used", "#/workspace/USER.md", "light", 1700), ("phone", f"#/context/{sid}", "light", 900)]
        if real_sid:
            pages += [("real_blocks", f"#/context/{real_sid}", "light", 1700),
                      ("real_why", f"#/context/{real_sid}?tab=why&turn=3", "light", 1300),
                      ("real_transcript", f"#/session/{real_sid}", "dark", 1000)]
        try:
            for name, frag, theme, height in pages:
                png = out / f"context_{name}_{theme}.png"
                width = 420 if name == "phone" else 1400
                cmd = [a.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                       f"--user-data-dir={Path(tmp) / 'browser'}", f"--window-size={width},{height}",
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
