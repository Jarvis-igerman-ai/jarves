"""#1311: default worker grants must not permit indirect policy writes."""
import fnmatch

import pytest

from jarves.orchestrator import claude


def _allows(rules, command):
    for rule in rules:
        if rule.startswith("Bash("):
            pattern = rule[5:-1]
            if pattern.endswith(":*"):
                prefix = pattern[:-2]
                if command == prefix or command.startswith(prefix + " "):
                    return True
            elif fnmatch.fnmatchcase(command, pattern):
                return True
    return False


@pytest.mark.parametrize("command", [
    "git diff --output=/outside/policy", "git log --output=/outside/policy",
    "pytest --junitxml=/outside/policy", "pytest --basetemp=/outside",
    "pytest -o cache_dir=/outside", "pytest worker_written_test.py",
    "C:/py/python.exe -m pytest worker_written_test.py",
    r"C:\py\python.exe -m pytest worker_written_test.py",
    "npm run rewrite-policy", "npx worker-written-code",
    "git commit -m change", "git merge topic", "git rebase main",
    "ruff check --output-file=/outside/policy", "gh run download --dir /outside",
])
def test_default_worker_does_not_preapprove_indirect_writes(command):
    rules = claude.autopilot_allowed_tools(
        r"C:\py\python.exe", console="C:/repo/core/tools/workflow_console.py", home="C:/home")
    assert not _allows(rules, command)


def test_default_grants_are_read_only_and_override_is_preserved():
    assert set(claude.WORKER_ALLOWED_TOOLS) == {
        "Read", "Grep", "Glob", "Bash(ls:*)", "Bash(grep:*)", "Bash(wc:*)", "Bash(cd:*)"}
    override = ["Bash(ls:*)"]
    assert claude.autopilot_allowed_tools(override=override) == override


def test_safe_defaults_reach_worker_argv():
    rules = claude.autopilot_allowed_tools("C:/py/python.exe")
    worker = claude.ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda *_: None,
                                  allowed_tools=rules, permission_mode=claude.WORKER_PERMISSION_MODE)
    start = worker.argv.index("--allowedTools") + 1
    actual = worker.argv[start:worker.argv.index("--disallowedTools")]
    assert actual == list(claude.WORKER_ALLOWED_TOOLS)
    assert not _allows(actual, "C:/py/python.exe -m pytest worker_written_test.py")


def test_default_grants_pass_the_override_validator():
    from jarves.workflow import autopilot
    autopilot._worker_tools(list(claude.WORKER_ALLOWED_TOOLS))


def _console_rules(rules):
    return [r for r in rules if "workflow_console.py" in r]


def test_console_rules_are_kept_when_console_lives_outside_the_worker_cwd(tmp_path):
    """Review R1: the usual layout, core checkout beside `<project>/.jarves/worktrees/<slug>`; routes stay."""
    con = tmp_path / "proj" / "core" / "tools" / "workflow_console.py"
    wt = tmp_path / "proj" / ".jarves" / "worktrees" / "t1"
    rules = claude.autopilot_allowed_tools("C:/py/python.exe", console=str(con), home="C:/home", cwd=str(wt))
    assert _console_rules(rules)


@pytest.mark.parametrize("rel", ["proj", "proj/core", "proj/core/tools"])
def test_console_rules_are_dropped_when_the_worker_can_edit_the_console(tmp_path, rel):
    """Review R1: the worker runs in the folder that holds the console, its directory or the core/src import root;
    acceptEdits would let it edit code that the preapproved console rule then runs, so no console rule."""
    con = tmp_path / "proj" / "core" / "tools" / "workflow_console.py"
    rules = claude.autopilot_allowed_tools("C:/py/python.exe", console=str(con), home="C:/home",
                                           cwd=str(tmp_path / rel))
    assert not _console_rules(rules) and "Read" in rules


def test_worker_tools_pass_the_worker_cwd(tmp_path, monkeypatch):
    """Review R1: the service hands the worker's cwd to the rule builder, so the check above is applied."""
    from jarves.orchestrator import service
    seen = {}
    monkeypatch.setattr(service.claude, "autopilot_allowed_tools", lambda *a, **k: seen.update(k) or [])
    svc = object.__new__(service.Orchestrator)
    svc.home = tmp_path
    svc._worker_tools(str(tmp_path / "wt"))
    assert seen["cwd"] == str(tmp_path / "wt")
