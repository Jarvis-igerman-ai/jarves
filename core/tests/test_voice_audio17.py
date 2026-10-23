"""Issue #17: false wakes, echo/barge-in without a headset, dead air, optional-note rejections, shutdown signals.
Offline: synthetic audio frames, fake clocks and fake connections (no microphone, speaker or network)."""
import asyncio
import signal
import time
from types import SimpleNamespace

import numpy as np

from jarves.voice import live as live_mod
from jarves.voice.echo_gate import EchoBargeIn
from jarves.voice.live import CHUNK_BYTES, LiveVoice, cue_pcm
from jarves.voice.wake_confirm import ConfirmingDetector
from jarves.voice.wake_guard import (SINGLE_STAGE_MIN, WakeOutcomeLog, advice_text, apply_to_detector,
                                     suggest_threshold)
from jarves.skills import SkillRegistry


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ (1) false-wake self-tuning
def test_false_wake_level_follows_the_measured_rate_and_decays(tmp_path):
    clk = Clock()
    log = WakeOutcomeLog(tmp_path, clock=clk)
    for _ in range(2):
        log.record(True)
    assert log.level() == 0 and log.bump() == 0 and log.no_speech_s(12) == 12
    log.record(True)                                           # 3 false wakes in 24 h
    assert log.level() == 1 and log.bump() == 0.08 and not log.always_confirm()
    assert log.no_speech_s(12) == 6 and log.no_speech_s(0) == 0
    for _ in range(3):
        log.record(True)
    assert log.level() == 2 and log.always_confirm()
    for _ in range(12):                                        # many real wakes pull the level back down
        log.record(False)
    assert log.level() == 1
    clk.t += 25 * 3600                                         # a quiet day undoes it
    assert log.level() == 0 and log.counts() == (0, 0)


def test_outcome_log_is_persisted_without_content_and_reset(tmp_path):
    clk = Clock()
    log = WakeOutcomeLog(tmp_path, clock=clk)
    log.record(True)
    log.record(False)
    import json
    data = json.loads((tmp_path / "wake" / "outcomes.json").read_text())
    assert all(k in ("f", "g") and isinstance(t, float) for t, k in data)       # a time and a letter, nothing else
    assert WakeOutcomeLog(tmp_path, clock=clk).counts() == (1, 1)
    WakeOutcomeLog(tmp_path, clock=clk).reset()
    assert WakeOutcomeLog(tmp_path, clock=clk).counts() == (0, 0)


def test_suggested_threshold_and_advice_text():
    assert suggest_threshold(0.15, 2, 0) is None
    assert suggest_threshold(0.15, 3, 0) == round(SINGLE_STAGE_MIN + 0.08, 2)       # 0.15 is too low to build on
    assert suggest_threshold(0.5, 6, 0) == 0.66
    log = WakeOutcomeLog(None, clock=Clock())
    for _ in range(4):
        log.record(True)
    text = advice_text(log.summary(0.15), 0.15)
    assert "4 false wake" in text and "jarvis wake test --apply" in text and "voice.wake_threshold = 0.33" in text
    assert "Nothing to fix" in advice_text(WakeOutcomeLog(None, clock=Clock()).summary(0.3), 0.3)


class Scored:
    """A wake model double whose score the test sets; reads like OpenWakeWordDetector (threshold + last_score)."""
    blocking = False
    name = "hey_jarvis"

    def __init__(self, threshold=0.15):
        self.threshold, self.last_score = threshold, 0.0

    def feed(self, pcm):
        return self.last_score if self.last_score >= self.threshold else None

    def reset(self):
        pass


def test_single_stage_threshold_is_floored_and_raised_by_false_wakes():
    det = Scored(0.15)
    used = apply_to_detector(det, 0.0, False)                  # the owner's 0.15 no longer fires on room noise
    assert used["threshold"] == SINGLE_STAGE_MIN and det.threshold == SINGLE_STAGE_MIN
    det.last_score = 0.2
    assert det.feed(b"") is None
    det.last_score = 0.3
    assert det.feed(b"") == 0.3
    apply_to_detector(det, 0.16, True)                          # never stacks on the previous bump
    assert det.threshold == round(SINGLE_STAGE_MIN + 0.16, 3)
    apply_to_detector(det, 0.0, False)
    assert det.threshold == SINGLE_STAGE_MIN


def test_two_stage_strong_score_needs_the_speech_check_after_false_wakes():
    asked = []

    def confirm(audio):
        asked.append(len(audio))
        return False

    base = Scored(1.01)
    det = ConfirmingDetector(base, confirm, accept=0.5, gate=0.02, settle_frames=2)
    frame = (np.zeros(1280, np.int16)).tobytes()
    base.last_score = 0.9
    assert det.feed(frame) == 0.9                               # default: a strong score wakes at once
    det.reset()
    apply_to_detector(det, 0.08, True)
    assert det.accept == 0.58 and det.always_confirm
    got = [det.feed(frame) for _ in range(4)]
    assert got == [None, None, None, None] and asked, "strong score must be checked by the local ASR"
    det2 = ConfirmingDetector(Scored(1.01), lambda a: True, accept=0.5, gate=0.02, settle_frames=2)
    det2.always_confirm = True
    det2.base.last_score = 0.9
    got = [det2.feed(frame) for _ in range(3)]
    assert any(g is not None for g in got), "a confirmed name still wakes"


def test_wake_ignores_the_tail_of_its_own_speech_after_a_conversation():
    from jarves.app import Core
    ns = SimpleNamespace(cfg=SimpleNamespace(voice=SimpleNamespace(wake_enabled=True)),
                         dev=SimpleNamespace(mic_muted=False), live=None, listening=False, stopping=False,
                         _voice_connecting=False, _hold_wake_until=0.0, runtime=None, _busy=None,
                         _wake_grace_until=time.monotonic() + 2.0)
    assert Core._wake_should_listen(ns) is False
    ns._wake_grace_until = time.monotonic() - 0.1
    assert Core._wake_should_listen(ns) is True


# ------------------------------------------------------------------ (2) echo gate / barge-in
def test_echo_level_is_not_the_user_but_a_loud_voice_over_it_is():
    g = EchoBargeIn()
    assert not any(g.observe(0.03, 0.1, True) for _ in range(30))        # steady room echo of its own speech
    assert not g.observe(0.2, 0.1, True) and not g.observe(0.2, 0.1, True)  # 200 ms is not yet a voice
    assert g.observe(0.2, 0.1, True)                                       # 300 ms well above the echo
    g2 = EchoBargeIn()
    [g2.observe(0.03, 0.1, True) for _ in range(10)]
    g2.observe(0.5, 0.1, True)                                             # one door slam
    assert not any(g2.observe(0.03, 0.1, True) for _ in range(5))
    assert not g2.observe(0.5, 0.1, False) and g2.echo == 0.0              # not gated: state resets


def pcm(level: float) -> bytes:
    return (np.ones(CHUNK_BYTES // 2) * int(level * 32767)).astype("<i2").tobytes()


class FakeAudio:
    def __init__(self, chunks):
        self.chunks, self.queued, self.flushed = chunks, [], 0

    async def mic_chunks(self, rate, size):
        for c in self.chunks:
            yield c
            await asyncio.sleep(0)
        while True:
            await asyncio.sleep(0.01)
            yield pcm(0.0)

    def flush(self):
        self.flushed += 1
        return 123

    def enqueue(self, b):
        self.queued.append(b)

    def queued_s(self):
        return 0.0

    def stats(self):
        return {}


class Mic:
    def __init__(self):
        self.sent = []
        outer = self

        async def append(audio):
            outer.sent.append(audio)
        self.session = SimpleNamespace(input_audio=SimpleNamespace(append=append))


async def test_user_talking_over_jarves_through_the_echo_gate_stops_it_and_opens_the_mic():
    chunks = [pcm(0.03)] * 8 + [pcm(0.25)] * 3 + [pcm(0.25)] * 3
    audio = FakeAudio(chunks)
    lv = LiveVoice(SkillRegistry(), lambda: "", audio_io=audio, half_duplex=True)
    lv.conn, lv.running = Mic(), True
    lv._speaking_until = time.monotonic() + 30                           # JARVES is speaking on the speakers
    task = asyncio.create_task(lv._mic())
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert lv._stats["echo_barge_ins"] == 1 and lv._stats["barge_ins"] == 1
    assert audio.flushed == 1 and lv._speaking_until == 0.0
    assert not lv._gated()                                               # the mic is open for the user now
    silence = live_mod.base64.b64encode(bytes(CHUNK_BYTES)).decode()
    real = [a for a in lv.conn.sent if a != silence]
    assert len(real) >= 3, "the first loud chunks (pre-roll) and the rest reach the model"
    assert lv.conn.sent[:8] == [silence] * 8, "echo of its own voice is still never sent to the model"


async def test_headset_free_echo_alone_does_not_barge_in():
    audio = FakeAudio([pcm(0.04)] * 40)
    lv = LiveVoice(SkillRegistry(), lambda: "", audio_io=audio, half_duplex=True)
    lv.conn, lv.running = Mic(), True
    lv._speaking_until = time.monotonic() + 30
    task = asyncio.create_task(lv._mic())
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert lv._stats.get("echo_barge_ins", 0) == 0 and lv._gated()


# ------------------------------------------------------------------ (3) dead air: cue + latency stages
async def run_cue(lv, seconds=0.7):
    task = asyncio.create_task(lv._dead_air_cue())
    await asyncio.sleep(seconds)
    lv.running = False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def waiting_live(allowed=True, waited=3.5):
    audio = FakeAudio([])
    lv = LiveVoice(SkillRegistry(), lambda: "", audio_io=audio, cue_after_s=3.0, cue_allowed=lambda: allowed)
    lv.running, lv._heard_user, lv._user_turn_n = True, True, 1
    lv._last_user_voice = time.monotonic() - waited
    return lv, audio


async def test_working_cue_plays_after_three_seconds_of_dead_air():
    lv, audio = waiting_live()
    await run_cue(lv)
    assert audio.queued == [cue_pcm()] and lv._stats["working_cues"] == 1
    assert len(cue_pcm()) / 2 / live_mod.RATE < 0.3, "short and soft"
    assert lv._gated() is False or lv.half_duplex                           # cue gate only closes the mic briefly


async def test_working_cue_respects_quiet_hours_early_answers_and_approvals():
    lv, audio = waiting_live(allowed=False)                                  # quiet hours / chime off
    await run_cue(lv)
    assert audio.queued == []
    lv, audio = waiting_live(waited=1.0)                                     # not long enough yet
    await run_cue(lv)
    assert audio.queued == []
    lv, audio = waiting_live()
    lv._last_speech_t = time.monotonic()                                     # JARVES already answered
    await run_cue(lv)
    assert audio.queued == []
    lv, audio = waiting_live()
    lv._confirm_pending = True                                               # the user is asked to approve
    await run_cue(lv)
    assert audio.queued == []
    lv, audio = waiting_live()
    lv._in_text = "עוד מדבר"                                                  # the user is still talking
    await run_cue(lv)
    assert audio.queued == []


async def test_working_cue_repeats_at_most_a_few_times_per_turn():
    lv, audio = waiting_live(waited=60)
    lv._cue_turn, lv._cue_n, lv._cue_last = 1, live_mod.CUE_MAX, time.monotonic() - 100
    await run_cue(lv)
    assert audio.queued == []


def test_latency_log_names_the_slowest_stage(monkeypatch):
    seen = []
    monkeypatch.setattr(live_mod.logs, "info", lambda *a, **k: seen.append(("info", a, k)))
    monkeypatch.setattr(live_mod.logs, "warn", lambda *a, **k: seen.append(("warn", a, k)))
    lv = LiveVoice(SkillRegistry(), lambda: "")
    lv._lat = {"deleg": 101.0, "backend_start": 101.4, "backend_end": 110.4}
    lv._log_latency(111.0, 100.0)
    lvl, a, k = seen[-1]
    assert lvl == "warn" and a[1] == "LIVE_LATENCY" and k["slowest"] == "backend" and k["total_s"] == 11.0
    assert k["backend_s"] == 9.0 and k["to_delegation_s"] == 1.0 and k["backend_to_audio_s"] == 0.6
    lv._lat = {}
    lv._log_latency(101.0, 100.0)
    lvl, a, k = seen[-1]
    assert lvl == "info" and k["slowest"] == "model_direct"


# ------------------------------------------------------------------ (4) optional notes rejected by the server
async def test_a_transient_progress_rejection_does_not_silence_the_session_for_good(monkeypatch):
    seen = []
    monkeypatch.setattr(live_mod.logs, "warn", lambda *a, **k: seen.append(("warn", a[1])))
    monkeypatch.setattr(live_mod.logs, "debug", lambda *a, **k: seen.append(("debug", a[1])))
    monkeypatch.setattr(live_mod.logs, "info", lambda *a, **k: seen.append(("info", a[1])))
    lv = LiveVoice(SkillRegistry(), lambda: "", progress_after_s=2.5, clock_context=True)
    err = {"type": "error", "client_event_id": "jarves_progress_1",
           "error": {"code": "context_injection_incomplete", "message": "context injection incomplete"}}
    await lv.handle_event(err)
    await lv.handle_event({**err, "client_event_id": "jarves_progress_2"})
    assert lv.progress_after_s == 2.5 and lv.clock_context is True            # still on after two transient rejections
    assert seen.count(("warn", "LIVE_EXTRA_REJECTED")) == 1                    # only the first is a WARNING
    await lv.handle_event({**err, "client_event_id": "jarves_progress_3"})
    assert lv.progress_after_s == 0.0                                          # repeated: off for this session
    assert lv._stats["extras_rejected"] == 3 and lv._stats["errors"] == 0      # never a session error
    other = {"type": "error", "client_event_id": "jarves_clock_202610231200",
             "error": {"code": "invalid_event", "message": "not supported"}}
    await lv.handle_event(other)
    assert lv.clock_context is False                                           # a real format error switches it off


async def test_a_transiently_rejected_announcement_is_resent_unchanged(monkeypatch):
    sent = []

    async def append(content, delegation_id, event_id=None):
        sent.append((event_id, content))
        if len(sent) == 1:                                                     # the server rejects the first send
            lv._rejected = {event_id}
            lv._reject_codes[event_id] = "context_injection_incomplete context injection incomplete"
    lv = LiveVoice(SkillRegistry(), lambda: "")
    lv.conn = SimpleNamespace(session=SimpleNamespace(commentary=SimpleNamespace(append=append)))
    lv.running = True
    real_sleep = asyncio.sleep

    async def fast(s, *a):
        await real_sleep(0)
    monkeypatch.setattr(live_mod.asyncio, "sleep", fast)
    ok = await lv._send_note("x" * 400, "t")
    assert ok and len(sent) == 2 and sent[0][1] == sent[1][1] == "x" * 400


# ------------------------------------------------------------------ (5) shutdown: signals ask for an orderly stop
async def test_sigterm_and_sigbreak_request_the_orderly_stop():
    from jarves.app import Core
    core = Core.__new__(Core)
    core.stop_event = asyncio.Event()
    old = signal.getsignal(signal.SIGTERM)
    core._install_stop_signals()
    try:
        assert signal.getsignal(signal.SIGTERM) is not old
        signal.raise_signal(signal.SIGTERM)
        await asyncio.wait_for(core.stop_event.wait(), 2)
    finally:
        core._restore_stop_signals()
    assert signal.getsignal(signal.SIGTERM) is old
