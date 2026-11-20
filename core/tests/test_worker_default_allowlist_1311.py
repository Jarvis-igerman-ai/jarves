"""#1311: the built-in autopilot worker allow list (used when autopilot.json has no override) must not admit commands
that write outside the worktree or run worker-written code (and so could rewrite autopilot.json)."""
import pytest

from jarves.orchestrator import claude

PY, CON, HOME = "C:/py/python.exe", "C:/r/core/tools/workflow_console.py", "C:/h"


def _default():
    return claude.autopilot_allowed_tools(PY, None, CON, HOME)


@pytest.mark.parametrize("rule", [
    "Bash(git diff:*)", "Bash(git log:*)",                      # --output=<path>
    "Bash(pytest:*)", f"Bash({PY} -m pytest:*)",                 # --junitxml/--basetemp/-o cache_dir, runs conftest.py
    "Bash(npm:*)", "Bash(npx:*)",                                # run arbitrary package scripts
    "Bash(git commit:*)", "Bash(git merge:*)", "Bash(git rebase:*)",   # run hooks / --exec
    "Bash(ruff:*)", "Bash(cd:*)", "Bash(mkdir:*)", "Bash(git worktree:*)", "Bash(gh run:*)", "Bash(gh pr:*)",
])
def test_issue_1311_rules_are_not_in_the_default_list(rule):
    assert rule not in claude.WORKER_ALLOWED_TOOLS
    assert rule not in _default()


@pytest.mark.parametrize("rule", ["Bash(pytest:*)", "Bash(git diff:*)", "Bash(npm *)", "Bash(git commit -m *)", "Bash(*)",
                                  "Bash(python -m pytest:*)", "Bash(sed -i x:*)", "Bash(cd /h)"])
def test_unsafe_rules_are_reported(rule):
    assert claude.worker_rule_problem(rule)


def test_default_list_passes_the_check():
    """The same check that names the #1311 rules finds nothing in the shipped default (console rules included)."""
    bad = [(r, claude.worker_rule_problem(r, PY, CON)) for r in _default() if claude.worker_rule_problem(r, PY, CON)]
    assert bad == []
    assert "Bash(git status)" in _default() and "Bash(gh pr view:*)" in _default()


@pytest.mark.parametrize("rule", [f"Bash(node {CON} --home {HOME} next *)",          # another interpreter
                                  f"Bash({PY} /tmp/workflow_console.py --home {HOME} next *)",   # another console file
                                  f"Bash({PY} {CON} --home {HOME} autopilot *)",       # an owner-only subcommand
                                  f"Bash({PY} {CON} --home {HOME} next * ; sh x *)"])
def test_console_routes_are_tied_to_the_exact_python_and_console(rule):
    """Review R4: a console route counts only with the exact interpreter and console autopilot_allowed_tools uses."""
    assert claude.worker_rule_problem(rule, PY, CON)
    assert claude.worker_rule_problem(f"Bash({PY} {CON} --home {HOME} next *)")   # without the expected values: refused
    assert not claude.worker_rule_problem(f"Bash({PY} {CON} --home {HOME} next *)", PY, CON)


def test_worker_cannot_edit_git_metadata():
    """#1334 R1: git status/diff read worktree git config (core.fsmonitor, diff drivers); the `.git` pointer and any
    gitdir under the worktree are denied to Edit and Write, and the deny reaches the worker argv."""
    argv = claude.ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None,
                                allowed_tools=_default(), permission_mode="acceptEdits").argv
    denied = argv[argv.index("--disallowedTools") + 1:]
    for rule in ("Edit(**/.git)", "Write(**/.git)", "Edit(**/.git/**)", "Write(**/.git/**)"):
        assert rule in claude.WORKER_DENY and rule in denied
