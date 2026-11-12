"""JAR-86: auto_update no longer swallows errors silently; each failed probe leaves a bounded structured log."""
from types import SimpleNamespace

import pytest

from jarves import auto_update, features, logs


@pytest.fixture
def seen(monkeypatch):
    out = []
    monkeypatch.setattr(logs, "warn", lambda comp, code, msg, **f: out.append((code, f)))
    auto_update._warned.clear()
    yield out
    auto_update._warned.clear()


class Boom:
    def __getattr__(self, n):
        raise RuntimeError("secret detail")


def _core():
    return SimpleNamespace(approvals=Boom(), chat=Boom(), orchestrator=Boom(), ops=Boom())


def _boom(*a, **k):
    raise OSError("secret disk path")


def test_busy_probe_failures_are_logged_once_each(seen):
    assert auto_update.core_busy(_core()) == []
    assert [c for c, _ in seen] == ["AUTO_UPDATE_BUSY_PROBE_FAILED"] * 4
    assert [f["where"] for _, f in seen] == ["approvals", "chat", "agents", "quiet"]
    assert all(f["error_type"] == "RuntimeError" and "secret" not in str(f) for _, f in seen)
    auto_update.core_busy(_core())          # the Core looks every TICK_S: the same failure is not repeated
    assert len(seen) == 4


def test_unreadable_switch_is_logged(seen, monkeypatch, tmp_path):
    monkeypatch.setattr(features, "Features", _boom)
    assert auto_update.is_enabled(None, tmp_path) is False
    assert seen == [("AUTO_UPDATE_SWITCH_UNREADABLE", {"error_type": "OSError"})]


def _updater(tmp_path):
    return auto_update.AutoUpdater(tmp_path, checker=lambda: None, enabled=lambda: False,
                                   busy=lambda: [], notify=lambda n: None)


def test_default_note_failure_is_logged_once(seen, monkeypatch, tmp_path):
    au = _updater(tmp_path)
    monkeypatch.setattr(features, "Features", _boom)
    assert au._default_note() is None and au._default_note() is None   # status() reads it on every poll
    assert seen == [("AUTO_UPDATE_DEFAULT_NOTE_FAILED", {"error_type": "OSError"})]


def test_channel_check_failure_is_logged_and_owner_still_asked(seen, monkeypatch, tmp_path):
    au = _updater(tmp_path)
    monkeypatch.setattr(features, "Features", _boom)
    au._first_run_prompt()
    assert seen == [("AUTO_UPDATE_CHANNEL_CHECK_FAILED", {"error_type": "OSError"})]
    assert au.state()["prompted"] is True   # behaviour unchanged: the owner is still asked
