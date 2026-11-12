"""JAR-232: the daily installed-beta check. Off by default (owner switch); a failing check becomes a local P1/P2 bug
draft with redacted evidence and a short demo transcript; nothing is sent anywhere."""
import json

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
    assert all(b["evidence"].endswith("demo.txt") and b["local_only"] for b in bugs) and not res["ok"]


def test_missing_report_is_a_p1_and_green_run_has_no_bugs(home):
    deps, _ = _deps(home, report=None, fail=())
    assert [b["priority"] for b in qa_daily.check(deps)["bugs"]] == ["P1"]
    ok = REPORT.replace("FAIL", "PASS")
    deps, _ = _deps(home, report=ok, fail=())
    assert qa_daily.check(deps) == {"ok": True, "bugs": [], "dir": str(home / "qa" / "daily" / "2026-11-12")}


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
