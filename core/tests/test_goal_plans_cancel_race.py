"""#813 / JAR-235: once the owner cancelled a plan, no later read-then-write (restart recovery, the runner's step
write, a skip) may set it back to running/paused/done; and a cancel on a stale view never overwrites a finished plan."""
from __future__ import annotations

import pytest

from test_v5_goal_plans import env, steps  # noqa: F401  (the shared fixture and helper)


def _cancel_after_first_read(env, method):
    """Wrap a db read so the owner's cancel lands right after `method` read the plan, before its write."""
    real = getattr(env.db, method)
    fired = []

    def racing(sql, args=()):
        out = real(sql, args)
        if not fired and "FROM goal_plans" in sql:
            fired.append(1)
            for row in out if isinstance(out, list) else [out]:
                env.store.cancel(row["id"])
        return out
    setattr(env.db, method, racing)
    return lambda: setattr(env.db, method, real)


def test_recover_racing_a_cancel_keeps_cancelled(env):
    p = env.store.propose("r", steps(("fake_read", {"text": "a"})))
    env.store.approve(p["id"])
    env.db.x("UPDATE goal_plans SET state='running' WHERE id=?", (p["id"],))   # the Core stopped mid-run
    undo = _cancel_after_first_read(env, "rows")
    env.store.recover()
    undo()
    d = env.store.get(p["id"])
    assert d["state"] == "cancelled" and d["note"] == "cancelled by the owner"


async def test_runner_step_write_after_a_cancel_never_runs_the_skipped_step(env):
    p = env.store.propose("s", steps(("fake_read", {"text": "a"})))
    env.store.approve(p["id"])
    real = env.store.steps

    def racing(pid):                                              # cancel lands after the runner picked its step
        env.store.steps = real
        out = real(pid)
        env.store.cancel(pid)
        return out
    env.store.steps = racing
    await env.runner.tick()
    env.store.steps = real
    d = env.store.get(p["id"])
    assert d["state"] == "cancelled" and env.ran == []
    assert [s["status"] for s in d["steps"]] == ["skipped"]


async def test_cancel_on_a_stale_view_never_overwrites_a_finished_plan(env):
    p = env.store.propose("f", steps(("fake_read", {"text": "a"})))
    env.store.approve(p["id"])
    real = env.store._plan
    old = dict(real(p["id"]))                                     # the owner's view: still 'ready'
    await env.runner.tick()
    assert env.store.get(p["id"])["state"] == "done"
    env.store._plan = lambda pid: dict(old)
    with pytest.raises(ValueError):
        env.store.cancel(p["id"])
    env.store._plan = real
    assert env.store.get(p["id"])["state"] == "done"


@pytest.mark.parametrize("seen,now", [("ready", "running"), ("paused", "ready"), ("paused", "blocked"),
                                      ("running", "blocked"), ("running", "failed"), ("blocked", "paused")])
def test_cancel_on_a_stale_open_view_still_cancels_a_plan_that_moved_on(env, seen, now):
    p = env.store.propose("m", steps(("fake_read", {"text": "a"})))
    env.store.approve(p["id"])
    real = env.store._plan
    env.db.x("UPDATE goal_plans SET state=? WHERE id=?", (seen, p["id"]))
    old = dict(real(p["id"]))                                     # the owner's view
    env.db.x("UPDATE goal_plans SET state=? WHERE id=?", (now, p["id"]))   # the plan moved on meanwhile
    env.store._plan = lambda pid: dict(old)
    try:
        env.store.cancel(p["id"])
    finally:
        env.store._plan = real
    d = env.store.get(p["id"])
    assert d["state"] == "cancelled" and [s["status"] for s in d["steps"]] == ["skipped"]


@pytest.mark.parametrize("seen,now,kept", [("ready", "running", "running"), ("paused", "blocked", "blocked"),
                                           ("blocked", "failed", "paused")])
def test_skip_on_a_stale_open_view_uses_the_current_state(env, seen, now, kept):
    p = env.store.propose("k", steps(("fake_read", {"text": "a"}), ("fake_read", {"text": "b"})))
    env.store.approve(p["id"])
    real = env.store._plan
    env.db.x("UPDATE goal_plans SET state=? WHERE id=?", (seen, p["id"]))
    old = dict(real(p["id"]))
    env.db.x("UPDATE goal_plans SET state=? WHERE id=?", (now, p["id"]))
    env.store._plan = lambda pid: dict(old)
    try:
        env.store.skip(p["id"], 1)
    finally:
        env.store._plan = real
    d = env.store.get(p["id"])
    assert d["state"] == kept and d["steps"][1]["status"] == "skipped"


@pytest.mark.parametrize("seen", ["ready", "running", "paused"])
def test_skip_on_a_stale_view_after_a_cancel_keeps_cancelled(env, seen):
    p = env.store.propose("c", steps(("fake_read", {"text": "a"})))
    env.store.approve(p["id"])
    real = env.store._plan
    env.db.x("UPDATE goal_plans SET state=? WHERE id=?", (seen, p["id"]))
    old = dict(real(p["id"]))
    env.store.cancel(p["id"])
    env.store._plan = lambda pid: dict(old)
    try:
        with pytest.raises(ValueError):
            env.store.skip(p["id"], 0)
    finally:
        env.store._plan = real
    assert env.store.get(p["id"])["state"] == "cancelled"
