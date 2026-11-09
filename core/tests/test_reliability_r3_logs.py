"""JAR-103: health snapshot and phone replay failures leave bounded structured logs instead of passing silently."""
from jarves import health, logs, mobile


def _capture(monkeypatch):
    seen = []
    monkeypatch.setattr(logs, "warn", lambda comp, code, msg, **f: seen.append((code, f)))
    return seen


def test_health_save_failure_logged_once_per_outage(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    h = health.Health(tmp_path / "missing_dir" / "health.json")   # parent does not exist -> OSError
    h.save()
    h.save()
    assert [c for c, _ in seen] == ["HEALTH_SNAPSHOT_WRITE_FAILED"]
    assert seen[0][1] == {"error_type": "FileNotFoundError"}
    (tmp_path / "missing_dir").mkdir()
    h.save()                                                       # recovered: flag resets
    assert (tmp_path / "missing_dir" / "health.json").exists()
    (tmp_path / "missing_dir" / "health.json").unlink()
    (tmp_path / "missing_dir").rmdir()
    h.save()
    assert [c for c, _ in seen].count("HEALTH_SNAPSHOT_WRITE_FAILED") == 2


def test_replay_lookup_failure_logged(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    b = mobile.MobileAccess(tmp_path)

    class Boom:
        def seen(self, *a, **k):
            raise RuntimeError("secret detail")
    b._cont = Boom()
    assert b.replay("dev", "m1") is None
    assert seen and seen[0][0] == "MOBILE_REPLAY_LOOKUP_FAILED"
    assert "secret" not in str(seen)
