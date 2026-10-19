"""SILENT background-task + stalled-lookup test against the REAL GPT-Live (no speakers, no microphone).

Reuses tools/silent_live.py (virtual mic/speaker, fake face client). Scenario:
  1. ask for a slow RESEARCH -> the backend starts a background task (task_start)
  2. keep chatting meanwhile (a joke, small talk) -> answers must not wait for the task
  3. the task finishes -> its result is announced at a quiet moment (LIVE_ANNOUNCE) and spoken; the face gets
     tasks.update messages
  4. (--stall) a web lookup with a forced 1 s deadline -> LIVE_BACKEND_STALL, then the Core retry or the late
     original answer; no endless "עוד רגע".
COSTS MONEY: about $0.05 per voice minute + backend tokens (~3-5 minutes).

    uv run python tools/silent_tasks.py [--stall] [--wait 150]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import silent_live as sl                                             # noqa: E402

RESEARCH = ("תעשה לי מחקר ברקע: תשווה בין שלושת הטלפונים החדשים של אפל, סמסונג וגוגל, ותגיד לי מה ההבדלים "
            "העיקריים ביניהם")
CHAT = ["תספר לי בדיחה קצרה", "מה שלומך היום?"]
STALL_Q = "מה מזג האוויר בירושלים עכשיו?"


async def say(he, text: str, core, home) -> dict:
    """Inject one question, wait for the answer to finish; returns what happened."""
    samples, rate = await he.synth(text)
    mark = len(sl.events(home))
    for r in (16000, 24000):
        sl.VirtualAudio.inject[r] += sl.to_pcm(samples, rate, r)
    while sl.VirtualAudio.inject[24000]:
        await asyncio.sleep(0.05)
    t_end = time.time()
    first = None
    while time.time() - t_end < 40:
        first = next((t for t, d, r in sl.VirtualAudio.play_log if t >= t_end - 0.2 and r >= 0.004), None)
        if first:
            break
        await asyncio.sleep(0.05)
    await sl.quiet(2.5, 60, core)
    ev = sl.events(home)[mark:]
    row = {"asked": text, "dead_air_s": round(first - t_end, 2) if first else None,
           "said": " / ".join(e.get("text", "") for e in ev if e["code"] == "LIVE_JARVES_SAID")[:220],
           "codes": sorted({e["code"] for e in ev if e["code"].startswith(("TASK_", "LIVE_BACKEND_STALL",
                                                                              "LIVE_BACKEND_RETRY", "LIVE_BACKEND_LATE",
                                                                              "LIVE_ANNOUNCE", "LIVE_TOOL_CALL"))})}
    print(f"\n> {text}\n  dead air {row['dead_air_s']} s\n  said: {row['said']}\n  {row['codes']}", flush=True)
    return row


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stall", action="store_true", help="also force a 1 s backend deadline on one web lookup")
    ap.add_argument("--wait", type=float, default=150, help="max seconds to wait for the task announcement")
    ap.add_argument("--home")
    ap.add_argument("--port", type=int, default=8797)
    args = ap.parse_args()
    import os
    home = sl.bootstrap(SimpleNamespace(home=args.home or str(Path(os.environ.get("TEMP", ".")) / "jarves-silent-tasks"),
                                        idle_s=0))
    conf = home / "config.toml"                      # own port: another silent run may hold silent_live's 8793
    conf.write_text(conf.read_text(encoding="utf-8").replace("port = 8793", f"port = {args.port}"), encoding="utf-8")
    import jarves.app as app_mod
    from jarves.app import Core
    from jarves.config import load_config
    from jarves.voice.audio import SimAudio
    from jarves.voice.tts import WinRTTTS
    app_mod.StreamingAudio = sl.VirtualAudio
    cfg, _ = load_config()
    he = WinRTTTS("he-IL")
    t0 = time.time()
    core = Core(cfg, home, audio=SimAudio(), console_logs=False, use_real_audio=True, stt=sl._NoSTT(), tts=sl._NoTTS())
    await core.start()
    await core.prepare_desktop()
    face = sl.FaceClient(home, cfg.link.port)
    await face.start()
    while not sl.VirtualAudio.play_log and time.time() - t0 < 30:
        await asyncio.sleep(0.05)
    await sl.quiet(1.5, 40, core)
    print(f"connected in {time.time() - t0:.1f} s; tasks enabled: {core.tasks is not None}")
    rows = [await say(he, RESEARCH, core, home)]
    for q in CHAT:
        rows.append(await say(he, q, core, home))
    t_w = time.time()
    while time.time() - t_w < args.wait:
        codes = [e["code"] for e in sl.events(home)]
        if "LIVE_ANNOUNCE" in codes or any(c in codes for c in ("TASK_FAILED", "TASK_TIMEOUT")):
            break
        await asyncio.sleep(0.5)
    mark = len(sl.events(home))
    await asyncio.sleep(1.0)
    await sl.quiet(3.0, 60, core)
    ev_all = sl.events(home)
    announced = [e for e in ev_all if e["code"] == "LIVE_ANNOUNCE"]
    after = " / ".join(e.get("text", "") for e in ev_all[mark - 5:] if e["code"] == "LIVE_JARVES_SAID")[:300]
    task_codes = [(e["code"], e.get("duration_s")) for e in ev_all if e["code"].startswith("TASK_")]
    print(f"\nTASK events: {task_codes}\nannounced: {bool(announced)}\nspoken after announcement: {after}")
    if args.stall and core.live is not None:
        core.live.backend_deadline_s = 1.0
        rows.append(await say(he, STALL_Q, core, home))
        await asyncio.sleep(3)
        await sl.quiet(2.5, 40, core)
    face_tasks = [m for m in face.msgs if m[1] == "tasks.update"]
    acts = sorted({(m[2].get("kind"), m[2].get("text")) for m in face.msgs if m[1] == "assistant.activity"
                   and m[2].get("kind") not in (None, "done")}, key=str)
    snap = core.snapshot()
    print(f"\nface tasks.update messages: {len(face_tasks)}; last: {face_tasks[-1][2] if face_tasks else None}")
    print(f"screen activity texts: {acts[:20]}")
    print(f"voice minutes {snap.get('voice_minutes_today')} ai requests {snap.get('ai_requests_today')} "
          f"wall {round((time.time() - t0) / 60, 2)} min")
    print("warnings/errors:", [(e["code"], e["msg"][:70]) for e in sl.events(home) if e["level"] in ("ERROR", "WARNING")][:8])
    await face.stop()
    await core.stop()


if __name__ == "__main__":
    asyncio.run(main())
