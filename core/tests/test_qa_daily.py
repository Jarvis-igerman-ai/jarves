"""JAR-232: the daily installed-beta check. Off by default (owner switch); a failing check becomes a local P1/P2 bug
draft with redacted evidence and a short demo transcript; nothing is sent anywhere."""
import json
import os
import types
from datetime import datetime, timezone

import pytest

from jarves import qa_daily
from jarves.config import Config
from jarves.features import CATALOG, Features, stage_of

REPORT = ("FAIL  install-latest  2026-11-12 06:00 UTC\nversion: 4.5.0 -> 4.5.1-beta.3\n"
          "  [PASS] version\n  [FAIL] doctor: mic missing, mail me at someone@example.com\n  [PASS] selftest")


def _deps(home, report=REPORT, fail=("health",)):
    calls = []

    def run_cli(argv):
        calls.append(argv)
        return (1, "boom C:\\Users\\igal\\x token=abc123") if argv[0] in fail else (0, "ok")
    return qa_daily.Deps(home=home, report=lambda: report, run_cli=run_cli, day="2026-11-12"), calls


def test_feature_is_beta_and_off_by_default(home):
    f = CATALOG[qa_daily.FEATURE]
    assert qa_daily.FEATURE == "qa_installs_enabled" and f.default_enabled is False
    assert stage_of(qa_daily.FEATURE) == "beta" and not Features(Config(), home).enabled(qa_daily.FEATURE)


def test_off_switch_does_nothing(home, capsys):
    assert qa_daily.cmd_qa_daily(type("A", (), {"action": "check", "dry_run": False})(), Config(), home) == 2
    assert "qa_installs_enabled" in capsys.readouterr().out and not (home / "qa").exists()


def test_failures_become_p1_p2_bug_drafts_with_local_redacted_evidence(home):
    deps, calls = _deps(home)
    res = qa_daily.check(deps)
    assert [c[0] for c in calls] == [s[1][0] for s in qa_daily.SCENARIOS]
    bugs = json.loads((home / "qa" / "daily" / "2026-11-12" / "bugs.json").read_text(encoding="utf-8"))
    assert [(b["priority"], b["check"]) for b in bugs] == [("P1", "doctor"), ("P2", "health")]
    text = json.dumps(bugs) + (home / "qa" / "daily" / "2026-11-12" / "demo.txt").read_text(encoding="utf-8")
    assert "someone@example.com" not in text and "igal" not in text and "abc123" not in text
    assert all(b["evidence"] == "qa/daily/2026-11-12/demo.txt" and b["local_only"] for b in bugs) and not res["ok"]


def test_missing_report_is_a_p1_and_green_run_has_no_bugs(home):
    deps, _ = _deps(home, report=None, fail=())
    assert [b["priority"] for b in qa_daily.check(deps)["bugs"]] == ["P1"]
    ok = REPORT.replace("FAIL", "PASS")
    deps, _ = _deps(home, report=ok, fail=())
    assert qa_daily.check(deps) == {"ok": True, "bugs": [], "dir": "qa/daily/2026-11-12"}


def test_stale_pending_or_non_beta_report_is_never_a_pass(home):
    ok = REPORT.replace("FAIL", "PASS")
    cases = {"PENDING": ok.replace("PASS  install", "PENDING  install", 1),
             "DRY RUN": ok.replace("PASS  install", "DRY RUN  install", 1),
             "earlier day": ok.replace("2026-11-12", "2026-11-10", 1),
             "not a beta": ok.replace("4.5.1-beta.3", "4.5.1")}
    for want, report in cases.items():
        deps, _ = _deps(home, report=report, fail=())
        bugs = qa_daily.check(deps)["bugs"]
        assert [(b["priority"], b["check"]) for b in bugs] == [("P1", "install-latest")], want
        assert want in bugs[0]["detail"], want


def test_no_user_name_from_the_home_path_in_drafts_or_output(tmp_path, capsys):
    home = tmp_path / "Users" / "igal" / ".jarves"
    deps, _ = _deps(home)
    res = qa_daily.check(deps)
    saved = (home / "qa" / "daily" / "2026-11-12" / "bugs.json").read_text(encoding="utf-8")
    assert "igal" not in saved and "igal" not in json.dumps(res) and str(tmp_path) not in saved


def _on(home):
    f = Features(Config(), home)
    f.set_beta(True, by="test")
    f.set(qa_daily.FEATURE, True, by="test")


def _args(action, dry_run=False):
    return types.SimpleNamespace(action=action, dry_run=dry_run)


@pytest.mark.parametrize("dry", [False, True])
def test_install_forces_the_beta_channel_and_passes_dry_run(home, monkeypatch, dry):
    from jarves import install_latest
    seen = []
    monkeypatch.setattr(install_latest, "cmd_install_latest", lambda a, cfg, h: seen.append(a) or 0)
    assert qa_daily.cmd_qa_daily(_args("install", dry), Config(), home) == 2 and seen == []    # OFF: refused
    _on(home)
    assert qa_daily.cmd_qa_daily(_args("install", dry), Config(), home) == 0
    assert (seen[0].channel, seen[0].dry_run, seen[0].report, seen[0].finish) == ("beta", dry, False, None)


def test_check_and_status_commands_read_the_report_under_home(home, monkeypatch, capsys):
    from jarves import install_latest
    _on(home)
    monkeypatch.setattr(install_latest, "_cli_subprocess", lambda argv: (0, "ok"))
    monkeypatch.setattr(qa_daily, "_today", lambda: "2026-11-12")
    (home / "updates").mkdir()
    (home / "updates" / install_latest.REPORT_FILE).write_text(REPORT.replace("FAIL", "PASS"), encoding="utf-8")
    assert qa_daily.cmd_qa_daily(_args("check"), Config(), home) == 0
    assert "PASS  qa-daily" in capsys.readouterr().out
    assert json.loads((home / "qa" / "daily" / "2026-11-12" / "bugs.json").read_text(encoding="utf-8")) == []
    assert qa_daily.cmd_qa_daily(_args("status"), Config(), home) == 0
    assert "2026-11-12: PASS" in capsys.readouterr().out


def test_daily_tick_installs_once_then_checks_once_a_day(home):
    """JAR-232 review: the Core runs the routine itself once a day (install, then check), never twice, never when OFF."""
    state = {"on": False, "report": None, "now": 1000.0}
    spawned, checked = [], []
    tick = lambda: qa_daily.tick(home, enabled=lambda: state["on"], day=lambda: "2026-11-12",  # noqa: E731
                                 report=lambda _d, _s: state["report"], spawn=spawned.append,
                                 do_check=lambda _d: checked.append(1), clock=lambda: state["now"])
    assert tick() == "off" and spawned == [] and checked == []
    state["on"] = True
    assert tick() == "installing" and spawned == [["qa-daily", "install"]]
    assert tick() == "waiting" and len(spawned) == 1                      # setup still running: no second install
    state["report"] = REPORT.replace("FAIL", "PASS")
    assert tick() == "checked" and checked == [1]
    assert tick() == "done" and len(spawned) == 1 and checked == [1]


def test_daily_tick_checks_anyway_when_the_install_never_reports(home):
    state = {"now": 1000.0}
    checked = []
    tick = lambda: qa_daily.tick(home, enabled=lambda: True, day=lambda: "2026-11-12", report=lambda _d, _s: None,  # noqa: E731
                                 spawn=lambda argv: None, do_check=lambda _d: checked.append(1), clock=lambda: state["now"])
    assert tick() == "installing" and tick() == "waiting"
    state["now"] += qa_daily.INSTALL_WAIT_S + 1
    assert tick() == "checked" and checked == [1]                          # the missing report becomes a P1 draft


def test_daily_tick_installs_the_beta_even_after_an_earlier_install_today(home):
    """JAR-232 review: a stable install-latest finished earlier today must not skip the beta install (false P1)."""
    spawned, checked = [], []
    assert qa_daily.tick(home, enabled=lambda: True, day=lambda: "2026-11-12", report=lambda _d, _s: REPORT,
                         spawn=spawned.append, do_check=lambda _d: checked.append(1), clock=lambda: 1000.0) == "installing"
    assert spawned == [["qa-daily", "install"]] and checked == []


def test_up_to_date_or_early_stop_is_recorded_and_judged_not_lost(home, monkeypatch, capsys):
    """JAR-232 doctor: install-latest writes no report file when nothing newer exists or it stops early, so the
    qa install keeps its own record; an older (stable) report from today must not be judged instead."""
    from jarves import install_latest
    _on(home)
    monkeypatch.setattr(qa_daily, "_today", lambda: "2026-11-12")
    (home / "updates").mkdir()
    (home / "updates" / install_latest.REPORT_FILE).write_text(
        "PASS  install-latest  2026-11-12 01:00 UTC\nversion: 4.5.0 -> 4.5.1\n", encoding="utf-8")
    up = "PASS  install-latest  2026-11-12 06:00 UTC\n  [PASS] find newest beta installer\n  [PASS] up to date: nothing newer"
    monkeypatch.setattr(install_latest, "cmd_install_latest", lambda a, cfg, h: print(up) or 0)
    assert qa_daily.cmd_qa_daily(_args("install"), Config(), home) == 0
    deps = qa_daily.Deps(home, qa_daily._report_reader(home, "2026-11-12"),
                         lambda argv: (0, "4.5.1-beta.3" if argv[0] == "update" else "ok"), "2026-11-12")
    assert qa_daily.check(deps)["bugs"] == []
    stop = "FAIL  install-latest  2026-11-12 07:00 UTC\n  [FAIL] backup now: BACKUP_DISK_FULL"
    monkeypatch.setattr(install_latest, "cmd_install_latest", lambda a, cfg, h: print(stop) or 1)
    assert qa_daily.cmd_qa_daily(_args("install"), Config(), home) == 1
    assert [b["check"] for b in qa_daily.check(deps)["bugs"]] == ["backup now"]


def test_daily_tick_waits_for_a_quiet_moment_before_the_silent_install(home):
    spawned = []
    r = qa_daily.tick(home, enabled=lambda: True, day=lambda: "2026-11-12", report=lambda _d, _s: None, spawn=spawned.append,
                      do_check=lambda _d: None, clock=lambda: 1000.0, busy=lambda: ["voice_session"])
    assert r == "busy" and spawned == [] and not (home / "qa" / "daily" / "2026-11-12" / "install.started").exists()


def test_a_manual_check_does_not_cancel_the_daily_beta_install(home):
    """JAR-232 self-review: `qa-daily check` by hand before the Core's tick wrote bugs.json, and the tick then
    returned 'done' and skipped that day's beta install and its check."""
    day = home / "qa" / "daily" / "2026-11-12"
    day.mkdir(parents=True)
    (day / "bugs.json").write_text("[]", encoding="utf-8")
    spawned, checked = [], []
    tick = lambda rep: qa_daily.tick(home, enabled=lambda: True, day=lambda: "2026-11-12",  # noqa: E731
                                     report=lambda _d, _s: rep, spawn=spawned.append,
                                     do_check=lambda _d: checked.append(1), clock=lambda: 1000.0)
    assert tick(None) == "installing" and spawned == [["qa-daily", "install"]]
    assert tick(REPORT) == "checked" and checked == [1]
    assert tick(REPORT) == "done" and checked == [1] and len(spawned) == 1


def test_an_install_started_before_utc_midnight_is_checked_before_the_next_day_starts(home):
    """JAR-232 self-review: an install begun at 23:30 UTC was never checked, and the 00:30 tick started a second
    install while the first was still running (a false 'no other install-latest running' P1 for the new day)."""
    state = {"day": "2026-11-12", "report": None}
    spawned, checked = [], []
    tick = lambda: qa_daily.tick(home, enabled=lambda: True, day=lambda: state["day"],  # noqa: E731
                                 report=lambda d, _s: state["report"] if d == "2026-11-12" else None,
                                 spawn=spawned.append, do_check=checked.append, clock=lambda: 1000.0)
    assert tick() == "installing"
    state["day"] = "2026-11-13"                                           # midnight passes while setup runs
    assert tick() == "waiting" and len(spawned) == 1
    state["report"] = REPORT.replace("FAIL", "PASS")
    assert tick() == "checked" and checked == ["2026-11-12"]
    assert tick() == "installing" and len(spawned) == 2                   # only now the new day's install


def _write(path, text, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_an_earlier_report_in_the_same_minute_never_ends_the_day(home):
    """JAR-232 review: a PASS written earlier (even in the same UTC minute) was taken as the new install's result."""
    from jarves.install_latest import REPORT_FILE
    t0 = datetime(2026, 11, 12, 8, 0, 30, tzinfo=timezone.utc).timestamp()
    rp = home / "updates" / REPORT_FILE
    _write(rp, REPORT.replace("FAIL", "PASS").replace("06:00", "08:00"), t0 - 10)   # same minute, before our install
    checked = []
    tick = lambda: qa_daily.tick(home, enabled=lambda: True, day=lambda: "2026-11-12",  # noqa: E731
                                 report=lambda d, s: qa_daily._report_reader(home, d, s)(), spawn=lambda argv: None,
                                 do_check=checked.append, clock=lambda: t0)
    assert tick() == "installing" and tick() == "waiting" and checked == []
    _write(rp, REPORT.replace("06:00", "08:40"), t0 + 2400)                # the new install ends and fails
    assert tick() == "checked" and checked == ["2026-11-12"]


def test_manual_check_judges_the_newest_record_not_an_older_up_to_date_one(home):
    """JAR-232 review: install.txt 'up to date' at 06:00 hid the 09:00 FAIL report and `check` printed PASS."""
    from jarves.install_latest import REPORT_FILE
    t = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc).timestamp()
    _write(home / "qa" / "daily" / "2026-11-12" / qa_daily.INSTALL_FILE,
           "PASS  install-latest  2026-11-12 06:00 UTC\n  [PASS] up to date: nothing newer", t)
    _write(home / "updates" / REPORT_FILE, REPORT.replace("06:00", "09:00"), t + 3 * 3600)
    deps, _ = _deps(home, fail=())
    deps.report = qa_daily._report_reader(home, "2026-11-12")
    res = qa_daily.check(deps)
    assert not res["ok"] and ("P1", "doctor") in [(b["priority"], b["check"]) for b in res["bugs"]]


def test_hidden_spawn_reuses_the_cli_launcher_and_a_failed_start_leaves_evidence(home, monkeypatch):
    from jarves import install_latest
    seen = {}
    monkeypatch.setattr(qa_daily.subprocess, "Popen", lambda argv, **kw: seen.update(argv=argv, **kw))
    qa_daily._spawn_hidden(["qa-daily", "install"])
    flags = getattr(qa_daily.subprocess, "CREATE_NO_WINDOW", 0) | getattr(qa_daily.subprocess, "DETACHED_PROCESS", 0)
    assert seen["argv"] == install_latest.cli_argv(["qa-daily", "install"]) and seen["creationflags"] == flags

    def boom(argv):
        raise OSError("no python here")
    kw = dict(enabled=lambda: True, day=lambda: "2026-11-12", spawn=boom, clock=lambda: 1000.0,
              report=lambda d, s: qa_daily._report_reader(home, d, s)())
    drafts = []
    assert qa_daily.tick(home, do_check=lambda d: None, **kw) == "spawn_failed"
    deps, _ = _deps(home, fail=())
    deps.report = qa_daily._report_reader(home, "2026-11-12", 1000.0)
    assert qa_daily.tick(home, do_check=lambda d: drafts.extend(qa_daily.check(deps)["bugs"]), **kw) == "checked"
    assert [b["check"] for b in drafts] == ["start qa-daily install"] and "no python here" in drafts[0]["detail"]


def test_up_to_date_counts_only_when_a_beta_is_installed(home):
    """JAR-232 review: '[PASS] up to date' skipped the beta check, so a stable install passed the beta check."""
    up = "PASS  install-latest  2026-11-12 06:00 UTC\n  [PASS] up to date: nothing newer"
    for status, want in (("installed: 4.5.1", ["install-latest"]), ("installed: 4.5.1-beta.3", [])):
        deps, _ = _deps(home, report=up, fail=())
        deps.run_cli = lambda argv, s=status: (0, s if argv == ["update", "status"] else "ok")
        assert [b["check"] for b in qa_daily.check(deps)["bugs"]] == want


def test_up_to_date_still_runs_doctor_and_selftest_and_files_p1(home):
    """JAR-232 review: with nothing newer, setup ran no doctor/selftest, so the day passed without them."""
    up = "PASS  install-latest  2026-11-12 06:00 UTC\n  [PASS] find newest beta installer\n  [PASS] up to date: nothing newer"
    deps, calls = _deps(home, report=up, fail=("doctor",))
    run = deps.run_cli
    deps.run_cli = lambda argv: (0, "4.5.1-beta.3") if argv == ["update", "status"] else run(argv)
    res = qa_daily.check(deps)
    assert ["doctor"] in calls and ["selftest"] in calls
    assert [(b["priority"], b["check"]) for b in res["bugs"]] == [("P1", "doctor")]
