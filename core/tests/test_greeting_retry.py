"""#179: greeting retry. nudge -> short wait -> ONE retry -> continue without greeting. Stubbed session, no network, no spend."""
import asyncio
import base64
import json
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

import jarves.voice.live as live_mod
from jarves import logs, speedtest
from jarves.skills import SkillRegistry
from jarves.voice.live import LiveVoice
from test_readiness import _loud
from test_voice_faults import FakeClient


class _Notes:
    def __init__(self, log, kind, on_call=None):
        self.log, self.kind, self.on_call = log, kind, on_call

    async def append(self, **kw):
        self.log.append((self.kind, kw.get("event_id"), kw.get("content")))
        if self.on_call:
            self.on_call(kw.get("event_id"))


async def _voice(monkeypatch, audio_on=None, fail_notes=False):
    """A started LiveVoice whose nudge/retry notes are recorded; `audio_on` = event_id that makes audio arrive."""
    monkeypatch.setattr(live_mod, "GREETING_NUDGE_S", 0.05)
    monkeypatch.setattr(live_mod, "GREETING_RETRY_AFTER_S", 0.05)
    voice = LiveVoice(SkillRegistry(), lambda: "backend", client=FakeClient(), greeting="שלום", hold_output=True)
    await voice.start()
    conn, notes = voice.conn, []

    def maybe_audio(eid):
        if eid == audio_on:
            conn.push({"type": "session.output_audio.delta", "delta": base64.b64encode(_loud(4800)).decode()})
    conn.session.thinking = _Notes(notes, "thinking", maybe_audio)
    conn.session.commentary = _Notes(notes, "commentary", maybe_audio)
    return voice, notes


def test_budget_is_unchanged_and_retry_fits_inside_it():
    assert live_mod.GREETING_BUDGET_S == 15.0
    assert live_mod.GREETING_NUDGE_S + live_mod.GREETING_RETRY_AFTER_S < live_mod.GREETING_BUDGET_S


async def test_greeting_without_stall_is_ok_and_sends_nothing(monkeypatch):
    voice, notes = await _voice(monkeypatch)
    try:
        voice.conn.push({"type": "session.output_audio.delta", "delta": base64.b64encode(_loud(4800)).decode()})
        await voice.wait_ready(timeout=1)
        assert voice.greeting_outcome == "ok" and notes == []
    finally:
        await voice.stop()


async def test_nudge_recovers_counts_as_ok_without_retry(monkeypatch):
    voice, notes = await _voice(monkeypatch, audio_on="jarves_greeting_nudge")
    try:
        await voice.wait_ready(timeout=1)
        assert voice.greeting_outcome == "ok" and [n[1] for n in notes] == ["jarves_greeting_nudge"]
    finally:
        await voice.stop()


async def test_retry_recovers_after_failed_nudge(monkeypatch):
    voice, notes = await _voice(monkeypatch, audio_on="jarves_greeting_retry")
    try:
        t0 = time.monotonic()
        await voice.wait_ready(timeout=1)
        assert voice.greeting_outcome == "retry_ok"
        assert [n[1] for n in notes] == ["jarves_greeting_nudge", "jarves_greeting_retry"]
        assert "שלום" in notes[1][2] and time.monotonic() - t0 < 0.5
    finally:
        await voice.stop()


async def test_missed_after_nudge_and_one_retry_stays_inside_the_budget(monkeypatch):
    voice, notes = await _voice(monkeypatch)
    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="audio was not received"):
            await voice.wait_ready(timeout=0.4)
        assert 0.35 <= time.monotonic() - t0 < 0.6            # the retry did not extend the stated budget
        assert voice.greeting_outcome == "missed"
        assert [n[1] for n in notes] == ["jarves_greeting_nudge", "jarves_greeting_retry"]    # exactly one of each
    finally:
        await voice.stop()


async def test_short_timeout_skips_retry(monkeypatch):
    voice, notes = await _voice(monkeypatch)
    try:
        with pytest.raises(RuntimeError):
            await voice.wait_ready(timeout=0.08)               # nudge fits, retry would pass the budget
        assert [n[1] for n in notes] == ["jarves_greeting_nudge"]
    finally:
        await voice.stop()


async def test_failing_notes_never_break_the_wait(monkeypatch):
    monkeypatch.setattr(live_mod, "GREETING_NUDGE_S", 0.02)
    monkeypatch.setattr(live_mod, "GREETING_RETRY_AFTER_S", 0.02)
    voice = LiveVoice(SkillRegistry(), lambda: "backend", client=FakeClient(), greeting="שלום", hold_output=True)
    await voice.start()                                        # the fake session has no thinking/commentary: both raise
    try:
        with pytest.raises(RuntimeError, match="audio was not received"):
            await voice.wait_ready(timeout=0.2)
        assert voice.greeting_outcome == "missed"
    finally:
        await voice.stop()


def _log(home, recs):
    (home / "logs").mkdir(exist_ok=True)
    (home / "logs" / "jarves.log").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def test_speedtest_counts_greeting_outcomes_and_rate(home):
    ts = datetime.fromtimestamp(time.time() - 3600).isoformat()
    codes = ["LIVE_GREETING_OK"] * 3 + ["LIVE_GREETING_RETRY_OK", "LIVE_GREETING_MISSED"]
    _log(home, [{"ts": ts, "code": c} for c in codes] + [{"ts": "2020-01-01T00:00:00", "code": "LIVE_GREETING_MISSED"}])
    got = speedtest.measure_history(home)
    assert (got["greeting_ok"], got["greeting_retry_ok"], got["greeting_missed"]) == (3, 1, 1)
    assert got["greeting_missed_pct"] == 20.0


def test_speedtest_rate_needs_five_starts_and_counters_have_no_trend(home):
    ts = datetime.fromtimestamp(time.time() - 3600).isoformat()
    _log(home, [{"ts": ts, "code": "LIVE_GREETING_OK"}, {"ts": ts, "code": "LIVE_GREETING_MISSED"}])
    got = speedtest.measure_history(home)
    assert got == {"greeting_ok": 1, "greeting_retry_ok": 0, "greeting_missed": 1}
    m = speedtest.judge("greeting_ok", 50, speedtest.load_budgets(), "owner", [10, 10, 10])
    assert m.trend_pct is None and m.status == speedtest.PASS
    b = speedtest.load_budgets()
    assert b["greeting_missed_pct"]["baseline"] <= b["greeting_missed_pct"]["owner_max"] <= b["greeting_missed_pct"]["ci_max"]


def test_advisor_knows_the_greeting_metric():
    from jarves import perf_advisor
    assert "greeting_missed_pct" in perf_advisor.SUGGESTIONS
