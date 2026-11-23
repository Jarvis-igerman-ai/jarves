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


def test_1557_core_tick_scrubs_once_privacy_is_on(ctx, monkeypatch):
    from jarves.operations import Operations
    c = Continuity(ctx.home, clock=lambda: 1_800_000_000.0)
    c.store("phone", "command-1557", "chat", 200, {"ok": True, "answer": "private answer"})
    ops = SimpleNamespace(core=SimpleNamespace(home=ctx.home))

    monkeypatch.setattr(logs, "privacy", lambda: False)
    Operations._privacy_scrub(ops)                      # privacy off: the text is the owner's choice
    assert "private answer" in json.dumps(c.seen("phone", "command-1557"))

    monkeypatch.setattr(logs, "privacy", lambda: True)  # e.g. switched on in another process, or the window ended
    Operations._privacy_scrub(ops)
    body = c.seen("phone", "command-1557")
    assert "private answer" not in json.dumps(body) and body["code"] == "NOT_KEPT"
    assert ops._privacy_scrubbed is True


def test_1557_runner_reasons_are_all_allow_listed():
    """A new fixed reason in the runner must join FIXED_REASONS, or the scrub would blank it."""
    import inspect
    import re as _re
    from jarves import goal_plans
    src = inspect.getsource(goal_plans)
    found = set(_re.findall(r'self\._(?:block|fail)\(pid, step, "([^"]+)"', src))
    assert found and found <= goal_plans.FIXED_REASONS
    assert goal_plans.reason_at_rest("TOOL_ERROR: private") == "TOOL_ERROR"
    assert goal_plans.reason_at_rest("free text from an older version") == goal_plans.REASON_NOT_KEPT
