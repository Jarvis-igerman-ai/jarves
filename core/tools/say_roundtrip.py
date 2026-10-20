"""Live round-trip check for `jarvis say`: synthesize the owner's sentence and names with an engine, transcribe the
audio with the OpenAI transcription API, print transcript vs expected with a similarity score. Costs a few cents.
Usage (from core/):  python tools/say_roundtrip.py [--engine auto|openai-tts|openai-realtime|winrt] [--wav PATH]
The key comes from the environment / `jarvis key` store and is never printed."""
from __future__ import annotations

import argparse
import asyncio
import difflib
import io
import os
import re
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SENTENCE = "שלום לנועה, לאביטל ולצחי"
NAMES = ["שלום נועה", "שלום אביטל", "שלום צחי", "שלום לנועה", "שלום לאביטל", "שלום ולצחי", "תגידי לנועה ולצחי שאני בדרך"]  # names inside a phrase: a lone word is transcribed as English


def norm(t: str) -> str:
    t = re.sub(r"[\u0591-\u05c7]", "", t)
    return re.sub(r"[^\w\s]", "", t).strip()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


async def transcribe(wav: bytes, key: str) -> str:
    import httpx
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post("https://api.openai.com/v1/audio/transcriptions",
                         headers={"Authorization": f"Bearer {key}"},
                         data={"model": "gpt-4o-transcribe", "language": "he", "response_format": "text",
                               "prompt": "תמלול בעברית. שמות: נועה, אביטל, צחי."},
                         files={"file": ("a.wav", wav, "audio/wav")})
    r.raise_for_status()
    return r.text.strip()


def build(engine: str, cfg):
    from jarves.voice.realtime import RealtimeSpeaker
    from jarves.voice.tts import OpenAISpeechTTS, WinRTTTS, make_tts
    v = cfg.voice
    if engine == "auto":
        t = make_tts(cfg, None)
        ok, q, d = t.check()
        print("auto chain usable:", ok, [e.name for e in t.usable])
        return t
    return {"openai-tts": lambda: OpenAISpeechTTS(v.speech_http_model, v.speech_voice),
            "openai-realtime": lambda: RealtimeSpeaker(v.speech_model, v.realtime_voice, v.language),
            "winrt": lambda: WinRTTTS(v.tts_voice_hint)}[engine]()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="auto")
    ap.add_argument("--wav", default=r"C:\work\say_test.wav")
    ap.add_argument("--no-names", action="store_true", help="only the full sentence")
    a = ap.parse_args()
    from jarves.config import load_config, load_secrets, state_dir
    home = state_dir()
    load_secrets(home)
    cfg, _ = load_config(None)
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("ERROR: no OPENAI_API_KEY (run: jarvis key)")
        return 2
    eng = build(a.engine, cfg)
    worst = 1.0
    for text in [SENTENCE] + ([] if a.no_names else NAMES):
        samples, rate = await eng.synth(text)
        wav = wav_bytes(samples, rate)
        if text == SENTENCE:
            Path(a.wav).write_bytes(wav)
        got = await transcribe(wav, key)
        sc = similarity(text, got)
        worst = min(worst, sc)
        print(f"expected: {text}\n  heard : {got}\n  score : {sc:.2f}")
    print(f"engine {getattr(eng, 'last_used', '') or a.engine}  WORST SCORE {worst:.2f}  wav: {a.wav}")
    return 0 if worst >= 0.6 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
