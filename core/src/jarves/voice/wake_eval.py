"""Measured wake-word evaluation: `jarvis wake eval` (docs/reviews/SWV3_WAKE_REPORT.md).

Synthetic corpus, all generated in memory (nothing is played aloud, nothing comes from a real microphone):
- positives: natural variants of the wake phrase ("hey jarvis", "hi jarvis", "hey jarves", "ok jarvis", Hebrew
  "היי ג'רוויס" ...) said by every local Windows voice and, opt-in (`--openai`, metered, ~US$0.3 once, cached), by
  OpenAI TTS voices, some with an Israeli accent; each rendered at several conditions (normal, quiet webcam-mic
  level, pink noise, TV-like babble, room reverb).
- negatives: >= 20 min of everyday Hebrew/English speech, English near-misses ("hey Travis", "hey Charles"), a
  TV-like mix (two overlapping talkers + a music bed) and noise.

Every clip is streamed through the REAL `WakeListener` (the class the Core's wake loop uses) from a virtual mic
(an async iterator of 80 ms int16 chunks, as `StreamingAudio.mic_chunks` yields them). Detection rate = positives
with a wake within [start, end + 1 s]; false wakes per hour on the negative stream. Synthetic numbers are NOT
real-room evidence: the owner's own voice/mic must still be checked with `jarvis wake test`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .wake_train import NEG_EN, NEG_HE, augment, local_synth, local_voices, noise, resample, trim

SR = 16000
CHUNK_BYTES = 2560                    # 80 ms int16, what the Core's wake loop reads

VARIANTS_EN = ["hey jarvis", "hi jarvis", "hey jarves", "ok jarvis", "okay jarvis", "hello jarvis"]
VARIANTS_HE = ["היי ג'רוויס", "הי ג'ארוויס", "היי ג'ארביס", "אוקיי ג'ארביס"]
NEAR_MISS = ["hey Travis", "hey Jarvie", "hey Charles", "hey Marvin", "hi Davis", "hey service", "harvest time",
             "nervous", "hey Garvey", "hey Mavis", "hey Harvey", "hey Jarrod", "hi Travis", "Jarvis"]
TV_EN = ["and now back to our main story tonight", "the weather tomorrow will be cloudy with some rain in the north",
         "scientists say the new telescope can see further than ever before", "join us after the break for the "
         "latest sports results", "the president will meet the prime minister later this week",
         "traffic is heavy on the main highway this morning", "this recipe needs two cups of flour and an egg",
         "the match ended in a draw after extra time", "prices rose again last month according to the report",
         "here is what you need to know before you travel"]
TV_HE = ["ועכשיו נחזור לכותרות של הערב", "מחר צפוי מזג אוויר מעונן עם גשם בצפון", "המשחק הסתיים בתיקו אחרי הארכה",
         "המחירים עלו שוב בחודש שעבר", "ראש הממשלה ייפגש עם השר מחר בבוקר", "יש עומסי תנועה בכביש החוף",
         "הנה מה שצריך לדעת לפני הנסיעה", "נחזור אחרי הפרסומות עם כל החדשות", "התחזית לסוף השבוע נאה",
         "בתוכנית הערב נדבר על בריאות ותזונה"]
# bare "Jarvis" is reported on its own (calling a name without "hey" is a product choice, not a clear false wake)
BARE = {"Jarvis"}

OPENAI_TTS_MODEL = "gpt-4o-mini-tts"             # newest /v1/audio/speech model listed for this account, 2026-10-20
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse",
                 "marin", "cedar"]
OPENAI_EVAL_VOICES = {"echo", "nova", "sage", "cedar", "ballad"}   # held out (never used for any training)
ACCENTS = {"": "Say it casually, like calling a voice assistant across the room.",
           "il": "Speak English with a strong native Israeli Hebrew accent, casually, like calling a voice "
                 "assistant across the room."}


@dataclass
class Clip:
    text: str
    voice: str
    audio: np.ndarray = field(repr=False)      # float32 16 kHz, peak-normalised
    kind: str = "pos"                          # pos | neg | near | bare


# ---------------------------------------------------------------------------------------------- speech sources
def _cache_path(cache: Path, *key: str) -> Path:
    return cache / (hashlib.sha1("|".join(key).encode()).hexdigest()[:20] + ".wav")


def _write_wav(p: Path, a: np.ndarray) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())


def _read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0


def _norm(a: np.ndarray) -> np.ndarray:
    a = trim(a.astype(np.float32))
    return a / (np.abs(a).max() + 1e-9)


def local_clips(texts: list[str], kind: str, cache: Path, rates=(1.0,), split: str = "eval") -> list[Clip]:
    """Windows/espeak voices. split 'eval' -> Mark + Asaf (+ espeak); 'train' -> David, Zira, Asaf."""
    from .wake_train import _is_he
    voices = local_voices()
    pick = {"eval": {"Microsoft Mark", "Microsoft Asaf"}, "train": {"Microsoft David", "Microsoft Zira",
                                                                    "Microsoft Asaf"}}.get(split)
    voices = [v for v in voices if v.engine != "winrt" or v.id in pick] if pick else voices
    synth = local_synth(voices)
    out = []
    for v in voices:
        for t in texts:
            # a Hebrew voice also reads the English variants (Hebrew-accented English); English voices skip Hebrew
            if _is_he(t) and v.lang != "he":
                continue
            for r in rates:
                p = _cache_path(cache, "local", v.id, t, str(r))
                if p.exists():
                    a = _read_wav(p)
                else:
                    try:
                        raw, sr = synth(t, v.id, r, 1.0)
                    except Exception:
                        continue
                    a = _norm(resample(raw, sr))
                    _write_wav(p, a)
                if 0.2 * SR < len(a) < 6 * SR:
                    out.append(Clip(t, f"{v.id}@{r}", a, kind))
    return out


def openai_clips(items: list[tuple[str, str]], kind: str, cache: Path, voices: list[str],
                 cost: dict | None = None) -> list[Clip]:
    """OpenAI TTS (metered). items = [(text, accent key)]. Cached as WAV, so a re-run costs nothing."""
    import httpx
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    out, todo = [], []
    for v in voices:
        for text, acc in items:
            p = _cache_path(cache, "openai", OPENAI_TTS_MODEL, v, text, acc)
            if p.exists():
                out.append(Clip(text, f"openai:{v}{'+' + acc if acc else ''}", _read_wav(p), kind))
            else:
                todo.append((v, text, acc, p))
    if todo and not key:
        raise RuntimeError("OPENAI_API_KEY is not set (jarvis key): needed for --openai samples")

    async def one(client, sem, v, text, acc, p):
        async with sem:
            for attempt in range(3):
                try:
                    r = await client.post("https://api.openai.com/v1/audio/speech", json={
                        "model": OPENAI_TTS_MODEL, "voice": v, "input": text, "response_format": "pcm",
                        "instructions": ACCENTS[acc] if not any("֐" <= c <= "׿" for c in text)
                        else "Speak Hebrew naturally and casually, like calling a voice assistant."})
                    r.raise_for_status()
                    a = _norm(resample(np.frombuffer(r.content, "<i2").astype(np.float32) / 32768.0, 24000))
                    _write_wav(p, a)
                    if cost is not None:
                        cost["requests"] = cost.get("requests", 0) + 1
                        cost["audio_s"] = cost.get("audio_s", 0.0) + len(a) / SR
                        cost["chars"] = cost.get("chars", 0) + len(text)
                    return Clip(text, f"openai:{v}{'+' + acc if acc else ''}", a, kind)
                except httpx.HTTPError:
                    await asyncio.sleep(1.5 * (attempt + 1))
            return None

    async def run():
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {key}"}) as c:
            return await asyncio.gather(*(one(c, sem, *t) for t in todo))
    if todo:
        out += [c for c in asyncio.run(run()) if c is not None]
    return out


# ---------------------------------------------------------------------------------------------- conditions
CONDITIONS = ("normal", "quiet", "pink10", "babble10", "reverb_quiet")


def room_floor(n: int, rng: np.random.Generator, db: float = -62.0) -> np.ndarray:
    return noise("pink", n, rng) * 10 ** (db / 20)


def condition(a: np.ndarray, cond: str, rng: np.random.Generator, babble: np.ndarray) -> np.ndarray:
    """Render one clip under a listening condition. Peak levels: normal 0.3, quiet 0.03 (a webcam mic ~2-3 m away:
    the owner's log showed level 0.37 ~ -38 dBFS RMS), reverb_quiet = room reverb + quiet."""
    if cond == "reverb_quiet":
        a = augment(a, rng)
    a = a / (np.abs(a).max() + 1e-9) * (0.03 if "quiet" in cond else 0.3)
    if cond in ("pink10", "babble10"):
        s = float(np.sqrt(np.mean(a ** 2)))
        if cond == "pink10":
            n = noise("pink", len(a), rng)
        else:
            o = int(rng.integers(0, max(1, len(babble) - len(a))))
            n = babble[o:o + len(a)]
            n = n / (np.sqrt(np.mean(n ** 2)) + 1e-9)
        a = a + n * s / 10 ** (10 / 20)
    return a.astype(np.float32)


def make_babble(clips: list[Clip], rng: np.random.Generator, seconds: float = 60) -> np.ndarray:
    """Several talkers summed: TV/party babble used as interference (never the wake phrase)."""
    n = int(seconds * SR)
    out = np.zeros(n, np.float32)
    for _ in range(4):
        pos = 0
        while pos < n and clips:
            c = clips[int(rng.integers(0, len(clips)))].audio
            k = min(len(c), n - pos)
            out[pos:pos + k] += c[:k] * rng.uniform(0.3, 1.0)
            pos += k + int(SR * rng.uniform(0.05, 0.4))
    return out / (np.abs(out).max() + 1e-9)


def music_bed(n: int, rng: np.random.Generator) -> np.ndarray:
    """A simple chord progression with a beat: TV/radio music, not speech."""
    t = np.arange(n) / SR
    out = np.zeros(n, np.float32)
    seg = int(SR * 2)
    for i in range(0, n, seg):
        root = 110 * 2 ** (rng.integers(0, 12) / 12)
        tt = t[i:i + seg]
        for m in (1, 1.26, 1.5, 2):
            out[i:i + seg] += np.sin(2 * np.pi * root * m * tt).astype(np.float32) * 0.2
    beat = (np.sin(2 * np.pi * 2 * t) > 0.95).astype(np.float32) * rng.standard_normal(n).astype(np.float32) * 0.3
    return (out + beat) / (np.abs(out + beat).max() + 1e-9)


# ---------------------------------------------------------------------------------------------- streams
# Streams are generators of (segment float32, event | None): only one clip is in memory at a time (a 24-min
# negative stream as one array would be ~100 MB).
def positive_stream(clips: list[Clip], seed: int, babble: np.ndarray, conditions=CONDITIONS):
    """Every clip x condition, separated by 3 s of room tone (> the 2 s wake cooldown)."""
    rng = np.random.default_rng(seed)
    yield room_floor(SR, rng), None
    for c in clips:
        for cond in conditions:
            a = condition(c.audio, cond, rng, babble)
            seg = np.concatenate([a, np.zeros(int(SR * 3.0), np.float32)])
            yield seg + room_floor(len(seg), rng), {"len": len(a), "text": c.text, "voice": c.voice, "cond": cond,
                                                    "kind": c.kind}


def negative_stream(clips: list[Clip], seed: int, babble: np.ndarray, minutes: float = 24.0):
    """>= `minutes` of non-wake audio: sentences back to back (normal + quiet levels), TV-like mixes (speech over
    a music bed / babble) and stretches of noise/music only. Near-miss sentences are in the mix (listed by name)."""
    rng = np.random.default_rng(seed)
    target, pos, k = int(minutes * 60 * SR), 0, 0
    order = np.arange(len(clips))
    while pos < target and clips:
        if k % len(order) == 0:
            rng.shuffle(order)
        c = clips[int(order[k % len(order)])]
        k += 1
        style = ("talk", "talk", "quiet", "tv", "tv_babble")[k % 5]
        a = c.audio * (0.03 if style == "quiet" else 0.3)
        if style == "tv":
            a = a + music_bed(len(a), rng) * 0.08
        elif style == "tv_babble":
            o = int(rng.integers(0, max(1, len(babble) - len(a))))
            a = a + babble[o:o + len(a)] * 0.1
        seg = np.concatenate([a, np.zeros(int(SR * rng.uniform(0.2, 1.0)), np.float32)])
        pos += len(seg)
        yield seg + room_floor(len(seg), rng), {"len": len(a), "text": c.text, "voice": c.voice, "kind": c.kind,
                                                "style": style}
        if k % 40 == 0:                                       # room noise / fan / music only
            n = int(SR * rng.uniform(5, 15))
            filler = noise(str(rng.choice(["pink", "brown", "white"])), n, rng) * 10 ** (rng.uniform(-55, -35) / 20)
            if rng.random() < 0.5:
                filler = filler + music_bed(n, rng) * 0.1
            pos += n
            yield filler.astype(np.float32), None


# ---------------------------------------------------------------------------------------------- the real listener
async def run_listener(detector, segments, cooldown_s: float = 2.0) -> tuple[list[int], list[dict], dict]:
    """Stream the segments through the real WakeListener from a virtual mic (80 ms int16 chunks, as
    StreamingAudio.mic_chunks yields them). The listener clock is the stream position, so the cooldown is in
    audio time as on a live mic. Returns (wake sample positions, events with start/end, stats)."""
    from .wakeword import WakeListener
    pos = {"n": 0}
    wakes: list[int] = []
    events: list[dict] = []

    async def on_wake(score: float) -> None:
        wakes.append(pos["n"])

    lst = WakeListener(detector, on_wake, cooldown_s=cooldown_s, clock=lambda: pos["n"] / SR)
    carry = b""
    total = {"n": 0}

    async def mic():
        nonlocal carry
        for seg, ev in segments:
            if ev is not None:
                ev = dict(ev, start=total["n"], end=total["n"] + ev["len"])
                events.append(ev)
            total["n"] += len(seg)
            carry += (np.clip(seg, -1, 1) * 32767).astype("<i2").tobytes()
            while len(carry) >= CHUNK_BYTES:
                pos["n"] += CHUNK_BYTES // 2
                chunk, carry = carry[:CHUNK_BYTES], carry[CHUNK_BYTES:]
                yield chunk
    t0 = time.perf_counter()
    await lst.run(mic())
    st = lst.stats.as_dict()
    st["wall_s"] = round(time.perf_counter() - t0, 1)
    st["audio_min"] = round(total["n"] / SR / 60, 1)
    st["rtf"] = round(st["wall_s"] / max(1e-9, total["n"] / SR), 4)
    st["samples"] = total["n"]
    return wakes, events, st


def score_positives(wakes: list[int], events: list[dict]) -> dict:
    w = np.asarray(wakes)
    hit = [bool(len(w) and ((w >= e["start"]) & (w <= e["end"] + SR)).any()) for e in events]
    by: dict[str, dict] = {}
    for e, h in zip(events, hit):
        for key in (f"cond:{e['cond']}", f"text:{e['text']}", f"voice:{e['voice'].split('@')[0].split('+')[0]}"):
            d = by.setdefault(key, {"n": 0, "hit": 0})
            d["n"] += 1
            d["hit"] += h
    n = len(events)
    return {"detected": int(sum(hit)), "positives": n, "rate": round(sum(hit) / max(1, n), 3),
            "by": {k: round(v["hit"] / v["n"], 3) for k, v in sorted(by.items())},
            "missed": [f"{e['text']} [{e['voice']}, {e['cond']}]" for e, h in zip(events, hit) if not h]}


def score_negatives(wakes: list[int], events: list[dict], n_samples: int) -> dict:
    hours = n_samples / SR / 3600
    texts, bare = [], 0
    for w in wakes:
        e = next((e for e in events if e["start"] - SR * 0.2 <= w <= e["end"] + SR * 1.0), None)
        if e is not None and e["text"] in BARE:
            bare += 1
            continue
        texts.append(e["text"] if e else "(noise/music)")
    return {"false_wakes": len(texts), "minutes": round(hours * 60, 1),
            "per_hour": round(len(texts) / max(1e-9, hours), 2), "texts": texts, "bare_name_wakes": bare,
            "bare_name_clips": sum(e["text"] in BARE for e in events)}


@dataclass
class Corpus:
    pos: list
    neg: list
    babble: np.ndarray = field(repr=False)
    minutes: float
    seed: int
    info: dict
    conditions: tuple = CONDITIONS

    def positives(self):
        return positive_stream(self.pos, self.seed, self.babble, self.conditions)

    def negatives(self):
        return negative_stream(self.neg, self.seed + 1, self.babble, self.minutes)


def build_corpus(cache: Path, *, use_openai: bool = False, minutes: float = 24.0, seed: int = 11,
                 progress: Callable[[str], None] = print, cost: dict | None = None) -> Corpus:
    """Positives come from the eval voices only (never used by any training); negatives from every voice."""
    pos = local_clips(VARIANTS_EN + VARIANTS_HE, "pos", cache, rates=(0.9, 1.15))
    neg_texts = [t for t in NEG_EN + NEG_HE if "jarv" not in t.lower() and "ג'ארוויס" not in t] + TV_EN + TV_HE
    neg = local_clips(neg_texts, "neg", cache, rates=(0.9, 1.0, 1.2), split="all")
    near = local_clips(NEAR_MISS, "near", cache, rates=(1.0,), split="all")
    if use_openai:
        ev = sorted(OPENAI_EVAL_VOICES)
        pos += openai_clips([(t, a) for t in VARIANTS_EN for a in ACCENTS] + [(t, "") for t in VARIANTS_HE], "pos",
                            cache, ev, cost)
        near += openai_clips([(t, a) for t in NEAR_MISS for a in ACCENTS], "near", cache, ev, cost)
        neg += openai_clips([(t, "") for t in TV_EN + TV_HE + NEG_EN[:14] + NEG_HE[:14]
                             if "jarv" not in t.lower()], "neg", cache, ev, cost)
    for c in near:
        if c.text in BARE:
            c.kind = "bare"
    babble = make_babble(neg, np.random.default_rng(seed))
    info = {"positive_clips": len(pos), "positive_trials": len(pos) * len(CONDITIONS), "conditions": CONDITIONS,
            "negative_sentences": len(neg), "near_miss_clips": len(near), "negative_minutes_target": minutes,
            "openai": use_openai, "openai_tts_model": OPENAI_TTS_MODEL if use_openai else None, "seed": seed,
            "positive_voices": sorted({c.voice.split("@")[0] for c in pos})}
    progress(f"corpus: {len(pos)} positive clips x {len(CONDITIONS)} conditions, {len(neg)} negative sentences, "
             f"{len(near)} near-miss clips")
    return Corpus(pos, neg + near * 3, babble, minutes, seed, info)


async def evaluate_pipeline(name: str, make: Callable[[], object], corpus: Corpus,
                            progress: Callable[[str], None] = print) -> dict:
    det = make()
    wakes, ev_p, st_p = await run_listener(det, corpus.positives())
    p = score_positives(wakes, ev_p)
    stats_p = det.stats() if callable(getattr(det, "stats", None)) else None
    det = make()
    wakes_n, ev_n, st_n = await run_listener(det, corpus.negatives())
    n = score_negatives(wakes_n, ev_n, st_n["samples"])
    stats_n = det.stats() if callable(getattr(det, "stats", None)) else None
    row = {"pipeline": name, "detection": p, "negatives": n, "cpu": {"pos": st_p, "neg": st_n},
           "confirm": {"pos": stats_p, "neg": stats_n} if stats_p else None}
    progress(f"{name}: detected {p['detected']}/{p['positives']} ({p['rate']:.1%}), false wakes "
             f"{n['false_wakes']} in {n['minutes']} min = {n['per_hour']}/h {n['texts'][:8]}"
             + (f"; bare 'Jarvis' woke {n['bare_name_wakes']}/{n['bare_name_clips']}" if n["bare_name_clips"] else ""))
    return row


def save_report(path: Path, rows: list[dict], info: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "corpus": info, "results": rows,
                                "evidence": "synthetic TTS + generated noise through the real WakeListener; "
                                            "NOT a real-room measurement"}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
