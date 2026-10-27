"""`jarvis wake record`: the guided wake-word recorder (issue: owner's mic setup, 26 Oct 2026, 19/20 clips usable).

Picks a WORKING microphone first (a mic that is saturated in a silent room can never be used; USB webcam mics are the
known trap), checks that Core does not hold the mic, measures the room, coaches 3 good practice phrases (auto-level of
the Windows default input only after two identical bad verdicts, 25 % steps, always printed), records 20 clips with
the same robust analysis the owner's script used, moves old clips to wake/samples_old (never deletes) and runs the
existing sample check (voice/wake_prod.check_samples).

Everything with a side effect is injected (`Deps`: sounddevice module, level control, input/print, clock, sleep), so the
tests run with fake devices and a frozen clock. Only levels are measured; nothing is persisted except the WAV clips.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import wake_prod

RATE = 16000
CLIP_SECONDS = 2.6
CLIPS = 20                              # clips to record
PRACTICE_OK = 3                         # good practice phrases in a row before recording
MAX_PRACTICE = 14
PROBE_SECONDS = 2.5
PROBE_BLOCKS = 5
SETTLE_AFTER_ENTER = 1.5                # s: the key click and your hands settle
SETTLE_AFTER_LEVEL = 2.5                # s: a Windows level change pops
LEVEL_STEP = 0.25                       # 25 % per change
FRAME_MS = 30
SPEECH_PERCENTILE = 90
CLIP_FRACTION = 0.003                   # >= 0.3 % of samples at full scale = clipped (a single click is ignored)
CLIP_FULL = 0.98
MIN_SNR = 20.0
# JARVES's own sample check (wake_prod.check_sample) rejects a clip whose body RMS is below MIN_RMS_DB; the live check
# uses the frame-level 90th percentile, so it is allowed 1 dB below that (-43 dBFS). Derived, so it cannot drift.
HARD_MIN = wake_prod.MIN_RMS_DB - 1.0
SATURATED_DB = -35.0                    # median room level above this, or median peak >= 0.97: saturated
SATURATED_PEAK = 0.97
NOISY_DB = -50.0
PHRASES = ["hey_jarvis", "hey_jarvis", "hi_jarvis", "hey_jarvis", "ok_jarvis"]
WEBCAM_WORDS = ("camera", "webcam", "cam ", "hd cam", "c920", "c922", "brio", "facetime")

GOOD_STEPS = ("sit about 30 cm from the microphone, normal voice, the Windows input level near 80 %, "
              "stop JARVES while recording (jarvis stop), and avoid webcam microphones")


def db(x: np.ndarray) -> float:
    r = float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0
    return 20 * np.log10(max(r, 1e-9))


def frames_db(x: np.ndarray, ms: int = FRAME_MS) -> np.ndarray:
    n = int(RATE * ms / 1000)
    return np.array([db(x[i:i + n]) for i in range(0, max(1, x.size - n), n)])


def is_webcam(name: str) -> bool:
    low = (name or "").lower() + " "
    return any(w in low for w in WEBCAM_WORDS)


def webcam_hint(name: str) -> str:
    """Appended to wake diagnoses when the mic in use looks like a webcam microphone."""
    if not is_webcam(name):
        return ""
    return (f" Note: \"{name}\" looks like a USB webcam microphone; those are often saturated or have a fixed gain "
            "that the Windows slider does not change. Use the laptop's microphone array or a headset instead "
            "(jarvis wake record picks a working one).")


def classify_probe(noise_db: float, peak: float) -> str:
    """'saturated' | 'noisy' | 'good' from the MEDIAN of the probe blocks (so one click does not decide)."""
    if peak >= SATURATED_PEAK or noise_db > SATURATED_DB:
        return "saturated"
    if noise_db > NOISY_DB:
        return "noisy"
    return "good"


def mic_diagnosis(name: str, floor_db: float | None, clipping: bool = False) -> str:
    """One line for `jarvis wake test` / the dashboard wake card: mic too hot / saturated / quiet / webcam. '' = fine."""
    parts: list[str] = []
    if floor_db is not None and floor_db > SATURATED_DB:
        parts.append(f"the microphone is saturated even in a quiet room ({floor_db:.0f} dBFS): it cannot be used; "
                     "pick another one (jarvis wake record)")
    elif clipping:
        parts.append("the microphone is too hot (the audio clips): lower its Windows input level (jarvis wake record "
                     "sets it for you)")
    elif floor_db is not None and floor_db < -75:
        parts.append(f"the microphone is silent ({floor_db:.0f} dBFS): muted, off or the wrong device "
                     "(jarvis wake record)")
    msg = ("; ".join(parts) + ".") if parts else ""
    hint = webcam_hint(name)
    return (msg + hint).strip() if (msg or hint) else ""


# ---------------------------------------------------------------------- analysis
def analyse(audio: np.ndarray, noise_db: float) -> tuple[float, float, float]:
    """(clipped fraction, speech dBFS, SNR dB). Speech = 90th percentile frame level, noise = median frame level."""
    x = audio[:, 0].astype(np.float32) / 32768.0 if audio.ndim == 2 else audio.astype(np.float32) / 32768.0
    fr = frames_db(x)
    speech = float(np.percentile(fr, SPEECH_PERCENTILE)) if fr.size else -99.0
    clipped = float(np.mean(np.abs(x) >= CLIP_FULL)) if x.size else 0.0
    return clipped, speech, speech - noise_db


def verdict(clipped: float, speech: float, snr: float) -> tuple[str, str]:
    if clipped >= CLIP_FRACTION:
        return "loud", f"too LOUD (clipping {clipped * 100:.1f} % of samples)"
    if speech < HARD_MIN:
        return "quiet", f"too QUIET ({speech:.0f} dBFS)"
    if snr < MIN_SNR:
        return "noisy", f"too much background noise (speech only {snr:.0f} dB above the room)"
    return "ok", f"good ({speech:.0f} dBFS)"


# ---------------------------------------------------------------------- Windows default-input level (ctypes only)
class WindowsInputLevel:
    """Master level 0..1 of the Windows DEFAULT recording device through IAudioEndpointVolume, with plain ctypes
    (no comtypes, no PowerShell). `get()`/`set()` return None when unavailable. Windows only."""

    def available(self) -> bool:
        return sys.platform == "win32"

    def get(self) -> float | None:
        return self._call(None)

    def set(self, level: float) -> float | None:
        return self._call(max(0.0, min(1.0, float(level))))

    def _call(self, new: float | None) -> float | None:
        if sys.platform != "win32":
            return None
        try:
            return _endpoint_volume(new)
        except Exception:
            return None


def _endpoint_volume(new: float | None) -> float:
    import ctypes
    import uuid
    from ctypes import POINTER, WINFUNCTYPE, byref, c_float, c_int, c_void_p, wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d", ctypes.c_ubyte * 16)]

    def guid(s: str) -> GUID:
        g = GUID()
        ctypes.memmove(byref(g), uuid.UUID(s).bytes_le, 16)
        return g

    def method(obj, index, *argtypes):
        vt = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        fn = WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vt[index])
        return lambda *a: fn(obj, *a)

    def release(obj):
        if obj:
            method(obj, 2)()

    ole = ctypes.oledll.ole32
    try:
        ole.CoInitializeEx(None, 0)
    except OSError:
        pass                                     # already initialised in another mode: fine
    enum, dev, vol = c_void_p(), c_void_p(), c_void_p()
    try:
        ole.CoCreateInstance(byref(guid("BCDE0395-E52F-467C-8E3D-C4579291692E")), None, 23,
                             byref(guid("A95664D2-9614-4F35-A746-DE8DB63617E6")), byref(enum))
        method(enum, 4, c_int, c_int, POINTER(c_void_p))(1, 0, byref(dev))          # eCapture, eConsole
        method(dev, 3, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(
            byref(guid("5CDF2C82-841E-4546-9722-0CF74078229A")), 23, None, byref(vol))
        if new is not None:
            method(vol, 7, c_float, c_void_p)(new, None)                             # SetMasterVolumeLevelScalar
        out = c_float()
        method(vol, 9, POINTER(c_float))(byref(out))                                 # GetMasterVolumeLevelScalar
        return round(float(out.value), 3)
    finally:
        for o in (vol, dev, enum):
            try:
                release(o)
            except Exception:
                pass


class AutoLeveler:
    """Changes the Windows default-input level, but only after TWO identical bad verdicts in a row, in 25 % steps, with a
    2.5 s settle, and it ALWAYS prints the old and the new level. Nothing else is persisted."""

    def __init__(self, ctl, say: Callable[[str], None], sleep: Callable[[float], None], enabled: bool = True) -> None:
        self.ctl, self.say, self.sleep, self.enabled = ctl, say, sleep, enabled
        self.last: str | None = None

    def reset(self) -> None:
        self.last = None

    def on_verdict(self, kind: str) -> bool:
        """True when the level was changed (the caller asks the user to say the phrase again)."""
        if not self.enabled or kind not in ("loud", "quiet"):
            self.last = kind if kind != "ok" else None
            return False
        repeat = kind == self.last
        self.last = kind
        if not repeat:
            return False
        cur = self.ctl.get()
        if cur is None:
            self.enabled = False
            return False
        new = max(0.05, cur * (1 - LEVEL_STEP)) if kind == "loud" else min(1.0, cur * (1 + LEVEL_STEP))
        if abs(new - cur) < 0.005:
            self.say(f"    The Windows mic level is already at {cur:.2f}: I cannot go further.")
            return False
        now = self.ctl.set(new)
        self.say(f"    Windows mic level changed {cur:.2f} -> {now if now is not None else new:.2f} "
                 "(undo: Windows Sound settings > Input, or run with --no-auto-level to stop me changing it).")
        self.sleep(SETTLE_AFTER_LEVEL)
        self.last = None
        return True


# ---------------------------------------------------------------------- devices
def input_devices(sd) -> list[tuple[int, str]]:
    """MME input devices, no Sound Mapper (the same names Windows Sound settings shows)."""
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0 and sd.query_hostapis(d["hostapi"])["name"] == "MME" \
                and "Sound Mapper" not in d["name"]:
            out.append((i, d["name"]))
    return out


def is_busy_error(e: Exception) -> bool:
    t = str(e).lower()
    return any(k in t for k in ("busy", "in use", "exclusive", "-9985", "-9996", "unanticipated host error"))


@dataclass
class Probe:
    index: int
    name: str
    noise_db: float | None
    peak: float | None
    verdict: str                         # good | noisy | saturated | error | busy
    detail: str = ""


def probe_device(sd, idx: int, name: str) -> Probe:
    """2.5 s of silence; the MEDIAN of 5 blocks of level and peak."""
    try:
        a = sd.rec(int(PROBE_SECONDS * RATE), samplerate=RATE, channels=1, dtype="int16", device=idx)
        sd.wait()
    except Exception as e:
        return Probe(idx, name, None, None, "busy" if is_busy_error(e) else "error", f"{e.__class__.__name__}")
    x = np.asarray(a)[:, 0].astype(np.float32) / 32768.0
    blocks = np.array_split(x, PROBE_BLOCKS)
    levels = sorted(db(b) for b in blocks)
    peaks = sorted(float(np.max(np.abs(b))) for b in blocks)
    n_db, pk = levels[len(levels) // 2], peaks[len(peaks) // 2]
    return Probe(idx, name, n_db, pk, classify_probe(n_db, pk))


def probe_all(sd, devices: list[tuple[int, str]] | None = None) -> list[Probe]:
    return [probe_device(sd, i, n) for i, n in (devices if devices is not None else input_devices(sd))]


def recommend(rows: list[Probe]) -> int | None:
    """1-based row of the best usable mic: good first (a webcam mic last among them), then noisy; None if none."""
    for want in ("good", "noisy"):
        cands = [k for k, r in enumerate(rows, 1) if r.verdict == want]
        non_cam = [k for k in cands if not is_webcam(rows[k - 1].name)]
        if non_cam or cands:
            return (non_cam or cands)[0]
    return None


VERDICT_TEXT = {"good": "good", "noisy": "noisy (usable)", "saturated": "SATURATED even in a quiet room: not usable",
                "busy": "cannot open: busy (is JARVES running?)", "error": "cannot open"}


def describe(r: Probe) -> str:
    nd = "" if r.noise_db is None else f"{r.noise_db:.0f} dBFS"
    cam = "  [webcam mic: avoid]" if is_webcam(r.name) else ""
    return f"{r.name[:40]:40s} room {nd:>9s}   {VERDICT_TEXT.get(r.verdict, r.verdict)}{cam}"


# ---------------------------------------------------------------------- the session
@dataclass
class Deps:
    sd: object
    level: object = None                          # WindowsInputLevel-like (get/set) or None
    ask: Callable[[str], str] = input
    say: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time
    core_running: Callable[[], bool] = lambda: False
    stop_core: Callable[[], bool] = lambda: False
    default_mic: Callable[[], str] = lambda: ""
    interactive: bool = True


@dataclass
class Result:
    code: int
    saved: int = 0
    moved: int = 0
    device: str = ""
    notes: list[str] = field(default_factory=list)


def _rec(d: Deps, device: int | None, seconds: float) -> np.ndarray:
    a = d.sd.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="int16", device=device)
    d.sd.wait()
    return np.asarray(a)


def move_old(samples: Path, old: Path, clock: Callable[[], float]) -> int:
    """Old clips are MOVED to samples_old, never deleted."""
    moved = 0
    if not samples.is_dir():
        return 0
    for p in sorted(samples.glob("*.wav")):
        old.mkdir(parents=True, exist_ok=True)
        dest = old / f"{int(clock())}_{p.name}"
        k = 1
        while dest.exists():
            dest = old / f"{int(clock())}_{k}_{p.name}"
            k += 1
        shutil.move(str(p), str(dest))
        moved += 1
    return moved


def save_wav(audio: np.ndarray, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.asarray(audio, dtype="<i2").tobytes())


def passes_sample_check(audio: np.ndarray) -> bool:
    """The recorder's clip must also pass JARVES's own check (voice/wake_prod.check_sample)."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "clip.wav"
        save_wav(audio, p)
        return wake_prod.check_sample(p).ok


def choose_device(d: Deps, preselected: str | None = None) -> tuple[int, str] | None:
    devs = input_devices(d.sd)
    if not devs:
        d.say("No input devices found.")
        return None
    d.say(f"Testing each microphone for {PROBE_SECONDS} s of silence (stay quiet, do not type)...\n")
    rows = probe_all(d.sd, devs)
    for k, r in enumerate(rows, 1):
        d.say(f"  {k}) {describe(r)}")
    busy = [r for r in rows if r.verdict == "busy"]
    if busy and not any(r.verdict in ("good", "noisy") for r in rows):
        d.say("\nEvery microphone is busy: JARVES Core probably holds it. Run `jarvis stop`, then this again.")
        return None
    best = recommend(rows)
    if best is None:
        d.say("\nNo usable microphone (all saturated, silent or unavailable). Plug in a headset or use the laptop "
              "microphone array, and run this again.")
        return None
    if preselected:
        for r in rows:
            if preselected.lower() in r.name.lower() and r.verdict in ("good", "noisy"):
                best = rows.index(r) + 1
                break
    d.say(f"\nRecommended: {rows[best - 1].name}")
    if not d.interactive:
        k = best
    else:
        while True:
            sel = d.ask(f"Which microphone? (number, Enter = {best}): ").strip()
            k = int(sel) if sel.isdigit() and 1 <= int(sel) <= len(rows) else best
            if rows[k - 1].verdict in ("good", "noisy"):
                break
            d.say(f"  {rows[k - 1].name} cannot be used ({VERDICT_TEXT.get(rows[k - 1].verdict)}): choose another.")
            if not sel:
                return None
    return rows[k - 1].index, rows[k - 1].name


def run(d: Deps, *, home: Path, phrase: str = "hey jarvis", auto_level: bool = True, device: str | None = None,
        clips: int = CLIPS) -> Result:
    samples, old = home / "wake" / "samples", home / "wake" / "samples_old"
    # 2. STOP-CORE awareness
    if d.core_running():
        d.say("JARVES Core is running and holds the microphone: the recording would fail or hear the wrong thing.")
        if d.interactive and d.ask("Stop JARVES now (jarvis stop)? [y/N]: ").strip().lower() in ("y", "yes"):
            if not d.stop_core():
                d.say("Could not stop JARVES. Run `jarvis stop`, then this again.")
                return Result(3)
            d.say("JARVES stopped. Start it again afterwards with `jarvis start`.")
        else:
            d.say("Run `jarvis stop` first, then `jarvis wake record`.")
            return Result(3)

    chosen = choose_device(d, device)
    if chosen is None:
        return Result(2)
    idx, name = chosen
    d.say(f"Using: {name}")
    res = Result(0, device=name)

    # 4. level control only for the Windows default input
    default = d.default_mic()
    ctl = d.level if (d.level is not None and getattr(d.level, "available", lambda: True)()) else None
    k = min(len(name), len(default), 20)
    is_default = bool(default) and name[:k].lower() == default[:k].lower()
    cur = ctl.get() if (ctl is not None and is_default) else None
    can_auto = auto_level and cur is not None
    if can_auto:
        d.say(f"This is the Windows default input: after two identical bad readings I change its level in "
              f"{int(LEVEL_STEP * 100)} % steps and print the old and new level (now {cur:.2f}). "
              "Use --no-auto-level to forbid it.")
    elif not auto_level:
        d.say("Auto-level is off (--no-auto-level): I only tell you what to change.")
    else:
        d.say("I cannot change this microphone's level (not the Windows default input, or not Windows): I tell you "
              "what to change and wait.")
    leveler = AutoLeveler(ctl, d.say, d.sleep, enabled=can_auto)

    d.say("\nSTEP 1: room noise. Stay QUIET for 3 seconds.")
    if d.interactive:
        d.ask("Press Enter... ")
    d.sleep(SETTLE_AFTER_ENTER)
    n = _rec(d, idx, 3.0)[:, 0].astype(np.float32) / 32768.0
    noise_db = float(np.median(frames_db(n)))
    d.say(f"  room noise: {noise_db:.0f} dBFS" + (" (good)" if noise_db < -50 else " (a laptop fan is normal)"))

    d.say("\nSTEP 2: practice. Sit where you will usually sit and say 'Hey Jarvis' when told.")
    ok_row, tries = 0, 0
    while ok_row < PRACTICE_OK and tries < MAX_PRACTICE:
        tries += 1
        d.sleep(0.8)
        d.say(f"  practice {tries}: SAY NOW:  Hey Jarvis")
        clipped, speech, snr = analyse(_rec(d, idx, CLIP_SECONDS), noise_db)
        kind, msg = verdict(clipped, speech, snr)
        if kind == "ok":
            leveler.reset()
            ok_row += 1
            d.say(f"    OK: {msg}")
            continue
        ok_row = 0
        was = leveler.last
        if leveler.on_verdict(kind):
            d.say("    Say it again.")
            continue
        if leveler.enabled and kind in ("loud", "quiet") and was != kind:
            d.say(f"    {msg}: let me check once more before I change anything. Say it again.")
            continue
        tip = {"loud": "lower the mic level 10 points, or sit a bit farther / speak softer",
               "quiet": "MOVE CLOSER (about 20-30 cm), or raise the mic level by 10",
               "noisy": "move closer to the mic or reduce the noise (fan, music, window)"}[kind]
        d.say(f"    ADJUST: {msg}: {tip}")
        if d.interactive:
            d.ask("    Make the change, then press Enter when you are READY... ")
    if ok_row < PRACTICE_OK:
        d.say("\nI could not get 3 good clips in a row. Try another microphone (run this again), "
              f"and check: {GOOD_STEPS}.")
        res.code = 1
        return res

    res.moved = move_old(samples, old, d.clock)
    samples.mkdir(parents=True, exist_ok=True)
    d.say(f"\n(moved {res.moved} old recordings to {old}; nothing is deleted)")
    d.say(f"STEP 3: recording {clips} clips; bad ones are redone.")
    saved, attempt = 0, 0
    while saved < clips and attempt < clips * 3:
        ph = PHRASES[saved % len(PHRASES)]
        attempt += 1
        d.sleep(0.8)
        d.say(f"[{saved + 1}] SAY NOW:  {ph.replace('_', ' ').title()}")
        audio = _rec(d, idx, CLIP_SECONDS)
        clipped, speech, snr = analyse(audio, noise_db)
        kind, msg = verdict(clipped, speech, snr)
        if kind == "ok" and not passes_sample_check(audio):
            kind, msg = "noisy", "JARVES's own sample check would reject it"
        if kind == "ok":
            saved += 1
            save_wav(audio, samples / f"{int(d.clock())}_{ph}_{saved:02d}.wav")
            d.say(f"    saved, {msg}")
        else:
            d.say(f"    REDO: {msg}")
            d.sleep(0.5)
    res.saved = saved

    # 5. the existing sample check, then the next command
    checks = wake_prod.check_samples(samples)
    sm = wake_prod.sample_summary(checks)
    d.say(f"\nSTEP 4: JARVES's own check of {samples}: {sm['usable']} usable of {sm['files']}.")
    for c in checks:
        for m in c.problems:
            d.say(f"  REJECTED {c.path.name}: {m}")
    res.code = 0 if sm["minimum"] else 1
    d.say(f"\nNext:  jarvis wake train \"{phrase}\" --samples \"{samples}\"")
    d.say("(JARVES listens on the microphone named in voice.input_device; if you recorded with a different one, set it "
          "there. Start JARVES again with `jarvis start`.)")
    return res


# ---------------------------------------------------------------------- CLI glue
def cmd_record(a, cfg, home: Path) -> int:
    try:
        import sounddevice as sd
    except Exception as e:                           # noqa: BLE001
        print(f"No audio library available ({e.__class__.__name__}): install the voice extras.", file=sys.stderr)
        return 2
    from ..runtime.lock import held

    def stop_core() -> bool:
        import subprocess
        r = subprocess.run([sys.executable, "-m", "jarves", "stop"], capture_output=True, text=True, timeout=60)
        time.sleep(1.0)
        return r.returncode == 0 and not held(home)

    def default_mic() -> str:
        try:
            return str(sd.query_devices(kind="input")["name"])
        except Exception:
            return ""

    d = Deps(sd=sd, level=WindowsInputLevel() if sys.platform == "win32" else None,
             core_running=lambda: held(home), stop_core=stop_core, default_mic=default_mic,
             interactive=sys.stdin.isatty())
    r = run(d, home=home, phrase=cfg.voice.wake_phrase or "hey jarvis", auto_level=not getattr(a, "no_auto_level", False),
            device=getattr(a, "mic", None))
    return r.code
