"""PRE-USE voice test suite: numbered scenarios against the REAL GPT-Live, all SILENT and HEADLESS.

Same method as tools/silent_live.py: a virtual microphone carries locally synthesised speech (Windows WinRT voices,
never played), a virtual speaker measures JARVES's answers (paced in real time, never played), a fake face client
records every link message and answers confirmations. Real: Core, GPT-Live, backend model + web search, Skills,
budget, openWakeWord. No real microphone, speaker, Godot window or OS volume is touched.

Each scenario has a one-line description, explicit PASS/FAIL criteria and a key number; the run prints a table and
writes docs/reviews/PRE_USE_TEST_REPORT_VOICE.md. COSTS MONEY (~$0.05 per live voice minute + backend tokens);
the run stops starting new live scenarios at --max-minutes of live voice.

    uv --directory core run --no-sync python tools/suite_voice.py [--only 1,2,5-8] [--list] [--max-minutes 34]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import time
import tomllib
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np

REAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/state"))) / "jarves"
PORT = 8793
ROOT = Path(os.environ.get("TEMP", ".")) / "jarves-suite-voice"
REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "docs" / "reviews" / "PRE_USE_TEST_REPORT_VOICE.md"
SPEECH_RMS = 0.004
HE = re.compile(r"[֐-׿]")
LATIN = re.compile(r"[A-Za-z]")


# ============================================================================================ virtual audio
class VAudio:
    """Drop-in for StreamingAudio. mic = injected speech + faint room noise; speaker = measured, never played.
    Speech injected while no virtual mic is open at that rate is LOST (as in a real room), never buffered."""
    inject: dict[int, bytearray] = {16000: bytearray(), 24000: bytearray()}
    play_log: list[tuple[float, float, float]] = []
    last_play = 0.0
    instances: list["VAudio"] = []
    room_amp = 6

    @classmethod
    def reset(cls) -> None:
        cls.inject = {16000: bytearray(), 24000: bytearray()}
        cls.play_log, cls.last_play, cls.instances = [], 0.0, []

    def __init__(self, *a, rate: int = 24000, **k) -> None:
        self._q: deque[bytes] = deque()
        self._bytes = 0
        self._ready = asyncio.Event()
        self._open = True
        self.mic_rate: int | None = None
        self.closed_at: float | None = None
        self._rng = np.random.default_rng(len(VAudio.instances) + 1)
        VAudio.instances.append(self)

    @classmethod
    def open_mics(cls, rate: int) -> list["VAudio"]:
        return [a for a in cls.instances if a._open and a.mic_rate == rate]

    async def mic_chunks(self, rate: int, chunk_bytes: int):
        self.mic_rate = rate
        buf = VAudio.inject[rate]
        t_next = time.monotonic()
        while self._open:
            t_next += chunk_bytes / 2 / rate
            await asyncio.sleep(max(0.0, t_next - time.monotonic()))
            buf = VAudio.inject[rate]
            take = bytes(buf[:chunk_bytes])
            del buf[:chunk_bytes]
            a = np.frombuffer(take.ljust(chunk_bytes, b"\0"), "<i2").astype(np.int32)
            if len(take) < chunk_bytes and VAudio.room_amp:
                a = a + self._rng.integers(-VAudio.room_amp, VAudio.room_amp + 1, len(a))
            if not self._open:
                return
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
            for i in range(0, len(pcm), rate // 10 * 2):
                piece = pcm[i:i + rate // 10 * 2]
                a = np.frombuffer(piece, "<i2").astype(np.float32) / 32768.0
                now = time.time()
                VAudio.play_log.append((now, len(piece) / 2 / rate, float(np.sqrt(np.mean(a * a)))))
                VAudio.last_play = now
                await asyncio.sleep(len(piece) / 2 / rate)

    def stats(self) -> dict:
        return {"mic_drops": 0, "mic_max_depth_s": 0, "speaker_drops": 0, "speaker_dropped_s": 0,
                "speaker_max_depth_s": 0, "speaker_depth_s": round(self.queued_s(), 2), "speaker_flushes": 0,
                "speaker_flushed_s": 0}

    def close(self) -> None:
        self._open = False
        self.closed_at = time.time()
        self._ready.set()


def segments(t0: float, t1: float, gap: float = 0.25) -> list[tuple[float, float]]:
    segs: list[list[float]] = []
    for t, dur, r in VAudio.play_log:
        if t < t0 or t > t1 or r < SPEECH_RMS:
            continue
        if segs and t - segs[-1][1] <= gap:
            segs[-1][1] = t + dur
        else:
            segs.append([t, t + dur])
    return [(a, b) for a, b in segs]


def speech_s(t0: float, t1: float) -> float:
    return sum(d for t, d, r in VAudio.play_log if t0 <= t <= t1 and r >= SPEECH_RMS)


# ============================================================================================ speech synthesis
_SYN_CACHE: dict = {}


async def synth(text: str, lang: str = "he", voice: str | None = None, speed: float = 1.0) -> tuple[np.ndarray, int]:
    """WinRT speech to float samples (never played). voice = part of a display name ('Zira'); speed = rate."""
    key = (text, lang, voice, speed)
    if key in _SYN_CACHE:
        return _SYN_CACHE[key]
    from winrt.windows.media.speechsynthesis import SpeechSynthesizer
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
    from jarves.voice.tts import wav_to_np
    voices = [v for v in SpeechSynthesizer.all_voices if v.language.lower().startswith(lang)]
    if voice:
        voices = [v for v in voices if voice.lower() in v.display_name.lower()] or voices
    s = SpeechSynthesizer()
    s.voice = voices[0]
    try:
        s.options.speaking_rate = speed
    except Exception:
        pass
    stream = await s.synthesize_text_to_stream_async(text)
    buf = Buffer(stream.size)
    await stream.read_async(buf, stream.size, InputStreamOptions.NONE)
    out = wav_to_np(bytes(memoryview(buf)[: buf.length]))
    _SYN_CACHE[key] = out
    return out


def to_pcm(samples: np.ndarray, src_rate: int, dst_rate: int, level: float = 0.6) -> bytes:
    n = int(len(samples) * dst_rate / src_rate)
    out = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    out = out / max(1e-6, float(np.abs(out).max())) * level
    return (out * 32767).astype("<i2").tobytes()


def noise_track(seconds: float, rate: int, seed: int = 7) -> bytes:
    """Room tone with cough-like bursts, white-noise hiss, a door-knock click and quiet gaps (no speech)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    x = rng.normal(0, 0.002, n)                                   # quiet room tone (~-54 dBFS)
    t = 0.0
    while t < seconds - 2:
        kind = rng.choice(["cough", "hiss", "knock", "quiet"])
        i = int(t * rate)
        if kind == "cough":                                       # 2 short bursts of band-limited noise
            for off in (0.0, 0.32):
                L = int(0.22 * rate)
                b = rng.normal(0, 1, L)
                b = np.convolve(b, np.ones(6) / 6, "same")        # low-pass-ish
                env = np.exp(-np.linspace(0, 6, L)) * np.minimum(1, np.linspace(0, 40, L))
                j = i + int(off * rate)
                x[j:j + L] += b * env * 0.35
            t += 1.2
        elif kind == "hiss":
            L = int(1.5 * rate)
            x[i:i + L] += rng.normal(0, 0.04, L)
            t += 2.0
        elif kind == "knock":
            for off in (0.0, 0.25, 0.5):
                L = int(0.03 * rate)
                j = i + int(off * rate)
                x[j:j + L] += rng.normal(0, 0.3, L) * np.exp(-np.linspace(0, 8, L))
            t += 1.2
        else:
            t += 2.5
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


# ============================================================================================ fake face + helpers
class FaceClient:
    """Stands in for the Godot face: authenticates, records every message, answers confirm.request by policy."""

    def __init__(self, home: Path, port: int = PORT) -> None:
        self.home, self.port, self.msgs, self._task = home, port, [], None
        self.confirm_policy = "approve"     # approve | deny | none
        self.confirms: list[tuple[float, str, str]] = []   # (t, id, policy used)

    async def start(self) -> None:
        from websockets.asyncio.client import connect
        self.ws = await connect(f"ws://127.0.0.1:{self.port}")
        token = (self.home / "link_token").read_text(encoding="utf-8").strip()
        await self.ws.send(json.dumps({"type": "hello", "payload": {"token": token, "client": "godot", "protocol": 1}}))
        self._task = asyncio.create_task(self._read())

    async def send(self, t: str, payload: dict) -> None:
        await self.ws.send(json.dumps({"type": t, "payload": payload}))

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                p = m.get("payload") or {}
                self.msgs.append((time.time(), m["type"], p))
                if m["type"] == "confirm.request":
                    self.confirms.append((time.time(), str(p.get("id")), self.confirm_policy))
                    if self.confirm_policy in ("approve", "deny"):
                        await asyncio.sleep(0.4)
                        await self.send("confirm.reply", {"id": p.get("id"), "approved": self.confirm_policy == "approve"})
        except Exception:
            pass

    def of(self, t: str, since: float = 0.0) -> list[dict]:
        return [p for ts, ty, p in self.msgs if ty == t and ts >= since]

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


class _NoSTT:
    loaded_name = "none"

    async def transcribe(self, audio):
        return ""


class _NoTTS:
    def check(self):
        return True, "cloud", "silent test"

    async def synth(self, text):
        return np.zeros(2400, np.float32), 24000


class LogTail:
    """Incremental reader of the JSON log (rotation disabled by a large log.max_bytes)."""

    def __init__(self, path: Path) -> None:
        self.path, self.pos, self.ev, self._part = path, 0, [], b""

    def refresh(self) -> list[dict]:
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
        except FileNotFoundError:
            return self.ev
        data = self._part + data
        lines = data.split(b"\n")
        self._part = lines.pop()
        for l in lines:
            if l.strip():
                try:
                    self.ev.append(json.loads(l.decode("utf-8")))
                except ValueError:
                    pass
        return self.ev

    def since(self, mark: int) -> list[dict]:
        return self.refresh()[mark:]

    def mark(self) -> int:
        return len(self.refresh())

    def codes(self, code: str, mark: int = 0) -> list[dict]:
        return [e for e in self.since(mark) if e.get("code") == code]


def stamp(e: dict) -> float:
    return datetime.fromisoformat(e["ts"]).timestamp()


def db_usage(home: Path) -> dict:
    """Budget state straight from the SQLite file (after the Core closed it)."""
    try:
        db = sqlite3.connect(f"file:{home / 'memory.db'}?mode=ro", uri=True)
        day = date.today().isoformat()
        u = dict(db.execute("SELECT kind, count FROM usage WHERE day = ?", (day,)).fetchall())
        try:
            held = db.execute("SELECT COUNT(*), COALESCE(SUM(n),0) FROM reservations").fetchone()
        except sqlite3.Error:
            held = (0, 0)
        db.close()
        return {"ai_requests": u.get("ai_requests", 0), "voice_seconds": u.get("voice_seconds", 0),
                "reservations": held[0]}
    except sqlite3.Error as e:
        return {"error": str(e)}


def port_free(port: int = PORT) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ============================================================================================ session / harness
@dataclass
class Session:
    name: str
    core: object
    face: FaceClient
    home: Path
    log: LogTail
    t_boot: float
    t_face: float = 0.0
    greet_first: float | None = None
    greet_said: str = ""
    boot_error: str = ""
    turns: list = field(default_factory=list)


@dataclass
class Result:
    n: int
    name: str
    desc: str
    criteria: str
    ok: bool | None = None          # None = skipped
    key: str = ""
    seconds: float = 0.0
    voice_s: float = 0.0
    details: list = field(default_factory=list)
    evidence: list = field(default_factory=list)


class Harness:
    def __init__(self, args) -> None:
        self.args = args
        self.run_id = datetime.now().strftime("%m%d-%H%M%S")
        self.sessions: dict[str, Session] = {}
        self.voice_wall_s = 0.0          # conservative: wall time while any live session is open or connecting
        self.homes: list[Path] = []
        self.key = os.environ.get("OPENAI_API_KEY", "")
        self.all_face_msgs: list = []
        self._live_cores: list = []

    # ---------------------------------------------------------------- live-time meter (cost cap)
    async def meter(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if any(c.live is not None or c._voice_connecting for c in self._live_cores
                   if not c.stopping and c._live_factory is None):          # FakeLive costs nothing
                self.voice_wall_s += 0.5

    def minutes_left(self) -> float:
        return self.args.max_minutes - self.voice_wall_s / 60

    # ---------------------------------------------------------------- core lifecycle
    def write_cfg(self, home: Path, over: dict | None) -> None:
        cfg = tomllib.loads((REAL / "config.toml").read_text(encoding="utf-8")) if (REAL / "config.toml").exists() else {}
        v = cfg.setdefault("voice", {})
        v.update(activation="conversation", wake_enabled=False, chime=False, idle_disconnect_s=120)
        a = cfg.setdefault("ai", {})
        a.update(daily_realtime_minutes=90, daily_request_limit=400)
        cfg["link"] = {"port": PORT}
        cfg["music"] = {"provider": "none"}
        cfg["log"] = {"level": "VERBOSE", "privacy": False, "max_bytes": 50_000_000}
        for sec, kv in (over or {}).items():
            cfg.setdefault(sec, {}).update(kv)
        lines = []
        for sec, kv in cfg.items():
            lines.append(f"[{sec}]")
            lines += [f"{k} = " + (json.dumps(x) if isinstance(x, str) else str(x).lower() if isinstance(x, bool) else str(x))
                      for k, x in kv.items()]
            lines.append("")
        (home / "config.toml").write_text("\n".join(lines), encoding="utf-8")

    async def boot(self, name: str, over: dict | None = None, idle_s: float | None = None, live_factory=None,
                   greet: bool = True) -> Session:
        import jarves.app as app_mod
        from jarves.app import Core
        from jarves.config import load_config
        from jarves.voice.audio import SimAudio
        for other in list(self.sessions.values()):   # ONE Core at a time (one link port, one virtual audio set)
            await self.shutdown(other)
        self._boots = getattr(self, "_boots", 0) + 1
        home = ROOT / f"{self.run_id}-{self._boots:02d}-{name}"
        home.mkdir(parents=True)
        self.homes.append(home)
        self.write_cfg(home, over)
        os.environ["JARVES_HOME"] = str(home)
        app_mod.StreamingAudio = VAudio
        VAudio.reset()
        from jarves import approvals as appr          # campaign 20 Oct: a silent run never shows a (beeping) popup;
        if not getattr(appr.PopupChannel, "_silent", False):   # the real window still opens, invisible, never answers
            _init = appr.PopupChannel.__init__
            appr.PopupChannel.__init__ = lambda self, svc, **k: _init(self, svc, **{**k, "test_invoke":
                                                                                     k.get("test_invoke") or "hold"})
            appr.PopupChannel._silent = True
        cfg, _ = load_config(home / "config.toml")
        if idle_s is not None:                       # test-only: below the config minimum (30 s)
            object.__setattr__(cfg.voice, "idle_disconnect_s", int(idle_s))
        sim = SimAudio()
        t0 = time.time()
        core = Core(cfg, home, audio=sim, console_logs=False, use_real_audio=True, stt=_NoSTT(), tts=_NoTTS(),
                    live_factory=live_factory)
        core._suite_sim = sim
        self._live_cores.append(core)
        s = Session(name, core, None, home, LogTail(home / "logs" / "jarves.log"), t0)
        await core.start()
        try:
            await core.prepare_desktop()
        except Exception as e:
            s.boot_error = f"prepare_desktop failed: {type(e).__name__}: {e}"
        s.face = FaceClient(home)
        await s.face.start()
        s.t_face = time.time()
        if greet and cfg.voice.activation == "conversation" and not s.boot_error:
            while time.time() - s.t_face < 20:
                first = next((t for t, d, r in VAudio.play_log if t >= s.t_face - 0.5 and r >= SPEECH_RMS), None)
                if first:
                    s.greet_first = first
                    break
                await asyncio.sleep(0.05)
            await self.quiet(s, 1.5, 40)
            s.greet_said = " / ".join(e.get("text", "") for e in s.log.codes("LIVE_JARVES_SAID"))
        self.sessions[name] = s
        return s

    async def shutdown(self, s: Session) -> dict:
        self.all_face_msgs += [(s.name,) + m for m in s.face.msgs]
        await s.face.stop()
        await s.core.stop()
        if s.core in self._live_cores:
            self._live_cores.remove(s.core)
        self.sessions.pop(s.name, None)
        return db_usage(s.home)

    async def get(self, name: str, **kw) -> Session:
        s = self.sessions.get(name)
        if s is not None and s.core.live is not None:
            return s
        if s is not None:
            await self.shutdown(s)
        return await self.boot(name, **kw)

    # ---------------------------------------------------------------- talking
    async def quiet(self, s: Session, sec: float, timeout: float) -> None:
        t = time.time()
        while time.time() - t < timeout:
            live = s.core.live
            if live is None:
                return
            busy = (bool(getattr(live, "_backend_busy", False)) or bool(getattr(live, "_tools_running", 0))
                    # a tool that just finished: its continuation's response.created is still on the way
                    or time.monotonic() - getattr(live, "_last_activity", 0.0) < 1.0)
            if time.time() - VAudio.last_play >= sec and not busy:
                return
            await asyncio.sleep(0.1)

    async def inject(self, pcm24: bytes, pcm16: bytes) -> str:
        """Speak into whichever virtual mic is open (live 24 kHz first, then the 16 kHz wake mic).
        No mic open: the speech is lost in real time (returns "lost")."""
        rate = 24000 if VAudio.open_mics(24000) else 16000 if VAudio.open_mics(16000) else None
        dur = len(pcm24) / 2 / 24000
        if rate is None:
            await asyncio.sleep(dur)
            return "lost"
        VAudio.inject[rate] += pcm24 if rate == 24000 else pcm16
        gone_since = None
        while VAudio.inject[rate]:
            if not VAudio.open_mics(rate):
                gone_since = gone_since or time.time()
                if time.time() - gone_since > 0.3:
                    VAudio.inject[rate].clear()
                    return f"cut@{rate}"
            else:
                gone_since = None
            await asyncio.sleep(0.03)
        return str(rate)

    async def say(self, text: str, lang: str = "he", voice: str | None = None, speed: float = 1.0,
                  level: float = 0.6, pad_s: float = 0.0) -> tuple[str, float]:
        samples, rate = await synth(text, lang, voice, speed)
        if pad_s:
            samples = np.concatenate([np.zeros(int(pad_s * rate)), samples, np.zeros(int(pad_s * rate))])
        where = await self.inject(to_pcm(samples, rate, 24000, level), to_pcm(samples, rate, 16000, level))
        return where, len(samples) / rate

    async def turn(self, s: Session, text: str, lang: str = "he", quiet_s: float = 2.5, first_timeout: float = 30,
                   label: str = "") -> dict:
        mark, fmark_t = s.log.mark(), time.time()
        t_inj = time.time()
        where, dur = await self.say(text, lang)
        t_q_end = time.time()
        first = None
        while time.time() - t_q_end < first_timeout and s.core.live is not None:
            first = next((t for t, d, r in VAudio.play_log if t >= t_inj + 0.3 and r >= SPEECH_RMS), None)
            if first:
                break
            await asyncio.sleep(0.05)
        await self.quiet(s, quiet_s, 90)
        for _ in range(3):        # a backend answer finished after the last sound: wait until it is spoken
            done = [stamp(e) for e in s.log.codes("LIVE_BACKEND_DONE", mark)]
            if not done or VAudio.last_play > max(done) or s.core.live is None:
                break
            t_w = time.time()
            while VAudio.last_play <= max(done) and time.time() - t_w < 15:
                await asyncio.sleep(0.1)
            await self.quiet(s, quiet_s, 90)
        await asyncio.sleep(0.3)
        ev = s.log.since(mark)
        t_done = max(VAudio.last_play, t_q_end)
        acts = [p for p in s.face.of("assistant.activity", fmark_t) if p.get("kind") not in (None, "done")]
        row = {
            "label": label, "asked": text, "mic": where, "q_s": round(dur, 1),
            "dead_air_s": round(first - t_q_end, 2) if first else None,
            "answer_s": round(t_done - first, 1) if first else None,
            "speech_s": round(speech_s(t_inj + 0.3, t_done + 0.5), 1),
            "heard": " ".join(e.get("text", "") for e in ev if e["code"] == "LIVE_USER_SAID"),
            "said": " / ".join(e.get("text", "") for e in ev if e["code"] == "LIVE_JARVES_SAID"),
            "tools": [e["msg"].replace("backend called ", "") for e in ev if e["code"] == "LIVE_TOOL_CALL"],
            "delegations": sum(1 for e in ev if e["code"] == "LIVE_EVENT" and e["msg"] == "session.delegation.created"),
            "backend_s": [e.get("backend_s") for e in ev if e["code"] == "LIVE_BACKEND_DONE"],
            "activity": sorted({p.get("kind") for p in acts}),
            "emotions": [p.get("emotion") for p in s.face.of("face.emotion", fmark_t)],
            "face_end": s.core.dev.face_state,
            "busy_end": (bool(getattr(s.core.live, "_backend_busy", False)), getattr(s.core.live, "_tools_running", None)),
            "open": s.core.live is not None,
            "errors": [f"{e['code']}: {e['msg'][:90]}" for e in ev if e.get("level") == "ERROR"],
        }
        s.turns.append(row)
        print(f"   [{label or 'turn'}] asked: {text[:60]} | mic {where} | dead air {row['dead_air_s']} s | "
              f"answer {row['answer_s']} s | tools {row['tools']} | screen {row['activity']} | face {row['emotions']}\n"
              f"      heard: {row['heard'][:100]}\n      said:  {row['said'][:160]}", flush=True)
        return row


# ============================================================================================ checks
HOURS = {1: "אחת", 2: "שתיים", 3: "שלוש", 4: "ארבע", 5: "חמש", 6: "שש", 7: "שבע", 8: "שמונה", 9: "תשע", 10: "עשר",
         11: "אחת עשרה", 12: "שתים עשרה"}
DAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")


def has(text: str, *words) -> bool:
    """Keyword match that survives transcript pieces split mid-word ("מאת / יים") and punctuation."""
    def norm(s: str) -> str:
        return re.sub(r"[\s/.,!?\"'׳״־\-:]+", "", (s or "").lower())
    t = norm(text)
    return any(norm(w) in t for w in words)


def answered(r: dict) -> bool:
    # >= 0.3 s of answer audio: one-word answers ("שבע.") are ~0.4 s of speech
    return r["dead_air_s"] is not None and (r["speech_s"] or 0) >= 0.3


def lang_ratio(text: str) -> tuple[int, int]:
    return len(HE.findall(text or "")), len(LATIN.findall(text or ""))


# ============================================================================================ scenarios
SCEN: list[tuple[int, str, str, str, object]] = []


def scenario(n: int, name: str, desc: str, criteria: str):
    def deco(fn):
        SCEN.append((n, name, desc, criteria, fn))
        return fn
    return deco


MAIN = "main"


@scenario(1, "greeting", "Open: connected on open, greeting heard, dead air from face connect to first sound",
          "session open after the face connects; greeting audio >= 0.5 s; first sound <= 3.0 s after the face connects")
async def s_greeting(h: Harness, r: Result):
    s = await h.boot(MAIN) if MAIN not in h.sessions else h.sessions[MAIN]
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    g = round(s.greet_first - s.t_face, 2) if s.greet_first else None
    heard = speech_s(s.t_face - 0.5, s.t_face + 15)
    r.details += [f"boot to face {s.t_face - s.t_boot:.1f} s", f"greeting first sound {g} s after face connect",
                  f"greeting audio {heard:.1f} s", f"greeting said: {s.greet_said[:120]}"]
    r.ok = s.core.live is not None and heard >= 0.5 and g is not None and g <= 3.0
    r.key = f"dead air {g} s, greeting {heard:.1f} s"


@scenario(2, "smalltalk", "Small talk: 3 short Hebrew turns", "3/3 answered, each first sound <= 4 s, session open")
async def s_smalltalk(h, r):
    s = await h.get(MAIN)
    rows = [await h.turn(s, q, label="smalltalk") for q in
            ("שלום, מה שלומך היום?", "מה אתה אוהב לעשות בזמן הפנוי?", "נשמע טוב. איך עבר עליך הבוקר?")]
    ok = [x for x in rows if answered(x) and x["dead_air_s"] <= 4]
    r.ok = len(ok) == 3 and rows[-1]["open"]
    da = [x["dead_air_s"] for x in rows if x["dead_air_s"] is not None]
    r.key = f"{len(ok)}/3 answered, dead air avg {np.mean(da):.2f} s" if da else "0/3 answered"
    r.details = rows


@scenario(3, "time_date", "Time and date answered from the clock context",
          "time answer names the current hour (Hebrew words or digits, +-1 h); day answer names today's weekday; "
          "no get_time tool call")
async def s_time(h, r):
    s = await h.get(MAIN)
    a = await h.turn(s, "מה השעה עכשיו?", label="time")
    now = datetime.now()
    h12 = now.hour % 12 or 12
    cands = {HOURS[h12], HOURS[h12 % 12 + 1], f"{now.hour}:", f"{now.hour:02d}:", str(now.hour)}
    ok_t = has(a["said"], *cands)
    b = await h.turn(s, "ואיזה יום היום?", label="date")
    ok_d = has(b["said"], DAYS[now.weekday()], now.strftime("%A"))
    tools = a["tools"] + b["tools"]
    r.ok = ok_t and ok_d and "get_time" not in tools
    r.key = f"hour {'ok' if ok_t else 'WRONG'}, day {'ok' if ok_d else 'WRONG'}, delegations {a['delegations'] + b['delegations']}"
    r.details = [a, b]


MATHS = [("כמה זה שלוש מאות ארבעים ושבע כפול שמונה עשרה?", ("6246", "6,246", "מאתיים ארבעים ושש", "מאתיים ארבעים ושישה")),
         ("כמה זה שתים עשרה כפול שתים עשרה?", ("144", "מאה ארבעים וארבע", "מאה ארבעים וארבעה")),
         ("כמה זה מאה עשרים וחמש ועוד שבעים וחמש?", ("200", "מאתיים")),
         ("מה השורש הריבועי של שמונים ואחת?", ("9", "תשע"))]


@scenario(4, "maths", "Maths: 4 questions (multiply, add, square root)", ">= 3/4 correct, all answered")
async def s_maths(h, r):
    s = await h.get(MAIN)
    rows, good = [], 0
    for q, ans in MATHS:
        x = await h.turn(s, q, label="maths")
        x["correct"] = has(x["said"], *ans)
        good += x["correct"]
        rows.append(x)
    r.ok = good >= 3 and all(answered(x) for x in rows)
    r.key = f"{good}/4 correct"
    r.details = rows


def web_scenario(n, name, q, words, why):
    @scenario(n, name, f"Web search ({name}): {why}",
              "answered with plausible content (keywords), screen showed 'thinking' and 'searching', session open")
    async def fn(h, r):
        s = await h.get(MAIN)
        x = await h.turn(s, q, label=name, first_timeout=40)
        plaus = has(x["said"], *words) and len(x["said"]) >= 15
        acts = x["activity"]
        r.ok = answered(x) and plaus and "searching" in acts and "thinking" in acts and x["open"]
        r.key = f"backend {x['backend_s']} s, screen {acts}, plausible {plaus}"
        r.details = [x]
    return fn


web_scenario(5, "weather", "מה מזג האוויר היום בתל אביב?",
             ("מעלות", "טמפרטור", "שמש", "שמשי", "גשם", "חם", "קר", "מעונן", "נעים", "לח", "בהיר", "degrees", "°"),
             "Tel Aviv weather today")
web_scenario(6, "news", "מה החדשות הגדולות היום?", ("", ), "today's headlines")
web_scenario(7, "facts", "מה שער הדולר מול השקל היום?",
             ("שקל", "שקלים", "שלוש", "שלושה", "ארבע", "נקודה", "אגורות", "3."), "USD/ILS rate today")


def emotion_scenario(n, name, q, emos, desc):
    @scenario(n, name, desc, f"face.emotion in {list(emos)} arrives within the turn; answered")
    async def fn(h, r):
        s = await h.get(MAIN)
        x = await h.turn(s, q, label=name)
        hit = [e for e in x["emotions"] if e in emos]
        r.ok = bool(hit) and answered(x)
        r.key = f"emotions {x['emotions']}"
        r.details = [x]
    return fn


emotion_scenario(8, "joke_laugh", "תספר לי בדיחה קצרה", ("laugh",), "Joke -> the face laughs")
emotion_scenario(9, "sad_cry", "אני קצת עצוב היום, הכלב שלי חולה", ("sad", "cry"), "Sad news -> sad or cry face")
emotion_scenario(10, "thanks_love", "תודה רבה, אתה מדהים", ("love",), "Thanks -> love face")


def memories(s: Session) -> list[str]:
    return [m["text"] for m in s.core.memory.list(50)]


@scenario(11, "memory_recall", "Memory: remember a fact, then recall it in the same session",
          "remember tool called and the fact is stored; the recall answer contains it")
async def s_mem(h, r):
    s = await h.get(MAIN)
    a = await h.turn(s, "תזכור בבקשה שמספר החניה שלי הוא שלושים ושבע", label="remember")
    stored = [m for m in memories(s) if has(m, "37", "שלושים ושבע", "חניה")]
    b = await h.turn(s, "מה מספר החניה שלי?", label="recall")
    rec = has(b["said"], "37", "שלושים ושבע")
    r.ok = "remember" in a["tools"] and bool(stored) and rec
    r.key = f"stored {stored[:1]}, recalled {rec}"
    r.details = [a, b]


async def forget_flow(h, r, policy: str, fact_q: str, fact_words, forget_q: str, expect_deleted: bool):
    s = await h.get(MAIN)
    if not [m for m in memories(s) if has(m, *fact_words)]:
        w = await h.turn(s, fact_q, label="remember")
        r.details.append(w)
    before = [m for m in memories(s) if has(m, *fact_words)]
    s.face.confirm_policy = policy
    n_conf = len(s.face.confirms)
    t0 = time.time()
    x = await h.turn(s, forget_q, label=f"forget/{policy}", first_timeout=40)
    if policy == "none":                 # make sure the 20 s confirmation timeout has passed
        while time.time() - t0 < 26:
            await asyncio.sleep(0.5)
        await h.quiet(s, 2.5, 30)
    s.face.confirm_policy = "approve"
    after = [m for m in memories(s) if has(m, *fact_words)]
    confs = s.face.confirms[n_conf:]
    asked = len(confs)
    declined = s.log.codes("TOOL_DECLINED")
    deleted = bool(before) and not after
    said = x["said"]
    if expect_deleted:
        outcome = has(said, "מחקתי", "נמחק", "מחוק", "deleted")
    else:
        outcome = has(said, "לא מחקתי", "לא נמחק", "נשאר", "השארתי", "לא אישרת", "לא התקבל", "בוטל", "ביטלתי",
                      "לא אושר", "בלי אישור", "ללא אישור", "לא הצלחתי למחוק", "not deleted", "kept")
    wait_note = None
    if policy == "none" and confs:              # the user hears what to do while the 20 s wait runs
        t_c = confs[0][0]
        wait_note = round(speech_s(t_c, t_c + 10), 1)
    alive = await h.turn(s, "אתה עדיין שם?", label="alive")
    r.ok = (bool(before) and "forget" in x["tools"] and asked >= 1 and deleted == expect_deleted and outcome
            and answered(alive) and (wait_note is None or wait_note >= 0.5))
    r.key = (f"confirm.request {asked}, before {len(before)} after {len(after)} -> "
             f"{'deleted' if deleted else 'kept'}; outcome spoken {outcome}"
             + (f"; speech during the wait {wait_note} s" if wait_note is not None else "")
             + f"; declined logs {len(declined)}")
    r.details += [x, alive, {"before": before, "after": after}]


@scenario(12, "forget_approve", "forget: the face APPROVES the confirm.request -> memory deleted",
          "forget tool called, >= 1 confirm.request, memory gone, deletion spoken, session alive")
async def s_forget_yes(h, r):
    await forget_flow(h, r, "approve", "תזכור בבקשה שמספר החניה שלי הוא שלושים ושבע", ("37", "שלושים ושבע", "חניה"),
                      "תמחק מהזיכרון את מספר החניה שלי", True)


@scenario(13, "forget_deny", "forget: the face DENIES -> memory kept",
          "forget tool called, >= 1 confirm.request, memory still there, 'not deleted' spoken, session alive")
async def s_forget_no(h, r):
    await forget_flow(h, r, "deny", "תזכור בבקשה שהצבע האהוב עליי הוא ירוק", ("ירוק",),
                      "תמחק מהזיכרון את הצבע האהוב עליי", False)


@scenario(14, "forget_timeout", "forget: NO reply (20 s timeout = denial) -> memory kept, conversation continues",
          "forget tool called, >= 1 confirm.request, JARVES speaks (asks to approve) within 10 s of the request, "
          "memory still there after 26 s, 'not deleted' spoken, session alive and answering")
async def s_forget_none(h, r):
    await forget_flow(h, r, "none", "תזכור בבקשה שהצבע האהוב עליי הוא ירוק", ("ירוק",),
                      "תמחק מהזיכרון את הצבע האהוב עליי", False)


LONG = [
    ("היי, בוא נדבר קצת. מה שלומך?", None),
    ("אני עובד היום מהבית.", None),
    ("דרך אגב, לאחותי קוראים נועה, והיא גרה בחיפה.", None),
    ("כמה זה עשרים ושלוש ועוד תשע עשרה?", ("42", "ארבעים ושתיים", "ארבעים ושניים")),
    ("תספר לי עובדה מעניינת אחת על הירח.", None),
    ("מה בירת יפן?", ("טוקיו", "tokyo")),
    ("איזה יום היום?", tuple(DAYS[datetime.now().weekday()].split())),
    ("תן לי רעיון לארוחת ערב קלה.", None),
    ("כמה זה שבע כפול שמונה?", ("56", "חמישים ושש", "חמישים ושישה")),
    ("מה ההבדל בין תה ירוק לתה שחור, בקצרה?", None),
    ("תספר לי בדיחה קצרה על מתכנתים.", None),
    ("מה מזג האוויר בחיפה היום?", None),
    ("אני מתכנן טיול בצפון בסוף השבוע.", None),
    ("תמליץ לי על מקום אחד לטייל בגליל.", None),
    ("כמה דקות יש בשלוש שעות?", ("180", "מאה שמונים")),
    ("למה השמיים כחולים, במשפט אחד?", None),
    ("איך אומרים תודה ביפנית?", ("אריגטו", "arigato")),
    ("איך קוראים לאחותי ואיפה היא גרה?", ("נועה",)),
    ("יופי. כמה זה מאה חלקי ארבע?", ("25", "עשרים וחמש", "עשרים וחמישה")),
    ("מה השעה עכשיו?", None),
    ("תן לי טיפ אחד לשינה טובה.", None),
    ("מה החדשות בתחום הטכנולוגיה היום?", None),
    ("כמה רגליים יש לעכביש?", ("8", "שמונה")),
    ("תודה, היה נחמד לדבר איתך.", None),
    ("לפני שאני הולך, מה אמרתי לך שאני מתכנן לסוף השבוע?", ("טיול", "צפון", "לטייל")),
    ("טוב, להתראות!", None),
]


@scenario(15, "long_conversation", "LONG conversation: 26 mixed turns in ONE session, context from 15 turns earlier",
          ">= 24/26 answered, 0 disconnects, sister's name recalled (turn 18 <- turn 3), weekend plan recalled "
          "(turn 25 <- turn 13), ai_requests == completed backend responses (+-1), voice seconds within 25% of "
          "session wall time, no reservations left")
async def s_long(h, r):
    s = await h.boot("long")
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    t_start = stamp(s.log.codes("LIVE_STARTED")[0]) if s.log.codes("LIVE_STARTED") else s.t_boot
    rows, correct, checked = [], 0, 0
    for i, (q, ans) in enumerate(LONG, 1):
        if h.minutes_left() < 1.0:
            r.details.append("stopped: cost cap")
            break
        x = await h.turn(s, q, label=f"long{i}")
        if ans:
            x["correct"] = has(x["said"], *ans)
            checked += 1
            correct += x["correct"]
        rows.append(x)
    n_ans = sum(answered(x) for x in rows)
    disc = sum(not x["open"] for x in rows)
    ctx1 = rows[17].get("correct") if len(rows) > 17 else False
    ctx1_city = has(rows[17]["said"], "חיפה") if len(rows) > 17 else False
    ctx2 = rows[24].get("correct") if len(rows) > 24 else False
    done = len(s.log.codes("LIVE_BACKEND_DONE"))
    snap_before = s.core.snapshot()
    t_end = time.time()
    u = await h.shutdown(s)
    ended = [e for e in s.log.codes("VOICE_ENDED")]
    wall = (stamp(ended[0]) if ended else t_end) - t_start
    vs = u.get("voice_seconds", 0)
    acc_ok = done <= u.get("ai_requests", -1) <= done + 1
    voice_ok = abs(vs - wall) <= 0.25 * wall
    r.ok = (n_ans >= 24 and disc == 0 and ctx1 and ctx2 and acc_ok and voice_ok and u.get("reservations") == 0
            and len(rows) == len(LONG))
    r.key = (f"{n_ans}/{len(rows)} answered, {correct}/{checked} correct, disconnects {disc}, context "
             f"name={ctx1} city={ctx1_city} plan={ctx2}, requests {u.get('ai_requests')} vs backend done {done}, "
             f"voice {vs} s vs wall {wall:.0f} s")
    r.details = rows + [{"db": u, "snapshot_requests": snap_before.get("ai_requests_today")}]


RAPID = [("כמה זה שתיים ועוד שתיים?", ("4", "ארבע")), ("מה בירת צרפת?", ("פריז", "paris")),
         ("כמה ימים יש בשבוע?", ("7", "שבע")), ("מה הצבע של בננה?", ("צהוב", "צהובה")),
         ("כמה זה עשר פחות שלוש?", ("7", "שבע")), ("מה ההפך של חם?", ("קר",))]


@scenario(16, "rapid_fire", "Rapid-fire: 6 short questions back to back (next question 0.7 s after the answer)",
          "6/6 answered (none lost), >= 5/6 correct")
async def s_rapid(h, r):
    s = await h.get(MAIN)
    mark = s.log.mark()
    rows = []
    for q, ans in RAPID:
        rows.append(await h.turn(s, q, quiet_s=0.7, label="rapid"))
    await asyncio.sleep(3)          # the transcript of the last answer is logged ~2 s after it was spoken
    # Transcripts are logged up to 2 s AFTER the audio, so with 0.7 s gaps they land in the next turn's window:
    # judge the answers IN ORDER over the whole sequence; "answered" = answer audio after each question.
    said = " ".join(e.get("text", "") for e in s.log.codes("LIVE_JARVES_SAID", mark))
    pos, c = 0, 0
    norm = re.sub(r"[\s/.,!?]+", "", said.lower())
    for (q, ans), x in zip(RAPID, rows):
        hits = [norm.find(re.sub(r"[\s/.,!?]+", "", a.lower()), pos) for a in ans]
        hits = [i for i in hits if i >= 0]
        x["correct"] = bool(hits)
        if hits:
            c += 1
            pos = min(hits) + 1
    n = sum(1 for x in rows if x["dead_air_s"] is not None and x["speech_s"] >= 0.3)
    r.ok = n == 6 and c >= 5
    r.key = f"{n}/6 answered (audio), {c}/6 correct in order, dead air {[x['dead_air_s'] for x in rows]}"
    r.details = rows + [{"all_said": said}]


LONG_UTT = ("אני רוצה לספר לך משהו קטן שקרה לי. אתמול בבוקר קמתי מוקדם, הכנתי לעצמי קפה חזק, ויצאתי לטיול ארוך עם "
            "הכלב בפארק הירקון. היה מזג אוויר נעים מאוד, ראיתי הרבה אנשים רצים ורוכבים על אופניים לאורך הנחל, "
            "ואחר כך, ממש במקרה, נפגשתי עם חבר ותיק שלא ראיתי כבר כמה שנים. ישבנו בבית קפה קטן ודיברנו על העבודה, "
            "על המשפחות שלנו, ועל טיול שאנחנו רוצים לעשות יחד באילת בחורף הקרוב, עם צלילה ושמש. בסוף חזרתי הביתה "
            "עייף אבל שמח מאוד. ועכשיו השאלה שלי אליך: לאיזו עיר אנחנו רוצים לטייל בחורף?")


@scenario(17, "long_utterance", "A very long single utterance (~30 s of speech), then a question about it",
          "speech >= 25 s; answered; answer names Eilat; session open; no ERROR logs")
async def s_long_utt(h, r):
    s = await h.get(MAIN)
    x = await h.turn(s, LONG_UTT, label="long-utterance", first_timeout=40)
    ok_c = has(x["said"], "אילת", "eilat")
    r.ok = x["q_s"] >= 25 and answered(x) and ok_c and x["open"] and not x["errors"]
    r.key = f"utterance {x['q_s']} s, answered {answered(x)}, Eilat {ok_c}, dead air {x['dead_air_s']} s"
    r.details = [x]


@scenario(18, "english_switch", "English question inside the Hebrew conversation, then back to Hebrew",
          "English answer is mostly Latin letters and names Canberra; the next Hebrew answer is mostly Hebrew and "
          "names Ottawa")
async def s_english(h, r):
    s = await h.get(MAIN)
    a = await h.turn(s, "What is the capital city of Australia?", lang="en", label="english")
    b = await h.turn(s, "ומה עיר הבירה של קנדה?", label="back-to-hebrew")
    ha, la = lang_ratio(a["said"])
    hb, lb = lang_ratio(b["said"])
    en_ok = la > ha and has(a["said"], "canberra")
    he_ok = hb > lb and has(b["said"], "אוטווה", "אוטאווה", "ottawa")
    r.ok = en_ok and he_ok
    r.key = f"english {la}/{ha} latin/hebrew letters (Canberra {has(a['said'], 'canberra')}), hebrew {hb}/{lb}"
    r.details = [a, b]


@scenario(19, "noise_robustness", "40 s of coughs, hiss, knocks and quiet room tone (no speech)",
          "JARVES says nothing (< 0.5 s output speech), no delegation, no transcript of JARVES speech, session open")
async def s_noise(h, r):
    s = await h.get(MAIN)
    await h.quiet(s, 2.0, 30)
    mark, t0 = s.log.mark(), time.time()
    pcm24 = noise_track(40, 24000)
    await h.inject(pcm24, noise_track(40, 16000))
    await asyncio.sleep(5)
    ev = s.log.since(mark)
    out = speech_s(t0, time.time())
    said = [e.get("text", "") for e in ev if e["code"] == "LIVE_JARVES_SAID"]
    heard = [e.get("text", "") for e in ev if e["code"] == "LIVE_USER_SAID"]
    dele = sum(1 for e in ev if e["code"] == "LIVE_EVENT" and e["msg"] == "session.delegation.created")
    r.ok = out < 0.5 and not said and dele == 0 and s.core.live is not None
    r.key = f"JARVES spoke {out:.1f} s, delegations {dele}, said {said[:2]}, heard-as {heard[:2]}"
    r.details = [{"said": said, "heard": heard}]


@scenario(20, "idle_disconnect", "Idle: idle_disconnect_s = 15; stays open through 8 s and 10 s pauses, then ends",
          "open after an 8 s and a 10 s pause; VOICE_IDLE after 13-22 s of quiet; reason idle")
async def s_idle(h, r):
    s = await h.boot("idle15", idle_s=15)
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    await asyncio.sleep(8)
    open1 = s.core.live is not None
    a = await h.turn(s, "שלום, אתה שומע אותי?", label="idle-a")
    await asyncio.sleep(10)
    open2 = s.core.live is not None
    b = await h.turn(s, "יופי, תודה.", label="idle-b")
    t_quiet = VAudio.last_play
    while s.core.live is not None and time.time() - t_quiet < 45:
        await asyncio.sleep(0.2)
    idle = s.log.codes("VOICE_IDLE")
    after = round(stamp(idle[-1]) - t_quiet, 1) if idle else None
    ended = [e["msg"] for e in s.log.codes("LIVE_ENDED")]
    await h.shutdown(s)
    r.ok = open1 and open2 and after is not None and 13 <= after <= 22 and any("(idle)" in m for m in ended)
    r.key = f"open after 8 s {open1}, after 10 s {open2}; ended {after} s after the last sound ({ended})"
    r.details = [a, b]


WAKE_PROFILE = {"voice": {"wake_enabled": True, "chime": True}}


def chimes(core, since_n: int = 0) -> list[str]:
    out = []
    for n, rate in core._suite_sim.played[since_n:]:
        out.append("wake" if n == int(rate * 0.10) else "ready" if n == int(rate * 0.08) + int(rate * 0.12) else f"{n}")
    return out


async def wait_for(pred, timeout: float, step: float = 0.1) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        if pred():
            return True
        await asyncio.sleep(step)
    return False


async def wake_cycle(h: Harness, s: Session, question: str, ans) -> dict:
    """Idle -> 'Hey Jarvis' into the 16 kHz wake mic -> chime -> new session -> ready chime -> question -> answer."""
    await wait_for(lambda: s.core.live is None, 60)
    await wait_for(lambda: s.core.snapshot()["wake_listening"] and VAudio.open_mics(16000), 15)
    mark, n_played = s.log.mark(), len(s.core._suite_sim.played)
    t0 = time.time()
    where, _ = await h.say("Hey Jarvis", lang="en", pad_s=0.3)
    woke = await wait_for(lambda: bool(s.log.codes("WAKE_WORD", mark)), 5)
    conn = await wait_for(lambda: s.core.live is not None and VAudio.open_mics(24000), 15)
    t_conn = time.time() - t0
    await asyncio.sleep(0.8)
    ch = chimes(s.core, n_played)
    x = await h.turn(s, question, label="after-wake") if conn else {"said": "", "dead_air_s": None, "speech_s": 0}
    return {"mic": where, "wake_logged": woke, "score": (s.log.codes("WAKE_WORD", mark) or [{}])[0].get("score"),
            "connected": conn, "connect_s": round(t_conn, 1), "chimes": ch,
            "answered": answered(x) if conn else False, "correct": has(x["said"], *ans), "turn": x}


@scenario(21, "wake_word_x3", "Wake word, silent: idle disconnect -> 'Hey Jarvis' -> chime -> session -> answer, 3x",
          "3/3: WAKE_WORD logged, wake chime + ready chime played (SimAudio), new session connected, question answered")
async def s_wake(h, r):
    s = await h.boot("wake", over=WAKE_PROFILE, idle_s=15)
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    qa = [("כמה זה שלוש ועוד ארבע?", ("7", "שבע")), ("מה בירת איטליה?", ("רומא",)), ("מה ההפך של גדול?", ("קטן",))]
    rows = []
    for q, ans in qa:
        x = await wake_cycle(h, s, q, ans)
        rows.append(x)
        print(f"   wake cycle: {({k: v for k, v in x.items() if k != 'turn'})}", flush=True)
    # the session stays: scenario 30 (idle soak) continues from this idle Core without a new greeting session
    good = [x for x in rows if x["wake_logged"] and "wake" in x["chimes"] and "ready" in x["chimes"]
            and x["connected"] and x["answered"]]
    r.ok = len(good) == 3
    r.key = (f"{len(good)}/3 full cycles; scores {[x['score'] for x in rows]}; connect "
             f"{[x['connect_s'] for x in rows]} s; correct {sum(x['correct'] for x in rows)}/3")
    r.details = rows


NEGATIVES = [("en", "hey Travis"), ("en", "hey service"), ("en", "Jarvis"), ("en", "hey Jarvie, no"),
             ("en", "Hey Charles, come here"), ("en", "Harvest season is here"), ("en", "Have a nice day"),
             ("en", "Hey, how are you doing?"), ("en", "A jar of peanut butter"), ("en", "Let's watch Jurassic Park"),
             ("en", "Hey there, good morning"), ("en", "Service, please"),
             ("he", "היי, מה נשמע?"), ("he", "ג'רוויס"), ("he", "תגיד לי מה השעה"), ("he", "היי טרוויס"),
             ("he", "איזה יום יפה היום"), ("he", "תדליק את האור בסלון"), ("he", "הרבה עבודה היום במשרד"),
             ("he", "הבאתי לך חרוזים")]
POSITIVES = [("David", 1.0, 0.6), ("Zira", 1.0, 0.6), ("Mark", 1.0, 0.6), ("David", 0.85, 0.6), ("Zira", 1.25, 0.6),
             ("Mark", 0.85, 0.6), ("David", 1.25, 0.6), ("Zira", 0.85, 0.15), ("Mark", 1.25, 0.15), ("David", 1.0, 0.1)]


class FakeLive:
    """Post-wake cloud session replaced by a no-cost stand-in (wake DETECTION stays fully real)."""
    started = 0

    def __init__(self, core, on_state, on_transcript, on_usage, **kw):
        self.on_end, self.running = kw["on_end"], False

    async def start(self):
        FakeLive.started += 1
        self.running = True
        asyncio.get_running_loop().call_later(1.0, lambda: asyncio.ensure_future(self._end()))

    async def _end(self):
        if self.running:
            self.running = False
            await self.on_end("idle")

    async def stop(self, reason="user"):
        await self._end()


async def offline_peak(text, lang, voice=None, speed=1.0, level=0.6) -> float:
    from jarves.voice.wakeword import FRAME_BYTES, OpenWakeWordDetector
    samples, rate = await synth(text, lang, voice, speed)
    det = OpenWakeWordDetector("hey_jarvis", 0.5)
    pcm = bytes(16000) + to_pcm(samples, rate, 16000, level) + bytes(32000)
    best = 0.0
    for i in range(0, len(pcm) - FRAME_BYTES, FRAME_BYTES):
        det.feed(pcm[i:i + FRAME_BYTES])
        best = max(best, det.last_score)
    return round(best, 3)


async def wake_rates(h: Harness, threshold: float, with_positives: bool) -> dict:
    over = {"voice": {"wake_enabled": True, "activation": "wake_word", "greeting": False, "chime": True,
                      "wake_threshold": threshold}}
    FakeLive.started = 0
    s = await h.boot(f"wakefp{int(threshold * 100):02d}", over=over, live_factory=FakeLive, greet=False)
    await wait_for(lambda: VAudio.open_mics(16000), 20)
    rows = []
    items = [("neg", lang, t, None, 1.0, 0.6) for lang, t in NEGATIVES]
    if with_positives:
        items += [("pos", "en", "Hey Jarvis", v, sp, lv) for v, sp, lv in POSITIVES]
    for kind, lang, text, voice, sp, lv in items:
        await wait_for(lambda: VAudio.open_mics(16000) and s.core.live is None, 10)
        await asyncio.sleep(0.6)
        mark = s.log.mark()
        where, _ = await h.say(text, lang=lang, voice=voice, speed=sp, level=lv, pad_s=0.2)
        await asyncio.sleep(1.2)
        w = s.log.codes("WAKE_WORD", mark)
        rows.append({"kind": kind, "text": text, "voice": voice, "speed": sp, "level": lv, "mic": where,
                     "woke": bool(w), "score": w[0].get("score") if w else None,
                     "near_miss": [e.get("score") for e in s.log.codes("WAKE_NEAR_MISS", mark)]})
    await h.shutdown(s)
    neg = [x for x in rows if x["kind"] == "neg"]
    pos = [x for x in rows if x["kind"] == "pos"]
    return {"threshold": threshold, "false_wakes": sum(x["woke"] for x in neg), "negatives": len(neg),
            "detected": sum(x["woke"] for x in pos), "positives": len(pos), "rows": rows,
            "false_wake_texts": [x["text"] for x in neg if x["woke"]],
            "lost": sum(x["mic"] == "lost" for x in rows)}


@scenario(31, "false_wake_closes", "A wake with NOBODY speaking (false wake) ends after ~12 s instead of idling for minutes; then a real wake works",
          "VOICE_NO_SPEECH logged 10-20 s after the session opened, session closed, the next 'Hey Jarvis' + question works")
async def s_false_wake(h, r):
    s = await h.boot("falsewake", over={"voice": {"activation": "wake_word", "wake_enabled": True, "chime": True, "greeting": False,
                                     "wake_no_speech_s": 12}}, idle_s=180, greet=False)
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    await wait_for(lambda: s.core.live is None, 60)
    await wait_for(lambda: s.core.snapshot()["wake_listening"] and VAudio.open_mics(16000), 15)
    mark = s.log.mark()
    await h.say("Hey Jarvis", lang="en", pad_s=0.3)                   # the wake phrase, then NOTHING
    woke = await wait_for(lambda: bool(s.log.codes("WAKE_WORD", mark)), 5)
    opened = await wait_for(lambda: s.core.live is not None, 15)
    t0 = time.time()
    closed = await wait_for(lambda: s.core.live is None, 40)
    dt = round(time.time() - t0, 1)
    ev = s.log.codes("VOICE_NO_SPEECH", mark)
    x = await wake_cycle(h, s, "כמה זה שתיים ועוד שתיים?", ("4", "ארבע"))     # and a normal wake still works
    r.ok = bool(woke and opened and closed and ev and 8 <= dt <= 22 and x["connected"] and x["answered"])
    r.key = (f"woke {woke}, opened {opened}, closed after {dt} s, VOICE_NO_SPEECH {len(ev)}; "
             f"next real wake connected {x['connected']}, answered {x['answered']}")
    r.details = {"closed_after_s": dt, "next": {k: v for k, v in x.items() if k != "turn"}}


@scenario(22, "wake_false_positives", "20 non-wake sentences (Hebrew/English, near-misses) while idle, threshold 0.15",
          "0 false wakes out of 20 (honest count; real openWakeWord in the Core wake loop, no live cost)")
async def s_wake_fp(h, r):
    x = await wake_rates(h, 0.15, with_positives=False)
    r.ok = x["false_wakes"] == 0 and x["lost"] == 0
    r.key = f"{x['false_wakes']}/{x['negatives']} false wakes {x['false_wake_texts']}"
    r.details = x["rows"]


@scenario(23, "wake_thresholds", "Wake detection vs false wakes at threshold 0.15 (user's) and 0.35 (default)",
          "at each threshold: >= 9/10 'Hey Jarvis' variants detected (3 voices, 3 speeds, quiet levels); report "
          "false wakes (PASS needs 0 at 0.35)")
async def s_wake_thr(h, r):
    out = []
    for thr in (0.15, 0.35):
        x = await wake_rates(h, thr, with_positives=True)
        out.append(x)
        print(f"   threshold {thr}: detected {x['detected']}/{x['positives']}, false {x['false_wakes']}/"
              f"{x['negatives']} {x['false_wake_texts']}", flush=True)
    peaks = []
    for lang, t in NEGATIVES:
        peaks.append((t, await offline_peak(t, lang)))
    for v, sp, lv in POSITIVES:
        peaks.append((f"Hey Jarvis [{v} x{sp} level {lv}]", await offline_peak("Hey Jarvis", "en", v, sp, lv)))
    a, b = out
    r.ok = a["detected"] >= 9 and b["detected"] >= 9 and b["false_wakes"] == 0
    r.key = (f"0.15: detect {a['detected']}/{a['positives']}, false {a['false_wakes']}/{a['negatives']}; "
             f"0.35: detect {b['detected']}/{b['positives']}, false {b['false_wakes']}/{b['negatives']}")
    r.details = [{k: v for k, v in x.items() if k != "rows"} for x in out] + [{"offline_peaks": peaks}]


@scenario(24, "software_mute", "Software mute: mic.set_mute during a conversation; wake ignored while muted; unmute",
          "mute -> live ends (user) and every virtual mic is closed; muted 'Hey Jarvis' -> no WAKE_WORD; "
          "unmute -> 'Hey Jarvis' wakes, session connects, question answered")
async def s_mute(h, r):
    s = await h.boot("mute", over=WAKE_PROFILE)
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    a = await h.turn(s, "שלום, מה שלומך?", label="before-mute")
    mark = s.log.mark()
    await s.face.send("mic.set_mute", {"muted": True})
    stopped = await wait_for(lambda: s.core.live is None, 10)
    await asyncio.sleep(1.0)
    mics_open = [m.mic_rate for m in VAudio.instances if m._open and m.mic_rate]
    ended = [e["msg"] for e in s.log.codes("LIVE_ENDED", mark)]
    where_muted, _ = await h.say("Hey Jarvis", lang="en", pad_s=0.3)
    await asyncio.sleep(3)
    woke_muted = bool(s.log.codes("WAKE_WORD", mark))
    await s.face.send("mic.set_mute", {"muted": False})
    x = await wake_cycle(h, s, "כמה זה חמש ועוד חמש?", ("10", "עשר"))
    await h.shutdown(s)
    r.ok = (stopped and not mics_open and any("(user)" in m for m in ended) and where_muted == "lost"
            and not woke_muted and x["wake_logged"] and x["connected"] and x["answered"])
    r.key = (f"live stopped {stopped}, open mics after mute {mics_open}, muted wake phrase -> {where_muted} / "
             f"woke {woke_muted}; after unmute woke {x['wake_logged']} connected {x['connected']} answered {x['answered']}")
    r.details = [a, {k: v for k, v in x.items() if k != "turn"}, x["turn"]]


@scenario(25, "budget_voice", "Voice budget: ai.daily_realtime_minutes = 1, talk until the limit",
          "the session ends by itself within 75 s of start, face gets error VOICE_BUDGET or AI_BUDGET, at most 2 "
          "LIVE_STARTED, no crash logs, voice_seconds <= 75, no reservations left")
async def s_budget_voice(h, r):
    s = await h.boot("budget_voice", over={"ai": {"daily_realtime_minutes": 1}})
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    t0 = time.time()
    qs = ["ספר לי בבקשה בשלושה משפטים על ההיסטוריה של ירושלים.", "ומה עם תל אביב?", "ומה עם חיפה?",
          "ספר לי על הגליל.", "ומה עם הנגב?", "ומה עם אילת?"]
    rows = []
    for q in qs:
        if s.core.live is None:
            break
        rows.append(await h.turn(s, q, label="budget"))
    await wait_for(lambda: s.core.live is None, 40)
    await asyncio.sleep(4)
    end_s = round(time.time() - t0, 1)
    errs = [p.get("code") for p in s.face.of("error")]
    starts = len(s.log.codes("LIVE_STARTED"))
    crash = [e["code"] for e in s.log.refresh() if e["code"] in ("VOICE_TASK_CRASHED", "CORE_TASK_CRASH",
                                                                  "LIVE_EVENT_HANDLER_CRASHED", "LINK_HANDLER_ERROR")]
    ended = [e["msg"] for e in s.log.codes("LIVE_ENDED")]
    u = await h.shutdown(s)
    r.ok = (any(c in ("VOICE_BUDGET", "AI_BUDGET") for c in errs) and starts <= 2 and not crash
            and u.get("voice_seconds", 999) <= 75 and u.get("reservations") == 0)
    r.key = f"face errors {errs}, sessions {starts}, ended {ended}, voice {u.get('voice_seconds')} s, db {u}"
    r.details = rows + [{"ended_after_s": end_s}]


@scenario(26, "budget_requests", "Request budget: ai.daily_request_limit = 2, ask web lookups",
          "graceful end: face gets error AI_BUDGET, session ends, ai_requests <= 3 (one overrun allowed), no crash, "
          "no reservations left")
async def s_budget_req(h, r):
    s = await h.boot("budget_req", over={"ai": {"daily_request_limit": 2}})
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    rows = []
    for q in ("מה מזג האוויר היום בירושלים?", "מה החדשות הגדולות היום?", "מה שער הדולר היום?"):
        if s.core.live is None:
            break
        rows.append(await h.turn(s, q, label="req-budget", first_timeout=40))
    await wait_for(lambda: s.core.live is None, 30)
    await asyncio.sleep(3)
    errs = [p.get("code") for p in s.face.of("error")]
    crash = [e["code"] for e in s.log.refresh() if e["code"] in ("VOICE_TASK_CRASHED", "CORE_TASK_CRASH",
                                                                  "LIVE_EVENT_HANDLER_CRASHED", "LINK_HANDLER_ERROR")]
    ended = [e["msg"] for e in s.log.codes("LIVE_ENDED")]
    budget_logs = [e["msg"][:80] for e in s.log.refresh() if e["code"] in ("AI_BUDGET", "AI_BUDGET_OVERRUN")]
    u = await h.shutdown(s)
    r.ok = "AI_BUDGET" in errs and not crash and u.get("ai_requests", 99) <= 3 and u.get("reservations") == 0
    r.key = f"face errors {errs}, ended {ended}, db {u}"
    r.details = rows + [{"budget_logs": budget_logs}]


FACE_STATES_OK = {"idle", "listening", "thinking", "speaking", "happy", "error", "sleeping", "muted"}
ACT_KINDS = {None, "thinking", "searching", "music", "time", "memory", "connecting", "tool", "done"}
EMOS = {"laugh", "cry", "sad", "surprised", "love"}


@scenario(27, "face_protocol", "Face protocol sanity over the real conversations of the main session",
          "valid states/kinds/emotions; every turn ends in 'listening' (never stuck in 'speaking'); after each "
          "'speaking' a non-speaking state follows within 3 s of the audio ending; mic.level ~5 Hz while listening")
async def s_face(h, r):
    s = await h.get(MAIN)
    if len(s.turns) < 3:
        for q in ("שלום!", "כמה זה שתיים כפול שלוש?", "מה מזג האוויר בירושלים?"):
            await h.turn(s, q, label="face")
    await h.quiet(s, 3, 30)
    msgs = s.face.msgs
    states = [(t, p.get("state")) for t, ty, p in msgs if ty == "face.set_state"]
    bad_states = sorted({x for _, x in states if x not in FACE_STATES_OK})
    kinds = sorted({str(p.get("kind")) for t, ty, p in msgs if ty == "assistant.activity"} - {str(k) for k in ACT_KINDS})
    emos = [(p.get("emotion"), p.get("ms")) for t, ty, p in msgs if ty == "face.emotion"]
    bad_emo = [e for e in emos if e[0] not in EMOS or not e[1]]
    stuck_end = [x["label"] for x in s.turns if x["open"] and x["face_end"] != "listening"]
    late = []
    for i, (t, st) in enumerate(states):
        if st != "speaking":
            continue
        nxt = next(((t2, s2) for t2, s2 in states[i + 1:] if s2 != "speaking"), None)
        seg_end = max([b for a, b in segments(t - 0.5, (nxt[0] if nxt else time.time()) + 0.1)] or [t])
        if nxt is None or nxt[0] - seg_end > 3.0:
            late.append(round((nxt[0] if nxt else time.time()) - seg_end, 1))
    lv = [t for t, ty, p in msgs if ty == "mic.level"]
    span = (lv[-1] - lv[0]) if len(lv) > 1 else 0
    hz = round(len(lv) / span, 1) if span else 0
    r.ok = not bad_states and not kinds and not bad_emo and not stuck_end and not late and 2 <= hz <= 7
    r.key = (f"{len(states)} states, {len(s.turns)} turns, not-listening at turn end {stuck_end}, late returns "
             f"{late[:5]}, mic.level {hz} Hz, bad {bad_states + kinds + [str(b) for b in bad_emo]}")
    seq = [x for _, x in states]
    r.details = [{"state_counts": {k: seq.count(k) for k in set(seq)}, "first_states": seq[:30],
                  "emotions": emos[:20]}]


@scenario(28, "start_stop_x5", "Core start -> greeting -> (question) -> stop, 5 cycles in one process",
          "every cycle: greeting heard, CORE_STOPPED logged, port 8793 free, no leftover asyncio tasks, memory.db "
          "not held open (rename works)")
async def s_cycles(h, r):
    rows = []
    for i in range(5):
        if h.minutes_left() < 0.8:
            rows.append({"cycle": i + 1, "skipped": "cost cap"})
            break
        base = {t for t in asyncio.all_tasks()}
        s = await h.boot(f"cycle{i + 1}")
        ok_greet = s.greet_first is not None
        ans = None
        if i in (0, 3) and not s.boot_error:
            ans = answered(await h.turn(s, "כמה זה שלוש כפול שלוש?", label=f"cycle{i + 1}"))
        await h.shutdown(s)
        await asyncio.sleep(0.5)
        left = [t.get_name() + ":" + getattr(t.get_coro(), "__qualname__", "?") for t in asyncio.all_tasks() - base
                if not t.done()]
        stopped = bool(s.log.codes("CORE_STOPPED"))
        free = port_free()
        try:
            (s.home / "memory.db").rename(s.home / "memory.moved")
            released = True
            (s.home / "memory.moved").rename(s.home / "memory.db")
        except OSError as e:
            released = f"locked ({e.__class__.__name__})"
        rows.append({"cycle": i + 1, "greeting": ok_greet, "answered": ans, "CORE_STOPPED": stopped,
                     "port_free": free, "leftover_tasks": left, "db_released": released, "boot_error": s.boot_error})
        print(f"   cycle {rows[-1]}", flush=True)
    good = [x for x in rows if x.get("greeting") and x.get("CORE_STOPPED") and x.get("port_free")
            and not x.get("leftover_tasks") and x.get("db_released") is True and x.get("answered") is not False]
    r.ok = len(good) == 5
    r.key = f"{len(good)}/5 clean cycles; leftovers {[x.get('leftover_tasks') for x in rows if x.get('leftover_tasks')]}"
    r.details = rows


@scenario(29, "log_privacy", "Logs hygiene: log.privacy = true hides transcripts; the API key never appears",
          "privacy on: none of the spoken/heard words (>= 4 letters) and no key in jarves.log; privacy off (every "
          "other run of this suite): the key never appears")
async def s_privacy(h, r):
    s = await h.boot("privacy", over={"log": {"privacy": True}})
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    rows = [await h.turn(s, "שלום, קוראים לי דניאל ואני גר ברחובות.", label="privacy"),
            await h.turn(s, "תזכיר לי איך קוראים לי?", label="privacy")]
    await asyncio.sleep(2.5)
    face_texts = [p.get("text", "") for t, ty, p in s.face.msgs if ty in ("assistant.text", "user.transcript")]
    await h.shutdown(s)
    raw = (s.home / "logs" / "jarves.log").read_text(encoding="utf-8")
    words = {"דניאל", "ברחובות", "רחובות"}
    for t in face_texts:
        words |= {w.strip(".,?!") for w in t.split() if len(w.strip(".,?!")) >= 4}
    words -= {"JARVES", "Jarvis"}            # the assistant's name is in normal startup lines, not private content
    leaked = sorted(w for w in words if w and w in raw)
    key_on = bool(h.key) and h.key in raw
    key_off = []
    for home in h.homes:
        for f in (home / "logs").glob("jarves.log*") if (home / "logs").exists() else []:
            if h.key and h.key in f.read_text(encoding="utf-8", errors="ignore"):
                key_off.append(str(f.name))
    r.ok = not leaked and not key_on and not key_off and bool(face_texts)
    r.key = (f"privacy on: {len(words)} content words checked, leaked {leaked[:5]}; key in logs: "
             f"{'YES' if key_on or key_off else 'no'} ({len(h.homes)} homes scanned); face texts {len(face_texts)}")
    r.details = [{"heard_said_on_face": len(face_texts)}] + rows


@scenario(30, "idle_soak_6min", "6-minute idle soak with room tone/noise on the wake mic, then 'Hey Jarvis' reconnect",
          "wake listener alive the whole time (WAKE_HEALTH every ~15 s), no false wake, no errors, then wake -> "
          "session -> answer")
async def s_soak(h, r):
    s = h.sessions.get("wake") or await h.boot("soak", over=WAKE_PROFILE, idle_s=15)
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    await wait_for(lambda: s.core.live is None, 60)
    await wait_for(lambda: VAudio.open_mics(16000), 15)
    mark, t0 = s.log.mark(), time.time()
    while time.time() - t0 < 360:
        await h.inject(noise_track(20, 24000, seed=int(time.time())), noise_track(20, 16000, seed=int(time.time())))
        await asyncio.sleep(10)
    health = s.log.codes("WAKE_HEALTH", mark)
    false = s.log.codes("WAKE_WORD", mark)
    errs = [e["code"] for e in s.log.since(mark) if e.get("level") == "ERROR"]
    x = await wake_cycle(h, s, "מה בירת ספרד?", ("מדריד",))
    await h.shutdown(s)
    r.ok = len(health) >= 20 and not false and not errs and x["wake_logged"] and x["answered"]
    r.key = (f"soak {time.time() - t0:.0f} s: WAKE_HEALTH {len(health)}, false wakes {len(false)}, errors {errs[:3]}; "
             f"then woke {x['wake_logged']} answered {x['answered']}")
    r.details = [{k: v for k, v in x.items() if k != "turn"}, x["turn"]]


# ============================================================================================ SW-V2 features (P0.4)
V2 = "v2live"


def strip(s: Session, since: float = 0.0) -> list[tuple[float, dict]]:
    return [(t, p) for t, ty, p in s.face.msgs if ty == "privacy.state" and t >= since]


@scenario(32, "privacy_strip_live", "Privacy strip in a REAL GPT-Live session: the face's privacy.state follows the "
          "session (mic, cloud audio, speaker); mute from the strip ends it",
          "while the session is open and JARVES is quiet: mic listening + cloud_audio sending (on); while JARVES "
          "speaks: speaker speaking; never cloud 'off' while the session is open; mic.set_mute -> mic muted + cloud "
          "off within 2 s and the session ends")
async def s_privacy_live(h, r):
    s = await h.get(V2)
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    x = await h.turn(s, "שלום, מה שלומך?", label="privacy-live")
    await h.quiet(s, 2.0, 30)
    t_open = time.time()
    await asyncio.sleep(1.0)
    msgs = strip(s)
    quiet_open = [p for t, p in strip(s, t_open - 0.5)] or [p for _, p in msgs[-1:]]
    sending = [p for _, p in msgs if p["cloud_audio"]["on"] and p["mic"]["state"] == "listening"]
    speaking = [p for _, p in msgs if p["speaker"]["state"] == "speaking"]
    gated = [p for _, p in msgs if p["mic"]["state"] == "gated"]
    off_while_open = [p["cloud_audio"]["state"] for p in quiet_open if p["cloud_audio"]["state"] == "off"]
    tm = time.time()
    await s.face.send("mic.set_mute", {"muted": True})
    # muted at once = no microphone audio leaves (cloud_audio.on false); the session itself then closes ("off")
    muted = await wait_for(lambda: any(p["mic"]["state"] == "muted" and not p["cloud_audio"]["on"]
                                       for _, p in strip(s, tm)), 2.0)
    dt = next((round(t - tm, 2) for t, p in strip(s, tm) if p["mic"]["state"] == "muted"), None)
    ended = await wait_for(lambda: s.core.live is None and any(p["cloud_audio"]["state"] == "off"
                                                                for _, p in strip(s, tm)), 8)
    dt_off = next((round(t - tm, 2) for t, p in strip(s, tm) if p["cloud_audio"]["state"] == "off"), None)
    await s.face.send("mic.set_mute", {"muted": False})
    await h.shutdown(s)
    r.ok = bool(sending) and bool(speaking) and not off_while_open and muted and ended and answered(x)
    r.key = (f"{len(msgs)} privacy.state msgs; listening+sending {len(sending)}, speaking {len(speaking)}, gated "
             f"{len(gated)}; muted (no mic audio sent) after {dt} s, cloud off after {dt_off} s, session ended {ended}")
    r.details = [x, {"states": [(p["mic"]["state"], p["cloud_audio"]["state"], p["speaker"]["state"])
                                for _, p in msgs][:40]}]


@scenario(33, "camera_off_honest", "Camera switched OFF (default): 'what do you see?' gets an honest answer",
          "answered; says the camera is off / not available (no description of a scene); no camera_* tool succeeded; "
          "no LIVE_FALSE_ACTION_CLAIM; the camera stayed closed (privacy camera state disabled/unavailable)")
async def s_camera_off(h, r):
    s = await h.get(V2)
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    mark = s.log.mark()
    x = await h.turn(s, "מה אתה רואה עכשיו במצלמה?", label="camera-off", first_timeout=40)
    ev = s.log.since(mark)
    cam_ok = [e for e in ev if e["code"] == "TOOL_RESULT" and str(e.get("tool", "")).startswith("camera_")
              and e.get("ok")]
    false_claim = [e for e in ev if e["code"] == "LIVE_FALSE_ACTION_CLAIM"]
    honest = has(x["said"], "כבויה", "כבוי", "לא מחובר", "אין לי מצלמה", "לא זמינה", "לא פעילה", "להדליק", "לא יכול לראות",
                 "אינני רואה", "לא רואה", "off")
    cams = {p["camera"]["state"] for _, p in strip(s)}
    r.ok = answered(x) and honest and not cam_ok and not false_claim and cams <= {"disabled", "unavailable"}
    r.key = f"honest {honest}, camera tools ok {len(cam_ok)}, false claims {len(false_claim)}, strip camera {sorted(cams)}"
    r.details = [x]


@scenario(34, "calendar_not_connected", "Google NOT connected: a calendar add request is never claimed as done",
          "answered; no 'הוספתי/קבעתי/רשמתי ביומן' claim; mentions connecting Google (or that the calendar is not "
          "connected); no calendar write tool ran; no LIVE_FALSE_ACTION_CLAIM left uncorrected")
async def s_calendar_off(h, r):
    s = await h.get(V2)
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    mark = s.log.mark()
    x = await h.turn(s, "תוסיף לי ביומן פגישה עם רופא השיניים מחר בעשר בבוקר", label="calendar-off", first_timeout=40)
    ev = s.log.since(mark)
    writes = [e for e in ev if e["code"] == "TOOL_RESULT" and str(e.get("tool", "")).startswith("calendar_")
              and e.get("ok")]
    claim = has(x["said"], "הוספתי", "קבעתי", "רשמתי ביומן", "הכנסתי ליומן", "נוסף ליומן", "האירוע נוצר")
    connect = has(x["said"], "גוגל", "Google", "לחבר", "מחובר", "חיבור")
    false_claim = [e for e in ev if e["code"] == "LIVE_FALSE_ACTION_CLAIM"]
    r.ok = answered(x) and not claim and connect and not writes
    r.key = (f"claimed done {claim}, mentions connecting {connect}, calendar tools ok {len(writes)}, tools "
             f"{x['tools']}, false-claim detector {len(false_claim)}")
    r.details = [x]


@scenario(35, "barge_in", "Barge-in with voice.half_duplex = false (the headphones/AEC setting): talking over a long "
          "answer stops it and the new question is answered",
          "JARVES's audio stops <= 2.0 s after the interrupting speech starts; the interrupting question is answered "
          "correctly (4); session open. (With the default half_duplex = true the mic is gated while he speaks: "
          "barge-in is off by design.)")
async def s_barge(h, r):
    s = await h.boot("barge", over={"voice": {"half_duplex": False}})
    if s.boot_error:
        r.ok, r.key = False, s.boot_error
        return
    await h.say("ספר לי בבקשה סיפור ארוך ומפורט על ההיסטוריה של העיר ירושלים, לפחות דקה.")
    t_q = time.time()
    talking = await wait_for(lambda: speech_s(t_q, time.time()) >= 3.0, 40)
    t_int = time.time()
    x = await h.turn(s, "רגע, עצור. כמה זה שתיים ועוד שתיים?", label="barge-in")
    segs = segments(t_int - 0.5, t_int + 30)
    stop_s = round(segs[0][1] - t_int, 2) if segs and segs[0][0] <= t_int + 0.3 else None
    correct = has(x["said"], "4", "ארבע")
    barges = (getattr(s.core.live, "_stats", {}) or {}).get("barge_ins") if s.core.live else None
    await h.shutdown(s)
    r.ok = talking and stop_s is not None and stop_s <= 2.0 and correct and x["open"]
    r.key = f"long answer playing {talking}; old speech stopped {stop_s} s after the interruption; answered 4 {correct}; " \
            f"barge_ins {barges}"
    r.details = [x, {"segments_after_interrupt": [[round(a - t_int, 1), round(b - t_int, 1)] for a, b in segs][:8]}]


# ============================================================================================ runner + report
def parse_only(spec: str | None) -> list[int]:
    if not spec:
        return [n for n, *_ in SCEN]
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


LIVE_COST = {32: 1.0, 33: 1.0, 34: 1.0, 35: 1.5, 19: 1.0, 20: 1.5, 21: 3.0, 22: 0, 23: 0, 24: 1.5, 25: 1.5, 26: 1.5, 28: 2.5, 29: 1.0, 30: 1.5, 15: 6.0}


def table(results: list[Result]) -> str:
    lines = [f"{'#':>2}  {'scenario':22} {'result':6} {'time':>6} {'voice':>6}  key number"]
    for x in results:
        res = "SKIP" if x.ok is None else "PASS" if x.ok else "FAIL"
        lines.append(f"{x.n:>2}  {x.name:22} {res:6} {x.seconds:5.0f}s {x.voice_s / 60:5.1f}m  {x.key}")
    return "\n".join(lines)


def _as_list(v) -> list:
    """details are a list; a stored result may hold a dict (KeyError on slicing in the 19 Oct #16 re-run)."""
    return v if isinstance(v, list) else [v] if v else []


def _md(v) -> str:
    return str(v).replace("|", "/").replace("\n", " ")


STORE = ROOT / "suite_results.json"


def load_store(fresh: bool = False) -> dict:
    if not fresh:
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"results": {}, "voice_s": 0.0, "runs": []}


def write_report(store: dict, notes: list[str]) -> None:
    results = [Result(**{k: v for k, v in d.items() if k in Result.__dataclass_fields__})
               for _, d in sorted(store["results"].items(), key=lambda kv: int(kv[0]))]
    runs_of = {int(k): d.get("run", "") for k, d in store["results"].items()}
    passed = sum(1 for x in results if x.ok)
    failed = [x for x in results if x.ok is False]
    skipped = [x for x in results if x.ok is None]
    voice_min = store["voice_s"] / 60
    out = [f"# Pre-use voice test report (silent, headless) — {datetime.now():%Y-%m-%d %H:%M}", "",
           "Produced by `core/tools/suite_voice.py`. Virtual mic with locally synthesised speech (WinRT, never "
           "played), virtual speaker (measured, never played), fake face client; REAL Core, GPT-Live, backend model "
           "and web search, Skills, budget and openWakeWord. No real microphone, speaker, Godot window or OS volume.",
           "Each row is the LATEST run of that scenario (runs are listed at the end; live voice is capped, so "
           "scenarios ran in groups).", "",
           f"**Result: {passed}/{len(results)} PASS, {len(failed)} FAIL, {len(skipped)} skipped.** Live voice over "
           f"all {len(store['runs'])} suite runs (conservative wall time while a session was open or connecting): "
           f"**{voice_min:.1f} min ≈ ${voice_min * 0.05:.2f}** plus backend tokens.", ""]
    out += notes
    out += ["", "| # | Scenario | Result | Key number | Time | Live voice | Run |", "|---|---|---|---|---|---|---|"]
    for x in results:
        res = "SKIP" if x.ok is None else "**PASS**" if x.ok else "**FAIL**"
        out.append(f"| {x.n} | {x.name} | {res} | {_md(x.key)} | {x.seconds:.0f} s | {x.voice_s / 60:.1f} min "
                   f"| {runs_of.get(x.n, '')} |")
    out += ["", "## Scenarios: description and PASS criteria", ""]
    for x in results:
        out.append(f"- **{x.n}. {x.name}**: {x.desc}. PASS if: {x.criteria}.")
    if failed:
        out += ["", "## Failures with evidence", ""]
        for x in failed:
            out.append(f"### {x.n}. {x.name}")
            out.append(f"- Key: {_md(x.key)}")
            for d in _as_list(x.details)[:30]:
                if isinstance(d, dict) and "asked" in d:
                    out.append(f"- asked `{_md(d['asked'][:70])}` heard `{_md(d['heard'][:80])}` said "
                               f"`{_md(d['said'][:140])}` dead air {d['dead_air_s']} s, tools {d['tools']}, screen "
                               f"{d['activity']}, face {d['emotions']}, open {d['open']}"
                               + (f", correct {d['correct']}" if "correct" in d else "")
                               + (f", errors {d['errors']}" if d.get("errors") else ""))
                else:
                    out.append(f"- {_md(json.dumps(d, ensure_ascii=False, default=str)[:400])}")
            for e in x.evidence[:12]:
                out.append(f"- log: `{_md(e)[:200]}`")
            out.append("")
    out += ["", "## Per-scenario details", ""]
    for x in results:
        out.append(f"<details><summary>{x.n}. {x.name}: {'SKIP' if x.ok is None else 'PASS' if x.ok else 'FAIL'}"
                   f"</summary>\n")
        for d in _as_list(x.details)[:40]:
            if isinstance(d, dict) and "asked" in d:
                out.append(f"- `{_md(d['asked'][:60])}` -> said `{_md(d['said'][:160])}` (dead air "
                           f"{d['dead_air_s']} s, answer {d['answer_s']} s, backend {d['backend_s']}, screen "
                           f"{d['activity']}, face {d['emotions']})")
            else:
                out.append(f"- {_md(json.dumps(d, ensure_ascii=False, default=str)[:600])}")
        out.append("\n</details>\n")
    out += ["## Suite runs", "", "| Run | Scenarios | Live voice | Wall |", "|---|---|---|---|"]
    for run in store["runs"]:
        out.append(f"| {run['id']} | {run['only']} | {run['voice_s'] / 60:.1f} min | {run['wall_s'] / 60:.1f} min |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="scenario numbers, e.g. 1-4,8")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=34, help="live-voice cap for this run")
    ap.add_argument("--no-report", action="store_true", help="do not (over)write the markdown report")
    ap.add_argument("--json", help="also write all results here")
    ap.add_argument("--fresh", action="store_true", help="forget the results of earlier runs")
    ap.add_argument("--notes", help="markdown lines to put at the top of the report (findings, fixes)")
    ap.add_argument("--report-only", action="store_true", help="rewrite the report from stored results; no test")
    args = ap.parse_args()
    if args.report_only:
        notes = Path(args.notes).read_text(encoding="utf-8").splitlines() if args.notes else []
        write_report(load_store(), notes)
        print(f"report: {REPORT}")
        return
    if args.list:
        for n, name, desc, crit, _ in sorted(SCEN):
            print(f"{n:>2} {name:22} {desc}\n   PASS: {crit}")
        return
    from jarves.config import load_secrets
    load_secrets(REAL)                           # key stays in the environment, never printed
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("no OPENAI_API_KEY (run: jarvis key)")
    if not port_free():
        sys.exit(f"port {PORT} is busy")
    h = Harness(args)
    meter = asyncio.create_task(h.meter())
    picked = parse_only(args.only)
    results = []
    t_run = time.time()
    for n, name, desc, crit, fn in sorted(SCEN, key=lambda x: picked.index(x[0]) if x[0] in picked else 999):
        if n not in picked:
            continue
        r = Result(n, name, desc, crit)
        if h.minutes_left() < LIVE_COST.get(n, 0.7):
            r.key = f"skipped: live-voice cap ({h.voice_wall_s / 60:.1f} of {args.max_minutes} min used)"
            results.append(r)
            continue
        print(f"\n=== {n}. {name}: {desc}", flush=True)
        t0, v0 = time.time(), h.voice_wall_s
        sess_marks = {k: s.log.mark() for k, s in h.sessions.items()}
        try:
            await asyncio.wait_for(fn(h, r), 900)
        except Exception as e:
            r.ok = False
            r.key = f"harness exception {type(e).__name__}: {str(e)[:160]}"
            r.evidence.append(traceback.format_exc().splitlines()[-3:])
        r.seconds, r.voice_s = time.time() - t0, h.voice_wall_s - v0
        if r.ok is False:
            for k, s in list(h.sessions.items()):
                r.evidence += [f"{e['code']} {e['msg'][:120]}" for e in s.log.since(sess_marks.get(k, 0))
                               if e.get("level") in ("ERROR", "WARNING")][:10]
            for home in h.homes[-3:]:
                lt = LogTail(home / "logs" / "jarves.log")
                r.evidence += [f"[{home.name}] {e['code']} {e['msg'][:120]}" for e in lt.refresh()
                               if e.get("level") in ("ERROR", "WARNING")][:6]
        results.append(r)
        print(f"--> {'PASS' if r.ok else 'FAIL'} {r.key} ({r.seconds:.0f} s, live {h.voice_wall_s / 60:.1f} min total)",
              flush=True)
    for s in list(h.sessions.values()):
        await h.shutdown(s)
    meter.cancel()
    results.sort(key=lambda x: x.n)
    print("\n" + table(results))
    print(f"\nlive voice {h.voice_wall_s / 60:.1f} min (~${h.voice_wall_s / 60 * 0.05:.2f}), run {(time.time() - t_run) / 60:.1f} min")
    if args.json:
        Path(args.json).write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1, default=str),
                                   encoding="utf-8")
    store = load_store(args.fresh)
    for r in results:
        if r.ok is None and str(r.n) in store["results"]:
            continue                                  # a cost-cap skip never hides an earlier real result
        store["results"][str(r.n)] = json.loads(json.dumps({**r.__dict__, "run": h.run_id}, ensure_ascii=False,
                                                           default=str))
    store["voice_s"] += h.voice_wall_s
    store["runs"].append({"id": h.run_id, "only": args.only or "all", "voice_s": h.voice_wall_s,
                          "wall_s": time.time() - t_run})
    ROOT.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"live voice over all stored runs: {store['voice_s'] / 60:.1f} min")
    if not args.no_report:
        notes = []
        if args.notes and Path(args.notes).exists():
            notes = Path(args.notes).read_text(encoding="utf-8").splitlines()
        write_report(store, notes)
        print(f"report: {REPORT}")


if __name__ == "__main__":
    asyncio.run(main())
