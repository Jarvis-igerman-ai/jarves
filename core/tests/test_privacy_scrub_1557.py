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


def _dump(home) -> str:
    out = []
    for name, sql in (("continuity.db", "SELECT status, body FROM ledger"),
                      ("memory.db", "SELECT status, evidence, error FROM goal_plan_steps")):
        cx = sqlite3.connect(home / name)
        try:
            out += [json.dumps(list(r)) for r in cx.execute(sql)]
        finally:
            cx.close()
    return "\n".join(out)


def test_switching_privacy_on_scrubs_older_text(ctx, home):
    _seed(home)
    assert "Dana" in _dump(home)
    privacy.set_privacy(ctx, True)
    dump = _dump(home)
    assert "Dana" not in dump
    assert "TOOL_ERROR" in dump and "failed" in dump          # the code and status stay
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
    assert privacy.scrub_at_rest(home) == {"ledger": 0, "plan_steps": 0}
    assert not (home / "continuity.db").exists() and not (home / "memory.db").exists()


def test_unreadable_database_is_logged_and_the_other_is_still_scrubbed(home, monkeypatch):
    _seed(home)
    (home / "continuity.db").write_bytes(b"not a database" * 100)
    seen = []
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append((a[1], k)))
    out = privacy.scrub_at_rest(home)
    assert out == {"ledger": 0, "plan_steps": 1}
    assert seen == [("PRIVACY_SCRUB_PARTIAL", {"target": "continuity.db", "error_type": "DatabaseError"})]
    assert privacy.scrub_at_rest(home)["plan_steps"] == 0          # already scrubbed: nothing rewritten again
