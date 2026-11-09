"""JAR-86: auto_update no longer swallows errors silently; each failed probe leaves a bounded structured log."""
from types import SimpleNamespace

from jarves import auto_update, logs


def _capture(monkeypatch):
    seen = []
    monkeypatch.setattr(logs, "warn", lambda comp, code, msg, **f: seen.append((code, f)))
    return seen


def test_busy_probe_failures_are_logged(monkeypatch):
    seen = _capture(monkeypatch)

    class Boom:
        def __getattr__(self, n):
            raise RuntimeError("secret detail")

    core = SimpleNamespace(live=None, listening=False, stopping=False, approvals=Boom(), chat=Boom(),
                           orchestrator=None, ops=Boom())
    assert auto_update.core_busy(core) == []
    probes = [c for c, _ in seen]
    assert probes == ["AUTO_UPDATE_BUSY_PROBE_FAILED"] * 3
    assert all(f["error_type"] == "RuntimeError" and "secret" not in str(f) for _, f in seen)


def test_unreadable_switch_is_logged(monkeypatch, tmp_path):
    from jarves import features
    seen = _capture(monkeypatch)

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(features, "Features", boom)
    assert auto_update.is_enabled(None, tmp_path) is False
    assert seen and seen[0][0] == "AUTO_UPDATE_SWITCH_UNREADABLE"
