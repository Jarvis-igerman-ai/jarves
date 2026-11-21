"""#1526: privacy mode blanks plan text written before it (step evidence / error, plan result / note) in memory.db;
status and UPPER_CASE error codes stay. Real SQLite in tmp, the fake skills of test_v5_goal_plans, no real waiting."""
from __future__ import annotations

import sqlite3

import pytest
from jarves import goal_plans as gp
from test_v5_goal_plans import Env, steps


def _words_left(db) -> list:
    return db.rows("SELECT evidence, error FROM goal_plan_steps WHERE evidence<>'' OR error NOT IN ('', 'X_BROKE') "
                   "UNION ALL SELECT result, note FROM goal_plans WHERE result<>'' OR note<>''")


async def _plans_with_text(e, monkeypatch, logged):
    monkeypatch.setattr(gp.logs, "privacy", lambda: False)
    monkeypatch.setattr(gp.logs, "info", lambda c, code, m, **f: logged.append((code, f)))
    monkeypatch.setattr(gp.logs, "warn", lambda c, code, m, **f: logged.append((code, f)))
    monkeypatch.setattr(gp.logs, "error", lambda c, code, m, **f: logged.append((code, f)))
    for s in (steps(("fake_read", {"text": "secret"})), steps(("fake_bad", {}))):
        e.store.approve(e.store.propose("p", s)["id"])
    await e.runner.tick()
    assert len(_words_left(e.db)) == 4                       # done evidence + result; failed evidence/error + note


async def test_1526_privacy_switched_on_blanks_older_plan_text_and_keeps_codes(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    before = e.db.row("PRAGMA secure_delete")["secure_delete"]          # the build's own default (R3)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)    # the privacy switch turns on
    await e.runner.tick()
    assert _words_left(e.db) == []
    assert sorted(s["error"] for p in e.store.list() for s in e.store.steps(p["id"])) == ["", "X_BROKE"]
    assert {p["state"] for p in e.store.list()} == {"done", "failed"}          # status stays
    assert ("PLAN_PRIVACY_SCRUBBED", {"count": 4}) in logged
    assert e.db.row("PRAGMA secure_delete")["secure_delete"] == before       # restored after the scrub
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


async def test_1526_busy_checkpoint_is_retried_until_the_wal_file_is_truncated(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)
    wal = tmp_path / "memory.db-wal"
    reader = sqlite3.connect(tmp_path / "memory.db")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM goal_plans").fetchone()          # a reader holds an old WAL snapshot
    await e.runner.tick()
    assert _words_left(e.db) == [] and e.store.wal_pending and wal.stat().st_size > 0
    assert ("PLAN_PRIVACY_SCRUBBED", {"count": 4}) in logged                # the committed count is kept (R3 of #1534)
    assert e.db.row("PRAGMA busy_timeout")["timeout"] == 5000                # restored after the checkpoint
    reader.close()
    await e.runner.tick()
    assert e.store.wal_pending                               # inside the retry delay: nothing tried yet
    e.clock.t += gp.SCRUB_RETRY_S
    reader = sqlite3.connect(tmp_path / "memory.db")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM goal_plans").fetchone()          # busy again on the first retry
    await e.runner.tick()
    reader.close()
    assert e.store.wal_pending
    assert [c for c, _ in logged].count("PLAN_PRIVACY_CHECKPOINT_BUSY") == 1    # once per incident
    e.clock.t += gp.SCRUB_RETRY_S
    await e.runner.tick()                                    # only the checkpoint runs again, and now it finishes
    assert not e.store.wal_pending and wal.stat().st_size == 0
    assert b"read secret" not in (tmp_path / "memory.db").read_bytes()   # tool output; #1526 names exactly
    # goal_plan_steps.evidence/error and goal_plans.result/note; args/titles are the owner's approved plan
    assert "PLAN_PRIVACY_WAL_TRUNCATED" in [c for c, _ in logged]
    assert [c for c, _ in logged].count("PLAN_PRIVACY_SCRUBBED") == 1
    e.db.close()


async def test_1526_a_crashing_scrub_is_logged_and_backs_off_while_plans_still_run(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("bug")
    monkeypatch.setattr(e.store, "scrub_private", boom)
    e.store.approve(e.store.propose("later", steps(("fake_read", {"text": "x"})))["id"])
    assert await e.runner.tick() == 1                        # the plan still advanced
    assert ("PLAN_PRIVACY_SCRUB_CRASH", {"exc": True}) in logged
    await e.runner.tick()
    assert calls == [1]                                      # no retry on every tick
    e.clock.t += gp.SCRUB_RETRY_S
    await e.runner.tick()
    assert calls == [1, 1]
    e.db.close()


async def test_1526_a_held_write_lock_fails_the_scrub_without_waiting(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)
    seen = []
    e.db._db.set_trace_callback(seen.append)
    writer = sqlite3.connect(tmp_path / "memory.db")
    writer.execute("BEGIN IMMEDIATE")                        # another connection holds the write lock
    assert e.store.scrub_private() == -1
    writer.close()
    e.db._db.set_trace_callback(None)
    assert ("PLAN_PRIVACY_SCRUB_FAILED", {"error_type": "OperationalError"}) in logged
    assert seen.index("PRAGMA busy_timeout=0") < seen.index("BEGIN IMMEDIATE")   # no 5 s stall on the event loop
    assert e.db.row("PRAGMA busy_timeout")["timeout"] == 5000
    e.db.close()


async def test_1526_a_large_history_is_scrubbed_in_bounded_chunks_one_per_tick(tmp_path, monkeypatch):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)           # 4 rows with text (round 3 R2: no long loop stall)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)
    monkeypatch.setattr(gp, "SCRUB_CHUNK", 1)
    checkpoints = []
    monkeypatch.setattr(e.store, "checkpoint_wal", lambda: checkpoints.append(1) or True)
    left = []
    for _ in range(2):
        await e.runner.tick()                                # no retry delay between chunks
        left.append(len(_words_left(e.db)))
    assert left == [2, 0] and checkpoints == [] and e.store.scrub_more     # 1 step + 1 plan per transaction
    await e.runner.tick()                                    # the last chunk finds nothing more: now the checkpoint
    assert checkpoints == [1] and not e.store.scrub_more
    await e.runner.tick()
    assert checkpoints == [1]                                # done: nothing runs again
    e.db.close()


def test_1526_kept_codes_and_the_sql_clean_test_are_one_definition():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE goal_plan_steps (evidence TEXT, error TEXT)")
    for err in ("X_BROKE", "_", "1_2", "A__B", "BROKE", "x_broke", "X_" + "A" * 63, "X_BROKE: it broke", "",
                "MY_PIN_1234", "HTTP_404"):
        db.execute("DELETE FROM goal_plan_steps")
        db.execute("INSERT INTO goal_plan_steps VALUES ('', ?)", (err,))
        clean = not db.execute(f"SELECT 1 FROM goal_plan_steps WHERE {gp._DIRTY}").fetchone()
        assert clean == (gp._code_only(err) == err), err     # a row is left alone exactly when it is already a code
    assert gp._code_only("MY_PIN_1234: x") == "" and gp._code_only("X_BROKE: x") == "X_BROKE"   # digits never kept


class _CheckpointRaises:
    """The real connection, except that one checkpoint statement raises (another writer holds the lock)."""

    def __init__(self, con, bad):
        self.con, self.bad = con, bad

    def execute(self, sql, *a):
        if self.bad in sql:
            raise sqlite3.OperationalError("database is locked")
        return self.con.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self.con, name)


@pytest.mark.parametrize("bad", ["wal_checkpoint", "journal_mode"])     # round 3 R1: the mode read too
async def test_1526_a_raising_checkpoint_is_treated_as_busy_and_only_it_is_retried(tmp_path, monkeypatch, bad):
    e, logged = Env(tmp_path), []
    await _plans_with_text(e, monkeypatch, logged)
    monkeypatch.setattr(gp.logs, "privacy", lambda: True)
    real = e.db._db
    e.db._db = _CheckpointRaises(real, bad)
    await e.runner.tick()
    assert _words_left(e.db) == [] and e.store.wal_pending
    assert ("PLAN_PRIVACY_CHECKPOINT_BUSY", {"error_type": "OperationalError"}) in logged
    assert "PLAN_PRIVACY_SCRUB_CRASH" not in [c for c, _ in logged]
    assert e.db.row("PRAGMA busy_timeout")["timeout"] == 5000
    e.db._db = real
    e.clock.t += gp.SCRUB_RETRY_S
    await e.runner.tick()                                    # only the checkpoint again; the scrub is not repeated
    assert not e.store.wal_pending and (tmp_path / "memory.db-wal").stat().st_size == 0
    assert [c for c, _ in logged].count("PLAN_PRIVACY_SCRUBBED") == 1
    e.db.close()
