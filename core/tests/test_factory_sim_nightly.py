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
    missing = factory_sim_alerts(tmp_path)        # #897: an uninstalled schedule must be visible
    assert [a["kind"] for a in missing] == ["factory_sim_missing"]
    assert "--schedule-command" in missing[0]["summary"]
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
    labels = [calls[1][i + 1] for i, part in enumerate(calls[1]) if part == "--label"]
    assert labels == ["showstopper", "P1", "testing"]   # #897: an area label; "testing" exists in the repo


def test_issue_is_filed_without_the_area_label_when_that_label_is_missing(monkeypatch):
    import subprocess
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        failed = cmd[2] == "create" and "testing" in cmd
        return subprocess.CompletedProcess(cmd, 1 if failed else 0, "[]", "label not found" if failed else "")
    monkeypatch.setattr(factory_sim.subprocess, "run", fake_run)
    factory_sim.file_issue(["x"], "FAILED a::b")
    creates = [c for c in calls if c[2] == "create"]
    assert len(creates) == 2 and "testing" not in creates[1] and "showstopper" in creates[1]


def test_issue_create_is_not_retried_after_a_non_label_failure(monkeypatch, capsys):
    """#897 review: an auth/network failure (or a create that half-succeeded) must not file a second issue."""
    import subprocess
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        create = cmd[2] == "create"
        return subprocess.CompletedProcess(cmd, int(create), "[]", "HTTP 401: Bad credentials" if create else "")
    monkeypatch.setattr(factory_sim.subprocess, "run", fake_run)
    factory_sim.file_issue(["x"], "FAILED a::b")
    assert len([c for c in calls if c[2] == "create"]) == 1
    assert "issue create failed: HTTP 401" in capsys.readouterr().out


def test_factory_sim_alerts_are_read_only_by_the_factory_console():
    """#897 review: the always-on factory_sim_missing alert must never reach the end-user Core dashboard."""
    src = Path(__file__).resolve().parents[1] / "src" / "jarves"
    users = sorted(p.relative_to(src).as_posix() for p in src.rglob("*.py")
                   if "factory_sim_alerts(" in p.read_text(encoding="utf-8"))
    assert users == ["workflow/autopilot_watch.py", "workflow/server.py"]


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


def test_nightly_hung_scenario_times_out_and_records_failure(tmp_path, monkeypatch):
    """JAR-403 (#757): a stalled scenario must not hang the nightly run; it is recorded as a failure."""
    import subprocess
    filed = []
    monkeypatch.setattr(factory_sim, "file_issue", lambda failing, tail: filed.append(failing))

    def hang(cmd, **kw):
        assert kw.get("timeout"), "pytest subprocess must have a timeout"
        raise subprocess.TimeoutExpired(cmd, kw["timeout"], output="PASSED t.py::test_sim_a\n")
    monkeypatch.setattr(factory_sim.subprocess, "run", hang)
    assert factory_sim.main(["--nightly", "--home", str(tmp_path)]) == 1
    data = json.loads((tmp_path / "factory_sim_last.json").read_text())
    assert data["ok"] is False and "timeout" in data["failed"]
    assert filed and "timeout" in filed[0]


def test_nightly_nonzero_pytest_exit_is_failure_even_if_scenarios_passed(tmp_path, monkeypatch):
    """JAR-403 (#757): the gate checks pytest's exit code, not only the parsed PASS lines."""
    import subprocess
    filed = []
    monkeypatch.setattr(factory_sim, "file_issue", lambda failing, tail: filed.append(failing))
    monkeypatch.setattr(factory_sim.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 3, "PASSED t.py::test_sim_a\nINTERNALERROR boom\n", ""))
    assert factory_sim.main(["--nightly", "--home", str(tmp_path)]) == 1
    data = json.loads((tmp_path / "factory_sim_last.json").read_text())
    assert data["ok"] is False and filed
