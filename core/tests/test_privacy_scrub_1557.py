"""#1557: switching privacy mode on scrubs text stored BEFORE it was on (continuity ledger answers, goal plan step
evidence and error text), not only new writes. Status, codes and the resend ledger rows themselves stay."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from jarves import logs, privacy
from jarves.config import Config
from jarves.continuity import Continuity
from jarves.userdata import UserDB

SECRET = "the answer mentions my sister Dana"


@pytest.fixture
def ctx(home):
    (home / "config.toml").write_text("[log]\nprivacy = false\n", encoding="utf-8")
    cfg = Config()
    cfg.log.privacy = False
    logs.setup(home / "logs", "INFO", console=False, privacy=False)
    yield SimpleNamespace(home=home, cfg=cfg, config_path=home / "config.toml")
    logs.setup(home / "logs", "INFO", console=False, privacy=True)


def _seed(home):
    Continuity(home).store("phone1", "c1", "chat", 200, {"reply": SECRET, "ok": True})
    db = UserDB(home / "memory.db")
    db.x("INSERT INTO goal_plans (title, state, created, updated) VALUES ('t', 'failed', 0, 0)")
    db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence, error) VALUES "
         "(1, 0, 'web_search', 'failed', ?, ?)", (SECRET, "TOOL_ERROR: " + SECRET))
    for err in ("Dana's phone: unreachable", "no colon, Dana did not answer"):      # free-text prefix / no code
        db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, error) VALUES (1, 1, 'call', 'failed', ?)",
             (err,))
    return db


def _dump(home, ledger=True) -> str:
    out = []
    for name, sql in ((("continuity.db", "SELECT status, body FROM ledger"),) if ledger else ()) + (
                      ("memory.db", "SELECT status, evidence, error FROM goal_plan_steps"),):
        cx = sqlite3.connect(home / name)
        try:
            out += [json.dumps(list(r)) for r in cx.execute(sql)]
        finally:
            cx.close()
    return "\n".join(out)


def test_switching_privacy_on_scrubs_older_text(ctx, home):
    db = _seed(home)                                          # kept open, like the running Core: the -wal file stays
    assert "Dana" in _dump(home)
    got = privacy.set_privacy(ctx, True)
    assert got["scrub"] == {"ledger": 1, "plan_steps": 3, "partial": []} and got["saved_in_config"]
    dump = _dump(home)
    assert "Dana" not in dump
    assert "TOOL_ERROR" in dump and "failed" in dump          # the code and status stay
    assert "Dana" not in (home / "memory.db-wal").read_bytes().decode("utf-8", "replace")   # no old page copy
    assert db.row("SELECT error FROM goal_plan_steps WHERE tool='call' AND error != ''") is None
    seen = Continuity(home).seen("phone1", "c1")              # a resend is still answered, not run again
    assert seen and seen.get("not_kept") is True and seen.get("ok") is True


def test_ending_full_text_scrubs_too(ctx, home):
    _seed(home)
    privacy.set_full_text(ctx, False)
    assert "Dana" not in _dump(home)


def test_switching_privacy_off_keeps_text(ctx, home):
    _seed(home)
    privacy.set_privacy(ctx, False)
    assert "Dana" in _dump(home)


def test_scrub_without_databases_is_harmless(home):
    assert privacy.scrub_at_rest(home) == {"ledger": 0, "plan_steps": 0, "partial": []}
    assert not (home / "continuity.db").exists() and not (home / "memory.db").exists()


def test_unreadable_database_is_logged_and_the_other_is_still_scrubbed(home, monkeypatch):
    _seed(home)
    (home / "continuity.db").write_bytes(b"not a database" * 100)
    seen = []
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append((a[1], k)))
    out = privacy.scrub_at_rest(home)
    assert out == {"ledger": 0, "plan_steps": 3, "partial": ["continuity.db"]}
    assert "Dana" not in _dump(home, ledger=False)
    assert seen == [("PRIVACY_SCRUB_PARTIAL", {"target": "continuity.db", "error_type": "DatabaseError"})]
    assert privacy.scrub_at_rest(home)["plan_steps"] == 0          # already scrubbed: nothing rewritten again


def test_malformed_ledger_rows_are_replaced_and_the_switch_still_saves(ctx, home):
    Continuity(home).store("phone1", "c1", "chat", 200, {"ok": True})
    cx = sqlite3.connect(home / "continuity.db")
    cx.execute("INSERT INTO ledger (device, cid, kind, status, body, ts) VALUES ('p', 'c2', 'chat', 'x', 'Dana{', 0)")
    cx.commit()
    cx.close()
    got = privacy.set_privacy(ctx, True)
    assert got["saved_in_config"] and got["scrub"]["partial"] == []
    cx = sqlite3.connect(home / "continuity.db")
    assert "Dana" not in json.dumps(cx.execute("SELECT body FROM ledger").fetchall())
    cx.close()
    assert Continuity(home).seen("p", "c2", max_age=1e12) == {"not_kept": True, "code": "NOT_KEPT", "ok": False}


def test_a_locked_database_is_reported_partial_to_the_owner(ctx, home, monkeypatch):
    _seed(home)
    monkeypatch.setattr(privacy, "SCRUB_TIMEOUT_S", 0.05)
    lock = sqlite3.connect(home / "continuity.db", isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        got = privacy.set_privacy(ctx, True)
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    assert got["scrub"]["partial"] == ["continuity.db"] and got["saved_in_config"]


async def test_core_start_with_privacy_on_scrubs_older_text(home, cfg):
    from test_m3_core import new_core
    _seed(home)
    cfg.log.privacy = True                                    # set in config.toml, no dashboard switch
    c = new_core(cfg, home)
    await c.start()
    try:
        assert "Dana" not in _dump(home)
    finally:
        await c.stop()


async def test_core_start_with_privacy_off_keeps_text(home, cfg):
    from test_m3_core import new_core
    _seed(home)
    cfg.log.privacy = False
    c = new_core(cfg, home)
    await c.start()
    try:
        assert "Dana" in _dump(home)
    finally:
        await c.stop()
