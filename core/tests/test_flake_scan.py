"""Flake quarantine (owner rule 24 Oct 2026, #160): the ledger, the CI wiring and the run-history scanner."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

import flake_scan as fs
from conftest import QUARANTINE_LEDGER, quarantined_ids

CORE = Path(__file__).resolve().parents[1]
CHECKS = CORE.parent / ".github" / "workflows" / "jarves-checks.yml"

LOG = (
    "Python Core tests (windows-2025 shard 2/3)\tUNKNOWN STEP\t2026-10-24T18:32:30.4711722Z ===== FAILURES =====\n"
    "Python Core tests (windows-2025 shard 2/3)\tUNKNOWN STEP\t2026-10-24T18:32:30.4712902Z _____ "
    "test_slow_probe[win] _____\n"
    "Python Core tests (windows-2025 shard 2/3)\tUNKNOWN STEP\t2026-10-24T18:32:30.4758329Z E   AssertionError: 168.4\n"
    "Python Core tests (windows-2025 shard 2/3)\tUNKNOWN STEP\t2026-10-24T18:32:30.4758966Z "
    "tests\\test_perf_probes.py:115: AssertionError\n"
    "x\ty\t2026-10-24T18:32:31Z ______________ TestBox.test_other ______________\n"
    "x\ty\t2026-10-24T18:32:31Z tests/test_box.py:9: KeyError\n"
)
HANG = ('a\tb\t2026-10-23T19:31:02Z   File "/home/runner/work/jarves/jarves/core/tests/test_web_chat.py", line 304 in '
        "test_new_resets_the_model\n")


def test_failed_tests_reads_header_and_location_lines():
    got = fs.failed_tests(LOG)
    assert set(got) == {"test_perf_probes.py::test_slow_probe", "test_box.py::test_other"}
    assert "AssertionError" in got["test_perf_probes.py::test_slow_probe"]


def test_a_hang_is_named_by_the_test_frame():
    assert fs.failed_tests(HANG) == {"test_web_chat.py::test_new_resets_the_model": "hung: pytest-timeout stack dump"}


def test_only_a_failure_whose_commit_later_passed_is_a_flake():
    runs = [{"id": 1, "head_sha": "a", "conclusion": "failure"},     # a later green run on the same commit
            {"id": 2, "head_sha": "a", "conclusion": "success"},
            {"id": 3, "head_sha": "b", "conclusion": "failure"},     # never green: a real failure, not a flake
            {"id": 4, "head_sha": "c", "conclusion": "success", "run_attempt": 2},   # re-run turned green
            {"id": 5, "head_sha": "d", "conclusion": "success", "run_attempt": 1}]
    assert [(r["id"], why.split()[0]) for r, why in fs.flaky_runs(runs)] == [(1, "same"), (4, "re-run")]


def test_issue_body_carries_the_ledger_entry_and_the_suspect_pr():
    body = fs.issue_body("test_x.py::test_y", [{"run": 9, "branch": "b", "prs": [150], "why": "re-run attempt 2 green",
                                                 "line": "tests/test_x.py:3: AssertionError"}])
    assert 'id = "test_x.py::test_y"' in body and "#150" in body and "quarantine.toml" in body


def test_every_ledger_entry_names_a_real_test_and_an_issue():
    ids = quarantined_ids()
    assert ids == {k: v for k, v in ids.items() if v > 0}
    for tid in ids:
        file_, name = tid.split("::")
        src = (CORE / "tests" / file_).read_text(encoding="utf-8")
        assert re.search(rf"def {re.escape(name)}\(", src), f"{tid}: no such test (fixed? delete the ledger entry)"
    assert fs.ledger_ids() == set(ids)
    assert QUARANTINE_LEDGER.name == "quarantine.toml"


def test_ledger_files_lists_each_quarantined_file_once(capsys):
    assert fs.main(["--ledger-files"]) == 0
    printed = capsys.readouterr().out.split()
    assert printed == sorted({f"tests/{t.split('::')[0]}" for t in quarantined_ids()})


def test_ci_deselects_quarantined_tests_and_still_runs_them_non_blocking():
    jobs = yaml.safe_load(CHECKS.read_text(encoding="utf-8"))["jobs"]
    steps = {s.get("name", ""): s for s in jobs["core"]["steps"]}
    blocking = next(s for n, s in steps.items() if n.startswith("pytest (offline; planned shard"))
    assert blocking["run"].count('-m "not quarantine"') == 2           # first run and the known-flake retry
    q = next(s for n, s in steps.items() if n.startswith("Quarantined tests"))
    assert q.get("continue-on-error") is True and "-m quarantine" in q["run"]
    shards = {m["shard"] for m in jobs["core"]["strategy"]["matrix"]["include"]}
    cond = re.search(r"startsWith\(matrix\.shard, '([^']+)'\)", q["if"])
    assert cond and all(any(s.startswith(cond.group(1)) for s in shards if s.endswith(n)) for n in ("/3", "/4")), \
        "the quarantine step must match shard 1 of each OS, or it never runs"
    browser = next(s for s in jobs["browser"]["steps"] if s.get("name", "").startswith("pytest real-browser"))
    assert '-m "not quarantine"' in browser["run"]


def test_failures_before_the_last_fix_or_in_the_dst_hour_are_not_reported():
    hit = lambda t: {"run": 1, "started": t}                                   # noqa: E731
    found = {"a.py::test_old": [hit("2026-10-22T13:45:00Z")], "a.py::test_new": [hit("2026-10-25T10:00:00Z")],
             "b.py::test_dst": [hit("2026-10-24T23:30:00Z")]}
    kept = fs.drop_stale(found, lambda t: "2026-10-23T00:00:00Z")
    assert list(kept) == ["a.py::test_new"]
