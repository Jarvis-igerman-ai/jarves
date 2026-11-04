"""Issue sync queues executable gh-N tasks for open agent-doable defects (metadata only)."""
import json
import tempfile
import os
import unittest
from unittest import mock
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

    def run_sync(self, issues):
        with mock.patch.dict(os.environ, {"JARVES_WORKFLOW_ISSUE_TASKS": "1"}):
            self.sync_now(issues)

    def sync_now(self, issues):
        apply_snapshot(self.store, {"sha": "m", "prs": [], "issues": issues}, PLAN, issue_tasks=True)

    def test_plain_apply_snapshot_creates_nothing(self):
        apply_snapshot(self.store, {"sha": "m", "prs": [], "issues": [issue(9, ["bug"])]}, PLAN)
        self.assertNotIn("gh-9", self.tasks())

    def test_creates_only_for_defects_and_agent_doable(self):
        self.run_sync([issue(2, ["enhancement"]), issue(3, ["enhancement", "agent-doable"]),
                       issue(4, ["bug", "owner-gated"]), issue(5, ["bug"], "closed"),
                       issue(6, ["bug", "P1"])])
        t = self.tasks()
        self.assertEqual(sorted(k for k in t if k.startswith("gh-")), ["gh-3", "gh-6"])
        self.assertEqual((t["gh-6"]["lane"], t["gh-6"]["priority"], t["gh-6"]["version"]), ("bugs", 1, "V4.5.1"))
        self.assertEqual((t["gh-3"]["lane"], t["gh-3"]["priority"]), ("features", 2))
        self.assertFalse(t["gh-6"]["execution_allowed"])
        self.assertEqual(t["gh-6"]["required_validation"], "app")
        self.assertEqual(len(t["gh-6"]["acceptance"]), 1)

    def test_issue_covered_by_imported_scope_gets_no_duplicate(self):
        self.run_sync([issue(1, ["bug"]), issue(2, ["bug", "v7"])])
        t = self.tasks()
        self.assertFalse([k for k in t if k.startswith("gh-")])
        self.assertFalse(t["V5-ONE"]["execution_allowed"])
        self.assertFalse(t["ISSUE-2"]["execution_allowed"])

    def test_default_off_without_opt_in(self):
        self.sync_now([issue(9, ["bug"])])
        self.assertNotIn("gh-9", self.tasks())

    def _write_policy(self, **over):
        from jarves.workflow import autopilot
        policy = {**autopilot.DEFAULTS, **over}
        (Path(self.temp.name) / "autopilot.json").write_text(json.dumps(policy), encoding="utf-8")

    def test_created_tasks_never_authorised_autopilot_off_or_killed(self):
        on = {"JARVES_WORKFLOW_ISSUE_TASKS": "1"}
        for n, over in ((40, {"enabled": False}), (41, {"enabled": True, "kill": True})):
            self._write_policy(**over)
            create_issue_tasks(self.store, {"issues": [issue(n, ["bug"])]}, environ=on)
            self.assertFalse(self.tasks()[f"gh-{n}"]["execution_allowed"])

    def test_v451_removed_from_policy_stays_unauthorised(self):
        self._write_policy(authorized_versions=["V5"], versions=["V4.5.1", "V5"])
        create_issue_tasks(self.store, {"issues": [issue(50, ["bug"]), issue(51, ["bug", "v6"])]},
                           environ={"JARVES_WORKFLOW_ISSUE_TASKS": "1"})
        t = self.tasks()
        self.assertFalse(t["gh-50"]["execution_allowed"])
        self.assertFalse(t["gh-51"]["execution_allowed"])

    def test_cap_and_existing_tasks_untouched(self):
        issues = [issue(n, ["bug"]) for n in range(10, 25)]
        self.run_sync(issues)
        self.assertEqual(len([k for k in self.tasks() if k.startswith("gh-")]), 10)
        self.store.register_agent({"id": "a", "name": "a"})
        self.store.update_metadata("gh-10", {"execution_allowed": True}, authorize_execution=True)
        self.store.claim("a", "gh-10")
        self.run_sync(issues)
        t = self.tasks()
        self.assertEqual(len([k for k in t if k.startswith("gh-")]), 15)
        self.assertEqual(t["gh-10"]["state"], "running")

    def test_manual_task_covers_and_closed_not_created(self):
        self.store.create_task({"id": "manual", "title": "m", "issue_numbers": [7]})
        self.run_sync([issue(7, ["bug"]), issue(8, ["bug"], "closed")])
        self.assertFalse([k for k in self.tasks() if k.startswith("gh-")])

    def test_opt_in_switch(self):
        snap = {"issues": [issue(30, ["bug"])]}
        self.assertEqual(create_issue_tasks(self.store, snap, environ={}), [])
        self.assertEqual(create_issue_tasks(self.store, snap, environ={"JARVES_WORKFLOW_ISSUE_TASKS": "1"}), ["gh-30"])

    def test_device_labels_require_device_validation(self):
        self.run_sync([issue(10, ["bug", "mic"]), issue(11, ["bug", "Phone"]), issue(12, ["bug", "wake_word"]),
                       issue(13, ["bug"])])
        t = self.tasks()
        self.assertEqual([t[f"gh-{n}"]["required_validation"] for n in (10, 11, 12, 13)],
                         ["device", "device", "device", "app"])

    def test_known_issue_and_needs_owner_are_skipped(self):
        self.run_sync([issue(20, ["bug", "known-issue-android"]), issue(21, ["bug", "needs-owner"]),
                       issue(22, ["bug", "Owner_Gated"]), issue(23, ["bug"])])
        self.assertEqual(sorted(k for k in self.tasks() if k.startswith("gh-")), ["gh-23"])

    def test_non_dict_source_issues_do_not_abort(self):
        self.store.create_task({"id": "m1", "title": "m", "lane": "bugs", "source_issues": ["x", 5, None]})
        self.run_sync([issue(31, ["bug"])])
        self.assertIn("gh-31", self.tasks())


if __name__ == "__main__":
    unittest.main()
