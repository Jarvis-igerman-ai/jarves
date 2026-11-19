"""#784/#932: an autopilot worker cannot run owner-only workflow actions or rewrite autopilot.json via interpreters."""
import pytest

from jarves.orchestrator.claude import WORKER_ALLOWED_TOOLS, WORKER_DENY, autopilot_allowed_tools
from jarves.workflow import __main__ as wf

OWNER_LINES = [
    ["--home", "H", "create", "--title", "x"],
    ["--home", "H", "dispatch"],
    ["--home", "H", "questions-rewrite", "--apply"],
    ["--home", "H", "trace", "--capture", "details"],
    ["--home", "H", "release", "--resource", "pr:1", "--force"],
    ["--home", "H", "recover", "--task", "T"],
    ["--home", "H", "seed"], ["--home", "H", "sync"], ["--home", "H", "handover"],
    ["--home", "H", "close-superseded", "1"], ["--home", "H", "agent"],
    ["--home", "H", "autopilot", "set", "worker_allowed_tools", "[]"],
    ["--home", "H", "authorize", "--task", "T"], ["--home", "H", "bootstrap", "--apply"],
    ["--home", "H", "layer-beat", "--layer", "supervisor"],
]


@pytest.mark.parametrize("argv", OWNER_LINES)
def test_owner_actions_refused_by_default_for_an_agent(monkeypatch, capsys, argv):
    monkeypatch.setenv("JARVES_WORKFLOW_AGENT", "ap-x")
    assert wf.main(argv) == 2
    assert "refused" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--home", "H", "status"], ["--home", "H", "next", "--agent", "a"],
                                  ["--home", "H", "autopilot", "status"], ["--home", "H", "trace"]])
def test_worker_actions_are_not_refused(argv, monkeypatch):
    monkeypatch.setenv("JARVES_WORKFLOW_AGENT", "ap-x")
    assert wf.worker_refusal(argv) == ""


def test_owner_session_without_agent_env_can_still_create(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("JARVES_WORKFLOW_AGENT", raising=False)
    home = str(tmp_path / "h")
    assert wf.worker_refusal(["--home", home, "create", "--title", "owner task"]) != ""   # guarded for agents...
    assert wf.main(["--home", home, "create", "--title", "owner task"]) == 0              # ...but the owner runs it
    assert "owner task" in capsys.readouterr().out


@pytest.mark.parametrize("word", ["python", "py", "node", "sed", "cp", "mv", "cat", "head", "tail"])
def test_no_blanket_interpreter_or_file_writer_rule(word):
    """#932: a worker could rewrite <home>/autopilot.json with these; none is allowed as a bare prefix."""
    assert f"Bash({word}:*)" not in WORKER_ALLOWED_TOOLS
    assert f"Bash({word}:*)" not in autopilot_allowed_tools("C:/py/python.exe", None, "C:/r/core/tools/workflow_console.py", "C:/h")
    assert "Bash(jarvis workflow:*)" not in WORKER_ALLOWED_TOOLS


def test_policy_file_is_denied_to_bash_edit_and_write_in_the_worker_argv():
    """#932: the deny rules reach `claude --disallowedTools` for every headless worker. Claude Code's own rule matcher
    is not run here (UNVERIFIED in the changelog); this proves the rules are generated and passed."""
    from jarves.orchestrator.claude import ManagedClaude
    argv = ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None,
                         allowed_tools=autopilot_allowed_tools("C:/py/python.exe"), permission_mode="acceptEdits").argv
    denied = argv[argv.index("--disallowedTools") + 1:]
    for rule in ("Bash(*autopilot.json*)", "Edit(**/autopilot.json)", "Write(**/autopilot.json)"):
        assert rule in WORKER_DENY and rule in denied


@pytest.mark.parametrize("argv", [["--home", "H", "autopilot"], ["--home", "H", "autopilot", "grant", "--hours", "1"],
                                  ["--home", "H", "autopilot", "revoke"], ["--home", "H", "autopilot", "grant-status"]])
def test_bare_autopilot_and_grant_routes_refused_for_an_agent(monkeypatch, capsys, argv):
    monkeypatch.setenv("JARVES_WORKFLOW_AGENT", "ap-x")
    assert wf.main(argv) == 2 and "refused" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["answer", "--id", "Q1", "--answer", "yes"], ["confirm", "--id", "Q1"],
                                  ["reopen", "--id", "Q1"], ["ask-more", "--id", "Q1", "--note", "n"], ["unlock"]])
def test_owner_answer_actions_refused_for_an_agent_without_a_store_write(monkeypatch, capsys, tmp_path, argv):
    monkeypatch.setenv("JARVES_WORKFLOW_AGENT", "ap-x")
    home = tmp_path / "h"
    assert wf.main(["--home", str(home), *argv]) != 0
    assert "agent environment" in capsys.readouterr().err
    assert not (home / "unlock.json").exists()
    if (home / "workflow.db").exists():   # the guard runs before any ledger write
        import sqlite3
        assert sqlite3.connect(home / "workflow.db").execute("select count(*) from workflow_events").fetchone()[0] == 0


def test_every_console_subcommand_the_worker_is_allowed_is_not_refused():
    """The Claude allow rules and the console guard must not drift, or every headless worker stalls on exit 2."""
    from jarves.orchestrator.claude import WORKER_CONSOLE_SUBCOMMANDS
    for sub in WORKER_CONSOLE_SUBCOMMANDS:
        assert wf.worker_refusal(["--home", "H", sub]) == "", sub


# #1311: the default list must not admit run-time write/exec options.
def _denied(command: str) -> bool:
    """A model of OUR deny rule set, not of Claude Code: each `Bash(glob)` is fnmatch-ed against the whole command. It
    says nothing about how the real matcher splits compound commands or handles quoting; that is checked only by the
    real-matcher run in docs/workflow/WORKER_PERMISSION_MODES.md ("Run-time matcher check"), re-run on matcher changes."""
    import fnmatch
    return any(fnmatch.fnmatchcase(command, r[5:-1]) for r in WORKER_DENY if r.startswith("Bash("))


@pytest.mark.parametrize("command", [
    "git diff --output=/h/autopilot2.json", "git log --output /h/x", "ruff check . --output-file /h/x",
    "ruff check --cache-dir /h/c", "pytest --junitxml=/h/j.xml", "pytest --junit-xml /h/j.xml", "pytest --basetemp=/h/bt",
    "pytest -o cache_dir=/h/cd", "pytest --log-file=/h/l", "pytest --report-log=/h/r", "pytest --cov-report=xml:/h/c.xml",
    "pytest --html=/h/r.html", "pytest --rootdir=/h", "git rebase --exec 'python x.py' main", "git rebase -x 'sh x' main",
    "git fetch --upload-pack='sh x' origin", "gh run download 1 -D /h", "ls /h/autopilot.json",
    # review of #1349: short and ini spellings, network, outside-path arguments
    "ruff check . -o /h/x", "ruff check -o/h/x .", "pytest -o log_file=/h/x", "pytest --override-ini=log_file=/h/x",
    "pytest -qo log_file=/h/x", "pytest -ocache_dir=/h/c", "pytest --debug=/h/x", "pytest --pastebin=all",
    "git rebase -x'sh x' main", "ruff format /h/x.py", "ruff check --fix ../outside", "ruff format ~/x.py",
    "ruff check --fix C:/h/x.py", "ruff format C:\\h\\x.py", "ruff check --fix=true --config=/h/r.toml .",
    # the default list also allows `<python> -m pytest:*`: pytest rules must catch that spelling too
    "C:/py/python.exe -m pytest -o log_file=/h/x", "C:/py/python.exe -m pytest --junitxml=/h/j.xml",
    "C:/py/python.exe -m pytest -qo cache_dir=/h/c", "C:/py/python.exe -m pytest --debug=/h/x",
    "C:/py/python.exe -m pytest --basetemp=/h/b", "git -C . diff --output=/h/x", "ruff check --output-file=/h/x .",
])
def test_default_list_write_and_exec_options_are_denied(command):
    assert _denied(command), command


@pytest.mark.parametrize("command", ["pytest -q tests/test_x.py", "git diff HEAD", "git log --oneline -5", "ruff check .",
                                     "git rebase main", "gh run view 1 --log-failed", "git fetch origin",
                                     "grep -rn cache_dir core/", "git rebase origin/fix-xyz", "grep -rn -- --html docs/",
                                     "ruff format core/src/jarves/x.py", "ruff check --fix core/tests",
                                     "pytest -q -k log_file core/tests/test_logs.py", "git worktree list",
                                     "grep -rn -- --output docs/", "grep -rn junitxml --rootdir", "grep -rn -- --basetemp core",
                                     "grep -rn -- --upload-pack docs/", "grep -rn -- --cov-report core",
                                     "C:/py/python.exe -m pytest -q core/tests/test_x.py"])
def test_ordinary_worker_commands_are_not_denied(command):
    assert not _denied(command), command


@pytest.mark.parametrize("rule", ["Bash(npm:*)", "Bash(npx:*)", "Bash(jarvis features:*)", "Bash(jarvis doctor:*)",
                                  "Bash(git worktree:*)"])   # review of #1349: `git worktree add <outside path>`
def test_default_list_drops_script_runners_and_owner_switches(rule):
    """npm/npx run any package script; `jarvis features on` installs packages; `jarvis doctor --live` costs money;
    `git worktree add` creates a checkout at any path (only `git worktree list` stays)."""
    assert rule not in WORKER_ALLOWED_TOOLS


def test_write_flag_denies_reach_the_worker_argv():
    from jarves.orchestrator.claude import WORKER_WRITE_FLAG_DENY, ManagedClaude
    argv = ManagedClaude(["claude"], ".", plugin_dir=None, on_event=lambda k, d: None,
                         allowed_tools=autopilot_allowed_tools("C:/py/python.exe"), permission_mode="acceptEdits").argv
    denied = argv[argv.index("--disallowedTools") + 1:]
    assert set(WORKER_WRITE_FLAG_DENY) <= set(denied)
