"""Nightly factory simulation record + dashboard alerts (#757). Hermetic: no gh, no pytest subprocess."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import factory_sim  # noqa: E402
from jarves.workflow.autopilot_watch import factory_sim_alerts  # noqa: E402


def test_record_pass_writes_file_without_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_sim, "file_issue", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no issue")))
    factory_sim.record(str(tmp_path), [{"name": "a", "result": "PASS"}], 0, "")
    data = json.loads((tmp_path / "factory_sim_last.json").read_text())
    assert data["ok"] and data["failed"] == []
    assert factory_sim_alerts(tmp_path) == []


def test_record_fail_files_issue_and_alerts(tmp_path, monkeypatch):
    filed = []
    monkeypatch.setattr(factory_sim, "file_issue", lambda failing, tail: filed.append(failing))
    factory_sim.record(str(tmp_path), [{"name": "stall", "result": "FAIL"}, {"name": "b", "result": "PASS"}], 1, "tail")
    assert filed == [["stall"]]
    alerts = factory_sim_alerts(tmp_path)
    assert alerts[0]["kind"] == "factory_sim_failed" and "stall" in alerts[0]["summary"]


def test_stale_and_missing(tmp_path):
    assert factory_sim_alerts(tmp_path) == []
    (tmp_path / "factory_sim_last.json").write_text(json.dumps({"at": 100.0, "ok": True}))
    assert factory_sim_alerts(tmp_path, now=100.0 + 37 * 3600)[0]["kind"] == "factory_sim_stale"
    assert factory_sim_alerts(tmp_path, now=100.0 + 3600) == []


def test_schedule_command_is_0300_daily_and_installs_nothing(capsys):
    assert factory_sim.main(["--schedule-command"]) == 0
    out = capsys.readouterr().out
    assert "/ST 03:00" in out and "--nightly" in out and "DAILY" in out
