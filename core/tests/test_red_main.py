"""Fix-forward for a red main (owner decision 24 Oct 2026, #160): the issue names the failing tests and the suspect
PRs, each suspect PR gets a hand-off comment, and nothing is ever reverted automatically."""
from __future__ import annotations

from pathlib import Path

import yaml

import red_main as rm

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows"
LOG = ("Python Core tests (ubuntu-24.04 shard 2/3)\tx\t2026-10-23T22:02:30Z ______ test_backend_prompt_ceiling ______\n"
       "Python Core tests (ubuntu-24.04 shard 2/3)\tx\t2026-10-23T22:02:30Z tests/test_tool_descriptions.py:41: "
       "AssertionError\n")


def test_report_names_failing_tests_suspects_and_markers():
    body = rm.render("https://run", "abcdef1234", rm.failed_tests(LOG), [80, 107])
    assert "`test_tool_descriptions.py::test_backend_prompt_ceiling`" in body and "#80, #107" in body
    assert "<!-- red-main-tests: test_tool_descriptions.py -->" in body and "<!-- red-main-suspects: 80,107 -->" in body
    assert "main-fix" in body and "No auto-revert" in body


def test_suspects_are_the_merged_prs_since_the_last_green_run():
    msgs = ["Merge pull request #80 from igalgerman/fix/x\n\nbody", "fix: y (#107)", "docs: direct push",
            "Merge pull request #80 again", "Merge pull request #107 from b"]
    assert rm.suspects_from_messages(msgs) == [80, 107]          # merge commits only (the API side keeps 2-parent ones)


def test_quick_gate_failure_names_its_test():
    log = ("Fast CI and documentation validation\tUNKNOWN STEP\t2026-10-23T22:02:30.6521026Z perf     FAIL      3.2s  "
           "tests/test_tool_descriptions.py::test_backend_prompt_ceiling_and_per_tool_cap: AssertionError: 48450\n")
    assert list(rm.failed_tests(log)) == ["test_tool_descriptions.py::test_backend_prompt_ceiling_and_per_tool_cap"]


def test_main_health_wiring():
    alert = yaml.safe_load((WF / "main-health.yml").read_text(encoding="utf-8"))["jobs"]["alert"]
    text = "\n".join(s.get("run", "") for s in alert["steps"])
    assert "red_main.py report" in text and "--label P1" in text and "gh pr comment" in text
    assert "git revert" not in text and "gh pr revert" not in text
    assert alert["permissions"] == {"contents": "read", "actions": "read", "issues": "write", "pull-requests": "write"}
    co = next(s for s in alert["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert co["with"] == {"sparse-checkout": "core/tools"}
