"""#1526: plan text written before privacy mode was switched on is scrubbed when privacy turns on (or at Core start
with privacy on). Step evidence / error and the plan's result / note lose their words; status and codes stay."""
from __future__ import annotations

from jarves import goal_plans as gp
from jarves import logs
from test_v5_goal_plans import Env, steps


def _texts(db) -> list[str]:
    rows = db.rows("SELECT evidence, error FROM goal_plan_steps") + db.rows("SELECT result, note FROM goal_plans")
    return [v for r in rows for v in r.values() if v]


async def test_1526_privacy_on_scrubs_older_plan_step_text_and_keeps_status_and_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        done = e.store.propose("ok", steps(("fake_read", {"text": "secret words"})))
        bad = e.store.propose("bad", steps(("fake_bad", {})))
        for p in (done, bad):
            e.store.approve(p["id"])
        await e.runner.tick()
        assert any("secret words" in t for t in _texts(e.db)) and any("it broke" in t for t in _texts(e.db))

        logs.set_privacy(True)                                   # the owner switches privacy on
        await e.runner.tick()
        assert _texts(e.db) == ["X_BROKE"]                       # only the step's error code is left
        d, b = e.store.get(done["id"]), e.store.get(bad["id"])
        assert d["state"] == "done" and d["steps"][0]["status"] == "done"
        assert b["state"] == "failed" and b["steps"][0]["status"] == "failed"
    finally:
        e.db.close()


async def test_1526_a_new_runner_with_privacy_on_scrubs_at_start_and_privacy_off_keeps_text(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        p = e.store.propose("ok", steps(("fake_read", {"text": "secret words"})))
        e.store.approve(p["id"])
        await e.runner.tick()
        await gp.PlanRunner(e.store, e.reg, budget=e.budget, clock=e.clock).tick()   # restart, privacy still off
        assert any("secret words" in t for t in _texts(e.db))

        infos = []
        monkeypatch.setattr(logs, "info", lambda _c, code, *_a, **k: infos.append((code, k)))
        monkeypatch.setattr(logs, "_privacy", True)               # the Core starts with privacy on
        await gp.PlanRunner(e.store, e.reg, budget=e.budget, clock=e.clock).tick()
        assert _texts(e.db) == []
        assert ("PLAN_PRIVACY_SCRUBBED", {"count": 2}) in infos  # one step row and one plan row, no text in the log
        assert e.store.scrub_private() == 0                       # nothing left: idempotent, no rewrite
    finally:
        e.db.close()


async def test_1526_scrub_runs_even_when_the_goal_plans_feature_is_off(tmp_path, monkeypatch):
    class Off:
        def active(self, _name):
            return False

    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        p = e.store.propose("ok", steps(("fake_read", {"text": "secret words"})))
        e.store.approve(p["id"])
        await e.runner.tick()
        monkeypatch.setattr(logs, "_privacy", True)
        assert await gp.PlanRunner(e.store, e.reg, budget=e.budget, clock=e.clock, features=Off()).tick() == 0
        assert _texts(e.db) == []
    finally:
        e.db.close()


async def test_1526_a_failed_scrub_rolls_back_logs_a_code_and_is_tried_again(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        p = e.store.propose("ok", steps(("fake_read", {"text": "secret words"})))
        e.store.approve(p["id"])
        await e.runner.tick()
        real, warned = e.db._db, []

        class Locked:                                            # another process holds the write lock
            def __getattr__(self, name):
                return getattr(real, name)

            def execute(self, sql, *a):
                if sql.startswith("UPDATE goal_plan_steps"):
                    raise sqlite3.OperationalError("database is locked")
                return real.execute(sql, *a)

        monkeypatch.setattr(logs, "warn", lambda _c, code, *_a, **_k: warned.append(code))
        monkeypatch.setattr(logs, "_privacy", True)
        e.db._db = Locked()
        assert e.store.scrub_private() == -1 and warned == ["PLAN_PRIVACY_SCRUB_FAILED"]
        assert not real.in_transaction and any("secret words" in t for t in _texts(e.db))   # rolled back, not half done
        runner = gp.PlanRunner(e.store, e.reg, budget=e.budget, clock=e.clock)
        await runner.tick()
        assert any("secret words" in t for t in _texts(e.db))
        e.db._db = real                                          # the lock is gone, but the runner backs off
        e.clock.t += gp.SCRUB_RETRY_S - 1
        await runner.tick()
        assert any("secret words" in t for t in _texts(e.db))
        e.clock.t += 1                                           # 30 s later: the next tick scrubs
        await runner.tick()
        assert _texts(e.db) == []
    finally:
        e.db._db = real
        e.db.close()


async def _failed_plan(e):
    p = e.store.propose("bad", steps(("fake_bad", {})))
    e.store.approve(p["id"])
    await e.runner.tick()
    assert any("it broke" in t for t in _texts(e.db))


async def test_1526_r1_r2_no_old_words_in_the_db_or_wal_file_and_secure_delete_is_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        await _failed_plan(e)
        assert e.db.row("PRAGMA journal_mode")["journal_mode"] == "wal"   # the checkpoint branch really runs
        assert b"it broke" in (tmp_path / "memory.db-wal").read_bytes()   # and the words were in the -wal file before
        e.db.x("PRAGMA secure_delete=OFF")                       # some SQLite builds default to ON: start from OFF
        monkeypatch.setattr(logs, "_privacy", True)
        assert e.store.scrub_private() == 2
        for f in (tmp_path / "memory.db", tmp_path / "memory.db-wal"):
            assert not f.exists() or b"it broke" not in f.read_bytes(), f.name
        assert e.db.row("PRAGMA secure_delete")["secure_delete"] == 0   # restored, not left on for the connection
    finally:
        e.db.close()


async def test_1526_r3_r4_never_commits_a_foreign_transaction_and_any_error_rolls_back(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        await _failed_plan(e)
        monkeypatch.setattr(logs, "_privacy", True)
        e.db._db.execute("INSERT INTO agent_settings (key, value) VALUES ('pending', '1')")   # someone else's write
        assert e.db._db.in_transaction and e.store.scrub_private() == -1
        assert e.db._db.in_transaction                           # left open for its owner: not committed by the scrub
        e.db._db.rollback()
        monkeypatch.setattr(gp, "_code_only", lambda _x: {}["boom"])
        with pytest.raises(KeyError):
            e.store.scrub_private()
        assert not e.db._db.in_transaction and any("it broke" in t for t in _texts(e.db))
    finally:
        e.db.close()


async def test_1526_r6_r7_scrub_runs_off_the_event_loop_and_a_bare_upper_case_word_is_not_kept(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        await _failed_plan(e)
        e.db.x("UPDATE goal_plan_steps SET error='PROJECTX: secret plan'")
        threaded, real = [], asyncio.to_thread
        monkeypatch.setattr(asyncio, "to_thread", lambda fn, *a: threaded.append(fn.__name__) or real(fn, *a))
        monkeypatch.setattr(logs, "_privacy", True)
        await gp.PlanRunner(e.store, e.reg, budget=e.budget, clock=e.clock).tick()
        assert threaded == [] and _texts(e.db) == []             # a small table: inline, no thread start-up delay
        e.db.x("UPDATE goal_plans SET note='old note'")
        monkeypatch.setattr(gp, "SCRUB_THREAD_ROWS", 0)          # a large table
        await gp.PlanRunner(e.store, e.reg, budget=e.budget, clock=e.clock).tick()
        assert threaded == ["scrub_private"] and _texts(e.db) == []
        assert gp._code_only("NOT_APPROVED: no") == "NOT_APPROVED" and gp._code_only("DENIED: x") == ""
    finally:
        e.db.close()


async def test_1526_round2_r3_the_core_run_loop_scrubs_with_the_feature_off(tmp_path, monkeypatch):
    class Off:
        def active(self, _name):
            return False

    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        await _failed_plan(e)
        monkeypatch.setattr(logs, "_privacy", True)

        async def once(_s):
            runner.stop()                                        # one loop pass, no real waiting
        runner = gp.PlanRunner(e.store, e.reg, budget=e.budget, clock=e.clock, features=Off(), sleep=once)
        await runner.run()                                       # what Operations.start() runs, feature on or off
        assert _texts(e.db) == ["X_BROKE"]
    finally:
        e.db.close()


async def test_1526_round2_r4_a_failed_secure_delete_restore_never_masks_the_original_error(tmp_path):
    import sqlite3

    import pytest

    e = Env(tmp_path)
    real = e.db._db

    class BadRestore:
        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, sql, *a):
            if sql.startswith("PRAGMA secure_delete=") and sql != "PRAGMA secure_delete=ON":
                raise sqlite3.OperationalError("disk I/O error")
            return real.execute(sql, *a)
    try:
        e.db._db = BadRestore()
        with pytest.raises(KeyError) as err:
            with e.db.immediate(secure=True):
                raise KeyError("body failed")
        assert "restoring secure_delete also failed: OperationalError" in err.value.__notes__
        with pytest.raises(sqlite3.OperationalError):            # no earlier error: the restore failure is raised
            with e.db.immediate(secure=True):
                pass
        assert not real.in_transaction
    finally:
        e.db._db = real
        e.db.close()


async def test_1526_round3_r1_a_busy_checkpoint_logs_its_own_code_once_and_backs_off(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    real, warned, calls = e.db._db, [], []
    try:
        await _failed_plan(e)

        class Busy:                                              # another connection holds a long read
            def __getattr__(self, name):
                return getattr(real, name)

            def execute(self, sql, *a):
                return real.execute("SELECT 1, 0, 0" if sql == "PRAGMA wal_checkpoint(TRUNCATE)" else sql, *a)

        monkeypatch.setattr(logs, "warn", lambda _c, code, *_a, **_k: warned.append(code))
        monkeypatch.setattr(logs, "_privacy", True)
        e.db._db = Busy()
        assert e.store.scrub_private() == -1 and _texts(e.db) == ["X_BROKE"]   # the commit landed
        assert e.store.scrub_private() == -1 and warned == ["PLAN_PRIVACY_CHECKPOINT_BUSY"]   # logged once
        real_scrub = e.store.scrub_private
        monkeypatch.setattr(e.store, "scrub_private", lambda: calls.append(e.clock()) or real_scrub())
        runner = gp.PlanRunner(e.store, e.reg, budget=e.budget, clock=e.clock)
        t0 = e.clock.t
        for dt in (0, 1, 29, 30, 31, 89, 90):                    # retries at +0, +30, +90 (30 s, then 60 s)
            e.clock.t = t0 + dt
            await runner.tick()
        assert calls == [t0, t0 + 30, t0 + 90] and warned == ["PLAN_PRIVACY_CHECKPOINT_BUSY"]
        e.db._db = real                                          # the reader is gone
        e.clock.t = t0 + 90 + 120
        await runner.tick()
        assert runner._private and e.store._busy_logged is False  # a later busy incident is logged again
    finally:
        e.db._db = real
        e.db.close()


async def test_1526_round3_r2_scrub_goes_in_batches_and_stops_when_privacy_goes_off(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", False)
    e = Env(tmp_path)
    try:
        for _ in range(3):
            await _failed_plan(e)
        monkeypatch.setattr(gp, "SCRUB_BATCH", 1)
        real, entered = e.db.immediate, []

        def batch(**k):                                          # the owner turns privacy off after the first batch
            entered.append(k)
            monkeypatch.setattr(logs, "_privacy", len(entered) < 2)
            return real(**k)
        monkeypatch.setattr(e.db, "immediate", batch)
        monkeypatch.setattr(logs, "_privacy", True)
        assert e.store.scrub_private() == gp.SCRUB_STOPPED
        assert len(entered) == 2 and all(k == {"secure": True, "truncate_wal": False} for k in entered)
        left = [r["id"] for r in e.db.rows("SELECT id FROM goal_plan_steps WHERE evidence<>''")]
        assert len(left) == 1 and e.db.rows("SELECT note FROM goal_plans WHERE note<>''")   # 2 batches done, rest kept
        entered.clear()
        monkeypatch.setattr(e.db, "immediate", lambda **k: entered.append(k) or real(**k))
        monkeypatch.setattr(logs, "_privacy", True)
        assert e.store.scrub_private() == 4                      # 1 step row left + 3 plan rows
        assert entered[-1] == {"secure": True} and len(entered) == 5   # 4 step batches, then plans + -wal truncate
        assert _texts(e.db) == ["X_BROKE"] * 3
    finally:
        e.db.close()
