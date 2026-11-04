"""#784: autopilot allow-list matches the commands workers run; a finished turn without a checkpoint is explicit."""
from pathlib import Path

import pytest

from jarves.workflow import trace
from jarves.workflow.store import Store


def test_autopilot_completed_without_checkpoint_blocks_with_a_clear_reason(tmp_path):
    profile = tmp_path / "profile"
    ledger = Store(profile / "workflow" / "workflow.db")
    try:
        ledger.create_task({"id": "t1", "title": "t1", "lane": "features", "version": "V4.5.1",
                            "acceptance": [{"id": "C1", "label": "ok"}]})
        ledger.register_agent({"id": "ap-features-t1", "session_id": "claude:s1", "lane": "features"})
        assert ledger.claim("ap-features-t1", "t1")
    finally:
        ledger.close()
    session = {"id": "claude:s1", "provider": "claude", "project": "jarves", "state": "IDLE"}
    trace.provider_event(profile, session, "completed", "turn done")
    ledger = Store(profile / "workflow" / "workflow.db")
    try:
        task = next(t for t in ledger.snapshot()["tasks"] if t["id"] == "t1")
        assert task["state"] == "blocked" and task["blocker"] == "worker_ended_without_checkpoint"
    finally:
        ledger.close()


def test_allow_list_has_only_the_worker_subcommands_of_the_workflow_console():
    from jarves.orchestrator import claude
    py, con = r"C:\py\python.exe", r"C:\repo\core\tools\workflow_console.py"
    fwd = py.replace("\\", "/")
    rules = claude.autopilot_allowed_tools(py, console=con)
    for p in (py, fwd):
        for c in (con, con.replace("\\", "/")):
            for sub in claude.WORKER_CONSOLE_SUBCOMMANDS:
                assert f"Bash({p} {c} --home * {sub} *)" in rules
    assert set(claude.WORKER_CONSOLE_SUBCOMMANDS) == {"next", "report", "task", "check", "transition", "ask", "check-touch",
                                                      "own", "msg", "status"}
    assert not any(r.endswith(":*)") and "workflow_console" in r for r in rules)
    assert f"Bash({py}:*)" not in rules and f"Bash({fwd}:*)" not in rules
    assert not any("workflow_console" in r for r in claude.autopilot_allowed_tools(py))
    assert claude.autopilot_allowed_tools(py, override=["Bash(ls:*)"], console=con) == ["Bash(ls:*)"]


@pytest.mark.parametrize("sub", ["autopilot", "authorize", "bootstrap", "dispatch", "seed", "sync", "recover",
                                 "close-superseded", "answer", "confirm", "reopen", "unlock", "open"])
def test_owner_subcommands_are_not_allowed_for_workers(sub):
    from jarves.orchestrator import claude
    rules = claude.autopilot_allowed_tools("/py/python", console="/r/workflow_console.py")
    assert not any(f" {sub} " in r or r.endswith(f" {sub})") for r in rules if "workflow_console" in r)


@pytest.mark.parametrize("argv", [["autopilot", "start"], ["--home", "H", "authorize", "--task", "t"], ["bootstrap", "--apply"],
                                  ["--home", "H", "autopilot", "grant"]])
def test_autopilot_authorize_bootstrap_refuse_in_agent_environment(monkeypatch, capsys, argv):
    from jarves.workflow import __main__ as wf
    monkeypatch.setenv("JARVES_WORKFLOW_AGENT", "ap-x")
    assert wf.main(argv) == 2
    assert "refused" in capsys.readouterr().err


def test_agent_environment_does_not_refuse_ordinary_text_mentioning_autopilot(monkeypatch):
    from jarves.workflow import __main__ as wf
    monkeypatch.setenv("JARVES_WORKFLOW_AGENT", "ap-x")
    assert wf._action_of(["--home", "H", "ask", "--title", "autopilot"]) == "ask"


def _ledger(tmp_path, agent="ap-features-t1", evidence=False, state=None):
    profile = tmp_path / "profile"
    ledger = Store(profile / "workflow" / "workflow.db")
    ledger.create_task({"id": "t1", "title": "t1", "lane": "features", "version": "V4.5.1", "acceptance": [{"id": "C1", "label": "ok"}]})
    ledger.register_agent({"id": agent, "session_id": "claude:s1", "lane": "features"})
    assert ledger.claim(agent, "t1")
    return profile, ledger


def _state(profile):
    ledger = Store(profile / "workflow" / "workflow.db")
    try:
        return next(t for t in ledger.snapshot()["tasks"] if t["id"] == "t1")
    finally:
        ledger.close()


SESSION = {"id": "claude:s1", "provider": "claude", "project": "jarves", "state": "IDLE"}


def test_ended_without_checkpoint_ignores_non_ap_worker(tmp_path):
    profile, ledger = _ledger(tmp_path, agent="claude-native1")
    ledger.close()
    trace.provider_event(profile, SESSION, "completed", "turn done")
    assert _state(profile)["state"] != "blocked"


def test_ended_without_checkpoint_ignores_non_completed_event(tmp_path):
    profile, ledger = _ledger(tmp_path)
    ledger.close()
    trace.provider_event(profile, SESSION, "started", "turn start")
    assert _state(profile)["state"] != "blocked"


def test_ended_without_checkpoint_skips_task_with_evidence(tmp_path, monkeypatch):
    profile, ledger = _ledger(tmp_path)
    ledger.close()
    monkeypatch.setattr(Store, "has_recorded_evidence", staticmethod(lambda task: True))
    trace.provider_event(profile, SESSION, "completed", "turn done")
    assert _state(profile)["state"] != "blocked"


def test_ended_without_checkpoint_skips_task_already_in_review(tmp_path):
    profile, ledger = _ledger(tmp_path)
    ledger.transition("t1", {"state": "review"})
    ledger.close()
    trace.provider_event(profile, SESSION, "completed", "turn done")
    assert _state(profile)["state"] == "review"


def test_assignment_tells_workers_not_to_chain_commands():
    from jarves.workflow.control import assignment
    text = assignment({"id": "t"}, "ap-x", Path("h"))
    assert "workflow_console.py" in text and "cd &&" in text
