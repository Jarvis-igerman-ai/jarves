"""#1311: the built-in default autopilot worker allow list grants no rule that can write a file or run worker-written code."""
import pytest

from jarves.orchestrator.claude import WORKER_ALLOWED_TOOLS, WORKER_DENY, ManagedClaude, autopilot_allowed_tools

PY, CON, HOME = "C:/py/python.exe", "C:/r/core/tools/workflow_console.py", "C:/h"

# Each prefix can write an arbitrary path or execute code the worker wrote (issue #1311 list, plus ruff --output-file).
WRITE_OR_EXEC = ["Bash(pytest:*)", f"Bash({PY} -m pytest:*)", "Bash(npm:*)", "Bash(npx:*)", "Bash(ruff:*)",
                 "Bash(git add:*)", "Bash(git commit:*)", "Bash(git merge:*)", "Bash(git rebase:*)",
                 "Bash(git switch:*)", "Bash(git worktree:*)", "Bash(git fetch:*)", "Bash(git branch:*)"]


@pytest.mark.parametrize("rule", WRITE_OR_EXEC)
def test_default_list_has_no_write_or_exec_rule(rule):
    assert rule not in WORKER_ALLOWED_TOOLS
    assert rule not in autopilot_allowed_tools(PY, None, CON, HOME)


@pytest.mark.parametrize("cmd", ["git diff", "git log"])
def test_diff_and_log_output_option_is_denied_in_the_worker_argv(cmd):
    """`git diff --output=<path>` / `git log --output=<path>` write any file; the read-only rules stay, the option is denied."""
    rule = f"Bash({cmd}*--output*)"
    assert f"Bash({cmd}:*)" in WORKER_ALLOWED_TOOLS and rule in WORKER_DENY
    argv = ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None,
                         allowed_tools=autopilot_allowed_tools(PY), permission_mode="acceptEdits").argv
    assert rule in argv[argv.index("--disallowedTools") + 1:]


def test_owner_override_is_still_used_verbatim():
    """The owner can restore any rule (e.g. pytest, git commit) through `worker_allowed_tools`; that path is unchanged."""
    assert autopilot_allowed_tools(PY, ["Bash(pytest:*)", "Bash(git commit:*)"], CON, HOME) == ["Bash(pytest:*)", "Bash(git commit:*)"]
