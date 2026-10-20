"""Per-microphone wake calibration: `<state_dir>/wake/calibration.json` = {mic name: {floor_db, speech_db, gain_db,
verdict, ts, by}}. Written by `jarvis wake test` and, on the first run with a new mic, by the Core's wake loop
(room noise floor + the level of the first speech). Only levels are stored, never audio.

`diagnose()` turns measured levels/scores into the one thing the user should fix."""
from __future__ import annotations

import json
import time
from pathlib import Path

TARGET_SPEECH_DB = -24.0     # where AutoGain puts speech
MAX_GAIN_DB = 24.0


def _path(home: Path) -> Path:
    return Path(home) / "wake" / "calibration.json"


def load_all(home: Path) -> dict:
    try:
        d = json.loads(_path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_calibration(home: Path, mic: str) -> dict | None:
    c = load_all(home).get(mic)
    return c if isinstance(c, dict) else None


def save_calibration(home: Path, mic: str, *, floor_db: float, speech_db: float | None, verdict: str,
                     by: str) -> dict:
    gain = recommended_gain_db(floor_db, speech_db)
    d = load_all(home)
    d[mic] = {"floor_db": round(floor_db, 1), "speech_db": None if speech_db is None else round(speech_db, 1),
              "gain_db": gain, "verdict": verdict, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": by}
    p = _path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return d[mic]


def recommended_gain_db(floor_db: float, speech_db: float | None) -> float:
    """Starting AGC gain: lift speech towards TARGET_SPEECH_DB, never the noise floor above -50 dBFS."""
    if speech_db is None:
        return 0.0
    g = min(TARGET_SPEECH_DB - speech_db, -50.0 - floor_db, MAX_GAIN_DB)
    return round(max(0.0, g), 1)


def diagnose(*, floor_db: float, speech_db: float | None, best_score: float, attempts: int, gate: float,
             threshold: float, confirmed: int = 0, digital_silence: bool = False) -> tuple[str, str]:
    """-> (verdict code, exact advice). Codes: WRONG_MIC, TOO_QUIET, TOO_NOISY, PHRASE_NOT_MATCHED, NEAR_MISS, OK."""
    if digital_silence:
        return "WRONG_MIC", ("The microphone sends pure digital silence: it is muted, blocked by Windows privacy, "
                             "or the wrong input. Check Settings > Privacy & security > Microphone, the mic's mute "
                             "key, and pick the right device: jarvis wake test --list-mics, then set "
                             "voice.input_device in config.toml.")
    if speech_db is None:
        return "TOO_QUIET", ("No speech was heard at all. Say the phrase louder or closer, check this is the mic "
                             "you are talking to (jarvis wake test --list-mics), and raise its level in Windows "
                             "Sound settings > Input > Volume.")
    snr = speech_db - floor_db
    if speech_db < -45 or snr < 10:
        return "TOO_QUIET", (f"Your voice reaches the mic at only {speech_db:.0f} dBFS ({snr:.0f} dB above the room "
                             f"noise). Move closer (under ~1.5 m for a webcam mic), raise the input volume in Windows "
                             f"Sound settings > Input to 80-100 %, or use a closer microphone. Automatic gain is on; "
                             f"it cannot fix noise.")
    if floor_db > -35:
        return "TOO_NOISY", (f"The room/mic noise is loud ({floor_db:.0f} dBFS): turn down TV/fan noise or use "
                             f"another microphone.")
    if confirmed or best_score >= threshold:
        return "OK", "The wake phrase was recognised at this mic level."
    if best_score >= gate:
        return "NEAR_MISS", (f"Close (best score {best_score:.2f}, needs {threshold}). Say it as two clear words, "
                             f"\"Hey ... JAR-vis\", or record your own samples (jarvis wake train).")
    return "PHRASE_NOT_MATCHED", ("Speech was heard at a good level but it did not sound like the wake phrase "
                                  f"(best score {best_score:.2f}). Say \"Hey Jarvis\" (or \"Hi Jarvis\"), with "
                                  f"a short pause before the next words. {attempts} speech burst(s) heard.")
