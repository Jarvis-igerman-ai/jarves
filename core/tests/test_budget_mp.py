import pytest
"""F3: the daily budget holds across PROCESSES (Core + `jarvis ask` + `jarvis say` share one SQLite file)."""
import subprocess
import sys
import textwrap
import time

from jarves.budget import Budget, BudgetExceeded
from jarves.config import AIConfig
from jarves.memory import MemoryStore

CHILD = textwrap.dedent("""
    import os, sys, time
    from jarves.budget import Budget, BudgetExceeded
    from jarves.config import AIConfig
    from jarves.memory import MemoryStore
    db, go, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    b = Budget(MemoryStore(db), AIConfig(daily_request_limit=3))
    while not os.path.exists(go):          # barrier: every process races for the units at the same moment
        time.sleep(0.005)
    try:
        r = b.reserve()
    except BudgetExceeded:
        print("DENIED"); sys.exit(0)
    if mode == "crash":
        print("RESERVED-CRASH", flush=True); os._exit(9)     # dies holding the reservation
    time.sleep(0.2)                         # "request in flight" while the others try
    r.commit()
    print("OK")
""")


def run_children(tmp_path, n, mode="ok"):
    db, go = tmp_path / "m.db", tmp_path / "go"
    MemoryStore(db).close()
    procs = [subprocess.Popen([sys.executable, "-c", CHILD, str(db), str(go), mode], stdout=subprocess.PIPE,
                              text=True) for _ in range(n)]
    time.sleep(1.5)                         # let every child import and reach the barrier
    go.write_text("go")
    return [p.communicate(timeout=60)[0].strip() for p in procs], db


def test_parallel_processes_never_exceed_limit(tmp_path):
    outs, db = run_children(tmp_path, 8)
    assert outs.count("OK") == 3 and outs.count("DENIED") == 5, outs
    b = Budget(MemoryStore(db), AIConfig(daily_request_limit=3))
    assert b.used("ai_requests") == 3 and b.held("ai_requests") == 0


def test_crashed_process_reservations_are_counted_not_freed(tmp_path):
    """Two processes die holding reservations (#13: owner-liveness expiry). Their units are never handed out
    again for free: as soon as any process runs the budget they are COUNTED; the limit still holds."""
    outs, db = run_children(tmp_path, 2, mode="crash")
    assert sorted(outs) == ["RESERVED-CRASH", "RESERVED-CRASH"], outs
    b = Budget(MemoryStore(db), AIConfig(daily_request_limit=3))
    assert b.used("ai_requests") + b.held("ai_requests") == 2             # held or already counted, never freed
    r = b.reserve()                                   # expiry of dead owners runs inside the reserve transaction
    assert b.used("ai_requests") == 2 and b.held("ai_requests") == 1
    r.commit()
    assert b.used("ai_requests") == 3 and b.held("ai_requests") == 0
    with pytest.raises(BudgetExceeded):
        b.reserve()


def test_two_budget_objects_share_inflight(tmp_path):
    """Same machine, two independent instances (e.g. Core and the CLI): the in-flight unit is visible to both."""
    mem = MemoryStore(tmp_path / "m.db")
    a, b = Budget(mem, AIConfig(daily_request_limit=1)), Budget(MemoryStore(tmp_path / "m.db"), AIConfig(daily_request_limit=1))
    r = a.reserve()
    try:
        b.reserve()
        raise AssertionError("second process got the last unit")
    except BudgetExceeded:
        pass
    r.release()                                       # never sent -> freed, not counted
    assert b.reserve() and a.used("ai_requests") == 0
