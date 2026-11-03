"""Issue sync queues executable gh-N tasks for open agent-doable defects (metadata only)."""
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.store import Store
from jarves.workflow.sync import apply_snapshot, create_issue_tasks

PLAN = {"baseline_sha": "b", "versions": [{"id": "V5", "requirements": [
    {"id": "V5-ONE", "title": "One", "issue_numbers": [1]}]}]}


def issue(number, labels, state="open"):
    return {"number": number, "state": state, "title": f"t{number}", "labels": labels, "url": "u"}


class IssueTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "workflow.db", clock=lambda: 1000)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def tasks(self):
        return {t["id"]: t for t in self.store.snapshot()["tasks"]}

    def test_creates_only_for_defects_and_agent_doable(self):
        snap = {"sha": "m", "prs": [], "issues": [
            issue(1, ["bug", "P1"]), issue(2, ["enhancement"]),
            issue(3, ["enhancement", "agent-doable", "4.5.1"]),
            issue(4, ["bug", "owner-gated"]), issue(5, ["bug"], "closed"),
            issue(6, ["showstopper", "v6"])]}
        apply_snapshot(self.store, snap, PLAN)
        t = self.tasks()
        self.assertEqual(sorted(k for k in t if k.startswith("gh-")), ["gh-1", "gh-3", "gh-6"])
        self.assertEqual((t["gh-1"]["lane"], t["gh-1"]["priority"], t["gh-1"]["version"]), ("bugs", 1, "V4.5.1"))
        self.assertEqual((t["gh-3"]["lane"], t["gh-3"]["priority"]), ("features", 2))
        self.assertEqual(t["gh-6"]["version"], "V6")
        self.assertTrue(t["gh-1"]["execution_allowed"])
        self.assertEqual(t["gh-1"]["required_validation"], "automated")
        self.assertFalse(t["V5-ONE"]["execution_allowed"])  # imported scope stays unauthorised

    def test_cap_and_existing_tasks_untouched(self):
        snap = {"sha": "m", "prs": [], "issues": [issue(n, ["bug"]) for n in range(10, 25)]}
        apply_snapshot(self.store, snap, PLAN)
        self.assertEqual(len([k for k in self.tasks() if k.startswith("gh-")]), 10)
        self.store.register_agent({"id": "a", "name": "a"})
        self.store.claim("a", "gh-10")
        apply_snapshot(self.store, snap, PLAN)
        t = self.tasks()
        self.assertEqual(len([k for k in t if k.startswith("gh-")]), 15)
        self.assertEqual(t["gh-10"]["state"], "running")

    def test_covered_and_closed_not_recreated(self):
        self.store.create_task({"id": "manual", "title": "m", "issue_numbers": [7]})
        snap = {"sha": "m", "prs": [], "issues": [issue(7, ["bug"]), issue(8, ["bug"])]}
        apply_snapshot(self.store, snap, PLAN)
        self.assertNotIn("gh-7", self.tasks())
        self.assertIn("gh-8", self.tasks())
        snap["issues"][1]["state"] = "closed"
        self.assertEqual(create_issue_tasks(self.store, snap, environ={}), [])

    def test_opt_out_switch(self):
        snap = {"issues": [issue(1, ["bug"])]}
        self.assertEqual(create_issue_tasks(self.store, snap, environ={"JARVES_WORKFLOW_ISSUE_TASKS": "0"}), [])
        self.assertEqual(create_issue_tasks(self.store, snap, environ={}), ["gh-1"])


if __name__ == "__main__":
    unittest.main()
