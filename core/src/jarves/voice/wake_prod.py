"""Custom wake word in production (V4 package J, issue #17): the safe, reproducible loop around `wake_train`.

- `check_samples`: quality checks on the owner's recordings (%LOCALAPPDATA%\\jarves\\wake\\samples) BEFORE training;
  bad files are left out of the training (never deleted) and the reasons are printed.
- `calibrate`: the threshold from a measured curve (synthetic false wakes per hour AND the owner's own recordings),
  written into the model report as a calibration table.
- `near_miss_check`: the false-wake regression list (sentences that sound like the wake phrase) rendered by every local
  voice and scored by the freshly trained head. Used by the CLI as a gate for `--apply` and by the tests.
- `promote` / `revert`: a trained model is a CANDIDATE (wake/candidates/) until `--apply`; applying archives the model
  it replaces (wake/history/) and records how to undo it; `jarvis wake train --revert` undoes the last apply.

Privacy: samples, models and reports are biometric-like. They stay in the private state dir; reports hold counts,
scores and a hash of the samples, never file names or audio.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import wake_train as wt

# The near-miss sentences of the voice campaign (suite 22 / 23: tools/suite_voice.py NEGATIVES; the V4 voice campaign
# report docs/reviews/SWV4_CAMPAIGN_VOICE.md is not on main, so this is the list that campaign inherits). Each one
# sounds like a wake phrase; none may wake JARVES. (lang, text)
NEAR_MISS_SENTENCES: list[tuple[str, str]] = [
    ("en", "hey Travis"), ("en", "hey service"), ("en", "Jarvis"), ("en", "hey Jarvie, no"),
    ("en", "Hey Charles, come here"), ("en", "Harvest season is here"), ("en", "Have a nice day"),
    ("en", "Hey, how are you doing?"), ("en", "A jar of peanut butter"), ("en", "Let's watch Jurassic Park"),
    ("en", "Hey there, good morning"), ("en", "Service, please"),
    ("he", "היי, מה נשמע?"), ("he", "ג'רוויס"), ("he", "תגיד לי מה השעה"), ("he", "היי טרוויס"),
    ("he", "איזה יום יפה היום"), ("he", "תדליק את האור בסלון"), ("he", "הרבה עבודה היום במשרד"),
    ("he", "הבאתי לך חרוזים")]

MIN_USABLE_SAMPLES = 3              # fewer usable own recordings: the model is TTS-only (warned, not blocked)
RECOMMENDED_SAMPLES = 10
# Sample-check thresholds (check_sample). voice/wake_record.py imports these, so the recorder can never accept a clip
# this check would reject.
CLIP_LEVEL = 0.99                   # |sample| at or above this counts as clipped
CLIP_FRACTION = 0.002               # more clipped samples than this share = "clipped (too loud)"
MIN_RMS_DB = -42.0                  # below this the phrase is "too quiet"
WARN_RMS_DB = -36.0
MIN_SNR_DB = 10.0                   # below this "too noisy"
WARN_SNR_DB = 18.0

MIN_OWNER_RECALL = 0.8              # --apply is refused below this on the owner's held-out recordings (when >= 4)


# ---------------------------------------------------------------------- 1. sample quality
@dataclass
class SampleCheck:
    path: Path
    ok: bool
    seconds: float = 0.0
    peak_db: float = -99.0
    rms_db: float = -99.0
    snr_db: float = 0.0
    problems: list[str] = field(default_factory=list)       # make the file unusable
    warnings: list[str] = field(default_factory=list)       # usable, but worth re-recording


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        sr, ch, width, raw = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{width * 8}-bit WAV (16-bit PCM needed)")
    a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    return (a.reshape(-1, ch).mean(1) if ch > 1 else a), sr


def check_sample(path: Path, *, seen: set[str] | None = None) -> SampleCheck:
    c = SampleCheck(Path(path), True)
    try:
        raw = Path(path).read_bytes()
        a, sr = _read_wav(path)
    except (OSError, wave.Error, EOFError, ValueError) as e:
        c.ok = False
        c.problems.append(f"not a readable 16-bit WAV ({str(e)[:60]})")
        return c
    digest = hashlib.sha256(raw).hexdigest()
    if seen is not None:
        if digest in seen:
            c.ok = False
            c.problems.append("duplicate of another recording")
        seen.add(digest)
    if not len(a):
        c.ok = False
        c.problems.append("empty")
        return c
    c.seconds = round(len(a) / sr, 2)
    peak = float(np.abs(a).max())
    c.peak_db = round(20 * np.log10(peak + 1e-9), 1)
    if sr < 16000:
        c.warnings.append(f"sample rate {sr} Hz is below 16000 (upsampled)")
    if c.seconds < 0.3:
        c.problems.append(f"too short ({c.seconds} s): say the whole phrase")
    elif c.seconds > 6.0:
        c.problems.append(f"too long ({c.seconds} s): one phrase per file")
    elif c.seconds > 3.0:
        c.warnings.append(f"{c.seconds} s: long silence around the phrase? (trimmed automatically)")
    if peak < 0.005:
        c.problems.append("silent or almost silent: check the microphone")
    body = wt.trim(a)
    c.rms_db = round(20 * np.log10(float(np.sqrt(np.mean(body ** 2))) + 1e-9), 1)
    if peak >= 0.005 and c.rms_db < MIN_RMS_DB:
        c.problems.append(f"too quiet ({c.rms_db} dBFS): move closer or raise the microphone level")
    elif c.rms_db < WARN_RMS_DB:
        c.warnings.append(f"quiet ({c.rms_db} dBFS)")
    if float((np.abs(a) >= CLIP_LEVEL).mean()) > CLIP_FRACTION:
        c.problems.append("clipped (too loud): lower the microphone level")
    frame = int(sr * 0.02)
    n = len(a) // frame
    if n >= 10:
        e = np.sqrt((a[: n * frame].reshape(n, frame) ** 2).mean(1)) + 1e-9
        srt = np.sort(e)
        k = max(1, n // 10)
        c.snr_db = round(float(20 * np.log10(srt[-k:].mean() / srt[:k].mean())), 1)
        if c.snr_db < MIN_SNR_DB:
            c.problems.append(f"too noisy (speech only {c.snr_db} dB above the background)")
        elif c.snr_db < WARN_SNR_DB:
            c.warnings.append(f"noisy (speech {c.snr_db} dB above the background)")
    c.ok = not c.problems
    return c


def check_samples(folder: Path) -> list[SampleCheck]:
    seen: set[str] = set()
    return [check_sample(p, seen=seen) for p in sorted(Path(folder).glob("*.wav"))]


def sample_summary(checks: list[SampleCheck]) -> dict:
    usable = [c for c in checks if c.ok]
    return {"files": len(checks), "usable": len(usable), "rejected": len(checks) - len(usable),
            "enough": len(usable) >= RECOMMENDED_SAMPLES, "minimum": len(usable) >= MIN_USABLE_SAMPLES}


def samples_fingerprint(folder: Path | None, files: list[Path] | None = None) -> str:
    """One hash over the recordings used (reproducibility: the same hash + seed gives the same model)."""
    if not folder:
        return ""
    h = hashlib.sha256()
    for p in (sorted(files) if files is not None else sorted(Path(folder).glob("*.wav"))):
        h.update(hashlib.sha256(Path(p).read_bytes()).digest())
    return h.hexdigest()


# ---------------------------------------------------------------------- 2. scores and the calibration curve
def stream_peak(head: dict, embed, audio: np.ndarray, rng: np.random.Generator) -> float:
    """Highest head score while `audio` plays inside a short quiet stream (RAM only)."""
    items = [(audio.astype(np.float32), True)]
    stream, _ = wt.build_stream(items, rng)
    embs, last = embed(stream)
    xs = [embs[j - wt.WINDOWS + 1: j + 1] for j in last if j >= wt.WINDOWS - 1]
    return float(wt.head_score(head, np.stack(xs)).max()) if xs else 0.0


def recall_at(peaks: list[float], threshold: float) -> float | None:
    return round(sum(p >= threshold for p in peaks) / len(peaks), 3) if peaks else None


def calibrate(head, embs, last, events, n_samples, own_peaks: list[float], held_idx: list[int],
              target_fph: float = 0.5) -> tuple[float, dict, list[dict]]:
    """(threshold, synthetic evaluation at it, the curve). The curve has one row per candidate threshold:
    synthetic recall and false wakes per hour on the held-out stream, plus the owner's recordings (all / held out).
    With >= 3 own recordings the operating point is the threshold under the false-wake target that the owner's own
    voice passes best (ties go to the HIGHER threshold: fewer false wakes); otherwise the synthetic pick."""
    held = [own_peaks[i] for i in held_idx if i < len(own_peaks)]
    curve, rows = [], {}
    for th in np.round(np.arange(0.3, 0.96, 0.05), 2):
        r = wt.evaluate(head, embs, last, events, n_samples, float(th))
        rows[float(th)] = r
        curve.append({"threshold": float(th), "recall": r["recall"], "false_per_hour": r["false_per_hour"],
                      "owner_recall": recall_at(own_peaks, float(th)),
                      "owner_held_out_recall": recall_at(held, float(th))})
    ok = [row for row in curve if row["false_per_hour"] <= target_fph]
    if ok and len(own_peaks) >= MIN_USABLE_SAMPLES:
        best = max(ok, key=lambda row: (row["owner_recall"], row["threshold"]))
        th = best["threshold"]
    elif ok:
        th = ok[0]["threshold"]
    else:
        th = curve[-1]["threshold"]
    return th, rows[th], curve


# ---------------------------------------------------------------------- 3. false-wake regression (near misses)
def near_miss_check(head: dict, threshold: float, voices, synth, embed, *, seed: int = 7,
                    sentences: list[tuple[str, str]] | None = None) -> dict:
    """Render every near-miss sentence with the local voices of its language (normal speed; RAM only) and score it.
    A 'false wake' = peak score >= threshold. `rendered` = how many renderings were actually scored (0 = no voice)."""
    rng = np.random.default_rng(seed + 500)
    rendered, wakes, worst = 0, [], 0.0
    for lang, text in (sentences if sentences is not None else NEAR_MISS_SENTENCES):
        for v in [v for v in voices if v.lang == lang]:
            try:
                a, sr = synth(text, v.id, 1.0, 1.0)
            except Exception:
                continue
            a = wt.trim(wt.resample(a, sr))
            if not 0.2 * wt.SR < len(a) < 6.0 * wt.SR:
                continue
            peak = stream_peak(head, embed, a, rng)
            rendered += 1
            worst = max(worst, peak)
            if peak >= threshold:
                wakes.append({"text": text, "voice": v.id, "score": round(peak, 3)})
    return {"sentences": len(sentences if sentences is not None else NEAR_MISS_SENTENCES), "rendered": rendered,
            "false_wakes": wakes, "worst_score": round(worst, 3), "threshold": threshold}


# ---------------------------------------------------------------------- 4. never overwrite; apply with history; revert
def wake_dir(home: Path) -> Path:
    return Path(home) / "wake"


def default_samples_dir(home: Path) -> Path:
    return wake_dir(home) / "samples"


def candidate_path(home: Path, phrase_slug: str) -> Path:
    return wake_dir(home) / "candidates" / f"{phrase_slug}.npz"


def _log_path(home: Path) -> Path:
    return wake_dir(home) / "apply_log.json"


def _read_log(home: Path) -> list[dict]:
    try:
        v = json.loads(_log_path(home).read_text(encoding="utf-8"))
        return v if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def _write_log(home: Path, entries: list[dict]) -> None:
    p = _log_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def gate(report: dict) -> list[str]:
    """Reasons NOT to apply this model (empty = fine). Synthetic numbers alone never block; the near-miss list and
    the owner's own held-out recordings do."""
    why = []
    nm = report.get("near_miss") or {}
    if nm.get("false_wakes"):
        who = ", ".join(f"\"{w['text']}\" ({w['score']})" for w in nm["false_wakes"][:3])
        why.append(f"{len(nm['false_wakes'])} near-miss sentence(s) would wake it: {who}")
    own = report.get("owner") or {}
    held = own.get("held_out_recall_at_threshold")
    if held is not None and len(own.get("held_out_indices") or []) >= 1 and held < MIN_OWNER_RECALL:
        why.append(f"your held-out recordings wake it only {held:.0%} of the time (need {MIN_OWNER_RECALL:.0%})")
    return why


def promote(home: Path, cfg, candidate: Path, phrase: str, threshold: float, active: Path) -> dict:
    """Make `candidate` the wake model: archive what it replaces, copy it to `active`, set the identity, remember
    how to undo. Raises IdentityError (nothing changed then)."""
    from ..identity import Identity
    ident = Identity(home, cfg)
    before = ident.wake()
    hist = wake_dir(home) / "history"
    archived = None
    if active.exists():
        hist.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        archived = hist / f"{active.stem}-{stamp}.npz"
        shutil.copy2(active, archived)
        if active.with_suffix(".json").exists():
            shutil.copy2(active.with_suffix(".json"), archived.with_suffix(".json"))
    active.parent.mkdir(parents=True, exist_ok=True)
    if candidate.resolve() != active.resolve():
        shutil.copy2(candidate, active)
        if candidate.with_suffix(".json").exists():
            shutil.copy2(candidate.with_suffix(".json"), active.with_suffix(".json"))
    try:
        ident.set_wake(str(active), phrase, threshold, by="cli")
    except Exception:
        if archived:                                  # identity refused: put the old model back
            shutil.copy2(archived, active)
        elif active.exists() and candidate.resolve() != active.resolve():
            active.unlink()
        raise
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": str(active), "phrase": phrase,
             "archived": str(archived) if archived else None,
             "previous": {k: before.get(k) for k in ("model", "phrase", "threshold")}}
    _write_log(home, _read_log(home) + [entry])
    return entry


def revert(home: Path, cfg) -> dict | None:
    """Undo the last `--apply`: the replaced model file and the previous wake setting come back. A trained file is
    never deleted (it stays on disk; the model it replaced goes back in place, the newer one is kept in history).
    Without a log (applied by an older version) the setting goes back to the stock "Hey Jarvis". None = already on
    the stock model and nothing to undo."""
    from ..identity import Identity
    ident = Identity(home, cfg)
    log = _read_log(home)
    if not log:
        cur = ident.wake()
        if cur.get("model") == "hey_jarvis":
            return None
        ident.set_wake("hey_jarvis", "Hey Jarvis", None, by="cli")
        return {"model": "hey_jarvis", "phrase": "Hey Jarvis", "restored_file": None}
    e = log[-1]
    active = Path(e["model"])
    arch = Path(e["archived"]) if e.get("archived") else None
    if arch is not None and arch.exists():
        hist = wake_dir(home) / "history"
        hist.mkdir(parents=True, exist_ok=True)
        if active.exists():
            keep = hist / f"{active.stem}-reverted-{time.strftime('%Y%m%d-%H%M%S')}.npz"
            shutil.copy2(active, keep)
        shutil.copy2(arch, active)
        if arch.with_suffix(".json").exists():
            shutil.copy2(arch.with_suffix(".json"), active.with_suffix(".json"))
    prev = e.get("previous") or {}
    model = prev.get("model") or "hey_jarvis"
    if model != "hey_jarvis" and not Path(model).is_file():
        model = "hey_jarvis"
    if model == "hey_jarvis":
        ident.set_wake("hey_jarvis", prev.get("phrase") or "Hey Jarvis", prev.get("threshold"), by="cli")
    else:
        ident.set_wake(model, prev.get("phrase"), prev.get("threshold"), by="cli")
    _write_log(home, log[:-1])
    return {"model": model, "phrase": prev.get("phrase"), "restored_file": str(arch) if arch else None}


def format_curve(curve: list[dict], chosen: float) -> list[str]:
    rows = ["  threshold  synthetic recall  false/hour  your recordings  held out"]
    for r in curve:
        f = lambda x: "   -  " if x is None else f"{x:6.0%}"
        rows.append(f"  {r['threshold']:<9}  {r['recall']:>14.0%}  {r['false_per_hour']:>10}  "
                    f"{f(r.get('owner_recall')):>15}  {f(r.get('owner_held_out_recall')):>8}"
                    + ("   <- chosen" if abs(r["threshold"] - chosen) < 1e-9 else ""))
    return rows
