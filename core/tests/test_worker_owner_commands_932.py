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


def test_owner_session_without_agent_env_can_still_create(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVES_WORKFLOW_AGENT", raising=False)
    assert wf.main(["--home", str(tmp_path / "h"), "status"]) == 0


@pytest.mark.parametrize("word", ["python", "py", "node", "sed", "cp", "mv", "cat", "head", "tail"])
def test_no_blanket_interpreter_or_file_writer_rule(word):
    """#932: a worker could rewrite <home>/autopilot.json with these; none is allowed as a bare prefix."""
    assert f"Bash({word}:*)" not in WORKER_ALLOWED_TOOLS
    assert f"Bash({word}:*)" not in autopilot_allowed_tools("C:/py/python.exe", None, "C:/r/core/tools/workflow_console.py", "C:/h")
    assert "Bash(jarvis workflow:*)" not in WORKER_ALLOWED_TOOLS


def test_policy_file_name_is_denied_in_any_command():
    assert "Bash(*autopilot.json*)" in WORKER_DENY
