"""Train a custom wake phrase on this device: `jarvis wake train "hey jarves" --say "היי ג'ארוויס"`.

Why this design (checked 2026-10-19):
- openWakeWord's own training notebook (automatic_model_training.ipynb) synthesizes ~100k clips with Piper
  (English-only sample generator), downloads tens of GB of negative features (ACAV100M) and room impulse responses,
  and trains in PyTorch on a GPU / Colab. Not feasible on this PC (tight RAM, no GPU) nor on a Pi, and no Hebrew.
- What IS feasible locally: keep openWakeWord's frozen front end (melspectrogram + the shared speech embedding,
  both bundled in pyopen-wakeword, already used by "hey_jarvis") and train only a small classifier head on top,
  like openWakeWord's custom models do. The head is plain numpy (.npz), so it runs on Windows and the Pi with no
  new dependency (no TensorFlow / PyTorch / ONNX export needed).

Pipeline (all local, cost-free, nothing is played aloud and nothing leaves the device):
1. positives: the phrase said by every local TTS voice (Windows OneCore voices incl. Hebrew "Asaf"; espeak-ng
   voices on Linux/Pi), at several speeds and pitches, plus the user's own recordings (`--samples DIR` of WAVs).
2. negatives: the phrase's single words, other assistants' wake words, everyday Hebrew/English sentences, noise.
3. augmentation: speed, gain, synthetic room reverb, white/pink/brown noise at 5-30 dB SNR.
4. features: the clips are streamed through the real openWakeWord front end (16 embeddings = ~2 s windows).
5. head: standardize -> 32 ReLU -> sigmoid, Adam, class-weighted.
6. evaluation on held-out data (one TTS voice never seen in training): recall, and false wakes per hour on a
   synthetic negative stream. The threshold is picked for <= 0.5 synthetic false wakes/hour.
Synthetic numbers are NOT real-room evidence: run `jarvis wake test --model <file>` (your voice) and
`jarvis wake test --false-wakes 30` (room audio, minutes) before trusting it.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import re
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

SR = 16000
CHUNK = 1280                 # 80 ms: one embedding per chunk in steady state
WINDOWS = 16                 # embeddings per classifier input (same as openWakeWord heads)
EMB = 96
SPAN_S = (WINDOWS - 1) * CHUNK / SR + 0.775      # audio seen by one window (~1.98 s)
POS_AFTER_S = 0.35           # a window ending up to this long after the phrase end is positive
FORMAT = "jarves-wake-head-v1"

NEG_EN = ["what time is it", "turn on the music", "good morning", "how is the weather today", "hey there",
          "can you hear me", "thank you very much", "let's go outside", "where are my keys", "play something nice",
          "I am going to the store", "call my mother", "the meeting is at ten", "just a moment please",
          "hey google", "hey siri", "alexa", "okay google", "hey mycroft", "hey jarvis", "jarvis", "service",
          "nervous", "hey honey", "harvest", "hey, are you there", "this is amazing", "open the window"]
# "hey <name>" hard negatives (SWV4_WAKE_TRAIN.md): the openWakeWord-based heads mostly false-wake on a greeting + a
# similar name. Deliberately DISJOINT from the eval near-misses (wake_eval.NEAR_MISS and the voice-campaign list:
# Travis, Jarvie, Charles, Garvey, Jarrod, Harvey, ...) so the measured false-wake rate stays an honest hold-out.
NEAR_EN = ["hey Marcus", "hey Carlos", "hey Jarrett", "hey Travers", "hey Chavez", "hey Darwin", "hey Garvin",
           "hey Jason", "hey Marvel", "hey Arthur", "hey Parvez", "hey Charlie", "hey Davies", "hey Jagger",
           "hey Gervais", "hey Barnes", "hi Marcus", "hi Carlos", "okay Darwin", "hey Jarrah", "hey Larkin",
           "hey Carver", "hey starfish", "hey Artie", "ok Jason", "hey Charvez", "hey Tavish", "hey Jarmusch"]
NEG_HE = ["מה השעה", "תדליק מוזיקה", "בוקר טוב", "מה מזג האוויר היום", "תודה רבה", "איפה המפתחות שלי",
          "בוא נלך הביתה", "אני הולך לחנות", "תתקשר לאמא", "הפגישה בעשר", "רגע אחד בבקשה", "היי", "שלום לך",
          "מה נשמע", "יאללה", "ג'ארוויס", "היי ג'ארוויס", "תעשה לי טובה", "סגור", "תסגור את החלון", "מעולה",
          "אני רעב", "מתי הארוחה", "יש לי שאלה", "מה השעה עכשיו", "תגיד לי משהו", "איך היה היום שלך",
          "אני לא יודע", "בוא נראה", "כמה זה עולה", "תשמיע לי שיר", "מה יש לי מחר", "תזכיר לי לקנות חלב",
          "הילדים בבית", "אני יוצא עכשיו", "זה לא נכון", "תחזור על זה", "כן בטח", "לא תודה", "מה אתה אומר",
          "החדשות של היום", "תפתח את הטלוויזיה", "אני עייף", "לילה טוב", "איפה הטלפון", "היי אמא", "היי חבר"]


def _is_he(text: str) -> bool:
    return bool(re.search(r"[֐-׿]", text))


def slug(phrase: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")
    return s or "custom_phrase"


# ---------------------------------------------------------------------- the head (numpy, also used at runtime)
def head_score(head: dict, x: np.ndarray) -> np.ndarray:
    """x: (n, WINDOWS, 96) embeddings -> (n,) probabilities."""
    z = (x.reshape(len(x), -1) - head["mu"]) / head["sd"]
    h = np.maximum(0.0, z @ head["w1"] + head["b1"])
    return 1.0 / (1.0 + np.exp(-(h @ head["w2"] + head["b2"]).ravel()))


def save_head(path: Path, head: dict, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, **{k: head[k] for k in ("mu", "sd", "w1", "b1", "w2", "b2")},
             meta=np.array(json.dumps({"format": FORMAT, "windows": WINDOWS, **meta}, ensure_ascii=False)))
    tmp.replace(path)


def load_head(path: str | Path) -> dict:
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        if meta.get("format") != FORMAT:
            raise ValueError("not a JARVES wake head")
        head = {k: z[k].astype(np.float32) for k in ("mu", "sd", "w1", "b1", "w2", "b2")}
    head.update(meta=meta, windows=int(meta.get("windows", WINDOWS)), phrase=meta.get("phrase", ""))
    return head


def train_head(x: np.ndarray, y: np.ndarray, *, hidden: int = 32, epochs: int = 40, lr: float = 2e-3,
               l2: float = 1e-4, seed: int = 0) -> dict:
    """Class-weighted binary MLP with Adam, numpy only (a few seconds for ~30k windows)."""
    rng = np.random.default_rng(seed)
    f = x.reshape(len(x), -1).astype(np.float32)
    mu, sd = f.mean(0), f.std(0) + 1e-3
    f = (f - mu) / sd
    d = f.shape[1]
    p = {"w1": (rng.standard_normal((d, hidden)) * np.sqrt(2 / d)).astype(np.float32),
         "b1": np.zeros(hidden, np.float32), "w2": (rng.standard_normal((hidden, 1)) * 0.1).astype(np.float32),
         "b2": np.zeros(1, np.float32)}
    m = {k: np.zeros_like(v) for k, v in p.items()}
    v2 = {k: np.zeros_like(v) for k, v in p.items()}
    pos = max(1, int(y.sum()))
    wpos, wneg = len(y) / (2 * pos), len(y) / (2 * max(1, len(y) - pos))
    step = 0
    for _ in range(epochs):
        order = rng.permutation(len(f))
        for i in range(0, len(f), 256):
            idx = order[i:i + 256]
            xb, yb = f[idx], y[idx].astype(np.float32)
            h_pre = xb @ p["w1"] + p["b1"]
            h = np.maximum(0, h_pre)
            out = 1 / (1 + np.exp(-(h @ p["w2"] + p["b2"]).ravel()))
            w = np.where(yb > 0, wpos, wneg).astype(np.float32)
            g_out = (w * (out - yb) / len(idx))[:, None]
            grads = {"w2": h.T @ g_out + l2 * p["w2"], "b2": g_out.sum(0)}
            g_h = (g_out @ p["w2"].T) * (h_pre > 0)
            grads["w1"] = xb.T @ g_h + l2 * p["w1"]
            grads["b1"] = g_h.sum(0)
            step += 1
            for k in p:
                m[k] = 0.9 * m[k] + 0.1 * grads[k]
                v2[k] = 0.999 * v2[k] + 0.001 * grads[k] ** 2
                p[k] -= lr * (m[k] / (1 - 0.9 ** step)) / (np.sqrt(v2[k] / (1 - 0.999 ** step)) + 1e-8)
    return {"mu": mu, "sd": sd, **p}


# ---------------------------------------------------------------------- sample sources (local TTS, files)
Synth = Callable[[str, str, float, float], "tuple[np.ndarray, int]"]   # (text, voice, rate, pitch) -> audio, sr


@dataclass
class Voice:
    id: str
    lang: str            # "he" | "en"
    engine: str


def local_voices() -> list[Voice]:
    """Every local TTS voice we can render to memory (never to the speakers)."""
    out: list[Voice] = []
    try:
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        for v in SpeechSynthesizer.all_voices:
            lang = v.language.lower()[:2]
            if lang in ("he", "en"):
                out.append(Voice(v.display_name, lang, "winrt"))
    except Exception:
        pass
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if exe:
        for lang, variants in (("en", ("m1", "m3", "f2", "f4", "m7")), ("he", ("m1", "f2", "m3"))):
            out += [Voice(f"{lang}+{x}", lang, "espeak") for x in variants]
    return out


def _wav_bytes_to_np(b: bytes) -> tuple[np.ndarray, int]:
    import io
    with wave.open(io.BytesIO(b)) as w:
        sr, ch, raw = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
    a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    return (a.reshape(-1, ch).mean(1) if ch > 1 else a), sr


def local_synth(voices: list[Voice]) -> Synth:
    by_id = {v.id: v for v in voices}

    def synth(text: str, voice: str, rate: float, pitch: float) -> tuple[np.ndarray, int]:
        v = by_id[voice]
        if v.engine == "espeak":
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            r = subprocess.run([exe, "-v", v.id, "-s", str(int(170 * rate)), "-p", str(int(50 * pitch)), "--stdout",
                                text], capture_output=True, timeout=30)
            return _wav_bytes_to_np(r.stdout)

        async def go() -> bytes:
            from winrt.windows.media.speechsynthesis import SpeechSynthesizer
            from winrt.windows.storage.streams import Buffer, InputStreamOptions
            s = SpeechSynthesizer()
            s.voice = next(x for x in SpeechSynthesizer.all_voices if x.display_name == v.id)
            s.options.speaking_rate = rate
            s.options.audio_pitch = pitch
            stream = await s.synthesize_text_to_stream_async(text)
            buf = Buffer(stream.size)
            await stream.read_async(buf, stream.size, InputStreamOptions.NONE)
            return bytes(memoryview(buf)[: buf.length])
        return _wav_bytes_to_np(asyncio.run(go()))
    return synth


def read_samples(folder: Path, files: list[Path] | None = None) -> list[np.ndarray]:
    """The user's own recordings of the phrase (any WAV, int16). `files` = only these (the ones that passed the
    quality check in wake_prod.check_samples)."""
    out = []
    for p in (sorted(files) if files is not None else sorted(Path(folder).glob("*.wav"))):
        a, sr = _wav_bytes_to_np(p.read_bytes())
        out.append(resample(a, sr))
    return out


# ---------------------------------------------------------------------- audio helpers
def resample(a: np.ndarray, sr: int, to: int = SR) -> np.ndarray:
    if sr == to or not len(a):
        return a.astype(np.float32)
    n = int(round(len(a) * to / sr))
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)


def trim(a: np.ndarray, floor: float = 0.01) -> np.ndarray:
    idx = np.flatnonzero(np.abs(a) > floor * max(1e-6, float(np.abs(a).max())))
    return a[idx[0]: idx[-1] + 1] if len(idx) else a


def noise(kind: str, n: int, rng: np.random.Generator) -> np.ndarray:
    w = rng.standard_normal(n).astype(np.float32)
    if kind == "white":
        return w
    spec = np.fft.rfft(w)
    f = np.arange(len(spec)) + 1.0
    spec /= np.sqrt(f) if kind == "pink" else f          # pink 1/f power, brown 1/f^2
    out = np.fft.irfft(spec, n).astype(np.float32)
    return out / (out.std() + 1e-9)


def augment(a: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    a = resample(a, SR, int(SR * rng.uniform(0.9, 1.1)))         # speed / pitch shift
    if rng.random() < 0.6:                                       # small room: exponentially decaying noise IR
        n = int(SR * rng.uniform(0.05, 0.35))
        ir = rng.standard_normal(n) * np.exp(-np.arange(n) / (n / rng.uniform(3, 8)))
        ir[0] = 1.0
        a = np.convolve(a, ir / np.abs(ir).sum() * 2.5)[: len(a) + n // 2].astype(np.float32)
    a = a / (np.abs(a).max() + 1e-9) * rng.uniform(0.15, 0.9)
    return a.astype(np.float32)


def background(n: int, rng: np.random.Generator, level: float) -> np.ndarray:
    return noise(rng.choice(["white", "pink", "brown"]), n, rng) * level


# ---------------------------------------------------------------------- stream -> windows
def build_stream(items: list[tuple[np.ndarray, bool]], rng: np.random.Generator) -> tuple[np.ndarray, list]:
    """Concatenate clips with noise gaps. Returns (float32 audio, [(start, end, is_positive)] in samples)."""
    parts, events, pos = [], [], 0
    for a, positive in items:
        gap = int(SR * rng.uniform(0.4, 1.2))
        snr_db = rng.uniform(5, 30)
        level = float(np.sqrt(np.mean(a ** 2)) + 1e-6) / (10 ** (snr_db / 20))
        seg = np.concatenate([np.zeros(gap, np.float32), a, np.zeros(int(SR * 0.3), np.float32)])
        seg = seg + background(len(seg), rng, level)
        events.append((pos + gap, pos + gap + len(a), positive))
        parts.append(seg)
        pos += len(seg)
    parts.append(background(int(SR * SPAN_S), rng, 0.002))
    return np.clip(np.concatenate(parts), -1, 1).astype(np.float32), events


def real_embedder() -> Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """The openWakeWord front end. Returns fn(audio) -> (embeddings (m, 96), index of the newest embedding after
    each 80 ms chunk)."""
    import pyopen_wakeword as m
    feats = m.OpenWakeWordFeatures.from_builtin()

    def embed(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        feats.reset()
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        embs, last = [], []
        for i in range(0, len(pcm) - CHUNK + 1, CHUNK):
            for e in feats.process_streaming(pcm[i:i + CHUNK].tobytes()):
                embs.extend(e.reshape(-1, EMB))
            last.append(len(embs) - 1)
        return np.asarray(embs, np.float32), np.asarray(last)
    return embed


def windows(embs: np.ndarray, last: np.ndarray, events: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X (n,16,96), y (n,), window end sample (n,)) with ambiguous windows left out (see module doc)."""
    starts = np.array([e[0] for e in events if e[2]] or [-1])
    ends = np.array([e[1] for e in events if e[2]] or [-1])
    xs, ys, ts = [], [], []
    span = int(SPAN_S * SR)
    for k, j in enumerate(last):
        if j < WINDOWS - 1:
            continue
        t = (k + 1) * CHUNK
        overlap = (starts < t) & (ends > t - span)
        label = 0
        if overlap.any():
            e = ends[overlap].max()
            if e <= t <= e + POS_AFTER_S * SR and starts[overlap].min() >= t - span - 0.2 * SR:
                label = 1
            elif t >= e - 0.3 * SR:
                continue                           # the phrase is (almost) whole but not at the end: ambiguous
        xs.append(embs[j - WINDOWS + 1: j + 1])
        ys.append(label)
        ts.append(t)
    if not xs:
        return np.zeros((0, WINDOWS, EMB), np.float32), np.zeros(0, int), np.zeros(0, int)
    return np.stack(xs), np.asarray(ys), np.asarray(ts)


def evaluate(head: dict, embs: np.ndarray, last: np.ndarray, events: list, n_samples: int,
             threshold: float, cooldown_s: float = 2.0) -> dict:
    """Streaming replay: recall over positive events, false wakes per hour outside them."""
    xs, ts = [], []
    for k, j in enumerate(last):
        if j >= WINDOWS - 1:
            xs.append(embs[j - WINDOWS + 1: j + 1])
            ts.append((k + 1) * CHUNK)
    if not xs:
        return {"recall": 0.0, "false_per_hour": 0.0, "hours": 0.0, "positives": 0}
    sc, ts = head_score(head, np.stack(xs)), np.asarray(ts)
    pos = [(s, e) for s, e, p in events if p]
    hit = 0
    for s, e in pos:
        m = (ts >= e) & (ts <= e + 0.6 * SR)
        hit += bool(m.any() and sc[m].max() >= threshold)
    false, cool = 0, -1
    for t, s in zip(ts, sc):
        if s >= threshold and t > cool:
            cool = t + cooldown_s * SR
            if not any(st - 0.2 * SR <= t <= e + 0.8 * SR for st, e in pos):
                false += 1
    neg_hours = max(1e-9, (n_samples - sum(e - s for s, e in pos)) / SR / 3600)
    return {"recall": round(hit / max(1, len(pos)), 3), "false_per_hour": round(false / neg_hours, 2),
            "false_wakes": false, "hours": round(neg_hours, 3), "positives": len(pos)}


def pick_threshold(head, embs, last, events, n, target_fph: float = 0.5) -> tuple[float, dict]:
    best = None
    for th in np.round(np.arange(0.3, 0.96, 0.05), 2):
        r = evaluate(head, embs, last, events, n, float(th))
        if r["false_per_hour"] <= target_fph:
            return float(th), r
        best = (float(th), r)
    return best


# ---------------------------------------------------------------------- the whole pipeline
def train_phrase(phrase: str, out: Path, *, say: list[str] | None = None, samples: Path | None = None,
                 voices: list[Voice] | None = None, synth: Synth | None = None,
                 embedder: Callable | None = None, seed: int = 7, rates=(0.8, 0.95, 1.1, 1.3),
                 pitches=(0.8, 1.0, 1.2), copies: int = 2, progress: Callable[[str], None] = print,
                 sample_files: list[Path] | None = None) -> dict:
    """Generate, train, evaluate and save `out` (.npz) + `out`.json (report). Returns the report."""
    t0 = time.monotonic()
    rng = np.random.default_rng(seed)
    texts = list(dict.fromkeys([phrase, *(say or [])]))
    voices = local_voices() if voices is None else voices
    if not voices and samples is None:
        raise RuntimeError("no local TTS voice found (Windows speech voices / espeak-ng) and no --samples folder")
    synth = synth or local_synth(voices)
    embed = embedder or real_embedder()
    held = next((v.id for v in voices if v.lang == "en" and sum(x.lang == "en" for x in voices) > 1), None)
    words = [w for t in texts for w in re.split(r"\s+", t) if len(w) > 1]
    near = lambda s: max(difflib.SequenceMatcher(None, s.lower(), t.lower()).ratio() for t in texts) > 0.75
    negs = {"en": [s for s in NEG_EN + NEAR_EN + words if not _is_he(s) and not near(s)],
            "he": [s for s in NEG_HE + words if _is_he(s) and not near(s)]}

    def render(text: str, v: Voice, rate: float, pitch: float) -> np.ndarray | None:
        try:
            a, sr = synth(text, v.id, rate, pitch)
        except Exception:
            return None
        a = trim(resample(a, sr))
        return a if 0.2 * SR < len(a) < 3.0 * SR else (a[: int(3.0 * SR)] if len(a) >= 3.0 * SR else None)

    sets = {"train": [], "test": []}
    n_tts = 0
    for v in voices:
        part = "test" if v.id == held else "train"
        for text in texts:
            if _is_he(text) != (v.lang == "he"):
                continue
            for r in rates:
                for p in pitches:
                    a = render(text, v, r, p)
                    if a is not None:
                        n_tts += 1
                        sets[part] += [(augment(a, rng), True) for _ in range(copies)]
        for text in negs[v.lang]:
            # as many voice variants for "other speech" as for the phrase: otherwise the head learns "this voice
            # speaking" instead of "this phrase" (seen on the first real run: Asaf saying "מה השעה עכשיו" scored 0.72)
            mid = rates[min(1, len(rates) - 1)]
            for r, p in ((1.0, 1.0), (rates[-1], pitches[0]), (rates[0], pitches[-1]), (mid, pitches[-1]),
                         (rates[-1], pitches[-1]), (rates[0], pitches[0])):
                a = render(text, v, r, p)
                if a is not None:
                    # the held-out stream gets 3 augmented copies: more negative minutes for the false-wake rate
                    sets[part] += [(augment(a, rng), False) for _ in range(3 if part == "test" else 1)]
    own = read_samples(samples, sample_files) if samples else []
    for i, a in enumerate(own):                      # every 4th own recording is kept for the test
        a = trim(a)
        part = "test" if i % 4 == 3 else "train"
        sets[part] += [(augment(a, rng), True) for _ in range(copies * 3)]
    for part in sets:                                # noise-only stretches
        sets[part] += [(background(int(SR * rng.uniform(1, 3)), rng, rng.uniform(0.01, 0.2)), False)
                       for _ in range(max(10, len(sets[part]) // 8))]
    if not any(p for _, p in sets["train"]):
        raise RuntimeError(f"no positive sample could be rendered for {texts}")
    if not any(p for _, p in sets["test"]):          # no held-out voice: hold out a random 20 % instead
        idx = rng.permutation(len(sets["train"]))
        cut = len(idx) // 5
        sets["test"] = [sets["train"][i] for i in idx[:cut]]
        sets["train"] = [sets["train"][i] for i in idx[cut:]]
        held = None
    progress(f"samples: {sum(p for _, p in sets['train'])} positive / {sum(not p for _, p in sets['train'])} "
             f"negative for training, {len(sets['test'])} held out ({n_tts} TTS renders, {len(own)} own)")
    data = {}
    for part, items in sets.items():
        items = [items[i] for i in rng.permutation(len(items))]
        audio, events = build_stream(items, rng)
        embs, last = embed(audio)
        data[part] = (audio, events, embs, last)
        progress(f"{part}: {len(audio) / SR / 60:.1f} min of audio -> {len(embs)} embeddings")
    x, y, _ = windows(data["train"][2], data["train"][3], data["train"][1])
    head = train_head(x, y, seed=seed)
    audio, events, embs, last = data["test"]
    from . import wake_prod                          # production loop: owner-calibrated curve + near-miss check
    own_trim = [trim(a) for a in own]
    own_peaks = [wake_prod.stream_peak(head, embed, a, np.random.default_rng(seed + 100 + i))
                 for i, a in enumerate(own_trim)]
    held_idx = [i for i in range(len(own_trim)) if i % 4 == 3]
    threshold, ev, curve = wake_prod.calibrate(head, embs, last, events, len(audio), own_peaks, held_idx)
    near = wake_prod.near_miss_check(head, threshold, voices, synth, embed, seed=seed)
    report = {"phrase": phrase, "texts": texts, "model": str(out), "threshold": threshold, "held_out_voice": held,
              "voices": [f"{v.engine}:{v.id}" for v in voices], "own_recordings": len(own),
              "seed": seed, "curve": curve, "target_false_per_hour": 0.5,
              "owner": {"recordings": len(own), "peak_scores": [round(float(x), 3) for x in own_peaks],
                        "held_out_indices": held_idx,
                        "recall_at_threshold": wake_prod.recall_at(own_peaks, threshold),
                        "held_out_recall_at_threshold": wake_prod.recall_at([own_peaks[i] for i in held_idx],
                                                                            threshold)},
              "near_miss": near, "samples_sha256": wake_prod.samples_fingerprint(samples, sample_files),
              "train_windows": int(len(y)), "train_positive_windows": int(y.sum()), "eval": ev,
              "train_eval": evaluate(head, data["train"][2], data["train"][3], data["train"][1],
                                     len(data["train"][0]), threshold),
              "seconds": round(time.monotonic() - t0, 1), "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "evidence": "synthetic (local TTS voices + generated noise): NOT a real-room measurement",
              "next": [f"jarvis wake test --model \"{out}\"  (say the phrase: your real voice + mic)",
                       f"jarvis wake test --model \"{out}\" --false-wakes 30  (30 min of normal room audio)"]}
    save_head(out, head, {k: report[k] for k in ("phrase", "texts", "threshold", "eval", "created",
                                                 "held_out_voice")})
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def list_models(home: Path) -> list[dict]:
    """Trained heads in <home>/wake with their reports (for the dashboard)."""
    out = []
    for p in sorted((home / "wake").glob("*.npz")):
        rep = {}
        try:
            rep = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append({"path": str(p), "name": p.stem, "phrase": rep.get("phrase", p.stem),
                    "threshold": rep.get("threshold"), "eval": rep.get("eval"), "created": rep.get("created"),
                    "evidence": rep.get("evidence", "")})
    return out
