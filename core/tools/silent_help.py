"""SILENT check that JARVES knows his own features and guides the user (SW-V2 WP-L), against the REAL GPT-Live (no
speakers, no microphone; tools/silent_live.py's virtual mic/speaker). ONE short session, two questions:
"איך אני מחבר את גוגל?" (expects the Google setup path: jarvis google start / Connections, from the guides) and
"מה אתה יכול לעשות?" (expects a few real features). The throw-away home has no Google sign-in, so Google is truthfully
"needs setup". COSTS MONEY: about 1-2 voice minutes + 1-2 backend calls (~$0.05-0.2).

    uv run python tools/silent_help.py [--home DIR] [--json FILE]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import silent_live as sl  # noqa: E402

QUESTIONS = [
    ("google", "איך אני מחבר את גוגל?", ("google start", "גוגל סטארט", "ג'ארביס גוגל", "חיבורים", "connections",
                                          "טרמינל", "jarvis google")),
    # run 1 (19 Oct) answered "...מידע עדכני, לזכור דברים..., קבצים והגדרות – כולל חיבור גוגל" and failed only on
    # this list (it had "תזכור", not "לזכור"); widened after that run
    ("abilities", "מה אתה יכול לעשות?", ("מוזיק", "זכור", "חיפוש", "פתק", "שיר", "יומן", "חדשות", "מזג", "מידע",
                                          "תזכורת", "קבצים")),
]


async def run(home: Path) -> dict:
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
    rows = []
    tts = WinRTTTS("he-IL")
    for label, text, expect in QUESTIONS:
        samples, rate = await tts.synth(text)
        mark = len(sl.events(home))
        for r in (16000, 24000):
            sl.VirtualAudio.inject[r] += sl.to_pcm(samples, rate, r)
        while sl.VirtualAudio.inject[24000]:
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        await sl.quiet(3.0, 75, core)
        ev = sl.events(home)[mark:]
        said = " / ".join(e.get("text", "") for e in ev if e["code"] == "LIVE_JARVES_SAID")
        tools = [e.get("tool") or e["msg"] for e in ev if e["code"] in ("LIVE_TOOL_CALL", "TOOL_RESULT")]
        ok = any(x.lower() in said.lower() for x in expect)
        rows.append({"label": label, "asked": text,
                     "heard": next((e.get("text") for e in ev if e["code"] == "LIVE_USER_SAID"), None),
                     "said": said, "tools": tools, "pass": ok})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    await face.stop()
    await core.stop()
    return {"rows": rows, "pass": all(r["pass"] for r in rows)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home")
    ap.add_argument("--json")
    args = ap.parse_args()
    args.home = args.home or str(Path(sl.os.environ.get("TEMP", ".")) / "jarves-silent-help")
    for p in ("google_token.json", "google_oauth.json", "google", "guides_state.json"):   # truthfully not connected
        q = Path(args.home) / p
        shutil.rmtree(q, ignore_errors=True) if q.is_dir() else q.unlink(missing_ok=True)
    args.turns = args.idle_s = None
    home = sl.bootstrap(args)
    res = await run(home)
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RESULT:", "PASS" if res["pass"] else "FAIL", flush=True)
    sys.exit(0 if res["pass"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
