"""#784: autopilot allow-list matches the commands workers run; a finished turn without a checkpoint is explicit."""
from pathlib import Path

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


def test_allow_list_has_the_exact_workflow_console_rule_only():
    from jarves.orchestrator import claude
    py, con = r"C:\py\python.exe", r"C:\repo\core\tools\workflow_console.py"
    fwd = py.replace("\\", "/")
    rules = claude.autopilot_allowed_tools(py, console=con)
    for p in (py, fwd):
        for c in (con, con.replace("\\", "/")):
            assert f"Bash({p} {c}:*)" in rules
    assert f"Bash({py}:*)" not in rules and f"Bash({fwd}:*)" not in rules
    assert not any("workflow_console" in r for r in claude.autopilot_allowed_tools(py))
    assert claude.autopilot_allowed_tools(py, override=["Bash(ls:*)"], console=con) == ["Bash(ls:*)"]


def test_assignment_tells_workers_not_to_chain_commands():
    from jarves.workflow.control import assignment
    text = assignment({"id": "t"}, "ap-x", Path("h"))
    assert "workflow_console.py" in text and "cd &&" in text
