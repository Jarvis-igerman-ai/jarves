"""Native Claude subagents are alive on hook events alone; a pause must not quarantine work for good."""
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.hooks import observe
from jarves.workflow.store import Store


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class WorkflowLivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.clock = Clock()
        self.store = Store(Path(self.temp.name) / "private" / "workflow.db", clock=self.clock)
        self.store.create_task({"id": "T1", "title": "Task one", "lane": "bugs"})
        self.ev("SubagentStart", agent_id="child", agent_type="workflow-bugs")
        self.aid = self.store.snapshot()["agents"][0]["id"]
        self.ev("PreToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Bash", tool_use_id="a")
        self.assertIsNotNone(self.store.claim(self.aid, "T1"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def ev(self, event, **kw):
        return observe(self.store, {"hook_event_name": event, "session_id": "s1", "cwd": str(self.root), **kw}, self.root)

    def task(self):
        return self.store.snapshot()["tasks"][0]

    def test_continuous_hook_events_never_trigger_worker_stalled(self):
        for i in range(30):                       # 60 minutes of steady tool events
            self.clock.t += 120
            self.ev("PostToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Bash", tool_use_id=f"c{i}")
            self.store.tick()
        self.assertEqual(self.task()["state"], "running")

    def test_native_worker_gets_long_grace_before_offline(self):
        self.ev("PostToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Bash", tool_use_id="a")
        self.clock.t += 600                       # long reasoning, no events: beyond the 120 s autopilot rule
        self.store.tick()
        self.assertEqual(self.task()["state"], "running")

    def test_quarantined_by_silence_recovers_when_events_resume(self):
        self.ev("PostToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Bash", tool_use_id="a")
        self.clock.t += 4000
        self.store.tick()
        self.assertEqual(self.task()["state"], "blocked")
        self.ev("PreToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Read", tool_use_id="b")
        task = self.task()
        self.assertEqual((task["state"], task["quarantined"], task["blocker"]), ("running", False, ""))
        agent = self.store.snapshot()["agents"][0]
        self.assertEqual(agent["status"], "running")
        self.store.tick()
        self.assertEqual(self.task()["state"], "running")

    def test_subagent_stop_quarantine_is_not_auto_recovered(self):
        self.ev("SubagentStop", agent_id="child", agent_type="workflow-bugs")
        self.assertEqual(self.task()["state"], "blocked")
        self.ev("PostToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Bash", tool_use_id="late")
        self.assertEqual(self.task()["state"], "blocked")

    def test_autopilot_worker_events_without_checkpoint_still_stall(self):
        self.store.create_task({"id": "T2", "title": "Task two", "lane": "bugs"})
        self.store.register_agent({"id": "ap-w1", "label": "ap", "lane": "bugs", "provider": "claude"})
        self.assertIsNotNone(self.store.claim("ap-w1", "T2"))
        for _ in range(20):                       # 40 minutes of tool events, never a checkpoint
            self.clock.t += 120
            self.store.observe_agent("ap-w1", "running", "tool")
            self.store.tick()
        t2 = [t for t in self.store.snapshot()["tasks"] if t["id"] == "T2"][0]
        self.assertEqual((t2["state"], t2["blocker"]), ("blocked", "worker_stalled"))

    def _tick_quarantine(self):
        self.ev("PostToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Bash", tool_use_id="a")
        self.clock.t += 4000
        self.store.tick()
        self.assertEqual(self.task()["state"], "blocked")

    def test_revoked_authorisation_blocks_recovery(self):
        self._tick_quarantine()
        self.store.transition("T1", {"state": "blocked", "execution_allowed": False})
        self.ev("PreToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Read", tool_use_id="b")
        task = self.task()
        self.assertEqual(task["state"], "blocked")
        self.assertFalse(task["execution_allowed"])

    def test_manual_quarantine_with_same_text_is_not_recovered(self):
        self.store.quarantine("T1", "worker_offline")
        self.ev("PreToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Read", tool_use_id="b")
        task = self.task()
        self.assertEqual((task["state"], task["quarantined"]), ("blocked", True))

    def test_long_offline_native_agents_are_archived(self):
        self.ev("SubagentStart", agent_id="other", agent_type="subagent")
        self.ev("SessionEnd", agent_id="other", agent_type="subagent")
        self.clock.t += 7 * 3600
        self.ev("PostToolUse", agent_id="child", agent_type="workflow-bugs", tool_name="Bash", tool_use_id="k")
        self.store.tick()
        ids = [a["id"] for a in self.store.snapshot()["agents"]]
        self.assertEqual(ids, [self.aid])


if __name__ == "__main__":
    unittest.main()
