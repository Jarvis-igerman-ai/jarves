"""#1526: privacy mode blanks plan text written before it (step evidence / error, plan result / note) in memory.db;
status and UPPER_CASE error codes stay. Real SQLite in tmp, the fake skills of test_v5_goal_plans, no real waiting."""
from __future__ import annotations

import sqlite3

from jarves import goal_plans as gp
from test_v5_goal_plans import Env, steps


def _words_left(db) -> list:
    return db.rows("SELECT evidence, error FROM goal_plan_steps WHERE evidence<>'' OR error NOT IN ('', 'X_BROKE') "
                   "UNION ALL SELECT result, note FROM goal_plans WHERE result<>'' OR note<>''")


async def _plans_with_text(e, monkeypatch, logged):
    monkeypatch.setattr(gp.logs, "privacy", lambda: False)
    monkeypatch.setattr(gp.logs, "info", lambda c, code, m, **f: logged.append((code, f)))
    monkeypatch.setattr(gp.logs, "warn", lambda c, code, m, **f: logged.append((code, f)))
    for s in (steps(("fake_read", {"text": "secret"})), steps(("fake_bad", {}))):
        e.store.approve(e.store.propose("p", s)["id"])
    await e.runner.tick()
    assert len(_words_left(e.db)) == 4                       # done evidence + result; failed evidence/error + note


async def test_1526_privacy_switched_on_blanks_older_plan_text_and_keeps_codes(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)    # the privacy switch turns on
    await e.runner.tick()
    assert _words_left(e.db) == []
    assert sorted(s["error"] for p in e.store.list() for s in e.store.steps(p["id"])) == ["", "X_BROKE"]
    assert {p["state"] for p in e.store.list()} == {"done", "failed"}          # status stays
    assert ("PLAN_PRIVACY_SCRUBBED", {"count": 4}) in logged
    assert e.db.row("PRAGMA secure_delete")["secure_delete"] == 0            # restored after the scrub
    logged.clear()
    await e.runner.tick()
    assert e.store.scrub_private() == 0 and logged == []    # idempotent: clean rows (a kept code too) are not rewritten
    e.db.close()


async def test_1526_core_start_with_privacy_on_scrubs_and_off_keeps_text(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    await gp.PlanRunner(e.store, e.reg, clock=e.clock).tick()           # restart, privacy still off: text stays
    assert len(_words_left(e.db)) == 4
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)
    await gp.PlanRunner(e.store, e.reg, clock=e.clock, features=_Off()).tick()   # restart with privacy on, feature off
    assert _words_left(e.db) == []
    e.db.close()


class _Off:
    def active(self, _name):
        return False


async def test_1526_failed_scrub_rolls_back_logs_and_is_retried_after_the_delay(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)
    e.db.x("CREATE TRIGGER boom BEFORE UPDATE ON goal_plans BEGIN SELECT RAISE(ABORT, 'disk'); END")
    await e.runner.tick()
    assert len(_words_left(e.db)) == 4                       # the step updates were rolled back with the plan update
    assert ("PLAN_PRIVACY_SCRUB_FAILED", {"error_type": "IntegrityError"}) in logged
    e.db.x("DROP TRIGGER boom")
    await e.runner.tick()
    assert len(_words_left(e.db)) == 4                       # still inside the retry delay
    e.clock.t += gp.SCRUB_RETRY_S
    await e.runner.tick()
    assert _words_left(e.db) == []
    e.db.close()


async def test_1526_busy_checkpoint_keeps_the_committed_count_and_is_logged(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)
    reader = sqlite3.connect(tmp_path / "memory.db")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM goal_plans").fetchone()          # a reader holds an old WAL snapshot
    assert e.store.scrub_private() == 4 and _words_left(e.db) == []
    assert ("PLAN_PRIVACY_SCRUBBED", {"count": 4}) in logged
    assert [c for c, _ in logged].count("PLAN_PRIVACY_CHECKPOINT_BUSY") == 1
    assert e.db.row("PRAGMA busy_timeout")["timeout"] == 5000                # restored after the checkpoint
    reader.close()
    e.db.close()
