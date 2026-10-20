"""SILENT long-conversation test against the REAL GPT-Live (no speakers, no microphone, safe at night).

The user's questions are synthesised locally (Windows voice, never played) and INJECTED as audio into the live
session through a virtual microphone; JARVES's spoken answers are measured through a virtual speaker (paced in
real time, never played). Everything else is real: Core, GPT-Live, backend model + web search, Skills, budget.
A fake face client connects to the Core link and records face.emotion / assistant.activity messages.

Measures per turn: DEAD AIR (question end -> first sound), pauses INSIDE the answer, answer length, what was
heard/said, tools, emotions, activity kinds. COSTS MONEY (about $0.05 per voice minute + backend tokens).

    uv run python tools/silent_live.py [--turns 1,2,3] [--idle-check] [--idle-s 25] [--max-minutes 12] [--home DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import tomllib
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np

REAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/state"))) / "jarves"

SCRIPT = [  # (label, Hebrew question, what it exercises)
    ("smalltalk", "שלום, מה שלומך היום?", "small talk"),
    ("time", "מה השעה עכשיו?", "get_time tool"),
    ("weather", "מה מזג האוויר היום בתל אביב?", "web search"),
    ("facts", "מי ראש הממשלה של בריטניה כרגע?", "web search / facts"),
    ("joke", "תספר לי בדיחה קצרה", "laugh reaction"),
    ("remember", "תזכור שהצבע האהוב עליי הוא כחול", "memory write"),
    ("recall", "מה אתה זוכר עליי?", "memory read"),
    ("sad", "אני קצת עצוב היום, הכלב שלי חולה", "cry reaction"),
    ("news", "מה החדשות הגדולות היום?", "web search"),
    ("math", "כמה זה שלוש מאות ארבעים ושבע כפול שמונה עשרה?", "reasoning"),
    ("thanks", "תודה רבה, אתה מדהים", "love reaction"),
    ("context", "מה אמרתי לך קודם על הצבע האהוב עליי?", "context kept in ONE session"),
]


def bootstrap(args) -> Path:
    home = Path(args.home or (Path(os.environ.get("TEMP", ".")) / "jarves-silent-live"))
    home.mkdir(parents=True, exist_ok=True)
    from jarves.config import load_secrets
    load_secrets(REAL)                                           # key stays in the environment, never printed
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("no OPENAI_API_KEY (run: jarvis key)")
    os.environ["JARVES_HOME"] = str(home)
    cfg = tomllib.loads((REAL / "config.toml").read_text(encoding="utf-8")) if (REAL / "config.toml").exists() else {}
    v = cfg.setdefault("voice", {})
    v.update(activation="conversation", wake_enabled=False, chime=False)
    if args.idle_s:
        v["idle_disconnect_s"] = max(30, int(args.idle_s))      # config minimum; a shorter test value is set below
    cfg["link"] = {"port": 8793}
    cfg["music"] = {"provider": "none"}
    cfg["log"] = {"level": "VERBOSE", "privacy": False}
    lines = []
    for sec, kv in cfg.items():
        lines.append(f"[{sec}]")
        lines += [f"{k} = " + (json.dumps(x) if isinstance(x, str) else str(x).lower() if isinstance(x, bool) else str(x))
                  for k, x in kv.items()]
        lines.append("")
    (home / "config.toml").write_text(chr(10).join(lines), encoding="utf-8")
    log = home / "logs" / "jarves.log"
    if log.exists():
        log.unlink()
    return home


class VirtualAudio:
    """Drop-in for StreamingAudio: mic = injected speech (+ faint noise), speaker = measured, paced, never played."""
    inject: dict[int, bytearray] = {16000: bytearray(), 24000: bytearray()}
    play_log: list[tuple[float, float, float]] = []             # (t_start, duration_s, rms)
    last_play = 0.0

    def __init__(self, *a, **k) -> None:
        self._q: deque[bytes] = deque()
        self._bytes = 0
        self._ready = asyncio.Event()
        self._open = True
        self._rng = np.random.default_rng(1)

    async def mic_chunks(self, rate: int, chunk_bytes: int):
        buf = VirtualAudio.inject[rate]
        while self._open:
            await asyncio.sleep(chunk_bytes / 2 / rate)
            take = bytes(buf[:chunk_bytes])
            del buf[:chunk_bytes]
            a = np.frombuffer(take.ljust(chunk_bytes, b"\0"), "<i2").astype(np.int32)
            if len(take) < chunk_bytes:                          # room noise where there is no speech
                a = a + self._rng.integers(-6, 7, len(a))
            yield np.clip(a, -32768, 32767).astype("<i2").tobytes()

    def enqueue(self, pcm: bytes) -> None:
        if self._open and pcm:
            self._q.append(pcm)
            self._bytes += len(pcm)
            self._ready.set()

    def flush(self) -> int:
        n = self._bytes
        self._q.clear()
        self._bytes = 0
        return n

    def queued_s(self) -> float:
        return self._bytes / 2 / 24000

    async def run_player(self, rate: int) -> None:
        while self._open:
            if not self._q:
                self._ready.clear()
                await self._ready.wait()
                continue
            pcm = self._q.popleft()
            self._bytes -= len(pcm)
            for i in range(0, len(pcm), rate // 10 * 2):         # 100 ms pieces, paced in real time
                piece = pcm[i:i + rate // 10 * 2]
                a = np.frombuffer(piece, "<i2").astype(np.float32) / 32768.0
                now = time.time()
                VirtualAudio.play_log.append((now, len(piece) / 2 / rate, float(np.sqrt(np.mean(a * a)))))
                VirtualAudio.last_play = now
                await asyncio.sleep(len(piece) / 2 / rate)

    def stats(self) -> dict:
        return {"mic_drops": 0, "mic_max_depth_s": 0, "speaker_drops": 0, "speaker_dropped_s": 0, "speaker_max_depth_s": 0,
                "speaker_depth_s": round(self.queued_s(), 2), "speaker_flushes": 0, "speaker_flushed_s": 0}

    def close(self) -> None:
        self._open = False
        self._ready.set()


def to_pcm(samples: np.ndarray, src_rate: int, dst_rate: int) -> bytes:
    n = int(len(samples) * dst_rate / src_rate)
    out = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    out = out / max(1e-6, float(np.abs(out).max())) * 0.6        # a normal speaking level
    return (out * 32767).astype("<i2").tobytes()


class FaceClient:
    """Stands in for the Godot face: connects with the link token, records every message."""

    def __init__(self, home: Path, port: int) -> None:
        self.home, self.port, self.msgs, self._task = home, port, [], None

    async def start(self) -> None:
        from websockets.asyncio.client import connect
        self.ws = await connect(f"ws://127.0.0.1:{self.port}")
        token = (self.home / "link_token").read_text(encoding="utf-8").strip()
        await self.ws.send(json.dumps({"type": "hello", "payload": {"token": token, "client": "godot", "protocol": 1}}))
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                self.msgs.append((time.time(), m["type"], m.get("payload") or {}))
        except Exception:
            pass

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.ws.close()


class _NoSTT:
    loaded_name = "none"

    async def transcribe(self, audio):
        return ""


class _NoTTS:
    def check(self):
        return True, "cloud", "silent test"

    async def synth(self, text):
        return np.zeros(2400, np.float32), 24000


def events(home: Path) -> list[dict]:
    try:
        return [json.loads(l) for l in (home / "logs" / "jarves.log").read_text(encoding="utf-8").splitlines()]
    except FileNotFoundError:
        return []


def stamp(e: dict) -> float:
    return datetime.fromisoformat(e["ts"]).timestamp()


def segments(t0: float, t1: float, gap: float = 0.25) -> list[tuple[float, float]]:
    """Merge the virtual player's 100 ms pieces into continuous speech segments within [t0, t1]."""
    segs: list[list[float]] = []
    for t, dur, rms in VirtualAudio.play_log:
        if t < t0 or t > t1 or rms < 0.004:
            continue
        if segs and t - segs[-1][1] <= gap:
            segs[-1][1] = t + dur
        else:
            segs.append([t, t + dur])
    return [(a, b) for a, b in segs]


async def quiet(sec: float, timeout: float, core) -> None:
    t = time.time()
    while time.time() - t < timeout:
        busy = bool(getattr(core.live, "_backend_busy", False)) if core.live else False
        if time.time() - VirtualAudio.last_play >= sec and not busy and core.live is not None:
            return
        await asyncio.sleep(0.1)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", help="comma separated 1-based turn numbers (default: all)")
    ap.add_argument("--idle-check", action="store_true", help="after the turns, stay silent and expect an idle disconnect")
    ap.add_argument("--idle-s", type=float, default=0, help="idle_disconnect_s for the test (default: your config)")
    ap.add_argument("--max-minutes", type=float, default=12, help="stop when this much live voice time has passed")
    ap.add_argument("--home")
    ap.add_argument("--json", help="write the per-turn results here")
    args = ap.parse_args()
    home = bootstrap(args)

    import jarves.app as app_mod
    from jarves.app import Core
    from jarves.config import load_config
    from jarves.voice.audio import SimAudio
    from jarves.voice.tts import WinRTTTS
    app_mod.StreamingAudio = VirtualAudio                       # the live session uses the virtual mic/speaker
    cfg, _ = load_config()
    if args.idle_s:                                             # test-only: may be below the config minimum (30 s)
        object.__setattr__(cfg.voice, "idle_disconnect_s", int(args.idle_s))
    he = WinRTTTS("he-IL")
    picked = [int(x) for x in args.turns.split(",")] if args.turns else list(range(1, len(SCRIPT) + 1))

    t_start = time.time()
    core = Core(cfg, home, audio=SimAudio(), console_logs=False, use_real_audio=True, stt=_NoSTT(), tts=_NoTTS())
    await core.start()
    await core.prepare_desktop()
    face = FaceClient(home, cfg.link.port)
    await face.start()                                           # -> Core calls live.present(): greeting plays (virtual)
    # The greeting only starts playing AFTER present(). Wait for it to start AND finish: a user talking over it is
    # gated out by the half-duplex echo guard (the turn-1 "no answer" of the first silent runs).
    t_wait = time.time()
    while not VirtualAudio.play_log and time.time() - t_wait < 20:
        await asyncio.sleep(0.05)
    await quiet(1.5, 40, core)
    greet = events(home)
    print(f"\nconnected in {time.time() - t_start:.1f} s; session open: {core.live is not None}")
    results = []
    for n in picked:
        label, text, what = SCRIPT[n - 1]
        if (time.time() - t_start) / 60 > args.max_minutes:
            print("max minutes reached, stopping")
            break
        samples, rate = await he.synth(text)
        mark, fmark = len(events(home)), len(face.msgs)
        for r in (16000, 24000):
            VirtualAudio.inject[r] += to_pcm(samples, rate, r)
        t_inj = time.time()
        while VirtualAudio.inject[24000]:
            await asyncio.sleep(0.05)
        t_q_end = time.time()
        first = None
        while time.time() - t_q_end < 60:
            first = next((t for t, d, r in VirtualAudio.play_log if t >= t_q_end - 0.2 and r >= 0.004), None)
            if first:
                break
            await asyncio.sleep(0.05)
        await quiet(2.5, 90, core)
        for _ in range(3):        # a backend answer finished after the last sound: wait for it to be spoken
            done = [stamp(e) for e in events(home)[mark:] if e["code"] == "LIVE_BACKEND_DONE"]
            if not done or VirtualAudio.last_play > max(done):
                break
            t_w = time.time()
            while VirtualAudio.last_play <= max(done) and time.time() - t_w < 15:
                await asyncio.sleep(0.1)
            await quiet(2.5, 90, core)
        t_done = VirtualAudio.last_play
        ev = events(home)[mark:]
        segs = segments(t_q_end - 0.2, t_done + 0.5)
        pauses = [round(b2 - a1, 1) for (_, a1), (b2, _) in zip(segs, segs[1:]) if b2 - a1 >= 1.0]
        acts = [m for m in face.msgs[fmark:] if m[1] == "assistant.activity" and m[2].get("kind") not in (None, "done")]
        emos = [m[2].get("emotion") for m in face.msgs[fmark:] if m[1] == "face.emotion"]
        row = {
            "turn": n, "what": what, "asked": text,
            "dead_air_s": round(first - t_q_end, 2) if first else None,
            "answer_s": round(t_done - first, 1) if first else None,
            "pauses_inside_answer_s": pauses,
            "heard_as": next((e.get("text") for e in ev if e["code"] == "LIVE_USER_SAID"), None),
            "jarves_said": " / ".join(e.get("text", "") for e in ev if e["code"] == "LIVE_JARVES_SAID")[:160],
            "tools": [e["msg"].replace("backend called ", "") for e in ev if e["code"] == "LIVE_TOOL_CALL"],
            "delegations": sum(1 for e in ev if e["code"] == "LIVE_EVENT" and e["msg"] == "session.delegation.created"),
            "backend_s": [e.get("backend_s") for e in ev if e["code"] == "LIVE_BACKEND_DONE"],
            "speech_segments_s": [[round(a - t_q_end, 1), round(b - t_q_end, 1)] for a, b in segs],
            "activity": sorted({m[2].get("kind") for m in acts}),
            "emotions": emos,
            "session_still_open": core.live is not None,
        }
        results.append(row)
        print(f"\n#{n} [{what}] asked: {text}\n   dead air {row['dead_air_s']} s | answer {row['answer_s']} s | "
              f"pauses {pauses} | open {row['session_still_open']}\n   heard: {row['heard_as']}\n   said: {row['jarves_said']}\n"
              f"   tools {row['tools']} | delegations {row['delegations']} backend_s {row['backend_s']} | "
              f"screen {row['activity']} | face {emos}\n   speech segments {row['speech_segments_s']}", flush=True)
        await asyncio.sleep(1.5)

    idle = None
    if args.idle_check:
        t_idle = time.time()
        print(f"\nidle check: staying silent (idle_disconnect_s={core.live.idle_disconnect_s if core.live else '?'}) ...")
        while core.live is not None and time.time() - t_idle < (args.idle_s or 180) + 40:
            await asyncio.sleep(0.5)
        idle = {"disconnected_after_s": round(time.time() - t_idle, 1) if core.live is None else None}
        print("idle result:", idle)

    snap = core.snapshot()
    ended = [e for e in events(home) if e["code"] == "LIVE_ENDED"]
    summary = {
        "turns": len(results), "voice_minutes": snap.get("voice_minutes_today"), "ai_requests": snap.get("ai_requests_today"),
        "session_wall_minutes": round((time.time() - t_start) / 60, 2),   # upper bound of billed voice time so far
        "progress_notes": sum(1 for e in events(home) if e["code"] == "LIVE_PROGRESS"),
        "disconnects_during_turns": sum(1 for r in results if not r["session_still_open"]),
        "dead_air_avg_s": round(float(np.mean([r["dead_air_s"] for r in results if r["dead_air_s"]])), 2) if results else None,
        "dead_air_max_s": max([r["dead_air_s"] for r in results if r["dead_air_s"]], default=None),
        "turns_with_pauses_over_1s": sum(1 for r in results if r["pauses_inside_answer_s"]),
        "live_ended_events": [e["msg"] for e in ended], "idle": idle,
        "warnings_errors": [(e["code"], e["msg"][:80]) for e in events(home) if e["level"] in ("ERROR", "WARNING")][:6],
    }
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"{k:30} {v}")
    if args.json:
        Path(args.json).write_text(json.dumps({"turns": results, "summary": summary}, ensure_ascii=False, indent=1), encoding="utf-8")
    await face.stop()
    await core.stop()


if __name__ == "__main__":
    asyncio.run(main())
