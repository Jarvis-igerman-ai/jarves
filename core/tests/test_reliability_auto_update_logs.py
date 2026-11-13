"""JAR-86: auto_update no longer swallows errors silently; each failed probe leaves a bounded structured log."""
from types import SimpleNamespace

import pytest

from jarves import auto_update, features, logs


@pytest.fixture
def seen(monkeypatch):
    out = []
    monkeypatch.setattr(logs, "warn", lambda comp, code, msg, **f: out.append((code, f)))
    auto_update._failing.clear()
    yield out
    auto_update._failing.clear()


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


class _Healthy:
    """Every busy probe answers 'busy', so a single failing probe must not hide the others."""
    approvals = SimpleNamespace(has_pending=lambda: True)
    chat = SimpleNamespace(turns=[1])
    orchestrator = SimpleNamespace(db=SimpleNamespace(sessions=lambda live_only: [{"state": "RUNNING"}]))
    ops = SimpleNamespace(alerts=SimpleNamespace(quiet_now=lambda: True, paused=lambda: False))


BUSY = {"approvals": "pending_approval", "chat": "chat_turn", "orchestrator": "coding_agent", "ops": "quiet_hours"}
WHERE = {"approvals": "approvals", "chat": "chat", "orchestrator": "agents", "ops": "quiet"}


@pytest.mark.parametrize("broken", list(BUSY))
def test_each_busy_probe_failure_is_logged_alone(seen, broken):
    core = _Healthy()
    setattr(core, broken, Boom())
    assert auto_update.core_busy(core) == [v for k, v in BUSY.items() if k != broken]
    assert seen == [("AUTO_UPDATE_BUSY_PROBE_FAILED", {"error_type": "RuntimeError", "where": WHERE[broken]})]


def test_unreadable_switch_is_logged_once(seen, monkeypatch, tmp_path):
    monkeypatch.setattr(features, "Features", _boom)
    assert auto_update.is_enabled(None, tmp_path) is False
    assert auto_update.is_enabled(None, tmp_path) is False     # read on every look: logged once
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


def test_busy_probe_logs_once_per_outage(seen):
    """fail, fail (another error type), ok, fail: one line per outage, and the second outage is not silent."""
    core = _Healthy()
    core.approvals = Boom()
    auto_update.core_busy(core)
    core.approvals = SimpleNamespace(has_pending=_boom)       # still down, different error type: same outage
    auto_update.core_busy(core)
    assert [f["error_type"] for _, f in seen] == ["RuntimeError"]
    core.approvals = _Healthy.approvals                       # recovered
    assert "pending_approval" in auto_update.core_busy(core)
    core.approvals = Boom()                                   # second outage logs again
    auto_update.core_busy(core)
    assert [(c, f["where"]) for c, f in seen] == [("AUTO_UPDATE_BUSY_PROBE_FAILED", "approvals")] * 2


def test_unreadable_switch_logs_again_after_recovery(seen, monkeypatch, tmp_path):
    real = features.Features
    for f in (_boom, real, _boom):                            # fail, a good read ends the outage, fail again
        monkeypatch.setattr(features, "Features", f)
        auto_update.is_enabled(None, tmp_path)
    assert [c for c, _ in seen] == ["AUTO_UPDATE_SWITCH_UNREADABLE"] * 2


@pytest.mark.parametrize("code,read", [
    ("AUTO_UPDATE_DEFAULT_NOTE_FAILED", lambda au: au._default_note()),
    ("AUTO_UPDATE_CHANNEL_CHECK_FAILED", lambda au: au._first_run_prompt()),
])
def test_channel_reads_log_again_after_recovery(seen, monkeypatch, tmp_path, code, read):
    real = features.Features
    for i, f in enumerate((_boom, _boom, real, _boom)):       # one outage, a good read, a second outage
        monkeypatch.setattr(features, "Features", f)
        read(_updater(tmp_path / str(i)))                     # fresh home: the channel prompt is asked once per home
    assert [c for c, _ in seen] == [code] * 2
