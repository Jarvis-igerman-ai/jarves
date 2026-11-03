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


def test_unreadable_result_file_alerts(tmp_path):
    (tmp_path / "factory_sim_last.json").write_text("{not json", encoding="utf-8")
    assert factory_sim_alerts(tmp_path)[0]["kind"] == "factory_sim_unreadable"


def test_file_issue_skips_when_duplicate_check_fails(monkeypatch):
    import subprocess
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "boom")
    monkeypatch.setattr(factory_sim.subprocess, "run", fake_run)
    factory_sim.file_issue(["x"], "tail")
    assert len(calls) == 1 and "list" in calls[0]


def test_file_issue_body_has_no_raw_output(monkeypatch):
    import subprocess
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")
    monkeypatch.setattr(factory_sim.subprocess, "run", fake_run)
    tail = "noise C:\\Users\\bob\\secret.py sk-abcdefghijkl123\nFAILED core/tests/t.py::x - boom\n=== 1 failed in 2s ==="
    factory_sim.file_issue(["x"], tail)
    body = calls[1][calls[1].index("--body") + 1]
    assert "noise" not in body and "sk-abc" not in body and "C:\\Users" not in body
    assert "FAILED core/tests/t.py::x" in body


def test_schedule_command_is_0300_daily_and_installs_nothing(capsys):
    assert factory_sim.main(["--schedule-command"]) == 0
    out = capsys.readouterr().out
    assert "/ST 03:00" in out and "--nightly" in out and "DAILY" in out


def test_nan_future_and_non_numeric_timestamps_alert_not_crash(tmp_path):
    f = tmp_path / "factory_sim_last.json"
    for body in ('{"at": NaN, "ok": true}', '{"at": 99999999999, "ok": true}', '{"at": "x", "ok": true}', '[1]'):
        f.write_text(body)
        assert factory_sim_alerts(tmp_path, now=1000.0)[0]["kind"] in ("factory_sim_stale", "factory_sim_unreadable")


def test_summary_redacts_more_tokens():
    out = factory_sim._summary("FAILED x github_pat_ABCDEFGH1234 ghs_ABCDEFGHIJ AKIAABCDEFGHIJKL")
    assert "github_pat_" not in out and "ghs_" not in out and "AKIA" not in out


def test_huge_int_timestamp_and_non_list_failed(tmp_path):
    f = tmp_path / "factory_sim_last.json"
    f.write_text('{"at": 1' + "0" * 400 + ', "ok": true}')
    assert factory_sim_alerts(tmp_path, now=1000.0)[0]["kind"] == "factory_sim_unreadable"
    for bad in ("5", '"x"', "null"):
        f.write_text('{"at": 1.0, "ok": false, "failed": ' + bad + '}')
        assert factory_sim_alerts(tmp_path, now=1000.0)[0]["kind"] == "factory_sim_failed"


def test_deep_nesting_and_string_ok(tmp_path):
    f = tmp_path / "factory_sim_last.json"
    f.write_text("[" * 100000)
    assert factory_sim_alerts(tmp_path)[0]["kind"] == "factory_sim_unreadable"
    f.write_text('{"at": 1.0, "ok": "false"}')
    assert factory_sim_alerts(tmp_path, now=1000.0)[0]["kind"] == "factory_sim_failed"
