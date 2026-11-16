"""Hermetic tests for core/tools/beta_daily.py (JAR-456): fake runner, explicit clock, tmp home, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import beta_daily as bd  # noqa: E402

NOW = 1_800_000_000.0


def runner(fail=(), newer=False):
    calls = []

    def run(argv):
        calls.append(list(argv))
        bad = any(word in argv for word in fail)
        if argv[1:] == list(bd.INSTALL) and not bad:
            return 0, "New version 9.9 (beta)" if newer else "JARVES 1.0\nUp to date (checked now)."
        return (1 if bad else 0), "line <one> & two\x1b[0m\n" + ("FAIL" if bad else "PASS")
    run.calls = calls
    return run


def test_green_run_writes_install_scenario_and_demo_records(tmp_path):
    run = runner()
    bd.run_daily(tmp_path, run, now=NOW)
    assert run.calls[0] == ["jarvis", "update", "check"]
    assert not any("install" in c or "--yes" in c for c in run.calls)  # never installs or pre-approves itself
    files = sorted(p.name for p in (tmp_path / "beta_records").iterdir())
    assert any(f.startswith("install-") for f in files) and any(f.startswith("scenarios-") for f in files)
    svgs = sorted((tmp_path / "beta_records").glob("demo-*.svg"))
    assert [p.name.split("-")[1] + "-" + p.name.split("-")[2] for p in svgs] == ["JAR-214", "JAR-320", "JAR-321"]
    scen = json.loads(next((tmp_path / "beta_records").glob("scenarios-*.json")).read_text(encoding="utf-8"))
    assert {s["id"]: s["result"] for s in scen["scenarios"]} == {f"S0{i}": "PASS" for i in range(1, 6)}
    report = bd.check(tmp_path, now=NOW + 3600)
    assert report == {"ok": True, "last_install_age_h": 1.0, "scenario_pass_pct": 100.0,
                      "demos_24h_with_image": 3, "demos_24h_not_passing": 0, "unreadable": 0}


def test_failing_scenario_is_recorded_as_fail(tmp_path):
    bd.run_daily(tmp_path, runner(fail=("test-limits",)), now=NOW)
    report = bd.check(tmp_path, now=NOW)
    assert report["scenario_pass_pct"] == 80.0 and report["ok"] is True
    assert report["demos_24h_with_image"] == 2 and report["demos_24h_not_passing"] == 1  # failing JAR-321 not counted


def test_only_failing_or_skipped_demos_fail_the_check(tmp_path):
    bd.run_daily(tmp_path, runner(fail=("-q", "test-limits")), now=NOW)
    assert bd.check(tmp_path, now=NOW)["ok"] is False
    home = tmp_path / "skip"
    base = runner()
    bd.run_daily(home, lambda argv: (1, "No module named pytest") if "-q" in argv else base(argv), now=NOW)
    results = {json.loads(p.read_text(encoding="utf-8"))["ticket"]: json.loads(p.read_text(encoding="utf-8"))["result"]
               for p in (home / "beta_records").glob("demo-*.json")}
    assert results == {"JAR-214": "SKIPPED", "JAR-320": "SKIPPED", "JAR-321": "PASS"}


def test_newer_build_waiting_is_not_a_good_install(tmp_path):
    run = runner(newer=True)
    bd.run_daily(tmp_path, run, now=NOW)
    assert len(run.calls) == 1 and bd.check(tmp_path, now=NOW)["last_install_age_h"] is None


def test_two_runs_in_one_second_keep_both_records(tmp_path):
    bd.run_daily(tmp_path, runner(), now=NOW)
    bd.run_daily(tmp_path, runner(), now=NOW)
    assert len(list((tmp_path / "beta_records").glob("install-*.json"))) == 2
    assert len(list((tmp_path / "beta_records").glob("demo-*.svg"))) == 6


def test_age_just_under_26h_passes_without_rounding(tmp_path):
    bd.run_daily(tmp_path, runner(), now=NOW)
    for svg in (tmp_path / "beta_records").glob("demo-*.json"):
        data = json.loads(svg.read_text(encoding="utf-8"))
        svg.write_text(json.dumps({**data, "at": NOW + 25.95 * 3600}), encoding="utf-8")
    assert bd.check(tmp_path, now=NOW + 25.95 * 3600)["ok"] is True


def test_demo_test_node_ids_exist():
    import importlib.util
    for _ticket, target, argv in bd.DEMOS:
        if target != "source-tests":
            continue
        path, *names = argv[-1].split("::")
        spec = importlib.util.spec_from_file_location("demo_" + _ticket.replace("-", "_"), bd.CORE / path)
        obj = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(obj)
        for name in names:
            obj = getattr(obj, name)


def test_failed_install_check_writes_record_but_runs_no_scenarios(tmp_path):
    run = runner(fail=("check",))
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
