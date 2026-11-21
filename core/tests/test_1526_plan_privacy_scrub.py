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
                if sql.startswith("UPDATE goal_plans"):
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
        e.db._db = real                                          # the lock is gone: the next tick scrubs
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
