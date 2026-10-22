"""core/tools/ci_comment.py: the one-comment-per-PR failure summary. Offline, no gh."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "tools"))

import ci_comment  # noqa: E402

JUNIT = """<testsuites><testsuite>
<testcase classname="tests.test_p_perf" name="test_big"><failure message="AssertionError: prompt 61000 chars&#10;second line secret=abc123">trace</failure></testcase>
<testcase classname="tests.test_x" name="test_flaky"><failure message="ConnectionResetError: x"/></testcase>
<testcase classname="tests.test_ok" name="test_ok"/>
</testsuite></testsuites>"""

JOBS = {"jobs": [
    {"name": "Fast CI and documentation validation", "conclusion": "failure", "html_url": "https://github.com/o/r/actions/runs/1/job/2",
     "steps": [{"name": "Set up", "conclusion": "success"}, {"name": "Budget checks", "conclusion": "failure"}]},
    {"name": "Python Core tests (ubuntu-24.04 shard 1/3)", "conclusion": "success", "html_url": "u"},
    {"name": "CI result (single required check)", "conclusion": "failure", "html_url": "u3"},
]}


def test_one_line_redacts_and_truncates():
    assert ci_comment.one_line("\n\nfirst `x` | y\nsecond") == r"first 'x' \| y"
    assert "ghp_" not in ci_comment.one_line("token ghp_" + "a" * 30)
    assert "alice" not in ci_comment.one_line(r"C:\Users\alice\proj\x.py failed")
    assert len(ci_comment.one_line("y" * 999)) == 200


def test_failed_jobs_skips_aggregate_and_green():
    jobs = ci_comment.failed_jobs(JOBS)
    assert [j["name"] for j in jobs] == ["Fast CI and documentation validation"]
    assert jobs[0]["step"] == "Budget checks"


def test_build_lists_first_failure_one_line_and_marks_flakes(tmp_path):
    art = tmp_path / "junit-linux-0"
    art.mkdir()
    (art / "pytest-junit.xml").write_text(JUNIT, encoding="utf-8")
    (art / "flake-report.json").write_text(json.dumps({"flaky_passed_on_retry": [{"test": "tests.test_p_perf::test_big"}],
                                                       "failed_again": []}), encoding="utf-8")
    body = ci_comment.build(JOBS, tmp_path, "https://github.com/o/r/actions/runs/1")
    assert body.startswith(ci_comment.MARKER)
    assert "Fast CI and documentation validation" in body and "Budget checks" in body
    assert "tests.test_p_perf::test_big" in body and "AssertionError: prompt 61000 chars" in body
    assert "known flake" in body
    assert "second line" not in body and "abc123" not in body and "trace" not in body
    assert "CI result" not in body


def test_build_without_junit_points_to_log(tmp_path):
    body = ci_comment.build(JOBS, tmp_path / "missing", "https://x/run")
    assert "No failing test in the junit files" in body and "[log](https://github.com/o/r/actions/runs/1/job/2)" in body


def test_build_no_failed_jobs(tmp_path):
    assert "No failed job" in ci_comment.build({"jobs": []}, tmp_path, "https://x/run")


def test_find_comment_updates_in_place():
    cs = [{"id": 5, "body": "hello"}, {"id": 9, "body": ci_comment.MARKER + "\nold"}]
    assert ci_comment.find_comment(cs) == 9
    assert ci_comment.find_comment(cs[:1]) is None


def test_quick_gate_table_is_parsed(tmp_path):
    art = tmp_path / "junit-prepush"
    art.mkdir()
    (art / "prepush.out").write_text(
        "check    result    time  detail\nlint     PASS      2.8s  log message lint\n"
        "perf     FAIL      5.1s  AssertionError: backend prompt+tools is 61000 chars\n", encoding="utf-8")
    body = ci_comment.build(JOBS, tmp_path, "https://x/run")
    assert "quick gate check perf" in body and "backend prompt+tools is 61000 chars" in body


def test_summary_job_is_pr_only_failure_only_and_least_privilege():
    import yaml
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "jarves-checks.yml").read_text(encoding="utf-8"))
    job = doc["jobs"]["failure-summary"]
    assert "failure()" in job["if"] and "pull_request" in job["if"]
    assert job["permissions"]["pull-requests"] == "write" and doc["permissions"] == {"contents": "read"}
    assert "secrets." not in yaml.safe_dump(job)
    assert set(job["needs"]) == set(doc["jobs"]["ci-ok"]["needs"])
    assert doc["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"   # main never cancelled
