"""Hermetic tests for core/tools/beta_daily.py (JAR-456): fake runner, explicit clock, tmp home, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import beta_daily as bd  # noqa: E402

NOW = 1_800_000_000.0


def runner(fail=()):
    calls = []

    def run(argv):
        calls.append(list(argv))
        bad = any(word in argv for word in fail)
        return (1 if bad else 0), "line <one> & two\x1b[0m\n" + ("FAIL" if bad else "PASS")
    run.calls = calls
    return run


def test_green_run_writes_install_scenario_and_demo_records(tmp_path):
    run = runner()
    bd.run_daily(tmp_path, run, now=NOW)
    assert run.calls[0] == ["jarvis", *bd.INSTALL]
    files = sorted(p.name for p in (tmp_path / "beta_records").iterdir())
    assert any(f.startswith("install-") for f in files) and any(f.startswith("scenarios-") for f in files)
    svgs = sorted((tmp_path / "beta_records").glob("demo-*.svg"))
    assert [p.name.split("-")[1] + "-" + p.name.split("-")[2] for p in svgs] == ["JAR-214", "JAR-320", "JAR-321"]
    scen = json.loads(next((tmp_path / "beta_records").glob("scenarios-*.json")).read_text(encoding="utf-8"))
    assert {s["id"]: s["result"] for s in scen["scenarios"]} == {f"S0{i}": "PASS" for i in range(1, 6)}
    report = bd.check(tmp_path, now=NOW + 3600)
    assert report == {"ok": True, "last_install_age_h": 1.0, "scenario_pass_pct": 100.0,
                      "demos_24h_with_image": 3, "unreadable": 0}


def test_failing_scenario_is_recorded_as_fail(tmp_path):
    bd.run_daily(tmp_path, runner(fail=("test-limits",)), now=NOW)
    report = bd.check(tmp_path, now=NOW)
    assert report["scenario_pass_pct"] == 80.0 and report["ok"] is True


def test_failed_install_writes_record_but_runs_no_scenarios(tmp_path):
    run = runner(fail=("install",))
    bd.run_daily(tmp_path, run, now=NOW)
    assert len(run.calls) == 1
    rec = json.loads(next((tmp_path / "beta_records").glob("install-*.json")).read_text(encoding="utf-8"))
    assert rec["ok"] is False and rec["rc"] == 1
    report = bd.check(tmp_path, now=NOW)
    assert report["ok"] is False and report["last_install_age_h"] is None and report["scenario_pass_pct"] is None


def test_stale_install_and_old_demos_fail_the_check(tmp_path):
    bd.run_daily(tmp_path, runner(), now=NOW)
    report = bd.check(tmp_path, now=NOW + 27 * 3600)
    assert report["ok"] is False and report["last_install_age_h"] == 27.0 and report["demos_24h_with_image"] == 0


def test_demo_without_its_image_does_not_count(tmp_path):
    bd.run_daily(tmp_path, runner(), now=NOW)
    for svg in (tmp_path / "beta_records").glob("demo-*.svg"):
        svg.unlink()
    assert bd.check(tmp_path, now=NOW)["demos_24h_with_image"] == 0


def test_malformed_records_are_counted_not_fatal(tmp_path):
    rec = tmp_path / "beta_records"
    rec.mkdir()
    (rec / "install-bad.json").write_text("{not json", encoding="utf-8")
    (rec / "scenarios-list.json").write_text("[1, 2]", encoding="utf-8")
    (rec / "demo-x.json").write_text('{"at": "soon"}', encoding="utf-8")
    report = bd.check(tmp_path, now=NOW)
    assert report["unreadable"] == 3 and report["ok"] is False and report["scenario_pass_pct"] is None


def test_svg_escapes_markup_and_control_chars():
    svg = bd.render_svg("JAR-1", ["a <b> & c\x07\x1b[31m"])
    assert "&lt;b&gt; &amp; c" in svg and "\x07" not in svg and "\x1b" not in svg and svg.startswith("<svg")


def test_real_runner_reports_missing_command_and_exit_code():
    assert bd.real_runner("/nonexistent/jarvis")(["jarvis", "status"])[0] == 127
    rc, out = bd.real_runner()([sys.executable, "-c", "print('hi'); raise SystemExit(3)"])
    assert rc == 3 and "hi" in out
