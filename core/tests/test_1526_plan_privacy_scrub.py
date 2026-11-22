"""#1526: privacy mode also removes plan text that was written BEFORE privacy was switched on (step evidence and
error, plan result and note), keeping only states and codes. Fake skills, real SQLite files in tmp, no clock."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_v5_goal_plans import Env, steps

from jarves import goal_plans as gp
from jarves import logs

WORDS = (b"read secret", b"it broke", b"steps done")


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


async def test_1526_a_failed_scrub_rolls_back_logs_once_and_is_tried_again_next_tick(env, seen, monkeypatch):
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
    await env.runner.tick()
    assert [c for c in seen if c[0] == "PLAN_PRIVACY_SCRUB_FAILED"] == [("PLAN_PRIVACY_SCRUB_FAILED", {"error_type": "OperationalError"})]
    assert env.store.get(done)["steps"][0]["evidence"] == "read secret"     # rolled back, nothing half-done
    assert env.store.get(bad)["note"]
    monkeypatch.setattr(gp, "_code_only", real)
    await env.runner.tick()
    assert env.store.get(done)["steps"][0]["evidence"] == "" and env.store.get(bad)["steps"][0]["error"] == "X_BROKE"


async def test_1526_a_busy_wal_checkpoint_is_logged_once_and_retried_without_scrubbing_again(env, seen, monkeypatch):
    await _two_plans_with_text(env)
    logs.set_privacy(True)
    reader = sqlite3.connect(env.db.path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM goal_plans").fetchall()     # an open read snapshot keeps the WAL from truncating
    await env.runner.tick()
    await env.runner.tick()
    assert [c for c, _ in seen].count("PLAN_PRIVACY_WAL_BUSY") == 1
    reader.close()
    monkeypatch.setattr(env.store, "scrub_private", lambda: pytest.fail("scrubbed twice"))
    await env.runner.tick()
    assert not any(w in _disk(env) for w in WORDS)


@pytest.mark.parametrize("text,code", [("X_BROKE: it broke", "X_BROKE"), ("TOOL_TIMEOUT", "TOOL_TIMEOUT"),
                                       ("the step timed out", ""), ("A_B1: 0521234567", ""), ("NOCODE: x", ""),
                                       ("X_" + "A" * 80 + ": x", ""), (None, "")])
def test_1526_only_a_leading_code_is_kept(text, code):
    assert gp._code_only(text) == code
