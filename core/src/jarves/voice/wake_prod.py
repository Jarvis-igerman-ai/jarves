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

# The GATE list: confusable families that are NOT in the training hard negatives (wake_train.HARD_NEG_EN/HE) and not in
# NEAR_MISS_SENTENCES, so the gate measures generalisation to unseen names, not memory. (lang, text)
NEAR_MISS_HELDOUT: list[tuple[str, str]] = [
    ("en", "hey Elvis"), ("en", "hi Melvin"), ("en", "hey Calvert"), ("en", "hey Dervish"), ("en", "hey Alvarez"),
    ("en", "hey Nolan"), ("en", "hey Jervis"), ("en", "hey Bernard"), ("en", "hey Sylvester"), ("en", "hey Norton"),
    ("en", "Hey Sarah, come here"), ("en", "Harvard is a good school"), ("en", "Carnival tickets are sold out"),
    ("en", "hey Morgan"), ("he", "היי מלווין"), ("he", "היי אלוויס"), ("he", "הרווארד זה בית ספר")]
# Bare name (no "hey"): reported, never gating. The stock model wakes on it too (SWV4_WAKE_TRAIN.md: score 0.99).
BARE_NAMES = {"Jarvis", "ג'רוויס"}
# Absolute ceilings on top of the relative gate (#595): a candidate that is merely as bad as a bad stock model on this machine
# or voice set must not pass. Tunable; chosen well above what a trained head measured at (about 0-5 false wakes per hour).
MAX_FALSE_PER_HOUR = 30.0           # synthetic stream
MAX_NEAR_MISS_WAKES = 3             # of the held-out near-miss list at the chosen threshold
BASELINE_THRESHOLD = 0.5            # the stock model's instant-accept level (voice.wake_accept) the owner quoted
RECALL_MARGIN = 0.10                # a candidate must beat the model it replaces by this much on the owner's voice

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

MIN_SYNTH_RECALL = 0.5              # a head that misses half of even its synthetic held-out phrase is broken (#issue)
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
    return scorer_peak(HeadScorer(head, embed), audio, rng)


def recall_at(peaks: list[float], threshold: float) -> float | None:
    return round(sum(p >= threshold for p in peaks) / len(peaks), 3) if peaks else None


THRESHOLDS = [float(x) for x in np.round(np.arange(0.3, 0.96, 0.05), 2)] + [0.97, 0.98, 0.99]


def calibrate(head, embs, last, events, n_samples, own_peaks: list[float], held_idx: list[int],
              target_fph: float = 0.5, *, near_peaks: list[dict] | None = None, near_budget: int = 0,
              fph_budget: float | None = None) -> tuple[float, dict, list[dict]]:
    """(threshold, synthetic evaluation at it, the curve). The curve has one row per candidate threshold:
    synthetic recall and false wakes per hour on the held-out stream, the owner's recordings (all / held out) and,
    with `near_peaks`, the held-out near-miss false wakes. Choice, in tiers (first tier with a row wins):
    1. false/hour <= target_fph and near-miss false wakes <= near_budget;
    2. false/hour <= fph_budget (the model being replaced) and near-miss <= near_budget;
    3. near-miss false wakes <= near_budget;
    4. the highest threshold.
    Inside a tier, with >= 3 own recordings, the threshold the owner's voice passes best (ties go to the HIGHER
    threshold: fewer false wakes); otherwise the lowest synthetic pick."""
    held = [own_peaks[i] for i in held_idx if i < len(own_peaks)]
    curve, rows = [], {}
    for th in THRESHOLDS:
        r = wt.evaluate(head, embs, last, events, n_samples, float(th))
        rows[float(th)] = r
        row = {"threshold": float(th), "recall": r["recall"], "false_per_hour": r["false_per_hour"],
               "owner_recall": recall_at(own_peaks, float(th)),
               "owner_held_out_recall": recall_at(held, float(th))}
        if near_peaks is not None:
            row["near_miss_false"] = sum(p["score"] >= th and p["text"] not in BARE_NAMES for p in near_peaks)
        curve.append(row)
    near_ok = lambda row: near_peaks is None or row["near_miss_false"] <= near_budget
    tiers = [[r for r in curve if r["false_per_hour"] <= target_fph and near_ok(r)]]
    if fph_budget is not None:
        tiers.append([r for r in curve if r["false_per_hour"] <= fph_budget and near_ok(r)])
    if near_peaks is not None:
        tiers.append([r for r in curve if near_ok(r)])
    ok = next((t for t in tiers if t), [])
    if ok and len(own_peaks) >= MIN_USABLE_SAMPLES:
        th = max(ok, key=lambda row: (row["owner_recall"], row["threshold"]))["threshold"]
    elif ok:
        th = ok[0]["threshold"]
    else:
        th = curve[-1]["threshold"]
    return th, rows[th], curve


# ---------------------------------------------------------------------- 3. false-wake regression (near misses)
def render_near(voices, synth, sentences: list[tuple[str, str]]) -> list[dict]:
    """Render every sentence with the local voices of its language (normal speed; RAM only)."""
    out = []
    for lang, text in sentences:
        for v in [v for v in voices if v.lang == lang]:
            try:
                a, sr = synth(text, v.id, 1.0, 1.0)
            except Exception:
                continue
            a = wt.trim(wt.resample(a, sr))
            if 0.2 * wt.SR < len(a) < 6.0 * wt.SR:
                out.append({"text": text, "voice": v.id, "audio": a})
    return out


def score_near(scorer, rendered: list[dict], seed: int = 7) -> list[dict]:
    """Peak score of every rendering. The noise stream of rendering i is the same for every model (same seed), so two
    models are compared on identical audio."""
    return [{"text": r["text"], "voice": r["voice"],
             "score": scorer_peak(scorer, r["audio"], np.random.default_rng(seed + 500 + i))}
            for i, r in enumerate(rendered)]


def near_miss_check(head: dict | None, threshold: float, voices, synth, embed, *, seed: int = 7,
                    sentences: list[tuple[str, str]] | None = None, peaks: list[dict] | None = None) -> dict:
    """Score every near-miss sentence (rendered by the local voices of its language; RAM only). A 'false wake' =
    peak score >= threshold; bare names (BARE_NAMES) are listed apart and never count. `rendered` = how many
    renderings were scored (0 = no voice). `peaks` = precomputed scores (train_phrase makes them once)."""
    sents = sentences if sentences is not None else NEAR_MISS_SENTENCES
    if peaks is None:
        peaks = score_near(HeadScorer(head, embed), render_near(voices, synth, sents), seed)
    wakes = [{"text": p["text"], "voice": p["voice"], "score": round(p["score"], 3)}
             for p in peaks if p["score"] >= threshold and p["text"] not in BARE_NAMES]
    bare = [{"text": p["text"], "voice": p["voice"], "score": round(p["score"], 3)}
            for p in peaks if p["score"] >= threshold and p["text"] in BARE_NAMES]
    return {"sentences": len(sents), "rendered": len(peaks), "false_wakes": wakes, "bare_name_wakes": bare,
            "worst_score": round(max([p["score"] for p in peaks if p["text"] not in BARE_NAMES] or [0.0]), 3),
            "threshold": threshold,
            "scores": [{"text": p["text"], "voice": p["voice"], "score": round(p["score"], 3)} for p in peaks]}


# ---------------------------------------------------------------------- 3b. scorers and the baseline model
class HeadScorer:
    """Per-80 ms scores of a trained head (same windows as `wake_train.evaluate`)."""
    name = "candidate"

    def __init__(self, head: dict, embed) -> None:
        self.head, self.embed = head, embed

    def scores(self, audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        embs, last = self.embed(audio)
        xs, ts = [], []
        for k, j in enumerate(last):
            if j >= wt.WINDOWS - 1:
                xs.append(embs[j - wt.WINDOWS + 1: j + 1])
                ts.append((k + 1) * wt.CHUNK)
        if not xs:
            return np.zeros(0, int), np.zeros(0)
        return np.asarray(ts), wt.head_score(self.head, np.stack(xs))


def phonetic_skeleton(text: str) -> str:
    """Consonant skeleton of the first word after an optional greeting ("hey Marvis" -> "mrvs", "היי מרוויס" -> "מרבס"),
    for the disjointness check. c/k and z/s sound alike; Hebrew: double vav is v, vowel letters (vav, yod, alef) drop."""
    import re
    t = text.lower().strip()
    if re.search(r"[֐-׿]", t):
        s = re.sub(r"^היי,?\s+", "", t)
        s = (re.split(r"[^֐-׿]", s) or [""])[0]
        s = s.translate(str.maketrans("ךםןףץ", "כמנפצ")).replace("וו", "ב")
        s = re.sub(r"ה$", "", re.sub(r"[ויא]", "", s))
    else:
        s = re.sub(r"^(hey|hi|hello|ok|okay),?\s+", "", t)
        s = (re.split(r"[^a-z]", s) or [""])[0].replace("c", "k").replace("z", "s")
        s = re.sub(r"[aeiouy]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def phonetic_twin(a: str, b: str) -> bool:
    """Two names a TTS voice would render almost alike: same first skeleton letter, skeleton edit distance <= 1
    ("Mavis" ~ "Marvis", "Kervin" ~ "Carvis"; "Elvis" and "Davis" only rhyme)."""
    sa, sb = phonetic_skeleton(a), phonetic_skeleton(b)
    if len(sa) < 3 or len(sb) < 3 or sa[0] != sb[0]:
        return False
    prev = list(range(len(sb) + 1))
    for i, x in enumerate(sa, 1):
        cur = [i]
        for j, y in enumerate(sb, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1] <= 1


class StandardScorer:
    """The stock openWakeWord model (default hey_jarvis) scored like the Core's OpenWakeWordDetector: the model a
    custom head would replace."""

    def __init__(self, model: str = "hey_jarvis", threshold: float = BASELINE_THRESHOLD) -> None:
        self.model, self.threshold = model, threshold
        self.name = f"standard {model}"
        self._f = self._w = None

    def _load(self) -> None:
        if self._w is None:
            import pyopen_wakeword as m
            self._f = m.OpenWakeWordFeatures.from_builtin()
            self._w = m.OpenWakeWord.from_builtin(m.Model(self.model))

    def scores(self, audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        self._load()
        self._f.reset()
        self._w.reset()
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        ts, sc = [], []
        for k, i in enumerate(range(0, len(pcm) - wt.CHUNK + 1, wt.CHUNK)):
            best = 0.0
            for emb in self._f.process_streaming(pcm[i:i + wt.CHUNK].tobytes()):
                for p in self._w.process_streaming(emb):
                    best = max(best, float(p))
            ts.append((k + 1) * wt.CHUNK)
            sc.append(best)
        return np.asarray(ts), np.asarray(sc)


def scorer_peak(scorer, audio: np.ndarray, rng: np.random.Generator) -> float:
    """Highest score of `scorer` while `audio` plays inside a short quiet stream (RAM only)."""
    stream, _ = wt.build_stream([(audio.astype(np.float32), True)], rng)
    lead = wt.background(int(wt.SR * 0.3) + wt.WARMUP_CHUNKS * wt.CHUNK, rng, 0.002).astype(np.float32)
    ts, sc = scorer.scores(np.concatenate([lead, stream]))      # a running mic: the start-up windows come first
    sc = np.asarray(sc)[np.asarray(ts) > wt.WARMUP_CHUNKS * wt.CHUNK]
    return float(np.max(sc)) if len(sc) else 0.0


def measure_baseline(scorer, rendered: list[dict], own: list[np.ndarray], test_audio: np.ndarray, test_events: list,
                     seed: int = 7) -> dict:
    """The model being replaced, measured on the SAME near-miss renderings, owner recordings and synthetic held-out
    stream as the candidate (at `scorer.threshold`)."""
    th = float(getattr(scorer, "threshold", BASELINE_THRESHOLD))
    peaks = score_near(scorer, rendered, seed)
    held = {t for _, t in NEAR_MISS_HELDOUT}
    gate_p = [p for p in peaks if p["text"] in held]
    camp_p = [p for p in peaks if p["text"] not in held]
    own_peaks = [scorer_peak(scorer, a, np.random.default_rng(seed + 100 + i)) for i, a in enumerate(own)]
    held_idx = [i for i in range(len(own)) if i % 4 == 3]
    ts, sc = scorer.scores(test_audio)
    ev = wt.replay(sc, ts, test_events, len(test_audio), th)
    bad = lambda ps: [p for p in ps if p["score"] >= th and p["text"] not in BARE_NAMES]
    return {"name": getattr(scorer, "name", "baseline"), "threshold": th,
            "near_miss_false": len(bad(gate_p)), "near_miss_rendered": len(gate_p),
            "near_miss_texts": [f"{p['text']} ({p['voice']}, {p['score']:.3f})" for p in bad(gate_p)],
            "campaign_false": len(bad(camp_p)), "campaign_rendered": len(camp_p),
            "bare_name_wakes": sum(p["score"] >= th and p["text"] in BARE_NAMES for p in peaks),
            "owner_recall": recall_at(own_peaks, th),
            "owner_held_out_recall": recall_at([own_peaks[i] for i in held_idx], th),
            "false_per_hour": ev["false_per_hour"], "synthetic_recall": ev["recall"]}


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
    the owner's own held-out recordings do. With a `baseline` block (the model it replaces, measured on the same
    renderings, recordings and stream) the bar is RELATIVE: not worse than that model on near-miss false wakes and
    false wakes per hour, and better on the owner's voice by RECALL_MARGIN. Without one: no near-miss may wake it."""
    why = []
    nm = report.get("near_miss") or {}
    base = report.get("baseline")
    wakes = nm.get("false_wakes") or []
    allowed = min(int(base["near_miss_false"]), MAX_NEAR_MISS_WAKES) if base else 0
    if len(wakes) > allowed:
        who = ", ".join(f"\"{w['text']}\" ({w['score']})" for w in wakes[:3])
        more = (f" (the {base['name']} wakes on {base['near_miss_false']}; the ceiling is {MAX_NEAR_MISS_WAKES})" if base else "")
        why.append(f"{len(wakes)} near-miss sentence(s) would wake it: {who}{more}")
    own = report.get("owner") or {}
    if base:
        camp = len((report.get("campaign") or {}).get("false_wakes") or [])
        if camp > int(base.get("campaign_false", 0)):
            why.append(f"{camp} campaign phrase(s) wake it, the {base['name']} wakes on {base['campaign_false']}")
        fph = (report.get("eval") or {}).get("false_per_hour")
        if fph is not None and fph > MAX_FALSE_PER_HOUR:
            why.append(f"{fph} false wakes/hour on the synthetic stream is above the ceiling of {MAX_FALSE_PER_HOUR:g}")
        elif fph is not None and base.get("false_per_hour") is not None and fph > base["false_per_hour"]:
            why.append(f"{fph} false wakes/hour on the synthetic stream, the {base['name']} has "
                       f"{base['false_per_hour']}")
        mine, theirs = own.get("recall_at_threshold"), base.get("owner_recall")
        if mine is not None and theirs is not None and (own.get("recordings") or 0) >= MIN_USABLE_SAMPLES \
                and mine < min(1.0, theirs + RECALL_MARGIN) - 1e-9:
            why.append(f"your recordings wake it {mine:.0%}, the {base['name']} {theirs:.0%}: it must beat it by "
                       f"{RECALL_MARGIN:.0%}")
    held = own.get("held_out_recall_at_threshold")
    if held is not None and len(own.get("held_out_indices") or []) >= 1 and held < MIN_OWNER_RECALL:
        why.append(f"your held-out recordings wake it only {held:.0%} of the time (need {MIN_OWNER_RECALL:.0%})")
    allr = own.get("recall_at_threshold")
    if (held is None or held >= MIN_OWNER_RECALL) and allr is not None and (own.get("recordings") or 0) >= MIN_USABLE_SAMPLES and allr < MIN_OWNER_RECALL:
        why.append(f"your recordings wake it only {allr:.0%} of the time (need {MIN_OWNER_RECALL:.0%})")
    syn = (report.get("eval") or {}).get("recall")
    if syn is not None and syn < MIN_SYNTH_RECALL:
        why.append(f"it recognises only {syn:.0%} of its own synthetic held-out phrases (need {MIN_SYNTH_RECALL:.0%}):"
                   " the training did not work")
    return why


def format_comparison(report: dict) -> list[str]:
    """The candidate next to the model it replaces (empty when there is no baseline)."""
    base = report.get("baseline")
    if not base:
        return []
    nm, camp, own = report.get("near_miss") or {}, report.get("campaign") or {}, report.get("owner") or {}
    pct = lambda x: "-" if x is None else f"{x:.0%}"
    rows = [("", "candidate", base["name"]),
            ("threshold", report["threshold"], base["threshold"]),
            ("your recordings woken", pct(own.get("recall_at_threshold")), pct(base.get("owner_recall"))),
            ("... held out", pct(own.get("held_out_recall_at_threshold")), pct(base.get("owner_held_out_recall"))),
            ("near-miss false wakes (held-out list)", f"{len(nm.get('false_wakes') or [])}/{nm.get('rendered', 0)}",
             f"{base['near_miss_false']}/{base['near_miss_rendered']}"),
            ("campaign phrases (trained on) false wakes",
             f"{len(camp.get('false_wakes') or [])}/{camp.get('rendered', 0)}",
             f"{base['campaign_false']}/{base['campaign_rendered']}"),
            ("bare name wakes (reported, not gated)", len(nm.get("bare_name_wakes") or []) +
             len(camp.get("bare_name_wakes") or []), base["bare_name_wakes"]),
            ("synthetic false wakes/hour", (report.get("eval") or {}).get("false_per_hour"), base["false_per_hour"]),
            ("synthetic recall", pct((report.get("eval") or {}).get("recall")), pct(base["synthetic_recall"]))]
    w = max(len(str(r[0])) for r in rows)
    return ["  " + f"{str(a):<{w}}  {str(b):>12}  {str(c):>12}" for a, b, c in rows]


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
    near = any("near_miss_false" in r for r in curve)
    rows = ["  threshold  synthetic recall  false/hour  your recordings  held out" + ("  near-miss" if near else "")]
    for r in curve:
        f = lambda x: "   -  " if x is None else f"{x:6.0%}"
        rows.append(f"  {r['threshold']:<9}  {r['recall']:>14.0%}  {r['false_per_hour']:>10}  "
                    f"{f(r.get('owner_recall')):>15}  {f(r.get('owner_held_out_recall')):>8}"
                    + (f"  {r['near_miss_false']:>9}" if near else "")
                    + ("   <- chosen" if abs(r["threshold"] - chosen) < 1e-9 else ""))
    return rows
