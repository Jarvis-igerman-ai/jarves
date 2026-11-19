"""#1311/#1316: autopilot worker allow rules hold no wildcard, redirection, compound command or code runner."""
import pytest

from jarves.orchestrator import claude

PY, CON, HOME = "C:/py/python.exe", "C:/r/core/tools/workflow_console.py", "C:/h"


def _worker_rules(rules):
    return [r for r in rules if "workflow_console" not in r]     # #784 console rules are reviewed separately


def test_1311_default_list_passes_the_same_check_as_the_override():
    rules = claude.autopilot_allowed_tools(PY, None, CON, HOME)
    for rule in (*claude.WORKER_ALLOWED_TOOLS, *_worker_rules(rules)):
        assert claude.worker_rule_problem(rule) == "", rule
    for runner in ("Bash(pytest:*)", f"Bash({PY} -m pytest:*)", "Bash(npm:*)", "Bash(npx:*)", "Bash(git commit:*)",
                   "Bash(git merge:*)", "Bash(git rebase:*)", "Bash(git diff:*)", "Bash(git log:*)"):
        assert runner not in rules


@pytest.mark.parametrize("rule", ["Bash(ls:*)", "Bash(ls *)", "Bash(ls > ~/autopilot.json)", "Bash(ls >> a)",
                                  "Bash(ls | tee a)", "Bash(ls; python -c x)", "Bash(ls && rm x)", "Bash(ls $(id))",
                                  "Bash(ls `id`)", "Bash(git diff --output=a)", "Bash(pytest)", "Bash(npm test)",
                                  "Bash(git commit -m x)", "Bash(python x.py)", "Edit", "Write", "Bash(tee a)",
                                  # review of #1334: arguments after a read-only command are not trusted
                                  "Bash(git branch -D main)", "Bash(git branch -f main HEAD)", "Bash(git branch -m a b)",
                                  "Bash(git branch x)", "Bash(git diff --outp=f)", "Bash(git log --out=f)",
                                  "Bash(git diff --ext-d)", "Bash(gh pr view --web)", "Bash(gh pr view 1 -w)",
                                  "Bash(gh issue list --web=true)", "Bash(gh pr list -R other/repo)"])
def test_1316_wildcard_redirection_compound_and_writers_are_refused(rule):
    assert claude.worker_rule_problem(rule) != ""


def test_1316_override_rules_that_fail_the_check_are_dropped_and_never_empty():
    bad = ["Bash(ls:*)", "Bash(ls > ~/autopilot.json)", "Bash(ls; python -c x)", "Bash(ls | tee a)"]
    assert claude.autopilot_allowed_tools(PY, override=[*bad, "Read", "Bash(git status)"]) == ["Read", "Bash(git status)"]
    # an override with nothing usable falls back to the checked default, never to "no --allowedTools" (owner settings)
    assert claude.autopilot_allowed_tools(PY, override=bad) == claude.autopilot_allowed_tools(PY)
    assert claude.autopilot_allowed_tools(PY, override=[None, 3, "Read"]) == ["Read"]   # malformed entries dropped


def test_1316_dropped_override_rules_are_logged_once_with_the_reason(monkeypatch):
    seen = []
    monkeypatch.setattr(claude.logs, "warn", lambda comp, code, msg, **f: seen.append((code, f)))
    monkeypatch.setattr(claude, "_DROPPED_SEEN", set())
    for _ in range(3):                                   # three worker starts with the same policy: one line per rule
        claude.autopilot_allowed_tools(PY, override=["Bash(ls:*)", "Read", 7])
    assert seen == [("AGENTS_WORKER_RULE_DROPPED", {"item": 0, "fallback": False,
                     "reason": "wildcard, redirection, compound command or substitution"}),
                    ("AGENTS_WORKER_RULE_DROPPED", {"item": 2, "fallback": False, "reason": "not a string"})]
    claude.autopilot_allowed_tools(PY, override=["Bash(ls > a)"])          # all bad: the fallback is visible
    assert seen[-1][1]["fallback"] is True and len(seen) == 3
