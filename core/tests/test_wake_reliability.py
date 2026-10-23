"""Wake reliability (#17): name matching, AGC, two-stage confirm, 'heard speech, no wake match', calibration,
`jarvis wake test` verdicts and the eval harness. Offline: scripted detectors / ASR, virtual mics, no model."""
import asyncio
import json
import types

import numpy as np
import pytest

from conftest import FakeSTT, FakeTTS, open_face
from jarves.app import Core
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio
from jarves.voice.wake_calib import diagnose, load_calibration, recommended_gain_db, save_calibration
from jarves.voice.wake_confirm import AutoGain, ConfirmingDetector, SpeechMonitor, name_match
from jarves.voice.wake_eval import run_listener, score_negatives, score_positives
from jarves.voice.wakeword import FRAME_BYTES, FakeDetector, make_wake_detector

CHUNK = 2560


def tone(ms, amp=0.3, f=220):
    n = 16 * ms
    return (np.sin(np.arange(n) * 2 * np.pi * f / 16000) * amp * 32767).astype("<i2").tobytes()


def frames(pcm):
    return [pcm[i:i + CHUNK] for i in range(0, len(pcm) - CHUNK + 1, CHUNK)]


def db(pcm):
    a = np.frombuffer(pcm, "<i2").astype(np.float32) / 32768
    return 20 * np.log10(np.sqrt(np.mean(a * a)) + 1e-9)


# ------------------------------------------------------------------------------------------------ name matching
@pytest.mark.parametrize("text", ["Hey Jarvis.", "Hi, Jarvis!", "ok jarves", "Okay Jarvis, what time is it",
                                  "היי ג'רוויס", "הי ג׳ארוויס", "Hello Jarvis", "heyjarvis", "Hey, Jervis.", "Hi, Javis.", "Hey Javis",
                                  "hey Yarvis", "Hi Jarbis", "Hey Jarvish"])
def test_name_match_accepts_natural_variants(text):
    assert name_match(text)


@pytest.mark.parametrize("text", ["Hey Travis", "hey Charles", "Hey Jarvie", "Hey Harvey", "Jarvis", "",
                                  "Thank you.", "service", "hey Marvin", "hey Davis", "hey Harvis", "hi Yarn", "hey Marvis"])
def test_name_match_rejects_near_misses_and_the_bare_name(text):
    assert not name_match(text)


# ------------------------------------------------------------------------------------------------ AGC
def test_agc_lifts_quiet_speech_and_never_clips():
    agc = AutoGain()
    quiet = frames(tone(4000, amp=0.01))               # ~-43 dBFS: a webcam mic across the room
    out = [agc.process(f) for f in quiet]
    assert db(out[-1]) > db(quiet[-1]) + 12             # boosted towards -24 dBFS
    loud = [agc.process(f) for f in frames(tone(2000, amp=0.9))]
    assert max(int(np.abs(np.frombuffer(f, "<i2")).max()) for f in loud) <= 32767 * 0.99


def test_agc_does_not_boost_room_noise_alone():
    rng = np.random.default_rng(0)
    agc = AutoGain()
    noise = (rng.standard_normal(16000 * 5) * 0.0005 * 32767).astype("<i2").tobytes()
    out = [agc.process(f) for f in frames(noise)]
    assert agc.gain_db == 0 and out[-1] == frames(noise)[-1]


# ------------------------------------------------------------------------------------------------ two-stage
class Scripted:
    """Wake gate stand-in: returns scores from a list, one per frame."""
    name, threshold, blocking = "hey_jarvis", 1.01, False

    def __init__(self, scores):
        self.scores, self.i, self.last_score, self.resets = list(scores), 0, 0.0, 0

    def feed(self, pcm):
        self.last_score = self.scores[self.i] if self.i < len(self.scores) else 0.0
        self.i += 1
        return None

    def reset(self):
        self.resets += 1


class Confirm:
    def __init__(self, ok):
        self.ok, self.calls, self.last_text = ok, [], ""

    def __call__(self, audio):
        self.calls.append(len(audio))
        self.last_text = "hey jarvis" if self.ok else "hey travis"
        return self.ok


def run_frames(det, n):
    return [det.feed(bytes(FRAME_BYTES)) for _ in range(n)]


def test_two_stage_instant_accept_skips_the_speech_check():
    c = Confirm(True)
    det = ConfirmingDetector(Scripted([0, 0.9]), c, accept=0.5, gate=0.02)
    out = run_frames(det, 3)
    assert out[1] == 0.9 and not c.calls and det.stats()["fast_accepts"] == 1


def test_two_stage_unsure_score_wakes_only_when_the_name_is_heard():
    c = Confirm(True)
    det = ConfirmingDetector(Scripted([0, 0.05, 0.1, 0, 0, 0, 0]), c, accept=0.5, gate=0.02, settle_frames=4)
    out = run_frames(det, 7)
    hits = [x for x in out if x is not None]
    assert hits == [0.5] and len(c.calls) == 1
    assert c.calls[0] <= 16000 * 2                      # at most the 2 s ring goes to the local ASR
    assert det.last_confirm["ok"] and det.stats()["confirmed"] == 1


def test_two_stage_rejects_when_the_speech_check_hears_another_name():
    c = Confirm(False)
    det = ConfirmingDetector(Scripted([0.3] * 30), c, accept=0.5, gate=0.02, settle_frames=4)
    assert all(x is None for x in run_frames(det, 30))
    assert 1 <= len(c.calls) <= 2                       # no ASR call storm on one long utterance
    assert det.stats()["rejected"] == len(c.calls)


def test_two_stage_reset_drops_the_audio_ring():
    det = ConfirmingDetector(Scripted([0] * 5), Confirm(True))
    for _ in range(5):
        det.feed(tone(80))
    det.reset()
    assert det._filled == 0 and not det._ring.any()


def test_make_wake_detector_composes_from_config(cfg):
    v = cfg.voice.model_copy()
    v.wake_threshold, v.wake_accept, v.wake_confirm, v.wake_agc = 0.15, 0.5, True, True
    d = make_wake_detector(v)
    assert isinstance(d, ConfirmingDetector) and d.accept == 0.5 and d.gate == v.wake_gate and d.agc is not None
    v.wake_threshold = 0.7
    assert make_wake_detector(v).accept == 0.7              # a stricter user threshold wins
    v.wake_confirm, v.wake_agc = False, False
    assert make_wake_detector(v).threshold == 0.7


# ------------------------------------------------------------------------------------------------ speech, no match
def feed_all(mon, pcm, score=0.0):
    return [e for e in (mon.feed(f, score) for f in frames(pcm)) if e is not None]


def test_speech_without_a_wake_score_is_reported():
    mon = SpeechMonitor()
    ev = feed_all(mon, bytes(CHUNK * 10) + tone(1200) + bytes(CHUNK * 10))
    assert len(ev) == 1 and ev[0].kind == "no_match" and ev[0].peak_score == 0 and ev[0].speech_ms >= 400


def test_quiet_speech_is_reported_as_too_quiet():
    mon = SpeechMonitor()
    ev = feed_all(mon, bytes(CHUNK * 10) + tone(1200, amp=0.003) + bytes(CHUNK * 10))
    assert len(ev) == 1 and ev[0].kind == "too_quiet"


def test_speech_with_a_wake_score_or_a_click_is_not_reported():
    mon = SpeechMonitor(min_score=0.05)
    assert not feed_all(mon, bytes(CHUNK * 10) + tone(1200) + bytes(CHUNK * 10), score=0.2)
    assert not feed_all(mon, bytes(CHUNK * 10) + tone(160) + bytes(CHUNK * 10))     # too short to be speech


# ------------------------------------------------------------------------------------------------ calibration
def test_calibration_round_trip_and_gain(tmp_path):
    assert recommended_gain_db(-70, -44) == 20.0
    assert recommended_gain_db(-55, -44) == 5.0             # the noise floor limits the gain
    assert recommended_gain_db(-70, None) == 0.0
    save_calibration(tmp_path, "Microphone (2K HD Camera)", floor_db=-70, speech_db=-44, verdict="auto", by="t")
    c = load_calibration(tmp_path, "Microphone (2K HD Camera)")
    assert c["gain_db"] == 20.0 and c["by"] == "t" and load_calibration(tmp_path, "other") is None


@pytest.mark.parametrize("kw,code", [
    (dict(floor_db=-90, speech_db=None, best_score=0, attempts=0, digital_silence=True), "WRONG_MIC"),
    (dict(floor_db=-70, speech_db=None, best_score=0, attempts=0), "TOO_QUIET"),
    (dict(floor_db=-60, speech_db=-52, best_score=0, attempts=2), "TOO_QUIET"),
    (dict(floor_db=-30, speech_db=-12, best_score=0, attempts=2), "TOO_NOISY"),
    (dict(floor_db=-65, speech_db=-25, best_score=0.0, attempts=3), "PHRASE_NOT_MATCHED"),
    (dict(floor_db=-65, speech_db=-25, best_score=0.3, attempts=3), "NEAR_MISS"),
    (dict(floor_db=-65, speech_db=-25, best_score=0.3, attempts=3, confirmed=1), "OK"),
    (dict(floor_db=-65, speech_db=-25, best_score=0.8, attempts=3), "OK"),
])
def test_diagnose_names_one_exact_fix(kw, code):
    verdict, advice = diagnose(gate=0.02, threshold=0.5, **kw)
    assert verdict == code and advice


# ------------------------------------------------------------------------------------------------ `jarvis wake test`
class VMic:
    """Virtual mic: yields a script of 80 ms chunks, then silence (never a real device)."""

    def __init__(self, pcm, tail_s=1.0):
        self.pcm, self.tail = pcm, int(tail_s * 12.5)
        self.closed = False

    async def mic_chunks(self, rate, chunk_bytes):
        for f in frames(self.pcm) + [bytes(chunk_bytes)] * self.tail:
            yield f
            await asyncio.sleep(0)

    def close(self):
        self.closed = True


async def listen(cfg, pcm, det=None, confirm=None, seconds=60):
    from jarves.cli import _wake_listen_test
    lines = []
    v = cfg.voice.model_copy()
    v.wake_agc = False
    mic = VMic(pcm)
    det = det or Scripted([])
    res = await _wake_listen_test(det, mic_factory=lambda: mic, seconds=seconds, phrase="Hey Jarvis",
                                  threshold=0.5, v=v, confirm=confirm,
                                  out=lambda *a, **k: lines.append(" ".join(map(str, a))))
    assert mic.closed
    return res, "\n".join(lines)


async def test_wake_test_says_phrase_not_matched_for_clear_other_speech(cfg):
    noise = (np.random.default_rng(1).standard_normal(16000) * 30).astype("<i2").tobytes()
    res, text = await listen(cfg, noise + tone(1200) + noise)
    assert res["verdict"] == "PHRASE_NOT_MATCHED" and "Say \"Hey Jarvis\"" in res["advice"]
    assert "attempt 1" in text and "wake score 0.00" in text


async def test_wake_test_says_wrong_mic_for_digital_silence(cfg):
    res, _ = await listen(cfg, bytes(CHUNK * 20))
    assert res["verdict"] == "WRONG_MIC" and "--list-mics" in res["advice"]


async def test_wake_test_says_too_quiet_for_a_far_away_voice(cfg):
    noise = (np.random.default_rng(2).standard_normal(16000) * 20).astype("<i2").tobytes()
    res, _ = await listen(cfg, noise + tone(1200, amp=0.004) + noise)
    assert res["verdict"] == "TOO_QUIET" and "closer" in res["advice"]


async def test_wake_test_shows_what_the_local_speech_check_heard(cfg):
    noise = (np.random.default_rng(3).standard_normal(16000) * 30).astype("<i2").tobytes()
    speech = tone(1200)
    n_noise, n_speech = len(frames(noise)), len(frames(speech))
    det = Scripted([0.0] * n_noise + [0.1] * n_speech)

    class Asr:
        def transcribe(self, audio):
            return "Hi Jarvis."
    res, text = await listen(cfg, noise + speech + noise, det=det, confirm=Asr())
    assert res["verdict"] == "OK" and 'heard "Hi Jarvis."' in text and "would WAKE" in text


# ------------------------------------------------------------------------------------------------ eval harness
async def test_eval_harness_counts_detections_and_false_wakes_through_the_real_listener():
    marker = np.zeros(1600, np.float32)
    marker[800] = FakeDetector.MARKER / 32767

    def pos():
        for i in range(3):
            seg = np.concatenate([marker if i != 1 else np.zeros(1600, np.float32), np.zeros(48000, np.float32)])
            yield seg, {"len": 1600, "text": "hey jarvis", "voice": "v", "cond": "normal", "kind": "pos"}

    wakes, events, st = await run_listener(FakeDetector(threshold=0.5), pos())
    p = score_positives(wakes, events)
    assert p["detected"] == 2 and p["positives"] == 3 and p["by"]["cond:normal"] == pytest.approx(0.667, abs=1e-3)

    def neg():
        yield np.zeros(16000 * 10, np.float32), {"len": 16000, "text": "hey Travis", "voice": "v", "kind": "near"}
        yield np.concatenate([marker, np.zeros(16000, np.float32)]), {"len": 1600, "text": "Jarvis", "voice": "v",
                                                                       "kind": "bare"}
    wakes, events, st = await run_listener(FakeDetector(threshold=0.5), neg())
    n = score_negatives(wakes, events, st["samples"])
    assert n["false_wakes"] == 0 and n["bare_name_wakes"] == 1 and st["audio_min"] > 0.15


# ------------------------------------------------------------------------------------------------ Core wake loop
class FakeMic:
    def __init__(self, script):
        self.script = script

    async def mic_chunks(self, rate, chunk_bytes):
        await asyncio.sleep(0.3)
        for c in self.script():
            yield c
            await asyncio.sleep(0.001)
        while True:
            yield bytes(chunk_bytes)
            await asyncio.sleep(0.005)

    def close(self):
        pass


async def test_core_says_heard_speech_no_wake_match_and_shows_it(home, cfg):
    """The owner's 20 Oct case: speech at a fine level, wake score 0 -> a clear log line + the privacy/wake note."""
    def talk():
        for _ in range(12):
            yield bytes(CHUNK)
        for f in frames(tone(1200)):
            yield f
        for _ in range(12):
            yield bytes(CHUNK)
    cfg.voice.wake_enabled = True
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, wake_detector=FakeDetector(), wake_audio=lambda: FakeMic(talk))
    await c.start()
    try:
        log = home / "logs" / "jarves.log"
        hit = []
        for _ in range(200):
            await asyncio.sleep(0.05)
            hit = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if "WAKE_SPEECH_NO_MATCH" in l]
            if hit and (home / "wake" / "calibration.json").exists():
                break
        assert hit and "say 'Hey Jarvis'" in hit[0]["msg"] and hit[0]["peak_score"] == 0
        assert not [l for l in log.read_text(encoding="utf-8").splitlines() if '"WAKE_WORD"' in l]
        w = c.snapshot()["privacy"]["wake"]
        assert w["state"] == "listening" and w["note"] == "no_match" and "Hey Jarvis" in w["hint"]
        cal = json.loads((home / "wake" / "calibration.json").read_text(encoding="utf-8"))
        (mic, entry), = cal.items()
        assert entry["by"] == "core" and entry["speech_db"] > -20     # first-run level calibration, levels only
    finally:
        await c.stop()
