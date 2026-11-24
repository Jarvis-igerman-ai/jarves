"""#1557: enabling privacy must scrub text persisted before the switch."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from jarves import logs, privacy
from jarves.config import Config
from jarves.continuity import Continuity
from jarves.userdata import open_db


@pytest.fixture
def ctx(home, monkeypatch):
    # Keep the transition deterministic, including the flag timestamp and log polling.
    monkeypatch.setattr(logs.time, "time", lambda: 1_800_000_000.0)
    for key, value in (("_privacy", False), ("_runtime", None), ("_flag", None), ("_until", 0.0)):
        monkeypatch.setattr(logs, key, value)
    cfg = Config()
    cfg.log.privacy = False
    path = home / "config.toml"
    path.write_text("[log]\nprivacy = false\n", encoding="utf-8")
    return SimpleNamespace(home=home, cfg=cfg, config_path=path)


@pytest.mark.parametrize("kind", ["chat", "goal"])
def test_1557_enabling_privacy_scrubs_existing_ledger_text(ctx, kind):
    c = Continuity(ctx.home, clock=lambda: 1_800_000_000.0)
    secret = "private answer before privacy was enabled"
    body = {"ok": True, "answer": secret} if kind == "chat" else {
        "ok": True, "goal": {"id": 7, "title": secret}, "error": secret,
    }
    c.store("phone", "command-1557", kind, 200, body)
    assert secret in json.dumps(c.seen("phone", "command-1557"))

    assert privacy.set_privacy(ctx, True)["privacy"] is True

    # A separate connection checks persisted data, rather than a redacted read view.
    with sqlite3.connect(c.path) as db:
        rows = db.execute("SELECT cid, body FROM ledger").fetchall()
    assert rows and rows[0][0] == "command-1557"  # keep duplicate-command protection
    assert secret not in rows[0][1]


@pytest.mark.parametrize("column", ["evidence", "error"])
def test_1557_enabling_privacy_scrubs_existing_plan_step_text(ctx, column):
    secret = "private tool output before privacy was enabled"
    db = open_db(ctx.home)
    try:
        pid = db.x("INSERT INTO goal_plans (title, state, created, updated) VALUES ('plan', 'done', 1, 1)").lastrowid
        db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence, error) "
             "VALUES (?, 0, 'test_tool', 'done', ?, ?)",
             (pid, secret if column == "evidence" else "", secret if column == "error" else ""))
        assert db.row("SELECT * FROM goal_plan_steps")[column] == secret

        assert privacy.set_privacy(ctx, True)["privacy"] is True

        with sqlite3.connect(ctx.home / "memory.db") as reader:
            row = reader.execute("SELECT evidence, error, status FROM goal_plan_steps").fetchone()
        assert row is not None and row[2] == "done"
        assert secret not in json.dumps(row[:2])
    finally:
        db.close()


def test_1557_failed_step_keeps_code_and_plan_note_loses_tool_text(ctx):
    secret = "private tool output before privacy was enabled"
    db = open_db(ctx.home)
    try:
        pid = db.x("INSERT INTO goal_plans (title, state, note, created, updated) VALUES ('plan', 'failed', ?, 1, 1)",
                   (f"step 1: TOOL_ERROR: {secret}",)).lastrowid
        db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence, error) VALUES (?, 0, 't', 'failed', ?, ?)",
             (pid, secret, f"TOOL_ERROR: {secret}"))
        db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, error) VALUES (?, 1, 't', 'blocked', ?)",
             (pid, "the owner did not approve this step"))
    finally:
        db.close()

    assert privacy.scrub_stored_text(ctx.home) == {"ledger": 0, "plan_steps": 2}
    with sqlite3.connect(ctx.home / "memory.db") as reader:
        steps = reader.execute("SELECT evidence, error FROM goal_plan_steps ORDER BY idx").fetchall()
        note = reader.execute("SELECT note FROM goal_plans").fetchone()[0]
    assert steps == [("", "TOOL_ERROR"), ("", "the owner did not approve this step")]
    assert note == "step 1: TOOL_ERROR"
    assert privacy.scrub_stored_text(ctx.home) == {"ledger": 0, "plan_steps": 0}   # idempotent


def test_1557_ledger_scrub_is_idempotent_whatever_the_stored_spacing(ctx):
    """Review R5: rows are compared parsed, so a body stored with other spacing or key order is not rewritten again."""
    from jarves import mobile
    c = Continuity(ctx.home, clock=lambda: 1_800_000_000.0)
    c.store("phone", "c1", "chat", 200, {"ok": True, "answer": "private"})
    c.store("phone", "g1", "goal", 200, {"ok": True, "goal": {"id": 1, "title": "private", "steps": []}})
    assert privacy.scrub_stored_text(ctx.home) == {"ledger": 2, "plan_steps": 0}
    with sqlite3.connect(c.path) as db:                      # the same answers, written with other separators
        db.execute("UPDATE ledger SET body=? WHERE cid='c1'",
                   (json.dumps(dict(reversed(list(mobile.chat_at_rest({"ok": True}).items()))), indent=1),))
    assert privacy.scrub_stored_text(ctx.home) == {"ledger": 0, "plan_steps": 0}


def test_1557_a_blocked_checkpoint_is_finished_by_the_retry(ctx):
    """Review R1: the first pass commits, then a reader blocks the checkpoint; the retry (nothing left to rewrite)
    must still fold the old frames out of the -wal file before the scrub counts as done."""
    secret = b"Bialik-street-secret"
    db = open_db(ctx.home)                                   # the Core's own connection stays open throughout
    try:
        pid = db.x("INSERT INTO goal_plans (title, state, created, updated) VALUES ('plan', 'done', 1, 1)").lastrowid
        db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence) VALUES (?, 0, 't', 'done', ?)",
             (pid, secret.decode()))
        reader = sqlite3.connect(ctx.home / "memory.db", isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM goal_plan_steps").fetchone()
        try:
            assert privacy.scrub_stored_text(ctx.home, timeout=0.05)["plan_steps"] is None
        finally:
            reader.close()
        assert secret in (ctx.home / "memory.db-wal").read_bytes()     # committed, but the old frames remain
        assert privacy.scrub_stored_text(ctx.home) == {"ledger": 0, "plan_steps": 0}
        assert secret not in (ctx.home / "memory.db-wal").read_bytes() + (ctx.home / "memory.db").read_bytes()
    finally:
        db.close()


def test_1557_core_tick_scrubs_once_privacy_is_on(ctx, monkeypatch):
    from jarves.operations import Operations
    c = Continuity(ctx.home, clock=lambda: 1_800_000_000.0)
    c.store("phone", "command-1557", "chat", 200, {"ok": True, "answer": "private answer"})
    ops = SimpleNamespace(core=SimpleNamespace(home=ctx.home), _privacy_scrubbed=False, _privacy_scrub_logged=False)

    monkeypatch.setattr(logs, "privacy", lambda: False)
    Operations._privacy_scrub(ops)                      # privacy off: the text is the owner's choice
    assert "private answer" in json.dumps(c.seen("phone", "command-1557"))

    monkeypatch.setattr(logs, "privacy", lambda: True)  # e.g. switched on in another process, or the window ended
    Operations._privacy_scrub(ops)
    body = c.seen("phone", "command-1557")
    assert "private answer" not in json.dumps(body) and body["code"] == "NOT_KEPT"
    assert ops._privacy_scrubbed is True


def test_1557_runner_reasons_come_from_the_named_constants():
    """Review R4: the runner writes named constants and FIXED_REASONS is built from them (no source grep)."""
    from jarves import goal_plans
    named = {v for k, v in vars(goal_plans).items() if k.startswith("REASON_") and k != "REASON_NOT_KEPT"}
    assert named and named <= goal_plans.FIXED_REASONS
    for why in goal_plans.FIXED_REASONS:
        assert goal_plans.reason_at_rest(why) == why
    assert goal_plans.reason_at_rest("TOOL_ERROR: private") == "TOOL_ERROR"
    assert goal_plans.reason_at_rest("free text from an older version") == goal_plans.REASON_NOT_KEPT


def test_1557_a_null_or_odd_ledger_body_is_scrubbed_not_raised(ctx):
    """Review R1: a NULL body (TypeError in json.loads) or an odd goal shape must not stop the switch."""
    c = Continuity(ctx.home, clock=lambda: 1_800_000_000.0)
    c.store("phone", "ok-1", "chat", 200, {"ok": True, "answer": "private answer"})
    with sqlite3.connect(c.path) as db:
        cols = [r[1] for r in db.execute("PRAGMA table_info(ledger)")]
        row = dict(zip(cols, db.execute("SELECT * FROM ledger").fetchone()))
        db.execute("DROP TABLE ledger")                       # an older schema that allowed NULL bodies
        db.execute(f"CREATE TABLE ledger ({', '.join(cols)})")
        db.execute(f"INSERT INTO ledger VALUES ({', '.join('?' * len(cols))})", tuple(row.values()))
        db.execute("INSERT INTO ledger (device, cid, kind, body) VALUES ('phone', 'null-1', 'chat', NULL)")
        db.execute("INSERT INTO ledger (device, cid, kind, body) VALUES ('phone', 'odd-1', 'goal', ?)",
                   (json.dumps({"goal": {"steps": 5}, "title": "private answer"}),))
    assert privacy.set_privacy(ctx, True)["privacy"] is True
    with sqlite3.connect(c.path) as db:
        bodies = dict(db.execute("SELECT cid, body FROM ledger").fetchall())
    assert set(bodies) == {"ok-1", "null-1", "odd-1"} and "private answer" not in json.dumps(bodies)


def test_1557_no_old_words_stay_in_the_database_files(ctx):
    """Review R3: memory.db runs in WAL mode; the old text must be gone from the main file and the -wal file."""
    secret = "Bialik-street-secret"
    c = Continuity(ctx.home, clock=lambda: 1_800_000_000.0)
    c.store("phone", "command-1557", "chat", 200, {"ok": True, "answer": secret})
    db = open_db(ctx.home)
    try:
        pid = db.x("INSERT INTO goal_plans (title, state, created, updated) VALUES ('plan', 'done', 1, 1)").lastrowid
        db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence) VALUES (?, 0, 't', 'done', ?)",
             (pid, secret))
        wal = ctx.home / "memory.db-wal"
        assert wal.exists() and secret.encode() in wal.read_bytes() + (ctx.home / "memory.db").read_bytes()
        assert privacy.set_privacy(ctx, True)["privacy"] is True
        for f in ("memory.db", "memory.db-wal", c.path.name, c.path.name + "-wal"):    # the Core still has it open
            p = ctx.home / f
            assert not p.exists() or secret.encode() not in p.read_bytes(), f
    finally:
        db.close()


def test_1557_core_tick_scrub_runs_off_the_loop_and_a_crash_is_logged_once(ctx, monkeypatch):
    """Review R1/R2: the tick runs the scrub in a worker thread; an unexpected error is logged, not raised."""
    import asyncio
    import threading
    from jarves.operations import Operations
    loop_thread, seen, errors = threading.get_ident(), [], []
    monkeypatch.setattr(logs, "privacy", lambda: True)
    monkeypatch.setattr(logs, "error", lambda *a, **k: errors.append(a[1]))

    def boom(home, **k):
        seen.append(threading.get_ident())
        raise RuntimeError("unexpected")
    monkeypatch.setattr(privacy, "scrub_stored_text", boom)

    async def flush():
        return None
    ops = Operations.__new__(Operations)
    ops.core = SimpleNamespace(home=ctx.home)
    ops.alerts = SimpleNamespace(flush=flush)
    for name in ("_signals_tick", "_connect_nudge"):
        monkeypatch.setattr(Operations, name, lambda self: flush())
    ops.cheer = SimpleNamespace(check=flush)
    ops._synced = float("inf")
    ops._privacy_scrubbed = ops._privacy_scrub_logged = False
    asyncio.run(ops._after_tick())
    asyncio.run(ops._after_tick())
    assert seen and all(t != loop_thread for t in seen) and len(seen) == 2
    assert errors == ["PRIVACY_SCRUB_FAILED"] and ops._privacy_scrubbed is False
    monkeypatch.setattr(privacy, "scrub_stored_text", lambda home, **k: {"ledger": 0, "plan_steps": 0})
    asyncio.run(ops._after_tick())                         # review R3: a success re-arms the log
    assert ops._privacy_scrubbed is True and ops._privacy_scrub_logged is False
    ops._privacy_scrubbed = False
    monkeypatch.setattr(privacy, "scrub_stored_text", boom)
    asyncio.run(ops._after_tick())
    assert errors == ["PRIVACY_SCRUB_FAILED", "PRIVACY_SCRUB_FAILED"]


def test_1557_the_switch_waits_briefly_and_leaves_a_busy_database_to_the_tick(ctx, monkeypatch):
    """Review R2: the dashboard / CLI switch uses a short busy timeout; the Core's off-loop tick retries."""
    seen = []
    monkeypatch.setattr(privacy, "scrub_stored_text", lambda home, timeout=99: seen.append(timeout) or {})
    privacy.set_privacy(ctx, True)
    privacy.set_full_text(ctx, False)
    assert seen and all(t == privacy.HANDLER_TIMEOUT_S < privacy.TICK_TIMEOUT_S for t in seen)


def test_1557_a_blocked_scrub_warns_once_per_failure_run_not_every_tick(ctx, monkeypatch):
    """Review R1 (round 3): the Core retries every tick; PRIVACY_SCRUB_PARTIAL must stay bounded."""
    from jarves.operations import Operations
    warns = []
    monkeypatch.setattr(logs, "privacy", lambda: True)
    monkeypatch.setattr(logs, "warn", lambda *a, **k: warns.append(a[1]))
    open_db(ctx.home).close()

    def busy(cx):
        raise sqlite3.OperationalError("checkpoint busy")
    monkeypatch.setattr(privacy, "_checkpoint", busy)
    ops = SimpleNamespace(core=SimpleNamespace(home=ctx.home), _privacy_scrubbed=False, _privacy_scrub_logged=False)
    for _ in range(4):
        Operations._privacy_scrub(ops)
    assert warns == ["PRIVACY_SCRUB_PARTIAL"] and ops._privacy_scrubbed is False
    monkeypatch.setattr(privacy, "_checkpoint", lambda cx: None)
    Operations._privacy_scrub(ops)                          # done: a later failure run warns again
    assert ops._privacy_scrubbed is True and ops._privacy_scrub_logged is False


@pytest.mark.parametrize("switch", ["privacy", "full_text"])
def test_1557_an_unexpected_scrub_error_does_not_fail_the_switch(ctx, monkeypatch, switch):
    """Review R2 (round 3): the switch is saved first; any scrub error is logged and left to the Core's tick."""
    errors = []
    monkeypatch.setattr(logs, "error", lambda *a, **k: errors.append(a[1]))

    def odd(home, timeout):
        raise AttributeError("odd stored shape")
    monkeypatch.setattr(privacy, "_scrub_ledger", odd)
    res = privacy.set_privacy(ctx, True) if switch == "privacy" else privacy.set_full_text(ctx, False)
    assert res["privacy"] is True and logs.privacy() is True
    assert errors == ["PRIVACY_SCRUB_FAILED"]


def test_1557_an_off_on_switch_between_two_ticks_scrubs_again(ctx, monkeypatch):
    """Review R3 (round 3): the tick re-arms on the switch count, not only on a privacy-off sample."""
    from jarves.operations import Operations
    calls, count = [], [0]
    monkeypatch.setattr(logs, "privacy", lambda: True)
    monkeypatch.setattr(logs, "privacy_switches", lambda: count[0])
    monkeypatch.setattr(privacy, "scrub_stored_text", lambda home, **k: calls.append(1) or {"ledger": 0, "plan_steps": 0})
    ops = SimpleNamespace(core=SimpleNamespace(home=ctx.home), _privacy_scrubbed=False, _privacy_scrub_logged=False)
    Operations._privacy_scrub(ops)
    Operations._privacy_scrub(ops)
    assert len(calls) == 1                                   # nothing changed: no second pass
    count[0] += 1                                            # off, then on again, both between two ticks
    Operations._privacy_scrub(ops)
    assert len(calls) == 2


def test_1557_a_flag_file_written_by_another_process_counts_as_a_switch(ctx, monkeypatch):
    """Review R3 (round 3): another process's write to privacy.json (seen once, already back on) bumps the count."""
    import os
    flag = ctx.home / "logs" / logs.PRIVACY_FILE
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(json.dumps({"privacy": True, "at": 1}), encoding="utf-8")
    monkeypatch.setattr(logs, "_flag", flag)
    monkeypatch.setattr(logs, "_flag_mtime", flag.stat().st_mtime)
    monkeypatch.setattr(logs, "_switches", 0)
    monkeypatch.setattr(logs, "_flag_checked", float("-inf"))
    before = logs.privacy_switches()
    flag.write_text(json.dumps({"privacy": True, "at": 2}), encoding="utf-8")   # off -> on happened in between
    os.utime(flag, (flag.stat().st_mtime + 5,) * 2)
    monkeypatch.setattr(logs, "_flag_checked", float("-inf"))
    assert logs.privacy_switches() == before + 1


def test_1557_runner_step_reasons_are_never_free_literals():
    """Review R4 (round 3): every _block/_fail reason in goal_plans.py is a named constant (or a failure code), so
    reason_at_rest keeps it instead of reducing it to "details not kept"."""
    import ast
    import inspect
    from jarves import goal_plans
    calls = [n for n in ast.walk(ast.parse(inspect.getsource(goal_plans)))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("_block", "_fail")]
    assert len(calls) >= 10
    for c in calls:
        literals = [x.value for x in ast.walk(c.args[2]) if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        assert set(literals) <= {": "}, (c.lineno, literals)       # ": " only joins a code to its (privacy-safe) text
