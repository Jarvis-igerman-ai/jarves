"""#1526: privacy mode also removes plan text that was written BEFORE privacy was switched on (step evidence and
error, plan result and note), keeping only states and codes. Fake skills, real SQLite files in tmp, no clock."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_v5_goal_plans import Env, Say, steps

from jarves import goal_plans as gp
from jarves import logs
from jarves.skills import Skill, Tool, ToolResult

WORDS = (b"read secret", b"it broke", b"steps done")


@pytest.fixture(autouse=True)
def _privacy_restored(monkeypatch):
    """logs.set_privacy only sets a module global (no flag file: conftest keeps logs._flag None); restore it."""
    monkeypatch.setattr(logs, "_privacy", logs._privacy)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


@pytest.fixture
def seen(monkeypatch):
    out = []
    for name in ("info", "warn", "error"):
        monkeypatch.setattr(gp.logs, name, lambda comp, code, msg, *a, **f: out.append((code, f)))
    return out


async def _two_plans_with_text(env):
    """With privacy off: one done plan (evidence + result) and one failed plan (error + note)."""
    logs.set_privacy(False)
    done = env.store.propose("a", steps(("fake_read", {"text": "secret"})))
    bad = env.store.propose("b", steps(("fake_bad", {})))
    env.store.approve(done["id"])
    env.store.approve(bad["id"])
    await env.runner.tick()
    d, b = env.store.get(done["id"]), env.store.get(bad["id"])
    assert d["steps"][0]["evidence"] == "read secret" and "steps done" in d["result"]
    assert b["steps"][0]["error"] == "X_BROKE: it broke" and "it broke" in b["note"]
    return done["id"], bad["id"]


def _disk(env) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(Path(env.db.path).parent.glob("memory.db*")))


async def test_1526_privacy_switched_on_blanks_older_plan_text_and_keeps_states_and_codes(env, seen):
    done, bad = await _two_plans_with_text(env)
    logs.set_privacy(True)
    await env.runner.tick()
    d, b = env.store.get(done), env.store.get(bad)
    assert (d["state"], d["steps"][0]["status"], d["steps"][0]["evidence"], d["result"], d["note"]) == ("done", "done", "", "", "")
    assert (b["state"], b["steps"][0]["status"], b["steps"][0]["error"], b["note"]) == ("failed", "failed", "X_BROKE", "")
    assert ("PLAN_PRIVACY_SCRUBBED", {"count": 2, "changed": 2}) in seen
    assert not any(w in _disk(env) for w in WORDS)          # not in memory.db, its free pages or memory.db-wal
    assert env.db.row("PRAGMA secure_delete")["secure_delete"] == 0      # the shared connection's setting is restored
    assert env.db.row("PRAGMA journal_mode")["journal_mode"] == "wal"    # no rollback journal holding old pages
    seen.clear()
    await env.runner.tick()                                  # once per switch-on: a second tick does nothing
    assert not [c for c, _ in seen if c.startswith("PLAN_PRIVACY")]


async def test_1526_core_start_with_privacy_on_scrubs_and_privacy_off_keeps_text(env, seen):
    done, _bad = await _two_plans_with_text(env)
    await gp.PlanRunner(env.store, env.reg, clock=env.clock).tick()     # a restart with privacy still off: text kept
    assert env.store.get(done)["steps"][0]["evidence"] == "read secret"
    logs.set_privacy(True)
    off = gp.PlanRunner(env.store, env.reg, clock=env.clock, features=type("F", (), {"active": lambda s, n: False})())
    assert await off.tick() == 0                             # Core start with privacy on, even with the feature off
    assert env.store.get(done)["steps"][0]["evidence"] == "" and env.store.get(done)["result"] == ""


async def test_1526_a_failed_scrub_rolls_back_logs_once_and_is_tried_again_after_the_delay(env, seen, monkeypatch):
    done, bad = await _two_plans_with_text(env)
    logs.set_privacy(True)
    real, calls = gp._code_only, []

    def flaky(text):                                         # fails on the 2nd row, after the 1st row was updated
        calls.append(text)
        if len(calls) % 2 == 0:
            raise sqlite3.OperationalError("database is locked")
        return real(text)
    monkeypatch.setattr(gp, "_code_only", flaky)
    await env.runner.tick()
    await env.runner.tick()                                  # within the delay: not tried again
    assert len(calls) == 2
    env.clock.t += gp.PRIVACY_RETRY_S
    await env.runner.tick()                                  # tried again, fails again: still logged once
    assert len(calls) == 4
    assert [c for c in seen if c[0] == "PLAN_PRIVACY_SCRUB_FAILED"] == [("PLAN_PRIVACY_SCRUB_FAILED", {"error_type": "OperationalError"})]
    assert env.store.get(done)["steps"][0]["evidence"] == "read secret"     # rolled back, nothing half-done
    assert env.store.get(bad)["note"]
    monkeypatch.setattr(gp, "_code_only", real)
    logs.set_privacy(False)
    await env.runner.tick()
    logs.set_privacy(True)                                   # a new switch-on clears the delay: scrubbed at once
    await env.runner.tick()
    assert env.store.get(done)["steps"][0]["evidence"] == "" and env.store.get(bad)["steps"][0]["error"] == "X_BROKE"


async def test_1526_a_busy_wal_checkpoint_is_logged_once_and_retried_after_the_delay_without_scrubbing_again(env, seen, monkeypatch):
    await _two_plans_with_text(env)
    logs.set_privacy(True)
    reader = sqlite3.connect(env.db.path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM goal_plans").fetchall()     # an open read snapshot keeps the WAL from truncating
    tries, real = [], env.store.truncate_wal
    monkeypatch.setattr(env.store, "truncate_wal", lambda: tries.append(1) or real())
    await env.runner.tick()
    await env.runner.tick()                                  # within the delay: no new connection, no retry
    assert len(tries) == 1
    env.clock.t += gp.PRIVACY_RETRY_S
    await env.runner.tick()                                  # retried, still busy: logged once
    assert len(tries) == 2 and [c for c, _ in seen].count("PLAN_PRIVACY_WAL_BUSY") == 1
    reader.close()
    monkeypatch.setattr(env.store, "scrub_private", lambda: pytest.fail("scrubbed twice"))
    env.clock.t += gp.PRIVACY_RETRY_S
    await env.runner.tick()
    assert not any(w in _disk(env) for w in WORDS)


def test_1526_secure_delete_fast_mode_is_restored_as_fast(env):
    env.db.x("PRAGMA secure_delete=FAST")
    env.store.scrub_private()
    assert env.db.row("PRAGMA secure_delete")["secure_delete"] == 2


async def test_1526_text_written_while_privacy_was_off_during_a_step_is_scrubbed_at_the_next_tick(env, seen):
    class Flip(Skill):
        name, description = "flip", "switches privacy off while it runs"

        def tools(self):
            async def flip(a: Say):
                logs.set_privacy(False)
                return ToolResult(True, f"out {a.text}")
            return [Tool("fake_flip", "f", flip, Say, permission="read")]
    env.reg.register(Flip())
    logs.set_privacy(True)
    await env.runner.tick()                                  # privacy on, nothing to scrub: clean
    p = env.store.propose("c", steps(("fake_flip", {"text": "secret"})))
    env.store.approve(p["id"])
    await env.runner.tick()                                  # the step writes its text with privacy off
    assert env.store.get(p["id"])["steps"][0]["evidence"] == "out secret"
    logs.set_privacy(True)                                   # on again before any tick start saw it off
    await env.runner.tick()
    assert env.store.get(p["id"])["steps"][0]["evidence"] == "" and env.store.get(p["id"])["result"] == ""


async def test_1526_plan_text_written_while_privacy_is_on_is_never_stored(env, seen):
    logs.set_privacy(True)
    done = env.store.propose("a", steps(("fake_read", {"text": "secret"})))
    bad = env.store.propose("b", steps(("fake_bad", {})))
    env.store.approve(done["id"])
    env.store.approve(bad["id"])
    await env.runner.tick()
    d, b = env.store.get(done["id"]), env.store.get(bad["id"])
    assert (d["state"], d["steps"][0]["status"], d["steps"][0]["evidence"]) == ("done", "done", "")
    assert d["result"] == "1/1 steps done. 1. fake_read: done"           # the plan's own counts and states stay
    assert (b["state"], b["steps"][0]["error"], b["steps"][0]["evidence"], b["note"]) == ("failed", "X_BROKE", "", "step 1: X_BROKE")
    assert not any(w in _disk(env) for w in (b"read secret", b"it broke"))


@pytest.mark.parametrize("text,code", [("X_BROKE: it broke", "X_BROKE"), ("TOOL_TIMEOUT", "TOOL_TIMEOUT"),
                                       ("the step timed out", ""), ("A_B1: 0521234567", ""), ("BUSY: x", "BUSY"),
                                       ("TIMEOUT", "TIMEOUT"), ("A: x", ""), ("HTTP_404: x", ""),
                                       ("X_" + "A" * 80 + ": x", ""), (None, "")])
def test_1526_only_a_leading_code_is_kept(text, code):
    assert gp._code_only(text) == code
