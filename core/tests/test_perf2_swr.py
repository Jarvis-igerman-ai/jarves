"""#159 perf2: slow dashboard reads (/api/selfmanage, /api/connect, /api/dev/status, /api/agents/doctor) are served
from a stale-while-revalidate cache; a poll never pays the full cost twice and never hangs the request thread."""
import statistics
import threading
import time

import pytest

from jarves.swr import SwrCache


def test_fresh_value_is_served_from_memory_and_compute_runs_once():
    n = []
    c = SwrCache(ttl_s=60, wait_s=5)

    def compute():
        n.append(1)
        time.sleep(0.2)
        return 200, {"v": len(n)}
    assert c.get("k", compute) == (200, {"v": 1})
    times = []
    for _ in range(7):
        t = time.perf_counter()
        assert c.get("k", compute) == (200, {"v": 1})
        times.append(time.perf_counter() - t)
    assert statistics.median(times) < 0.01 and len(n) == 1


def test_stale_value_is_served_at_once_while_one_refresh_runs():
    now = [0.0]
    c = SwrCache(ttl_s=10, wait_s=5, clock=lambda: now[0])
    gate = threading.Event()
    calls = []

    def compute():
        calls.append(1)
        if len(calls) > 1:
            gate.wait(5)
        return 200, {"v": len(calls)}
    assert c.get("k", compute)[1]["v"] == 1
    now[0] = 11.0
    t = time.perf_counter()
    assert c.get("k", compute)[1]["v"] == 1 and c.get("k", compute)[1]["v"] == 1   # stale, immediately
    assert time.perf_counter() - t < 0.5
    gate.set()
    for _ in range(100):
        if c.get("k", compute)[1]["v"] == 2:
            break
        time.sleep(0.02)
    assert c.get("k", compute)[1]["v"] == 2 and len(calls) == 2                     # exactly one refresh


def test_first_call_times_out_to_pending_instead_of_hanging():
    gate = threading.Event()
    c = SwrCache(ttl_s=60, wait_s=0.2)
    t = time.perf_counter()
    res = c.get("k", lambda: (gate.wait(5), (200, {"ok": 1}))[1], lambda: (200, {"pending": True}))
    assert time.perf_counter() - t < 1.5 and res == (200, {"pending": True})
    gate.set()
    for _ in range(100):
        if c.get("k", lambda: (200, {"ok": 2}), lambda: (200, {"pending": True}))[1].get("ok"):
            break
        time.sleep(0.02)
    assert c.get("k", lambda: (200, {"ok": 2}))[1] == {"ok": 1}                     # the slow result was kept


def test_invalidate_forces_recompute_and_errors_and_non_200_are_not_cached():
    c = SwrCache(ttl_s=60, wait_s=5)
    state = {"v": 1}
    assert c.get("k", lambda: (200, dict(state)))[1] == {"v": 1}
    state["v"] = 2
    assert c.get("k", lambda: (200, dict(state)))[1] == {"v": 1}
    c.invalidate()
    assert c.get("k", lambda: (200, dict(state)))[1] == {"v": 2}
    assert c.get("bad", lambda: (400, {"error": "x"})) == (400, {"error": "x"})
    assert c.get("bad", lambda: (200, {"ok": True})) == (200, {"ok": True})        # the 400 was not kept
    with pytest.raises(ValueError):
        c.get("boom", lambda: (_ for _ in ()).throw(ValueError("x")))
    assert c.get("boom", lambda: (200, {"ok": 1})) == (200, {"ok": 1})


def test_a_refresh_started_before_invalidate_does_not_store_its_old_result():
    c = SwrCache(ttl_s=60, wait_s=0.1)
    gate = threading.Event()
    c.get("k", lambda: (gate.wait(5), (200, {"old": 1}))[1], lambda: (200, {"pending": True}))
    c.invalidate()
    gate.set()
    time.sleep(0.3)
    assert c.get("k", lambda: (200, {"new": 1}))[1] == {"new": 1}
