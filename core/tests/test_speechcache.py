"""voice/speechcache.py: the disk cache for the fixed start-up greeting (hermetic, tmp_path only, no clock)."""
from __future__ import annotations

import wave

import numpy as np

from jarves.voice.speechcache import SpeechCache

RATE = 16000


def tone(seconds: float = 0.5) -> np.ndarray:
    return (0.5 * np.sin(np.arange(int(RATE * seconds)) / 10)).astype(np.float32)


def test_key_is_stable_and_depends_on_every_part():
    k = SpeechCache.key("hello", "voice", "model")
    assert k == SpeechCache.key("hello", "voice", "model") and len(k) == 24
    assert k != SpeechCache.key("hello", "voice", "other-model")
    assert k != SpeechCache.key("hello", "other-voice", "model")


def test_round_trip_and_miss(tmp_path):
    c = SpeechCache(tmp_path)
    assert c.get("absent") is None                          # no cache dir yet: a miss, not an error
    c.put("k", tone(), RATE)
    got = c.get("k")
    assert got is not None and got[1] == RATE and len(got[0]) == RATE // 2
    assert np.allclose(got[0], tone(), atol=1e-3)
    assert not list((tmp_path / "cache").glob("*.tmp"))    # the temp file was renamed into place


def test_short_audio_is_never_cached(tmp_path):
    c = SpeechCache(tmp_path)
    c.put("k", tone(0.1), RATE)                             # under a quarter second: a failed synthesis
    assert c.get("k") is None and not (tmp_path / "cache").exists()


def test_clipping_on_put(tmp_path):
    c = SpeechCache(tmp_path)
    c.put("k", np.full(RATE, 3.0, np.float32), RATE)
    assert float(c.get("k")[0].max()) <= 1.0


def test_garbage_file_is_a_miss(tmp_path):
    c = SpeechCache(tmp_path)
    (tmp_path / "cache").mkdir()
    c._path("k").write_bytes(b"not a wav file")
    assert c.get("k") is None


def test_truncated_file_with_odd_byte_count_is_a_miss(tmp_path):
    c = SpeechCache(tmp_path)
    c.put("k", tone(), RATE)
    p = c._path("k")
    p.write_bytes(p.read_bytes()[:-1])                      # cut mid-sample (disk full, crash while copying)
    assert c.get("k") is None                               # a miss: the greeting is synthesised again, no crash


def test_foreign_sample_format_is_a_miss(tmp_path):
    c = SpeechCache(tmp_path)
    (tmp_path / "cache").mkdir()
    with wave.open(str(c._path("k")), "wb") as w:           # 8-bit stereo: not what put() writes
        w.setnchannels(2)
        w.setsampwidth(1)
        w.setframerate(RATE)
        w.writeframes(bytes(RATE * 2))
    assert c.get("k") is None
