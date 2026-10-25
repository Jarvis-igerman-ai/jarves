"""V4.5 workstream C: recurring security scans. Stub GitHub client, fake clock, no network, no real issue creation, no
sleeping. Covers dedupe, timezone / DST, offline, failure / retry, quiet hours and budget, auto_fix refusal, and that a
synthetic finding creates exactly ONE non-sensitive issue."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jarves import cli, security_scans as ss

UTC = timezone.utc
VER = "4.5.0-test"
ISSUE_URL = f"https://github.com/{ss.REPO}/issues/901"
SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
T0 = datetime(2026, 10, 25, 5, 0, tzinfo=UTC)


class FakeGitHub:
    """Records every call; serves canned alert lists and workflow runs; keeps issues in memory."""

    def __init__(self, code_scanning=(), dependabot=(), secrets=(), checks="success", nightly="success", codeql="success",
                 access=True):
        self.code_scanning, self.dependabot, self.secrets = list(code_scanning), list(dependabot), list(secrets)
        self.runs = {"jarves-checks.yml": checks, "nightly.yml": nightly, "codeql.yml": codeql}
        self.access, self.issues, self.calls, self.fail = access, [], [], None

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if self.fail:
            raise self.fail
        if method == "GET":
            if "/code-scanning/alerts" in path:
                return (200, self.code_scanning) if self.access else (403, None)
            if "/dependabot/alerts" in path:
                return (200, self.dependabot) if self.access else (403, None)
            if "/secret-scanning/alerts" in path:
                return (200, self.secrets) if self.access else (403, None)
            if "/actions/workflows/" in path:
                wf = path.split("/actions/workflows/")[1].split("/")[0]
                return 200, {"workflow_runs": [{"conclusion": self.runs[wf], "created_at": "2026-10-23T00:00:00Z"}]}
            if path.startswith("/issues?"):
                return 200, [dict(i) for i in self.issues]
        if method == "POST" and path == "/issues":
            self.issues.append({"number": 901, "state": "open", "html_url": ISSUE_URL, "body": payload["body"],
                                "title": payload["title"], "labels": payload["labels"]})
            return 201, dict(self.issues[-1])
        if method == "PATCH" and path.startswith("/issues/"):
            self.issues[0]["body"] = payload["body"]
            return 200, dict(self.issues[0])
        return 404, None

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


def alert(n, rule="py/path-injection", path="core/src/jarves/files.py", sev="high"):
    return {"number": n, "rule": {"id": rule, "security_severity_level": sev},
            "most_recent_instance": {"location": {"path": path, "start_line": 10}, "message": {"text": f"secret {SECRET}"}}}


def scan(home, gh, kind="light", now=None, **kw):
    kw.setdefault("report", True)
    return ss.run_scan(home, kind, client=gh, now=now or T0, version=VER, sleep=lambda s: None, **kw)


def day(n):
    return T0 + timedelta(days=n)


# ====================================================================== the synthetic finding -> exactly one issue
def test_a_synthetic_finding_creates_exactly_one_non_sensitive_issue(home):
    gh = FakeGitHub(code_scanning=[alert(1), alert(2, rule="py/redos", path="core/src/jarves/x.py", sev="medium")],
                    secrets=[{"number": 5, "secret_type": "github_personal_access_token", "secret": SECRET,
                              "locations_url": "https://api.github.com/x"}])
    r = scan(home, gh)
    assert r["status"] == "ok" and r["issue"]["status"] == "filed" and r["llm_calls"] == 0
    assert len(gh.issues) == 1 and [c[0] for c in gh.writes()] == ["POST"]
    body = json.dumps(gh.issues[0])
    assert SECRET not in body and "A1b2C3d4" not in body and "locations_url" not in body
    assert "py/path-injection" in body and "github_personal_access_token" in body and ss.TRACKER_MARKER in body
    assert gh.issues[0]["labels"] == [ss.LABEL]
    assert {c[0] for c in gh.calls} <= {"GET", "POST"}                     # never a DELETE / dismissal / merge


def test_a_second_scan_with_the_same_findings_writes_nothing_and_a_new_one_updates_the_same_issue(home):
    gh = FakeGitHub(code_scanning=[alert(1)])
    scan(home, gh)
    n = len(gh.writes())
    again = scan(home, gh, now=day(1))
    assert again["issue"]["status"] == "unchanged" and len(gh.writes()) == n and len(gh.issues) == 1
    gh.code_scanning.append(alert(2, rule="py/xss"))
    third = scan(home, gh, now=day(2))
    assert third["issue"]["status"] == "updated" and len(gh.issues) == 1
    assert [c[0] for c in gh.writes()] == ["POST", "PATCH"] and "py/xss" in gh.issues[0]["body"]


def test_unchanged_is_decided_by_the_issue_itself_when_there_is_no_local_state():       # the hosted run keeps no state
    gh = FakeGitHub(code_scanning=[alert(1)])
    scan(None, gh)
    n = len(gh.writes())
    assert scan(None, gh)["issue"]["status"] == "unchanged" and len(gh.writes()) == n


def test_a_clean_scan_creates_no_issue_and_dry_run_never_writes(home):
    gh = FakeGitHub()
    assert scan(home, gh)["issue"]["status"] == "no_finding" and gh.issues == []
    gh2 = FakeGitHub(code_scanning=[alert(1)])
    r = scan(home, gh2, dry_run=True)
    assert r["issue"]["status"] == "dry_run" and gh2.writes() == []
    gh3 = FakeGitHub(code_scanning=[alert(1)])
    assert scan(home, gh3, report=False)["issue"]["status"] == "not_requested" and gh3.writes() == []


# ====================================================================== dedupe
def test_findings_are_deduplicated_by_fingerprint_rule_location_and_version(home):
    gh = FakeGitHub(code_scanning=[alert(1), alert(2), alert(3, path="core/src/jarves/other.py")])
    r = scan(home, gh)
    assert len(r["findings"]) == 2                                          # same rule + same file = one finding
    a = ss.finding("codeql", "py/x", "a.py", "P1", "1.0")
    assert a["fp"] != ss.finding("codeql", "py/x", "a.py", "P1", "1.1")["fp"]       # a new version is a new fingerprint
    assert a["fp"] != ss.finding("codeql", "py/y", "a.py", "P1", "1.0")["fp"]
    assert a["fp"] != ss.finding("codeql", "py/x", "b.py", "P1", "1.0")["fp"]
    assert a["fp"] == ss.finding("codeql", "py/x", "a.py", "P3", "1.0", number=4)["fp"]


def test_two_trackers_or_a_closed_one_never_get_a_new_issue(home):
    body = f"x {ss.TRACKER_MARKER}"
    gh = FakeGitHub(code_scanning=[alert(1)])
    gh.issues = [{"number": 1, "state": "open", "body": body, "html_url": ISSUE_URL},
                 {"number": 2, "state": "open", "body": body, "html_url": ISSUE_URL}]
    assert scan(home, gh)["issue"]["status"] == "duplicate_trackers" and gh.writes() == []
    gh2 = FakeGitHub(code_scanning=[alert(1)])
    gh2.issues = [{"number": 1, "state": "closed", "body": body, "html_url": ISSUE_URL}]
    assert scan(home, gh2)["issue"]["status"] == "tracker_closed" and gh2.writes() == []


# ====================================================================== privacy
def test_unsafe_fields_are_dropped_not_published(home):
    bad = [alert(1, rule="has space and /etc/passwd"), alert(2, path="C:\\Users\\me\\secret.py"),
           alert(3, rule=SECRET), alert(4, path="x@y.com")]
    gh = FakeGitHub(code_scanning=bad)
    assert scan(home, gh)["findings"] == []
    assert ss.finding("codeql", "py/ok", "a" * 40 + ".py", "P1", "1") is not None
    assert ss.finding("secret_scanning", "tok", SECRET, "P1", "1") is None


# ====================================================================== coverage and resolved
def test_no_access_is_incomplete_coverage_never_zero(home):
    gh = FakeGitHub(access=False)
    r = scan(home, gh)
    assert r["status"] == "ok" and not ss._complete(r["coverage"])
    assert r["coverage"]["code_scanning"].startswith("incomplete") and r["issue"]["status"] == "no_finding"
    gh.issues = [{"number": 1, "state": "open", "body": f"old {ss.TRACKER_MARKER}", "html_url": ISSUE_URL}]
    scan(home, gh, now=day(1))
    assert "INCOMPLETE" in gh.issues[0]["body"] and "No open findings" in gh.issues[0]["body"]
    assert "never read as zero" in gh.issues[0]["body"]


def test_resolved_only_when_the_source_was_read_completely(home):
    gh = FakeGitHub(code_scanning=[alert(1)])
    scan(home, gh)
    gh.code_scanning, gh.access = [], False                                 # alert gone from the list, but unreadable
    assert scan(home, gh, now=day(1))["resolved"] == 0
    h = FakeGitHub(code_scanning=[alert(1)])
    scan(home, h, now=day(3))
    h.code_scanning = []
    assert scan(home, h, now=day(4))["resolved"] == 1                       # read completely and really absent


def test_main_red_and_failed_nightly_are_findings_and_full_adds_codeql_and_boundary(home):
    gh = FakeGitHub(checks="failure", nightly="failure", codeql="failure")
    light = scan(home, gh)
    assert {f["rule"] for f in light["findings"]} == {"main_red", "nightly_failed"}
    full = scan(home, gh, kind="full", boundary=lambda: {"failed": 1, "failed_files": ["test_privacy.py"]}, now=day(1))
    assert {"codeql_run_failed", "boundary_test_failed"} <= {f["rule"] for f in full["findings"]}
    assert "boundary" not in light["coverage"] and full["coverage"]["boundary"] == "complete"
    nb = scan(home, FakeGitHub(), kind="full", now=day(2), boundary=lambda: {"not_run": True})
    assert nb["coverage"]["boundary"].startswith("incomplete")


def test_recurring_local_diagnostics_count_only_when_repeated(home):
    from jarves import issue_registry as ir
    reg = ir.Registry(home, version=VER)
    reg.record("tools", "TOOL_X", "P2", {"n": 1}, n=5)
    reg.record("tools", "TOOL_Y", "P2", {"n": 1}, n=1)
    r = scan(home, FakeGitHub())
    assert [f["rule"] for f in r["findings"]] == ["TOOL_X"]


# ====================================================================== offline, failure, retry
def test_offline_writes_nothing_records_a_failure_and_retries_with_backoff(home):
    gh = FakeGitHub()
    gh.fail = ss.Offline("URLError")
    r = scan(home, gh, now=T0)
    assert r["status"] == "offline" and gh.writes() == []
    st = ss.load_state(home)
    assert st["fail_count"] == 1 and st["last_success"] == {} and st["retry_at"] == T0.timestamp() + 1800
    scan(home, gh, now=T0 + timedelta(hours=1))
    scan(home, gh, now=T0 + timedelta(hours=4))
    st = ss.load_state(home)
    assert st["fail_count"] == 3 and st["retry_at"] == (T0 + timedelta(hours=4)).timestamp() + 21600      # capped
    gh.fail = None
    ok = scan(home, gh, now=T0 + timedelta(hours=20))
    assert ok["status"] == "ok" and ss.load_state(home)["fail_count"] == 0 and ss.load_state(home)["retry_at"] == 0


def test_transient_http_errors_are_retried_a_bounded_number_of_times(home):
    seen = {"n": 0}

    class Flaky(FakeGitHub):
        def __call__(self, method, path, payload=None):
            if "/code-scanning/" in path:
                seen["n"] += 1
                if seen["n"] < 3:
                    return 503, None
            return super().__call__(method, path, payload)

    sleeps = []
    r = ss.run_scan(home, "light", client=Flaky(code_scanning=[alert(1)]), version=VER, report=False, now=T0,
                    sleep=sleeps.append)
    assert r["status"] == "ok" and seen["n"] == 3 and sleeps == [1, 2] and len(r["findings"]) == 1


def test_an_unexpected_error_is_a_failed_run_not_a_crash(home):
    class Boom(FakeGitHub):
        def __call__(self, *a, **k):
            raise RuntimeError("boom with /private/path")

    r = scan(home, Boom())
    assert r["status"] == "failed" and ss.load_state(home)["last_failure"]["reason"] == "RuntimeError"


def test_a_report_that_cannot_be_written_is_retried_and_keeps_the_old_signature(home):
    class NoWrite(FakeGitHub):
        def __call__(self, method, path, payload=None):
            if method == "POST":
                return 500, None
            return super().__call__(method, path, payload)

    r = scan(home, NoWrite(code_scanning=[alert(1)]))
    assert r["status"] == "report_failed" and ss.load_state(home)["signature"] == "" and ss.load_state(home)["fail_count"] == 1


# ====================================================================== schedule: time zone / DST
def jer(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Jerusalem")).astimezone(UTC)


def test_slots_follow_the_wall_clock_across_the_israel_dst_change(home):
    cfg = ss.load_config(home)
    tz = ZoneInfo(cfg["tz"])
    got = ss.slots(cfg, datetime(2026, 10, 20, tzinfo=UTC), datetime(2026, 10, 30, tzinfo=UTC))
    local = [(s.astimezone(tz).strftime("%a %H:%M"), k) for s, k in got]
    assert all(t.endswith("07:30") or t.endswith("08:00") for t, _ in local)           # same wall time on both sides of 25 Oct
    assert [t for t, k in local if k == "full"] == ["Sun 08:00"]
    assert ss.local_to_utc(tz, datetime(2026, 10, 24).date(), "07:30").hour == 4       # IDT = UTC+3
    assert ss.local_to_utc(tz, datetime(2026, 10, 26).date(), "07:30").hour == 5       # IST = UTC+2 after 25 Oct


def test_a_time_in_the_spring_gap_runs_at_the_first_valid_minute_and_a_repeated_one_once():
    tz = ZoneInfo("America/New_York")
    gap = ss.local_to_utc(tz, datetime(2026, 3, 8).date(), "02:30")                    # 02:30 does not exist
    assert gap.astimezone(tz).strftime("%H:%M") == "03:30"
    twice = ss.local_to_utc(tz, datetime(2026, 11, 1).date(), "01:30")                 # 01:30 happens twice
    assert twice.hour == 5                                                              # first occurrence (EDT = UTC-4)


def test_three_days_a_week_and_the_actual_next_execution(home):
    ss.save_config(home, enabled=True, light_days=[0, 2, 4], full_days=[6], tz="Asia/Jerusalem")
    cfg, st = ss.load_config(home), ss.load_state(home)
    now = jer(2026, 10, 21, 12)                                                         # Wednesday midday, 07:30 slot passed
    st["last_success"] = {"light": now.timestamp()}
    p = ss.plan(cfg, st, now)
    assert p["kind"] == "light" and p["when"] == jer(2026, 10, 23, 7, 30) and not p["due"]      # Friday
    ss.save_state(home, st)
    assert ss.status(home, now)["next_run_local"].startswith("2026-10-23 07:30")


def test_catch_up_is_one_run_and_a_full_success_covers_the_light_slot(home):
    ss.save_config(home, enabled=True)
    cfg, st = ss.load_config(home), ss.load_state(home)
    now = jer(2026, 10, 24, 12)                                                         # Saturday; PC was off for days
    p = ss.plan(cfg, st, now)
    assert p["kind"] == "light" and p["due"] and p["why"] == "catch_up"
    st["last_success"] = {"full": jer(2026, 10, 25, 8, 5).timestamp()}
    assert ss.due_slot(cfg, st, jer(2026, 10, 25, 9)) is None                           # Sunday full covered
    assert ss.due_slot(cfg, st, jer(2026, 10, 26, 8)) is not None                       # Monday light is due


def test_quiet_hours_and_the_daily_budget_defer_the_run(home):
    ss.save_config(home, enabled=True, light_time="23:00", quiet="22:00-07:00", max_runs_per_day=1)
    cfg, st = ss.load_config(home), ss.load_state(home)
    now = jer(2026, 10, 21, 23, 30)                                                     # slot 23:00 is due but quiet
    p = ss.plan(cfg, st, now)
    assert p["due"] is False and p["when"] == jer(2026, 10, 22, 7, 0) and p["why"] == "quiet_hours"
    assert ss._in_quiet(cfg, jer(2026, 10, 21, 12)) is None and ss._in_quiet(cfg, jer(2026, 10, 21, 3)) == jer(2026, 10, 21, 7)
    ss.save_config(home, quiet="")
    cfg = ss.load_config(home)
    st["history"] = [{"t": jer(2026, 10, 21, 23, 40).timestamp(), "trigger": "auto"}]
    p = ss.plan(cfg, st, jer(2026, 10, 21, 23, 45))
    assert p["why"] == "budget" and p["when"] == jer(2026, 10, 22, 0)


def test_run_if_due_respects_off_waiting_and_runs_one_scan_then_waits(home):
    now = jer(2026, 10, 21, 9)
    gh = FakeGitHub()
    assert ss.run_if_due(home, now=now, client=gh, version=VER)["status"] == "off" and gh.calls == []
    ss.save_config(home, enabled=True)
    r = ss.run_if_due(home, now=now, client=gh, version=VER, sleep=lambda s: None)
    assert r["status"] == "ok" and ss.load_state(home)["history"][-1]["trigger"] == "auto"
    calls = len(gh.calls)
    w = ss.run_if_due(home, now=now + timedelta(hours=1), client=gh, version=VER)
    assert w["status"] == "waiting" and len(gh.calls) == calls                           # nothing due: no network at all


def test_a_failed_auto_run_waits_for_its_backoff(home):
    ss.save_config(home, enabled=True)
    now = jer(2026, 10, 21, 9)
    gh = FakeGitHub()
    gh.fail = ss.Offline("x")
    assert ss.run_if_due(home, now=now, client=gh, version=VER, sleep=lambda s: None)["status"] == "offline"
    calls = len(gh.calls)
    assert ss.run_if_due(home, now=now + timedelta(minutes=10), client=gh, version=VER)["why"] == "retry_backoff"
    assert len(gh.calls) == calls
    gh.fail = None
    assert ss.run_if_due(home, now=now + timedelta(minutes=31), client=gh, version=VER, sleep=lambda s: None)["status"] == "ok"


# ====================================================================== settings: validation and auto_fix
def test_auto_fix_cannot_be_turned_on_and_bad_settings_are_refused(home):
    assert ss.load_config(home)["auto_fix"] is False and ss.load_config(home)["enabled"] is False
    with pytest.raises(ValueError):
        ss.save_config(home, auto_fix=True)
    (home / ss.CONFIG_FILE).write_text(json.dumps({"auto_fix": True, "tz": "Nowhere/Land", "light_time": "25:99"}))
    c = ss.load_config(home)
    assert c["auto_fix"] is False and c["tz"] == "Asia/Jerusalem" and c["light_time"] == "07:30"
    for bad in ({"tz": "Mars/Base"}, {"light_time": "7:30"}, {"quiet": "late"}, {"light_days": [9]}, {"max_runs_per_day": 0}):
        with pytest.raises(ValueError):
            ss.save_config(home, **bad)


# ====================================================================== CLI
def test_cli_status_on_off_set_and_run_dry(home, capsys, monkeypatch):
    assert cli.main(["scans", "status"]) == 0 and "OFF" in capsys.readouterr().out
    assert cli.main(["scans", "on"]) == 0 and ss.load_config(home)["enabled"]
    assert cli.main(["scans", "set", "--light-days", "mon,wed,fri", "--full-days", "sat", "--quiet", "23:00-06:00",
                     "--budget", "3", "--report", "on"]) == 0
    c = ss.load_config(home)
    assert c["light_days"] == [0, 2, 4] and c["full_days"] == [5] and c["quiet"] == "23:00-06:00" and c["max_runs_per_day"] == 3
    out = capsys.readouterr().out
    assert "mon,wed,fri" in out and "next:" in out and "auto_fix off" in out
    assert cli.main(["scans", "set", "--auto-fix", "on"]) == 2 and not ss.load_config(home)["auto_fix"]
    assert cli.main(["scans", "set", "--tz", "Mars/Base"]) == 2
    gh = FakeGitHub(code_scanning=[alert(1)])
    monkeypatch.setattr(ss, "find_token", lambda env_only=False: "t")
    monkeypatch.setattr(ss, "http_client", lambda token, repo=ss.REPO: gh)
    capsys.readouterr()
    assert cli.main(["scans", "run", "--dry-run", "on", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"] == 1 and gh.writes() == []
    assert cli.main(["scans", "history"]) == 0 and "light" in capsys.readouterr().out
    assert cli.main(["scans", "off"]) == 0 and not ss.load_config(home)["enabled"]


def test_cli_run_offline_exits_1_without_a_token(home, monkeypatch, capsys):
    monkeypatch.setattr(ss, "find_token", lambda env_only=False: "")
    assert cli.main(["scans", "run"]) == 1 and "offline" in capsys.readouterr().out


# ====================================================================== the hosted workflow and tool
def test_the_hosted_workflow_is_scheduled_only_read_mostly_and_calls_the_tool():
    text = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "security-scans.yml").read_text(encoding="utf-8")
    on = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "schedule:" in on and "workflow_dispatch:" in on and "push:" not in on and "pull_request" not in on
    assert "tools/security_scans.py" in text and "timeout-minutes:" in text
    perms = text.split("\npermissions:\n", 1)[1].split("\nconcurrency:", 1)[0]
    assert "contents: read" in perms and "write" not in perms.replace("issues: write", "")
    assert len(re.findall(r"- cron:", text)) == 2


def test_the_hosted_tool_refuses_another_repository_and_needs_a_token(monkeypatch, capsys):
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "tools" / "security_scans.py"
    spec = importlib.util.spec_from_file_location("security_scans_tool", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--kind", "light", "--repo", "someone/else"]) == 2
    for k in ("JARVES_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert mod.main(["--kind", "light"]) == 1
    gh = FakeGitHub(code_scanning=[alert(1)])
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(ss, "http_client", lambda token, repo=ss.REPO: gh)
    assert mod.main(["--kind", "light", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"] == 1 and gh.writes() == []


def test_codeql_44_state_and_settings_files_never_store_a_planted_secret(tmp_path):
    """CodeQL #44 (py/clear-text-storage-sensitive-data, security_scans._write): the sink redacts. Fails if _scrub is removed."""
    st = ss.load_state(tmp_path)
    st["tracker_url"] = f"https://x.test/?t={SECRET}"
    st["last_failure"] = {"reason": SECRET, SECRET: 1}
    st["history"].append({"nested": [f"Bearer {SECRET}"]})
    ss.save_state(tmp_path, st)
    ss.save_config(tmp_path, tz="Asia/Jerusalem")
    for name in (ss.STATE_FILE, ss.CONFIG_FILE):
        blob = (tmp_path / name).read_text(encoding="utf-8")
        assert SECRET not in blob and "A1b2C3d4E5f6G7h8" not in blob
    assert "tracker_url" in (tmp_path / ss.STATE_FILE).read_text(encoding="utf-8")
