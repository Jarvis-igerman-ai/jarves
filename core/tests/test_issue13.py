"""Issue #13: GPT-Live overrun handling, voice time after abnormal disconnect, reservation expiry/settlement."""
import asyncio
import itertools
from datetime import date, timedelta

import pytest

from jarves.budget import HARD_TTL_S, Budget
from jarves.config import AIConfig
from jarves.memory import MemoryStore
from test_voice_faults import FakeClient, Rec, make_live, settle


def deleg(i):
    return {"type": "session.delegation.created", "offset_ms": 10,
            "delegation": {"id": f"d{i}", "target": "responses", "type": "delegation"}}


def done(i):
    return {"type": "response.event", "delegation_id": f"d{i}", "event": {"type": "response.completed"}}


def budget(tmp_path, limit, **kw):
    return Budget(MemoryStore(tmp_path / "m.db"), AIConfig(daily_request_limit=limit), **kw)


# ------------------------------------------------------------------ P1: overlapping delegations
@pytest.mark.parametrize("order", list(itertools.permutations([1, 2, 3])))
async def test_three_overlapping_delegations_all_counted_and_session_closed(tmp_path, order):
    """Limit 1: the server starts 3 delegations before any completes. The client cannot refuse them (no API
    gate), so it must (a) count EVERY one, never free, (b) close the session at the first one without credit,
    (c) never send a paid continuation. Exposure is reported, not hidden: 2 overruns here."""
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 1)
    lv = make_live(rec, client, budget=b)
    await lv.start()
    for i in (1, 2, 3):
        await lv.handle_event(deleg(i))
    assert lv._input_closed and lv._end_task is not None                 # closing already at delegation 2
    for i in order:
        await lv.handle_event(done(i))
    await settle(lv)
    assert rec.ends == ["budget"]
    assert b.used("ai_requests") == 3 and b.held("ai_requests") == 0     # every started request counted once
    assert lv.stats()["budget_overrun"] == 2
    assert ("response.create", None) not in client.conn.sent


async def test_completions_after_session_end_not_double_counted(tmp_path):
    rec, client, b = Rec(), FakeClient(), budget(tmp_path, 1)
    lv = make_live(rec, client, budget=b)
    await lv.start()
    await lv.handle_event(deleg(1))
    await lv.handle_event(deleg(2))                                       # overrun -> session ends
    await settle(lv)                                                      # finalize counts both pending
    assert b.used("ai_requests") == 2
    await lv.handle_event(done(1))                                        # stray late events
    await lv.handle_event(done(2))
    assert b.used("ai_requests") == 2


# ------------------------------------------------------------------ P1/P2: voice seconds after abrupt disconnect
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def started_live(rec, client, **kw):
    lv = make_live(rec, client, **kw)
    lv._clock = clock = Clock()
    await lv.start()
    await lv.handle_event({"type": "session.started"})
    return lv, clock


async def test_abrupt_disconnect_without_usage_counts_wall_clock(tmp_path):
    rec, client = Rec(), FakeClient(auto_closed=False)      # link dies: no session.closed / usage
    lv, clock = await started_live(rec, client)
    clock.t += 120                                                        # 2 paid minutes, no usage events
    lv._request_end("error:connection_lost")
    await settle(lv)
    assert rec.usage == [pytest.approx(120)]


async def test_partial_usage_then_drop_counts_the_larger(tmp_path):
    rec, client = Rec(), FakeClient(auto_closed=False)      # link dies: no session.closed / usage
    lv, clock = await started_live(rec, client)
    clock.t += 30
    await lv.handle_event({"type": "session.usage.updated", "usage": {"seconds": 25}})
    clock.t += 60                                                         # 90 s elapsed, server last said 25
    lv._request_end("remote_closed")
    await settle(lv)
    assert rec.usage == [pytest.approx(90)]


async def test_server_final_usage_wins_when_closed_gracefully(tmp_path):
    rec, client = Rec(), FakeClient()
    lv, clock = await started_live(rec, client)
    clock.t += 100
    await lv.handle_event({"type": "session.closed", "reason": "client_closed", "usage": {"seconds": 95}})
    await settle(lv)
    assert rec.usage == [pytest.approx(95)]


async def test_usage_reported_exactly_once_and_capped(tmp_path):
    rec, client = Rec(), FakeClient(auto_closed=False)      # link dies: no session.closed / usage
    lv, clock = await started_live(rec, client)
    clock.t += 99999                                                      # a hung clock / sleep cannot explode it
    await lv.stop()
    await lv.stop()
    lv._request_end("error:x")
    await settle(lv)
    assert len(rec.usage) == 1 and rec.usage[0] == pytest.approx(lv.max_session_s + 60)


# ------------------------------------------------------------------ P2: reservation expiry / settlement
OTHER = 4_000_000                                                         # a fake owner PID


def foreign(b, r):
    b._db.execute("UPDATE reservations SET pid = ? WHERE id = ?", (OTHER, r.id))


def test_active_owner_reservation_survives_ttl_and_settles_once(tmp_path):
    t = [10_000.0]
    owner = budget(tmp_path, 5, clock=lambda: t[0])
    r = owner.reserve()
    foreign(owner, r)                                                     # owned by a (live) other process
    other = budget(tmp_path, 5, clock=lambda: t[0], alive=lambda pid: True)
    t[0] += 3600                                                          # a long live session: 1 h in flight
    other.reserve().release()                                             # runs expiry
    assert other.held("ai_requests") == 1 and other.used("ai_requests") == 0      # not expired, not counted
    r.commit()
    assert owner.used("ai_requests") == 1 and owner.held("ai_requests") == 0      # counted exactly once


def test_crashed_owner_counted_immediately_and_late_commit_adds_nothing(tmp_path):
    t = [10_000.0]
    owner = budget(tmp_path, 5, clock=lambda: t[0])
    r = owner.reserve()
    foreign(owner, r)
    other = budget(tmp_path, 5, clock=lambda: t[0], alive=lambda pid: False)       # owner is gone
    other.reserve().release()
    assert other.used("ai_requests") == 1 and other.held("ai_requests") == 0       # conservative: counted
    r.commit()                                                            # (e.g. PID reused / zombie owner)
    assert owner.used("ai_requests") == 1                                 # no double count


def test_hard_ttl_backstop_then_owner_commit_no_double(tmp_path):
    t = [10_000.0]
    owner = budget(tmp_path, 5, clock=lambda: t[0])
    r = owner.reserve()
    foreign(owner, r)
    other = budget(tmp_path, 5, clock=lambda: t[0], alive=lambda pid: True)
    t[0] += HARD_TTL_S + 1
    other.reserve().release()
    assert other.used("ai_requests") == 1
    r.commit()
    assert owner.used("ai_requests") == 1


def test_settles_on_the_reservation_day_across_midnight(tmp_path):
    b = budget(tmp_path, 5)
    r = b.reserve()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r.day = yesterday
    b._db.execute("UPDATE reservations SET day = ? WHERE id = ?", (yesterday, r.id))
    r.commit()
    assert b.used("ai_requests") == 0                                     # today's allowance untouched
    got = b._db.execute("SELECT count FROM usage WHERE day = ? AND kind = 'ai_requests'", (yesterday,)).fetchone()
    assert got == (1,)


def test_release_after_expiry_is_safe(tmp_path):
    owner = budget(tmp_path, 5)
    r = owner.reserve()
    foreign(owner, r)
    budget(tmp_path, 5, alive=lambda pid: False).reserve().release()      # expiry counted it
    r.release()                                                           # no crash, no change
    assert owner.used("ai_requests") == 1 and owner.held("ai_requests") == 0
