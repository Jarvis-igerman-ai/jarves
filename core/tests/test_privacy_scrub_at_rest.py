"""#1557: switching privacy on also scrubs text stored BEFORE it was on (continuity ledger, goal plan steps)."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from jarves import logs, privacy
from jarves.config import Config
from jarves.continuity import Continuity
from jarves.userdata import open_db


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    logs.setup(h / "logs", console=False, privacy=False)
    yield h
    logs.set_privacy(True)


def _ctx(home):
    cfg = Config()
    cfg.log.privacy = False
    return SimpleNamespace(home=home, cfg=cfg, config_path=home / "missing" / "config.toml")


def _seed(home):
    c = Continuity(home)
    c.store("phone", "c1", "chat", 200, {"reply": "Dana's birthday is 3 May", "ok": True})
    c.store("phone", "g1", "goal", 200, {"ok": True, "goal": {"id": 4, "title": "Surprise for Dana", "status": "open",
                                         "percent": 0, "steps": [{"id": 1, "text": "flowers for Dana", "done": False}]}})
    c.store("phone", "g2", "goal", 400, {"error": "no goal called Surprise for Dana", "code": "GOAL_MISSING"})
    c.store("phone", "p1", "pref", 200, {"ok": True, "pref": 3, "state": "confirmed"})
    with sqlite3.connect(c.path) as cx:                                    # a malformed body is scrubbed too
        cx.execute("INSERT INTO ledger (device, cid, kind, status, body, ts) VALUES ('phone','c2','chat',200,?,?)",
                   ("not json: Dana", 1e12))
    db = open_db(home)
    db.x("INSERT INTO goal_plans (id, title, state, note, created, updated) VALUES (1, 'plan', 'failed', ?, 0, 0)",
         ("step 2: CALL_FAILED: Dana did not answer",))
    db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence, error) VALUES (1, 0, 'web', 'done', ?, '')",
         ("Dana lives at 5 Herzl St",))
    db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence, error) VALUES (1, 1, 'call', 'failed', ?, ?)",
         ("Dana did not answer", "CALL_FAILED: Dana did not answer"))
    db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence, error) VALUES (1, 2, 'x', 'unknown', '', ?)",
         ("the step timed out: it may already have happened",))
    return c


def _dump(home) -> str:
    out = []
    for f in ("continuity.db", "memory.db"):
        with sqlite3.connect(home / f) as cx:
            for t in ("ledger", "goal_plans", "goal_plan_steps"):
                try:
                    out += [repr(tuple(r)) for r in cx.execute(f"SELECT * FROM {t}")]
                except sqlite3.OperationalError:
                    pass
    return "\n".join(out)


def test_switching_privacy_on_scrubs_older_text(home):
    c = _seed(home)
    assert "Dana" in _dump(home)
    r = privacy.set_privacy(_ctx(home), True)
    assert "Dana" not in _dump(home)
    assert r["scrubbed"] == {"ledger": 4, "plan_steps": 2, "plans": 1, "errors": []}
    chat = c.seen("phone", "c1")                                           # a resend is still answered
    assert chat["code"] == "NOT_KEPT" and chat["ok"] is True
    assert c.seen("phone", "c2")["ok"] is False
    assert c.seen("phone", "g1") == {"ok": True, "goal": {"id": 4, "status": "open", "percent": 0,
                                                                 "steps": [{"id": 1, "done": False}]}}
    assert c.seen("phone", "g2")["code"] == "GOAL_MISSING"
    assert c.seen("phone", "p1") == {"ok": True, "pref": 3, "state": "confirmed"}     # no user text: kept
    with sqlite3.connect(home / "memory.db") as cx:
        steps = cx.execute("SELECT status, evidence, error FROM goal_plan_steps ORDER BY idx").fetchall()
        note = cx.execute("SELECT note FROM goal_plans").fetchone()[0]
    assert steps == [("done", "", ""), ("failed", "", "CALL_FAILED"),
                     ("unknown", "", "the step timed out: it may already have happened")]
    assert note == "step 2: CALL_FAILED"


def test_ending_full_text_scrubs_too_and_privacy_off_does_not(home):
    _seed(home)
    privacy.set_privacy(_ctx(home), False)
    assert "Dana" in _dump(home)
    privacy.set_full_text(_ctx(home), False)
    assert "Dana" not in _dump(home)


def test_an_unreadable_database_is_reported_and_the_switch_still_works(home):
    _seed(home)
    (home / "continuity.db").write_bytes(b"this is not a database " * 200)
    r = privacy.set_privacy(_ctx(home), True)
    assert r["privacy"] is True and logs.privacy() is True
    assert [e["db"] for e in r["scrubbed"]["errors"]] == ["continuity.db"]
    assert r["scrubbed"]["plan_steps"] == 2                                    # the other database was still scrubbed
    assert "PRIVACY_SCRUB_FAILED" in (home / "logs" / "jarves.log").read_text(encoding="utf-8")


def test_missing_databases_are_not_created(home):
    home.mkdir(parents=True, exist_ok=True)
    r = privacy.set_privacy(_ctx(home), True)
    assert r["scrubbed"] == {"ledger": 0, "plan_steps": 0, "plans": 0, "errors": []}
    assert not (home / "continuity.db").exists() and not (home / "memory.db").exists()
