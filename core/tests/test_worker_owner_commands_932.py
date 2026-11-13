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
