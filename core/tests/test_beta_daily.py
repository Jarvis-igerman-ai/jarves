"""Hermetic tests for core/tools/beta_daily.py (JAR-456): fake runner, explicit clock, tmp home, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import beta_daily as bd  # noqa: E402

NOW = 1_800_000_000.0


def update_line(available=False, error=None):
    return json.dumps({"current": "1.0", "available": available, "error": error, "latest": None, "checked_at": "now"})


def runner(fail=(), newer=False):
    calls = []

    def run(argv):
        calls.append(list(argv))
        bad = any(word in argv for word in fail)
        if argv[1:] == list(bd.INSTALL) and not bad:
            return 0, update_line(available=newer)
        return (1 if bad else 0), "line <one> & two\x1b[0m\n" + ("FAIL" if bad else "PASS")
    run.calls = calls
    return run


def test_green_run_writes_install_scenario_and_demo_records(tmp_path):
    run = runner()
    bd.run_daily(tmp_path, run, now=NOW)
    assert run.calls[0] == ["jarvis", "update", "check", "--json"]
    assert sum("test-limits" in c for c in run.calls) == 1  # the JAR-321 demo reuses S04 (R5)
    assert not any("install" in c or "--yes" in c for c in run.calls)  # never installs or pre-approves itself
    files = sorted(p.name for p in (tmp_path / "beta_records").iterdir())
    assert any(f.startswith("install-") for f in files) and any(f.startswith("scenarios-") for f in files)
    svgs = sorted((tmp_path / "beta_records").glob("demo-*.svg"))
    assert [p.name.split("-")[1] + "-" + p.name.split("-")[2] for p in svgs] == ["JAR-214", "JAR-320", "JAR-321"]
    scen = json.loads(next((tmp_path / "beta_records").glob("scenarios-*.json")).read_text(encoding="utf-8"))
    assert {s["id"]: s["result"] for s in scen["scenarios"]} == {f"S0{i}": "PASS" for i in range(1, 6)}
    assert "doctor" not in {s["cmd"] for s in scen["scenarios"]}  # records the mic and beeps: never unattended
    report = bd.check(tmp_path, now=NOW + 3600)
    assert report == {"ok": True, "newest_beta_installed_age_h": 1.0, "scenario_pass_pct": 100.0,
                      "demos_24h_with_image": 3, "demos_24h_installed_pass": 1, "demos_24h_not_passing": 0,
                      "unreadable": 0}


def test_failing_scenario_and_installed_demo_fail_the_check_even_if_source_demos_pass(tmp_path):
    bd.run_daily(tmp_path, runner(fail=("selftest", "test-limits")), now=NOW)
    report = bd.check(tmp_path, now=NOW)
    assert report["scenario_pass_pct"] == 60.0 and report["demos_24h_with_image"] == 2 and report["demos_24h_not_passing"] == 1
    assert report["demos_24h_installed_pass"] == 0 and report["ok"] is False


def test_old_scenario_record_gives_unknown_pass_pct(tmp_path):
    bd.run_daily(tmp_path, runner(), now=NOW - 27 * 3600)
    for p in (tmp_path / "beta_records").glob("*.json"):
        if not p.name.startswith("scenarios-"):
            p.write_text(json.dumps({**json.loads(p.read_text(encoding="utf-8")), "at": NOW}), encoding="utf-8")
    assert bd.check(tmp_path, now=NOW)["scenario_pass_pct"] is None


def test_run_budget_skips_remaining_steps(tmp_path):
    ticks = iter(range(0, 10_000, 1000))
    run = runner()
    bd.run_daily(tmp_path, run, now=NOW, clock=lambda: next(ticks), budget_s=2500)
    scen = json.loads(next((tmp_path / "beta_records").glob("scenarios-*.json")).read_text(encoding="utf-8"))
    assert [s["result"] for s in scen["scenarios"]] == ["PASS", "PASS", "SKIPPED", "SKIPPED", "SKIPPED"]
    assert len(run.calls) == 3 and not list((tmp_path / "beta_records").glob("demo-*"))
    assert bd.STEP_TIMEOUT_S == 300


def test_output_tails_are_redacted():
    tail = bd._tail("key sk-abcdefghijklmnop at C:\\Users\\noa\\x and /home/noa/y mail noa@example.com")
    assert "sk-abcdefghijklmnop" not in tail[0] and "noa" not in tail[0].replace("<user>", "")
    assert "withheld" not in tail[0] and "<user>" in tail[0] and "<email>" in tail[0]  # redaction really ran


def test_source_demos_without_pytest_are_skipped(tmp_path):
    home, base = tmp_path, runner()
    bd.run_daily(home, lambda argv: (1, "No module named pytest") if "-q" in argv else base(argv), now=NOW)
    results = {json.loads(p.read_text(encoding="utf-8"))["ticket"]: json.loads(p.read_text(encoding="utf-8"))["result"]
               for p in (home / "beta_records").glob("demo-*.json")}
    assert results == {"JAR-214": "SKIPPED", "JAR-320": "SKIPPED", "JAR-321": "PASS"}


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
    import ast
    for _ticket, target, argv in bd.DEMOS:
        if target != "source-tests":
            continue
        path, *names = argv[-1].split("::")
        body = ast.parse((bd.CORE / path).read_text(encoding="utf-8")).body  # parsed, never executed
        for name in names:
            node = next((n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == name), None)
            assert node is not None, f"{path}: {name} missing"
            body = node.body


def test_stale_install_and_old_demos_fail_the_check(tmp_path):
    bd.run_daily(tmp_path, runner(), now=NOW)
    report = bd.check(tmp_path, now=NOW + 27 * 3600)
    assert report["ok"] is False and report["newest_beta_installed_age_h"] == 27.0 and report["demos_24h_with_image"] == 0


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


def test_prompting_command_fails_at_once_instead_of_waiting_for_the_timeout():
    import subprocess
    # The caller's stdin is an open pipe nobody writes to (like an unattended task). Without stdin=DEVNULL the
    # prompting step inherits it, hangs for the whole step timeout and is recorded as rc 124.
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import beta_daily as bd; "
            "print(bd.real_runner(timeout=5)([sys.executable, '-c', 'input()'])[0])")
    child = subprocess.Popen([sys.executable, "-c", code, str(bd.CORE / "tools")], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        out = child.stdout.read()
    finally:
        child.stdin.close()
        child.wait(timeout=30)
    assert out.strip() not in ("0", "124")


def test_launcher_is_resolved_on_path_like_a_windows_cmd_shim(monkeypatch):
    monkeypatch.setattr(bd.shutil, "which", lambda name: sys.executable if name == "jarvis.cmd" else None)
    rc, out = bd.real_runner("jarvis.cmd")(["jarvis", "-c", "print('shim ok')"])
    assert rc == 0 and "shim ok" in out


def test_offline_or_unparsable_update_check_is_not_newest(tmp_path):
    # `update check --json` exits 1 with "error" when offline; a stray text line is not trusted either (R3)
    for rc, out in ((1, update_line(error="network unreachable")), (0, update_line(error="cached")),
                    (0, update_line(available=True)), (0, "JARVES 1.0\nUp to date (checked now)."), (0, "")):
        home, calls = tmp_path / str(len(list(tmp_path.iterdir()))), []
        bd.run_daily(home, lambda argv, r=(rc, out): calls.append(argv) or r, now=NOW)
        rec = json.loads(next((home / "beta_records").glob("install-*.json")).read_text(encoding="utf-8"))
        assert len(calls) == 1 and rec["ok"] is False and rec["rc"] == rc  # recorded, no scenarios run
        report = bd.check(home, now=NOW)
        assert report["newest_beta_installed_age_h"] is None and report["scenario_pass_pct"] is None


def test_future_dated_install_record_is_ignored(tmp_path):
    bd.run_daily(tmp_path, runner(), now=NOW - 30 * 3600)
    rec = tmp_path / "beta_records" / "install-future.json"
    rec.write_text(json.dumps({"at": NOW + 3600, "ok": True}), encoding="utf-8")
    assert bd.check(tmp_path, now=NOW)["newest_beta_installed_age_h"] == 30.0


def test_cli_update_check_json(tmp_path, monkeypatch, capsys):
    from jarves import cli, updates
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    for st, code in (({"available": False, "error": None}, 0), ({"available": False, "error": "offline"}, 1),
                     ({"available": True, "error": None, "latest": {"version": "9.9"}}, 0)):
        monkeypatch.setattr(updates.UpdateChecker, "status", lambda self, st=st: {"checked_at": "now", **st})
        assert cli.main(["update", "status", "--json"]) == code
        line = json.loads(capsys.readouterr().out.strip())
        assert (line["available"], line["error"]) == (st["available"], st["error"])
        assert bd._newest(json.dumps(line)) is (code == 0 and not st["available"])
    # the exact call beta_daily makes: `update check --json` through the real UpdateChecker.check branch (R2)
    from jarves import features
    monkeypatch.setattr(features.Features, "enabled", lambda self, name: True)
    for st, code in (({"available": False, "error": "network unreachable"}, 1), ({"available": False, "error": None}, 0)):
        monkeypatch.setattr(updates.UpdateChecker, "check", lambda self, force=False, st=st: {"checked_at": "now", **st})
        assert cli.main(["update", "check", "--json"]) == code
        line = json.loads(capsys.readouterr().out.strip())
        assert (line["available"], line["error"]) == (False, st["error"]) and bd._newest(json.dumps(line)) is (code == 0)
    for action in (["install"], ["auto", "status"]):  # --json is never silently ignored (R4)
        assert cli.main(["update", *action, "--json"]) == 2
