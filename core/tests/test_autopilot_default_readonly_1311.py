"""#1311: the built-in default autopilot worker allow list grants no rule that can write a file or run worker-written code."""
import fnmatch

import pytest

from jarves.orchestrator.claude import WORKER_ALLOWED_TOOLS, WORKER_DENY, ManagedClaude, autopilot_allowed_tools

PY, CON, HOME = "C:/py/python.exe", "C:/r/core/tools/workflow_console.py", "C:/h"

# Each prefix can write an arbitrary path or execute code the worker wrote (issue #1311 list, plus ruff --output-file).
WRITE_OR_EXEC = ["Bash(pytest:*)", f"Bash({PY} -m pytest:*)", "Bash(npm:*)", "Bash(npx:*)", "Bash(ruff:*)",
                 "Bash(git add:*)", "Bash(git commit:*)", "Bash(git merge:*)", "Bash(git rebase:*)",
                 "Bash(git switch:*)", "Bash(git worktree:*)", "Bash(git fetch:*)", "Bash(git branch:*)", "Bash(mkdir:*)"]


@pytest.mark.parametrize("rule", WRITE_OR_EXEC)
def test_default_list_has_no_write_or_exec_rule(rule):
    assert rule not in WORKER_ALLOWED_TOOLS
    assert rule not in autopilot_allowed_tools(PY, None, CON, HOME)


@pytest.mark.parametrize("cmd", ["git diff", "git log"])
def test_diff_and_log_output_option_is_denied_in_the_worker_argv(cmd):
    """`git diff --output=<path>` / `git log --output=<path>` write any file; the deny rule reaches the worker argv."""
    rule = f"Bash({cmd}*--output*)"
    assert rule in WORKER_DENY
    argv = ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None,
                         allowed_tools=autopilot_allowed_tools(PY), permission_mode="acceptEdits").argv
    assert rule in argv[argv.index("--disallowedTools") + 1:]


def _rule_matches(rule: str, cmd: str) -> bool:
    """A FAKE of Claude Code's Bash rule semantics (prefix `X:*`, `*` glob, else exact). Not the real matcher (UNVERIFIED)."""
    body = rule[len("Bash("):-1]
    if body.endswith(":*"):
        return cmd == body[:-2] or cmd.startswith(body[:-2] + " ")
    return fnmatch.fnmatchcase(cmd, body) if "*" in body else cmd == body


def _worker_may_run(cmd: str) -> bool:
    rules = [r for r in autopilot_allowed_tools(PY, None, CON, HOME) if r.startswith("Bash(")]
    return any(_rule_matches(r, cmd) for r in rules) and not any(_rule_matches(r, cmd) for r in WORKER_DENY if r.startswith("Bash("))


@pytest.mark.parametrize("cmd", [
    "git diff --output=../h/autopilot.json", "git diff --out=../h/x", "git diff --outp=../h/x", "git diff --ou ../h/x",
    "git log --output=../h/x", "git log --out=../h/x", "git log -p --outpu=../h/x", "git diff --stat --out=../h/x",
    "gh run download 1 -D ../h", "gh pr checkout 7", "gh issue develop 5 --checkout", "pytest -q", "npm test",
    f"{PY} -m pytest -q", "ruff check --output-file ../h/x", "git commit -m x", "mkdir ../h/x",
    "gh pr view 7 --web", "gh issue list -w", "gh run view 9 --web", "gh pr checks 7 --watch"])
def test_write_or_exec_commands_are_not_runnable_by_a_default_worker(cmd):
    """#1311 R2/R3: option abbreviations (`--out`, `--outp`) and local-write gh subcommands fall outside every allow rule."""
    assert not _worker_may_run(cmd)


@pytest.mark.parametrize("cmd", ["git status", "git diff", "git diff --stat", "git log --oneline -20", "gh pr view 7",
                                 "gh pr checks 7", "gh run view 9 --log", "gh issue list", "ls -la", "grep -rn x ."])
def test_read_only_commands_stay_runnable(cmd):
    assert _worker_may_run(cmd)


@pytest.mark.parametrize("path", ["**/.git/**", "**/.git"])
def test_git_dir_and_config_are_not_editable(path):
    """#1311 R4: hooks and config (core.fsmonitor, diff.external) under a .git dir or a worktree's .git file are denied."""
    assert f"Edit({path})" in WORKER_DENY


def test_owner_override_is_still_used_verbatim():
    """The owner can restore any rule (e.g. pytest, git commit) through `worker_allowed_tools`; that path is unchanged."""
    assert autopilot_allowed_tools(PY, ["Bash(pytest:*)", "Bash(git commit:*)"], CON, HOME) == ["Bash(pytest:*)", "Bash(git commit:*)"]
