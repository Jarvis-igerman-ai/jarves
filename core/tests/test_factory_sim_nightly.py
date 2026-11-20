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
    missing = factory_sim_alerts(tmp_path)  # #897: an uninstalled schedule must be visible
    assert [a["kind"] for a in missing] == ["factory_sim_missing"]
    assert missing[0]["summary"].startswith("No nightly factory simulation result found")
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


def _fake_gh(calls, labels_out="[]", labels_rc=0, create_rc=0, create_err=""):
    import subprocess

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(cmd, labels_rc, labels_out, "")
        if "create" in cmd:
            return subprocess.CompletedProcess(cmd, create_rc, "", create_err)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")
    return fake_run


def _create_labels(calls):
    (create,) = [c for c in calls if "create" in c]
    return [create[i + 1] for i, a in enumerate(create) if a == "--label"]


def test_file_issue_body_has_no_raw_output(monkeypatch):
    calls = []
    monkeypatch.setattr(factory_sim.subprocess, "run", _fake_gh(calls, labels_out='[{"name": "testing"}]'))
    tail = "noise C:\\Users\\bob\\secret.py sk-abcdefghijkl123\nFAILED core/tests/t.py::x - boom\n=== 1 failed in 2s ==="
    factory_sim.file_issue(["x"], tail)
    (create,) = [c for c in calls if "create" in c]
    body = create[create.index("--body") + 1]
    assert "noise" not in body and "sk-abc" not in body and "C:\\Users" not in body
    assert "FAILED core/tests/t.py::x" in body
    assert _create_labels(calls) == ["showstopper", "P1", "testing"]  # #897: the auto-issue carries an area label


def test_issue_is_filed_without_area_label_when_repo_lacks_it(monkeypatch):
    calls = []  # #897: decided from `gh label list`, not from gh's error wording; exactly one create call
    monkeypatch.setattr(factory_sim.subprocess, "run", _fake_gh(calls, labels_out='[{"name": "testing-old"}]'))
    factory_sim.file_issue(["x"], "tail")
    assert _create_labels(calls) == ["showstopper", "P1"]


def test_label_check_failure_or_malformed_output_files_without_area_label(monkeypatch, capsys):
    for kw in ({"labels_rc": 1}, {"labels_out": "{not json"}, {"labels_out": '{"name": "testing"}'}):
        calls = []
        monkeypatch.setattr(factory_sim.subprocess, "run", _fake_gh(calls, **kw))
        factory_sim.file_issue(["x"], "tail")
        assert _create_labels(calls) == ["showstopper", "P1"]
        assert "label check failed; filing without the 'testing' label" in capsys.readouterr().out


def test_issue_create_failure_is_reported_once(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(factory_sim.subprocess, "run",
                        _fake_gh(calls, labels_out='[{"name": "testing"}]', create_rc=1, create_err="HTTP 401: Bad credentials"))
    factory_sim.file_issue(["x"], "tail")
    assert len([c for c in calls if "create" in c]) == 1
    assert "issue create failed: HTTP 401: Bad credentials" in capsys.readouterr().out


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


def test_label_check_timeout_or_oserror_still_files_issue(monkeypatch, capsys):
    import subprocess  # #897 review R1: a hung or missing `gh label list` must not lose the failure report
    for exc in (subprocess.TimeoutExpired(["gh"], 60), OSError("gh not found")):
        calls = []
        base = _fake_gh(calls)

        def fake_run(cmd, _exc=exc, **kw):
            if cmd[:3] == ["gh", "label", "list"]:
                calls.append(cmd)
                raise _exc
            return base(cmd, **kw)
        monkeypatch.setattr(factory_sim.subprocess, "run", fake_run)
        factory_sim.file_issue(["x"], "tail")
        assert _create_labels(calls) == ["showstopper", "P1"]
        assert "label check failed; filing without the 'testing' label" in capsys.readouterr().out


def test_label_check_uses_short_timeout(monkeypatch):
    calls = []  # #897 review R2: a hung `gh label list` must not delay the failure report by a full minute
    base = _fake_gh(calls)
    timeouts = {}

    def fake_run(cmd, **kw):
        timeouts[cmd[1]] = kw.get("timeout")
        return base(cmd, **kw)
    monkeypatch.setattr(factory_sim.subprocess, "run", fake_run)
    factory_sim.file_issue(["x"], "tail")
    assert timeouts["label"] is not None and 0 < timeouts["label"] < 60
