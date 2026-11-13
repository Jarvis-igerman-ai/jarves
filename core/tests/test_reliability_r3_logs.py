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


def test_test_history_missing_is_silent_corrupt_logged_once(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    b = mobile.MobileAccess(tmp_path)
    assert mobile.health_view(b)["tests"] == {}                  # no history file yet: normal, no log
    assert not [c for c, _ in seen if c == "MOBILE_TEST_HISTORY_FAILED"]
    (tmp_path / "selfspeed.json").write_text('{"latest": {"selftest": {"status": "ok"}}}', encoding="utf-8")
    mobile.health_view(b)
    mobile.health_view(b)                                        # polled again: still one line per outage
    assert [c for c, _ in seen].count("MOBILE_TEST_HISTORY_FAILED") == 1


def test_one_bad_history_entry_hides_only_itself_and_recovery_resets(monkeypatch, tmp_path):
    import json
    seen = _capture(monkeypatch)
    b = mobile.MobileAccess(tmp_path)
    good = {"ts": 1.0, "status": "pass", "deep": False, "audio": False, "ms": 5.0, "checks": []}
    f = tmp_path / "selfspeed.json"
    f.write_text(json.dumps({"latest": {"speedtest": {"status": "ok"}, "selftest": good}}), encoding="utf-8")
    tests = mobile.health_view(b)["tests"]
    assert list(tests) == ["selftest"] and tests["selftest"]["status"] == "pass"
    assert [c for c, _ in seen].count("MOBILE_TEST_HISTORY_FAILED") == 1
    f.write_text(json.dumps({"latest": {"selftest": good}}), encoding="utf-8")
    mobile.health_view(b)                                        # repaired: the outage flag clears
    f.write_text("[]", encoding="utf-8")                         # valid JSON, wrong shape: a new outage
    assert mobile.health_view(b)["tests"] == {}
    assert [c for c, _ in seen].count("MOBILE_TEST_HISTORY_FAILED") == 2


def test_test_history_never_reads_perf_advisor(monkeypatch, tmp_path):
    from jarves import perf_advisor
    seen = _capture(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("unrelated")
    monkeypatch.setattr(perf_advisor, "recommendations", boom)  # a fault outside the history is not "corrupt history"
    monkeypatch.setattr(perf_advisor, "options", boom)
    assert mobile.health_view(mobile.MobileAccess(tmp_path))["tests"] == {}
    assert not [c for c, _ in seen if c == "MOBILE_TEST_HISTORY_FAILED"]


def test_replay_lookup_failure_logged_once_per_outage(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    b = mobile.MobileAccess(tmp_path)
    state = {"broken": True}

    class Store:
        def seen(self, *a, **k):
            if state["broken"]:
                raise RuntimeError("secret detail")
            return None
    b._cont = Store()
    for i in range(3):
        assert b.replay("dev", f"m{i}") is None
    assert [c for c, _ in seen] == ["MOBILE_REPLAY_LOOKUP_FAILED"]
    state["broken"] = False
    assert b.replay("dev", "m9") is None                          # recovered: the flag resets
    state["broken"] = True
    b.replay("dev", "m10")
    assert [c for c, _ in seen] == ["MOBILE_REPLAY_LOOKUP_FAILED"] * 2


def test_presence_failure_is_away_and_logged_once_per_outage(monkeypatch):
    import types
    from jarves import calm
    seen = _capture(monkeypatch)
    state = {"broken": True}

    def owner_at_pc(core):
        if state["broken"]:
            raise RuntimeError("secret detail")
        return True
    monkeypatch.setattr(calm, "owner_at_pc", owner_at_pc)
    bridge = mobile.core_bridge(types.SimpleNamespace(), None)
    assert bridge.desktop_present() is False
    assert bridge.desktop_present() is False
    assert seen == [("MOBILE_PRESENCE_UNKNOWN", {"error_type": "RuntimeError"})]
    state["broken"] = False
    assert bridge.desktop_present() is True                       # recovered: the flag resets
    state["broken"] = True
    bridge.desktop_present()
    assert [c for c, _ in seen] == ["MOBILE_PRESENCE_UNKNOWN"] * 2
