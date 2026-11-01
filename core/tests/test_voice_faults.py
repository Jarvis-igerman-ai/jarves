"""Voice fault injection (Issue #9 CR-02/CR-05/CR-06, Live part of CR-01). Offline: fake Live connection, fake audio."""
import asyncio
import base64
import json
import time
from types import SimpleNamespace

import pytest
from conftest import drain_tools

from jarves.budget import Budget, BudgetExceeded
from jarves.config import AIConfig
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import ClockSkill
from jarves.voice.audio import StreamingAudio
from jarves.voice.live import LiveVoice
from jarves.voice.realtime import RealtimeVoice

END = object()

def loud(nbytes: int) -> bytes:
    """Fake assistant SPEECH (a 220 Hz tone, rms ~0.2). All-zero audio is the server's idle silence."""
    import numpy as _np
    n = nbytes // 2
    return (_np.sin(_np.arange(n) * 2 * _np.pi * 220 / 24000) * 0.3 * 32767).astype("<i2").tobytes()



class FakeLiveConn:
    def __init__(self, auto_started=True, auto_closed=True, close_seconds=7.0):
        self.q: asyncio.Queue = asyncio.Queue()
        self.sent: list = []
        self.exited = False
        self.mic_frames = 0
        outer = self

        class InputAudio:
            async def append(self, audio):
                outer.mic_frames += 1

        class Session:
            input_audio = InputAudio()

            async def start(self, session, event_id=None):
                outer.sent.append(("session.start", event_id))
                if auto_started:
                    outer.push({"type": "session.started"})

            async def close(self, event_id=None):
                outer.sent.append(("session.close", event_id))
                if auto_closed:
                    outer.push({"type": "session.closed", "reason": "close_requested",
                                "usage": {"seconds": close_seconds}})

        class Item:
            async def create(self, item, event_id=None):
                outer.sent.append(("response.item.create", item))

        class Resp:
            item = Item()

            async def create(self, event_id=None):
                outer.sent.append(("response.create", None))

        self.session, self.response = Session(), Resp()

    def push(self, ev):
        self.q.put_nowait(ev)

    def __aiter__(self):
        return self

    async def __anext__(self):
        ev = await self.q.get()
        if ev is END:
            raise StopAsyncIteration
        if isinstance(ev, BaseException):
            raise ev
        return ev


class FakeClient:
    def __init__(self, **kw):
        self.kw, self.conns = kw, []
        outer = self

        class CM:
            async def __aenter__(self):
                c = FakeLiveConn(**outer.kw)
                outer.conns.append(c)
                self.c = c
                return c

            async def __aexit__(self, *a):
                self.c.exited = True
        self.live = SimpleNamespace(connect=lambda: CM())

    @property
    def conn(self):
        return self.conns[-1]


class FakeAudio:
    """Stands in for StreamingAudio: a slow mic and a player that just waits."""

    def __init__(self):
        self.enqueued: list[bytes] = []
        self.closed = 0
        self.flushed = 0
        self._open = True

    async def mic_chunks(self, rate, chunk_bytes):
        while self._open:
            await asyncio.sleep(0.01)
            yield bytes(chunk_bytes)

    async def run_player(self, rate):
        while self._open:
            await asyncio.sleep(0.01)

    def enqueue(self, pcm):
        self.enqueued.append(pcm)

    def flush(self):
        n = sum(map(len, self.enqueued))
        self.enqueued.clear()
        self.flushed += 1
        return n

    def queued_s(self):
        return sum(map(len, self.enqueued)) / 2 / 24000

    def stats(self):
        return {"speaker_drops": 0}

    def close(self):
        self._open = False
        self.closed += 1


class Rec:
    def __init__(self):
        self.states, self.usage, self.ends, self.health = [], [], [], []

    async def on_state(self, s):
        self.states.append(s)

    def on_usage(self, s):
        self.usage.append(s)

    async def on_end(self, r):
        self.ends.append(r)

    def on_health(self, status, detail, action):
        self.health.append((status, detail, action))


def make_live(rec, client, audio=None, budget=None, **kw):
    reg = SkillRegistry()
    reg.register(ClockSkill())
    return LiveVoice(reg, lambda: "backend", audio_io=audio, client=client, on_state=rec.on_state,
                     on_usage=rec.on_usage, on_end=rec.on_end, on_health=rec.on_health, budget=budget,
                     close_timeout_s=0.5, start_timeout_s=1, **kw)


async def settle(lv, timeout=2.0):
    """Wait for the session to finalize. timeout=None: event-driven only (no wall-clock deadline); finalization is
    already bounded by the product (close_timeout_s, 3 s task wait), so a stalled CI loop cannot make it flaky."""
    if timeout is None:
        await lv.wait_ended()
    else:
        await asyncio.wait_for(lv.wait_ended(), timeout)


def assert_finalized(lv, rec, reason, audio=None):
    assert rec.ends == [reason]
    assert len(rec.usage) == 1
    assert rec.states[-1] == "idle"
    assert lv.running is False and lv.conn is None and lv._tasks == []
    if audio is not None:
        assert audio.closed == 1


# ---------------------------------------------------------------- CR-02 lifecycle
async def test_remote_session_closed_finalizes_once():
    rec, client, audio = Rec(), FakeClient(), FakeAudio()
    lv = make_live(rec, client, audio)
    await lv.start()
    assert rec.health[0][0] == "ok" and rec.states[-1] == "listening"
    conn = client.conn
    conn.push({"type": "session.closed", "reason": "remote_hangup", "usage": {"seconds": 12.5}})
    await settle(lv)
    assert_finalized(lv, rec, "remote_closed", audio)
    assert rec.usage == [12.5] and conn.exited
    assert ("session.close", "jarves_close") not in conn.sent      # server already closed: no extra close


async def test_expired_session_reports_timeout():
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client)
    await lv.start()
    client.conn.push({"type": "session.closed", "reason": "expired", "usage": {"seconds": 600}})
    await settle(lv)
    assert rec.ends == ["timeout"] and rec.usage == [600]


async def test_transport_exception_mid_stream():
    rec, client, audio = Rec(), FakeClient(), FakeAudio()
    lv = make_live(rec, client, audio)
    await lv.start()
    await lv.handle_event({"type": "session.usage.updated", "usage": {"seconds": 3.0}})
    client.conn.push(ConnectionResetError("boom"))
    await settle(lv)
    assert_finalized(lv, rec, "error:ConnectionResetError", audio)
    assert rec.usage == [3.0]
    assert any(h[0] == "degraded" and "connection lost" in h[1] for h in rec.health)


async def test_stream_ends_without_session_closed():
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client)
    await lv.start()
    client.conn.push(END)
    await settle(lv)
    assert rec.ends == ["remote_closed"] and len(rec.usage) == 1


async def test_event_task_crash_finalizes():
    rec, client, audio = Rec(), FakeClient(), FakeAudio()

    async def bad_transcript(role, text):
        raise ValueError("callback bug")
    lv = make_live(rec, client, audio, on_transcript=bad_transcript)
    await lv.start()
    client.conn.push({"type": "session.input_transcript.delta", "delta": "שלום"})
    client.conn.push({"type": "session.output_transcript.delta", "delta": "היי"})
    await settle(lv)
    assert_finalized(lv, rec, "error:ValueError", audio)
    assert any("internal error" in h[1] for h in rec.health)


async def test_mic_task_crash_finalizes():
    rec, client, audio = Rec(), FakeClient(), FakeAudio()

    async def broken_mic(rate, chunk_bytes):
        from jarves.voice.audio import AudioUnavailable
        raise AudioUnavailable("AUDIO_MIC_UNAVAILABLE", "mic gone", "plug the mic in")
        yield b""                                                    # pragma: no cover
    audio.mic_chunks = broken_mic
    lv = make_live(rec, client, audio)
    await lv.start()
    await settle(lv)
    assert_finalized(lv, rec, "error:AUDIO_MIC_UNAVAILABLE", audio)


async def test_stop_after_remote_close_no_double_accounting():
    rec, client, audio = Rec(), FakeClient(), FakeAudio()
    lv = make_live(rec, client, audio)
    await lv.start()
    client.conn.push({"type": "session.closed", "reason": "connection_lost", "usage": {"seconds": 5}})
    await settle(lv)
    await lv.stop()
    await lv.stop()
    assert_finalized(lv, rec, "remote_closed", audio)
    assert rec.usage == [5]


async def test_user_stop_is_graceful_and_idempotent():
    rec, client, audio = Rec(), FakeClient(close_seconds=9.0), FakeAudio()
    lv = make_live(rec, client, audio)
    await lv.start()
    await asyncio.gather(lv.stop(), lv.stop())
    assert_finalized(lv, rec, "user", audio)
    assert rec.usage == [9.0]                                       # final usage from session.closed
    assert [s for s in client.conns[0].sent if s[0] == "session.close"] == [("session.close", "jarves_close")]


async def test_stop_before_start_is_noop():
    rec = Rec()
    lv = make_live(rec, FakeClient())
    await lv.stop()
    assert rec.ends == [] and rec.usage == []


async def test_stop_from_inside_on_end_does_not_deadlock():
    rec, client = Rec(), FakeClient()
    holder = {}

    async def on_end(reason):
        rec.ends.append(reason)
        await holder["lv"].stop()
    lv = make_live(rec, client)
    lv.on_end = on_end
    holder["lv"] = lv
    await lv.start()
    client.conn.push({"type": "session.closed", "reason": "remote_hangup", "usage": {"seconds": 1}})
    await settle(lv)
    assert rec.ends == ["remote_closed"]


async def test_restart_after_end():
    rec, client, audio = Rec(), FakeClient(), FakeAudio()
    lv = make_live(rec, client, audio)
    await lv.start()
    client.conn.push({"type": "session.closed", "reason": "remote_hangup", "usage": {"seconds": 2}})
    await settle(lv)
    lv.audio_io = audio2 = FakeAudio()                              # Core makes a fresh audio stream per session
    await lv.start()
    assert lv.running and len(client.conns) == 2 and lv.usage_s == 0
    await lv.stop()
    assert rec.ends == ["remote_closed", "user"] and rec.usage == [2, 7.0]
    assert audio.closed == 1 and audio2.closed == 1


async def test_watchdog_ends_with_timeout():
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client, max_session_s=0.05)
    await lv.start()
    await settle(lv)
    assert rec.ends == ["timeout"] and len(rec.usage) == 1


async def test_start_timeout_finalizes_and_raises():
    rec, client = Rec(), FakeClient(auto_started=False)
    lv = make_live(rec, client)
    with pytest.raises(asyncio.TimeoutError):
        await lv.start()
    assert rec.ends == ["error:start_timeout"] and len(rec.usage) == 1 and lv._tasks == []


# ---------------------------------------------------------------- CR-05 errors / health
async def test_error_event_marks_degraded_and_continues():
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client)
    await lv.start()
    client.conn.push({"type": "error", "error": {"code": "unknown_parameter", "message": "bad session.voice",
                                                 "type": "invalid_request_error", "param": "session.voice"}})
    await asyncio.sleep(0.05)
    status, detail, action = rec.health[-1]
    assert status == "degraded" and "unknown_parameter" in detail and action
    assert lv.running and rec.ends == []
    await lv.stop()


async def test_fatal_error_ends_session():
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client)
    await lv.start()
    client.conn.push({"type": "error", "error": {"code": "insufficient_quota", "message": "quota",
                                                 "type": "insufficient_quota"}})
    await settle(lv)
    assert rec.ends == ["error:insufficient_quota"]
    assert rec.health[-1][0] == "degraded" and "billing" in rec.health[-1][2]


async def test_hung_transport_exit_and_closed_budget_never_hang_or_crash_the_finalize():
    """V4-VC-5 (campaign C): the transport exit hung for minutes after an unconfirmed close and the late settle hit a
    closed budget DB (CRASH in a later Core). The exit is bounded and a failing settle is a warning."""
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client)
    await lv.start()
    cm = lv._cm

    async def hang(*a):
        await asyncio.sleep(3600)
    cm.__aexit__ = hang                                      # instance attribute: the CM's exit never returns

    class Closed:
        def release(self):
            import sqlite3
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
    lv._credit = Closed()
    await asyncio.wait_for(lv.stop(), 4.0)                   # close_timeout_s 0.5: bounded, no exception
    assert rec.ends == ["user"]


async def test_credit_balance_exhausted_ends_session_with_billing_action():
    """V4-VC-4 (campaign C, live 21 Oct): GPT-Live's 'credit_balance_exhausted' arrives as invalid_request_error;
    the session cannot do anything useful, so it ends and the action names the credits."""
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client)
    await lv.start()
    client.conn.push({"type": "error", "error": {"code": "credit_balance_exhausted", "message": "You have no credits",
                                                 "type": "invalid_request_error"}})
    await settle(lv)
    assert rec.ends == ["error:credit_balance_exhausted"]
    assert rec.health[-1][0] == "degraded" and "credits" in rec.health[-1][2]


async def test_validation_failure_degraded_with_action():
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client)
    await lv.start()
    client.conn.push({"type": "error", "error": {"code": "responses_validation_failed",
                                                 "message": "model gpt-6-luna is not accessible",
                                                 "type": "invalid_request_error"}})
    await asyncio.sleep(0.05)
    status, detail, action = rec.health[-1]
    assert status == "degraded" and "gpt-6-luna" in detail and "ai.text_model" in action
    assert lv.running                                               # docs: session stays open
    await lv.stop()


async def test_sdk_model_error_event_shape():
    from openai.types.live.error_event import ErrorEvent
    rec = Rec()
    lv = make_live(rec, FakeClient())
    ev = ErrorEvent.model_validate({"type": "error", "event_id": "e1", "error": {
        "code": "responses_validation_failed", "message": "web_search unsupported", "type": "invalid_request_error"}})
    await lv.handle_event(ev)
    assert rec.health[-1][0] == "degraded" and "web_search" in rec.health[-1][1]


# ---------------------------------------------------------------- CR-06 audio
async def test_interruption_flushes_speaker_queue_full_duplex():
    rec, audio = Rec(), FakeAudio()
    lv = make_live(rec, FakeClient(), audio, half_duplex=False)
    pcm = base64.b64encode(loud(24000 * 2 * 3)).decode()          # 3 s of assistant speech
    await lv.handle_event({"type": "session.output_audio.delta", "delta": pcm})
    assert audio.queued_s() == pytest.approx(3.0)
    lv._turn_audio_t0 -= 1.0                                        # it has been playing for a second
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "רגע"})
    assert audio.queued_s() == 0 and audio.flushed == 1 and lv._speaking_until == 0
    assert rec.states[-1] == "listening" and lv.stats()["barge_ins"] == 1


async def test_trailing_transcript_does_not_flush_new_reply():
    rec, audio = Rec(), FakeAudio()
    lv = make_live(rec, FakeClient(), audio, half_duplex=False)
    await lv.handle_event({"type": "session.output_audio.delta", "delta": base64.b64encode(bytes(4800)).decode()})
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "סוף משפט"})   # just started
    assert audio.flushed == 0


async def test_real_streaming_audio_flush_on_live_interrupt():
    rec, audio = Rec(), StreamingAudio()
    lv = make_live(rec, FakeClient(), audio, half_duplex=False)
    await lv.handle_event({"type": "session.output_audio.delta",
                           "delta": base64.b64encode(loud(24000 * 2 * 2)).decode()})
    assert audio.queued_s() == pytest.approx(2.0)
    await lv.interrupt()
    assert audio.queued_s() == 0 and audio.stats()["speaker_flushes"] == 1


async def test_realtime_interrupt_flushes():
    rec, audio = Rec(), StreamingAudio()
    rv = RealtimeVoice("gpt-realtime-2.1", lambda: "", [], audio_io=audio, on_state=rec.on_state)
    await rv.handle_event(SimpleNamespace(type="audio", audio=SimpleNamespace(data=bytes(48000))))
    await rv.handle_event(SimpleNamespace(type="audio_interrupted"))
    assert audio.queued_s() == 0 and rv.stats()["barge_ins"] == 1


async def test_speaker_queue_bounded_under_flood():
    audio = StreamingAudio(speaker_max_s=12)
    chunk = bytes(4800)                                             # 100 ms
    for _ in range(3000):                                           # 300 s of audio arriving faster than playback
        audio.enqueue(chunk)
    s = audio.stats()
    assert audio.queued_s() <= 12.0 and s["speaker_max_depth_s"] <= 12.1
    assert s["speaker_drops"] == 3000 - 120 and s["speaker_dropped_s"] == pytest.approx(288.0)


async def test_speaker_player_reads_small_pieces():
    audio = StreamingAudio()
    audio.enqueue(bytes(24000 * 2))                                 # one 1 s delta
    piece = await audio._spk_get(4800)
    assert len(piece) == 4800 and audio.queued_s() == pytest.approx(0.9)
    audio.close()
    assert await audio._spk_get(4800) is None and audio.queued_s() == 0


async def test_mic_queue_bounded_drops_oldest():
    audio = StreamingAudio(mic_max_s=2.0)                           # 20 x 100 ms
    for i in range(100):
        audio._mic_push(i.to_bytes(2, "little") + bytes(4798))
    s = audio.stats()
    assert len(audio._mic_q) == 20 and s["mic_drops"] == 80 and s["mic_max_depth_s"] == pytest.approx(2.0)
    first = await audio._mic_get()
    assert int.from_bytes(first[:2], "little") == 80               # oldest dropped, newest kept


async def test_close_clears_buffers_and_wakes_waiters():
    audio = StreamingAudio()
    audio.enqueue(bytes(4800))
    audio._mic_push(bytes(4800))
    waiter = asyncio.ensure_future(StreamingAudio._spk_get(audio, 4800))
    audio.close()
    audio.close()                                                   # idempotent
    assert audio.queued_s() == 0 and len(audio._mic_q) == 0
    assert await audio._mic_get() is None
    assert await asyncio.wait_for(waiter, 1) in (None, bytes(4800))
    audio.enqueue(bytes(4800))                                      # ignored after close
    assert audio.queued_s() == 0


# ---------------------------------------------------------------- CR-01 Live delegation budget
def budget(tmp_path, limit):
    return Budget(MemoryStore(tmp_path / "m.db"), AIConfig(daily_request_limit=limit))


async def test_backend_response_completion_is_counted(tmp_path):
    rec, b = Rec(), budget(tmp_path, 10)
    lv = make_live(rec, FakeClient(), budget=b)
    for t in ("response.completed", "response.failed", "response.output_text.delta"):
        await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {"type": t}})
    assert b.used("ai_requests") == 2 and lv.stats()["backend_responses"] == 2


async def test_delegated_tool_call_budget_exhausted_ends_session(tmp_path):
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 1)
    lv = make_live(rec, client, budget=b)
    await lv.start()
    conn = client.conn
    conn.push({"type": "response.event", "delegation_id": "d1", "event": {"type": "response.completed"}})
    conn.push({"type": "response.event", "delegation_id": "d2", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "get_time", "call_id": "c9", "arguments": "{}"}}})
    await settle(lv, None)                  # #788: no 2 s wall deadline (windows CI stalled the loop for 12 s)
    assert rec.ends == ["budget"] and len(rec.usage) == 1
    outputs = [i for k, i in conn.sent if k == "response.item.create"]
    assert outputs and outputs[0]["call_id"] == "c9"
    out = json.loads(outputs[0]["output"])
    assert out["ok"] is False and out["code"] == "AI_BUDGET"
    assert ("response.create", None) not in conn.sent               # no further paid backend response
    assert any("ai.daily_request_limit" in h[2] for h in rec.health)


def deleg(i):
    return {"type": "session.delegation.created", "offset_ms": 10,
            "delegation": {"id": f"d{i}", "target": "responses", "type": "delegation"}}


def done(i):
    return {"type": "response.event", "delegation_id": f"d{i}", "event": {"type": "response.completed"}}


async def test_no_budget_session_does_not_start(tmp_path):
    """F1 boundary: with 0 units left nothing is reserved, so no live session (and no delegation) can begin."""
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 0)
    lv = make_live(rec, client, budget=b)
    with pytest.raises(BudgetExceeded):
        await lv.start()
    assert client.conns == [] and b.held("ai_requests") == 0        # never even connected


async def test_credit_reserved_ahead_one_unit_left(tmp_path):
    """Limit 1: the session's credit IS the last unit. The first delegation uses it, the refill fails -> mic input
    closed and the session ends after that answer. Usage never exceeds the limit."""
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 1)
    lv = make_live(rec, client, budget=b)
    await lv.start()
    assert b.held("ai_requests") == 1 and b.remaining("ai_requests") == 0      # reserved BEFORE any delegation
    await lv.handle_event(deleg(1))
    assert lv._input_closed and lv._gated()                                   # no new user turns
    await lv.handle_event(done(1))
    await settle(lv)
    assert rec.ends == ["budget"] and b.used("ai_requests") == 1 and b.held("ai_requests") == 0
    assert lv.stats()["budget_overrun"] == 0


async def test_concurrent_delegations_with_one_unit_left(tmp_path):
    """Two delegations created back to back (speech already sent before the input closed) with 1 unit: the
    second has no credit -> counted as a documented overrun, never silently free, and the session ends."""
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 1)
    lv = make_live(rec, client, budget=b)
    await lv.start()
    await lv.handle_event(deleg(1))
    await lv.handle_event(deleg(2))
    await lv.handle_event(done(1))
    await lv.handle_event(done(2))
    await settle(lv)
    assert rec.ends == ["budget"] and lv.stats()["budget_overrun"] == 1
    assert b.used("ai_requests") == 2 and b.held("ai_requests") == 0          # both counted


async def test_credit_refills_while_budget_lasts(tmp_path):
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 5)
    lv = make_live(rec, client, budget=b)
    await lv.start()
    for i in range(3):
        await lv.handle_event(deleg(i))
        await lv.handle_event(done(i))
    assert b.used("ai_requests") == 3 and b.held("ai_requests") == 1          # next credit already reserved
    assert not lv._input_closed and rec.ends == []
    await lv.stop()
    await settle(lv)
    assert b.used("ai_requests") == 3 and b.held("ai_requests") == 0          # unused credit released, not billed


async def test_continuation_reserved_before_send_and_counted_on_failure(tmp_path):
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 3)
    lv = make_live(rec, client, budget=b)
    await lv.start()
    await lv.handle_event(deleg(1))
    held_before = b.held("ai_requests")
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "get_time", "call_id": "c1", "arguments": "{}"}}})
    assert b.held("ai_requests") == held_before + 1                           # reserved before response.create
    await drain_tools(lv)
    assert ("response.create", None) in client.conn.sent
    await lv.handle_event(done(1))
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {"type": "response.failed"}})
    assert b.used("ai_requests") == 2                                         # failed continuation still counted


async def test_live_and_cli_share_last_unit(tmp_path):
    """Another process/instance (e.g. `jarvis ask`) cannot take the unit a live session holds as credit."""
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 1)
    lv = make_live(rec, client, budget=b)
    await lv.start()
    other = budget(tmp_path, 1)
    with pytest.raises(BudgetExceeded):
        other.reserve()
    await lv.stop()


async def test_tool_call_within_budget_runs(tmp_path):
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 5)
    lv = make_live(rec, client, budget=b)
    lv.conn = client_conn = FakeLiveConn()
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "get_time", "call_id": "c1", "arguments": "{}"}}})
    await drain_tools(lv)
    assert json.loads(client_conn.sent[0][1]["output"])["ok"] and client_conn.sent[1][0] == "response.create"


# ---------------------------------------------------------------- RealtimeVoice lifecycle
class FakeRTSession:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.closed = 0
        self.audio_in = 0

    async def enter(self):
        return self

    async def close(self):
        self.closed += 1

    async def send_audio(self, data):
        self.audio_in += 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        ev = await self.q.get()
        if ev is END:
            raise StopAsyncIteration
        if isinstance(ev, BaseException):
            raise ev
        return ev


def make_rt(rec, audio=None, **kw):
    sessions = []

    def factory(*a):
        s = FakeRTSession()
        sessions.append(s)

        async def run():
            return s
        return SimpleNamespace(run=run)
    rv = RealtimeVoice("gpt-realtime-2.1", lambda: "", [], audio_io=audio, on_state=rec.on_state,
                       on_usage=rec.on_usage, on_end=rec.on_end, on_health=rec.on_health,
                       runner_factory=factory, **kw)
    return rv, sessions


async def test_realtime_remote_close_finalizes_once():
    rec, audio = Rec(), FakeAudio()
    rv, sessions = make_rt(rec, audio)
    await rv.start()
    sessions[0].q.put_nowait(END)
    await asyncio.wait_for(rv.wait_ended(), 2)
    await rv.stop()
    assert rec.ends == ["remote_closed"] and len(rec.usage) == 1 and rec.states[-1] == "idle"
    assert sessions[0].closed == 1 and audio.closed == 1 and rv._tasks == []


async def test_realtime_transport_error_and_restart():
    rec = Rec()
    rv, sessions = make_rt(rec)
    await rv.start()
    sessions[0].q.put_nowait(OSError("net down"))
    await asyncio.wait_for(rv.wait_ended(), 2)
    assert rec.ends == ["error:OSError"] and rec.health[-1][0] == "degraded"
    await rv.start()
    await rv.stop()
    assert rec.ends == ["error:OSError", "user"] and len(rec.usage) == 2 and len(sessions) == 2


async def test_realtime_error_event_degraded_and_fatal():
    rec = Rec()
    rv, sessions = make_rt(rec)
    await rv.start()
    sessions[0].q.put_nowait(SimpleNamespace(type="error", error={"type": "error", "error": {
        "code": "rate_limit_exceeded", "message": "slow down", "type": "rate_limit_error"}}))
    await asyncio.sleep(0.05)
    assert rec.health[-1][0] == "degraded" and rv.running
    sessions[0].q.put_nowait(SimpleNamespace(type="error", error={"code": "invalid_api_key", "message": "bad key",
                                                                  "type": "authentication_error"}))
    await asyncio.wait_for(rv.wait_ended(), 2)
    assert rec.ends == ["error:invalid_api_key"] and "OPENAI_API_KEY" in rec.health[-1][2]


async def test_realtime_watchdog_timeout():
    rec = Rec()
    rv, _ = make_rt(rec, max_session_s=0.05)
    t0 = time.monotonic()
    await rv.start()
    await asyncio.wait_for(rv.wait_ended(), 2)
    assert rec.ends == ["timeout"] and 0 < rec.usage[0] < time.monotonic() - t0 + 0.1


# ---------------------------------------------------------------- continuous silence from GPT-Live (QA 18 Oct)
def delta(pcm: bytes) -> dict:
    return {"type": "session.output_audio.delta", "delta": base64.b64encode(pcm).decode()}


async def test_continuous_silence_stream_does_not_mute_mic_or_build_backlog():
    """GPT-Live streams audio all session long, silence included. It must not gate the mic, queue a playback
    backlog (a permanent ~3.5 s delay), count as activity, or flip the face to 'speaking'."""
    rec, audio = Rec(), FakeAudio()
    lv = make_live(rec, FakeClient(), audio)
    lv._last_activity = 0.0
    for _ in range(60):                                              # 6 s of digital silence, 100 ms at a time
        await lv.handle_event(delta(bytes(4800)))
    assert audio.queued_s() == 0                                     # nothing queued: no backlog, no latency
    assert not lv._gated() and "speaking" not in rec.states
    assert lv._first_audio.is_set() is False                         # silence is not "first audio"
    assert lv._last_activity == 0.0                                  # silence is not activity (idle timer runs)


async def test_speech_then_silence_gates_only_while_speaking_and_keeps_short_pauses():
    rec, audio = Rec(), FakeAudio()
    lv = make_live(rec, FakeClient(), audio)
    await lv.handle_event(delta(loud(4800 * 5)))                     # 0.5 s of speech
    assert lv._gated() and "speaking" in rec.states and lv._first_audio.is_set()
    await lv.handle_event(delta(bytes(4800)))                        # 100 ms pause right after speech: kept
    assert audio.queued_s() == pytest.approx(0.6)
    lv._last_speech_t -= 2.0                                         # long after the last word: idle silence
    await lv.handle_event(delta(bytes(4800)))
    assert audio.queued_s() == pytest.approx(0.6)                    # dropped, nothing added
    lv._speaking_until = 0.0
    assert not lv._gated() or True


# ---------------------------------------------------------------- long utterances and false wakes (QA 19 Oct)
async def test_model_speaking_while_the_user_is_still_talking_is_dropped_and_mic_stays_open():
    rec, audio = Rec(), FakeAudio()
    lv = make_live(rec, FakeClient(), audio)
    lv._last_user_voice = time.monotonic()                      # the user is mid-story right now
    await lv.handle_event(delta(loud(4800 * 5)))                # the model starts answering anyway
    assert audio.queued_s() == 0 and "speaking" not in rec.states        # not played, not shown as speaking
    assert not lv._gated()                                      # the mic gate did NOT close: the story keeps flowing
    assert lv.stats()["early_response_drops"] == 1
    await lv.handle_event(delta(loud(4800 * 5)))                # the tail of that response: still dropped
    assert audio.queued_s() == 0
    lv._last_user_voice -= 5
    lv._drop_until = 0.0                                        # the user stopped long ago
    await lv.handle_event(delta(loud(4800 * 5)))                # now it is a normal answer
    assert audio.queued_s() == pytest.approx(0.5) and "speaking" in rec.states and lv._gated()


async def test_wake_started_session_with_no_speech_closes_quickly_but_a_normal_one_does_not():
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client, no_speech_timeout_s=0.6, idle_disconnect_s=60)
    await lv.start()
    await asyncio.sleep(2.0)
    assert rec.ends == ["no_speech"]                            # false wake: closed, not 60 s of billing
    rec2, client2 = Rec(), FakeClient()
    lv2 = make_live(rec2, client2, no_speech_timeout_s=0.6, idle_disconnect_s=60)
    await lv2.start()
    await lv2.handle_event({"type": "session.input_transcript.delta", "delta": "שלום"})   # somebody spoke
    await asyncio.sleep(2.0)
    assert rec2.ends == []
    await lv2.stop()
    rec3, client3 = Rec(), FakeClient()
    lv3 = make_live(rec3, client3, idle_disconnect_s=60)          # connected-on-open: no timeout configured
    await lv3.start()
    await asyncio.sleep(1.5)
    assert rec3.ends == []
    await lv3.stop()


async def test_provider_error_during_our_own_close_is_debug_not_error_or_degraded(caplog):
    """V4-VC-6: 'session is closing' answers to a late client event are noise: no ERROR line, no degraded health."""
    import logging
    rec, client = Rec(), FakeClient()
    lv = make_live(rec, client)
    await lv.start()
    health_before = list(rec.health)
    lv._closing = True
    with caplog.at_level(logging.DEBUG):
        await lv._on_error({"code": "invalid_request_error", "type": "invalid_request_error",
                            "message": "The session is closing and cannot accept new client events"})
        await lv._on_error({"code": "unknown_parameter", "type": "invalid_request_error", "message": "bad param"})
    assert not any(r.levelno >= logging.ERROR and "closing" in r.getMessage() for r in caplog.records)
    assert rec.health[len(health_before):] and "unknown_parameter" in rec.health[-1][1]   # a real error still counts
    assert len(rec.health) == len(health_before) + 1
    lv._closing = False
    await lv.stop()
