"""SILENT "is memory working?" check against the REAL GPT-Live (no speakers, no microphone).

Session 1: the user mentions facts IN PASSING (no "remember"), the Core shuts down -> the curator stores them.
Session 2: a fresh Core on the same home is asked about them; the spoken answer must contain them.
Uses tools/silent_live.py's virtual mic/speaker. COSTS MONEY: two short sessions (~$0.05-0.15 in total).

    uv run python tools/silent_memory.py [--home DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import silent_live as sl  # noqa: E402

TELL = "אגב, קוראים לי איגל, והכלב שלי נקרא רקס."
ASK = "איך קוראים לכלב שלי?"
EXPECT_ANY = ("רקס",)


async def session(home: Path, text: str, after_s: float = 0.0) -> dict:
    import jarves.app as app_mod
    from jarves.app import Core
    from jarves.config import load_config
    from jarves.voice.audio import SimAudio
    from jarves.voice.tts import WinRTTTS
    app_mod.StreamingAudio = sl.VirtualAudio
    sl.VirtualAudio.play_log.clear()
    cfg, _ = load_config()
    core = Core(cfg, home, audio=SimAudio(), console_logs=False, use_real_audio=True, stt=sl._NoSTT(), tts=sl._NoTTS())
    await core.start()
    await core.prepare_desktop()
    face = sl.FaceClient(home, cfg.link.port)
    await face.start()
    t = time.time()
    while not sl.VirtualAudio.play_log and time.time() - t < 20:
        await asyncio.sleep(0.05)
    await sl.quiet(1.5, 40, core)
    samples, rate = await WinRTTTS("he-IL").synth(text)
    mark = len(sl.events(home))
    for r in (16000, 24000):
        sl.VirtualAudio.inject[r] += sl.to_pcm(samples, rate, r)
    while sl.VirtualAudio.inject[24000]:
        await asyncio.sleep(0.05)
    await asyncio.sleep(1.0)
    await sl.quiet(3.0, 60, core)
    if after_s:                                  # e.g. a voice change restarts the session and greets again
        await asyncio.sleep(after_s)
        await sl.quiet(2.0, 30, core)
    ev = sl.events(home)[mark:]
    row_extra = {"voice_changed": [e.get("voice") for e in ev if e["code"] == "VOICE_CHANGED"],
                 "sessions_started": sum(1 for e in ev if e["code"] == "LIVE_STARTED")}
    row = {"heard": next((e.get("text") for e in ev if e["code"] == "LIVE_USER_SAID"), None),
           "said": " / ".join(e.get("text", "") for e in ev if e["code"] == "LIVE_JARVES_SAID"),
           "tools": [e["msg"] for e in ev if e["code"] == "LIVE_TOOL_CALL"], **row_extra}
    await face.stop()
    await core.stop()                       # session end -> curator
    row["curated"] = [e.get("facts") for e in sl.events(home) if e["code"] == "CURATOR_DONE"]
    return row


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home")
    ap.add_argument("--voice-check", action="store_true",
                    help="only: ask (in Hebrew) for another voice; expect set_voice + a restarted session")
    args = ap.parse_args()
    args.home = args.home or str(Path(sl.os.environ.get("TEMP", ".")) / "jarves-silent-memory")
    for p in ("memory.db", "memory.db-wal", "memory.db-shm", "workspace"):   # start from an empty memory
        q = Path(args.home) / p
        shutil.rmtree(q, ignore_errors=True) if q.is_dir() else q.unlink(missing_ok=True)
    args.turns = args.idle_s = None
    home = sl.bootstrap(args)
    if args.voice_check:
        s = await session(home, "תחליף את הקול שלך לקול מרידיאן", after_s=8)
        print("voice check:", s, flush=True)
        ok = s["voice_changed"] == ["meridian"] and s["sessions_started"] >= 1
        print("RESULT:", "PASS - voice changed and session restarted" if ok else "FAIL", flush=True)
        sys.exit(0 if ok else 1)
    s1 = await session(home, TELL)
    print("session 1:", s1, flush=True)
    db = sqlite3.connect(home / "memory.db")
    facts = [r for r in db.execute("SELECT text, source, confidence, session FROM memories")]
    db.close()
    print("stored:", facts, flush=True)
    s2 = await session(home, ASK)
    print("session 2:", s2, flush=True)
    # the synthetic voice's name may be heard differently (run 2 heard "בונזו" as "בלזו"): what counts is that
    # session 2 says the name session 1 STORED
    stored_names = [t.split()[-1] for t, *_ in facts if t.startswith("לכלב")]
    ok = any(x in s2["said"] for x in (*EXPECT_ANY, *stored_names))
    print("RESULT:", "PASS - recalled in a new session" if ok else "FAIL - not recalled", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
