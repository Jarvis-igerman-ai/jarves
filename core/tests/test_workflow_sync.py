"""GitHub is metadata only; closure/merge cannot manufacture acceptance."""
import tempfile
import unittest
import urllib.error
from pathlib import Path

from jarves.workflow.store import Store
from jarves.workflow.sync import apply_snapshot, collect, sync


class WorkflowSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "workflow.db", clock=lambda: 1000)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_closed_issue_and_merged_pr_remain_separate_from_verification(self):
        plan = {"baseline_sha": "baseline", "versions": [{"id": "V5", "requirements": [
            {"id": "V5-ONE", "title": "One", "issue_numbers": [1], "acceptance": [{"id": "C1", "label": "Real app works"}]},
            {"id": "V5-UNMAPPED", "title": "Unknown", "issue_numbers": []}]}]}
        source = apply_snapshot(self.store, {"sha": "main", "issues": [
            {"number": 1, "state": "closed", "title": "Fixed", "labels": ["bug"]}],
            "prs": [{"number": 2, "state": "closed", "merged_at": "date", "linked_issues": [1]}]}, plan)
        task = next(t for t in self.store.snapshot()["tasks"] if t["id"] == "V5-ONE")
        self.assertTrue(task["source_closed"])
        self.assertEqual(task["source_prs"][0]["number"], 2)
        self.assertEqual(task["validation"], "not_run")
        self.assertFalse(task["acceptance"][0]["passed"])
        self.assertFalse(task["execution_allowed"])
        self.assertIn("V5-UNMAPPED", source["unmapped"])

    def test_outage_keeps_last_snapshot_and_reports_staleness(self):
        self.store.set_source({"mode": "github", "sha": "last-good", "synced_at": 900, "unmapped": []})
        def unavailable(_):
            raise urllib.error.URLError("offline")
        result = sync(self.store, unavailable)
        self.assertEqual(result["sha"], "last-good")
        self.assertEqual(result["synced_at"], 900)
        self.assertTrue(result["error"])
        self.assertEqual(result["failed_at"], 1000)

    def test_collection_excludes_pr_issues_and_never_infers_merge_from_closed(self):
        def get(path):
            if "/issues?" in path:
                return [{"number": 1, "state": "closed"}, {"number": 2, "pull_request": {}}]
            if "/pulls?" in path:
                return [{"number": 2, "state": "closed", "body": "Fixes #1", "head": {"sha": "pr"}}]
            return {"commit": {"sha": "main"}}
        result = collect(get)
        self.assertEqual([x["number"] for x in result["issues"]], [1])
        self.assertIsNone(result["prs"][0]["merged_at"])
        self.assertEqual(result["prs"][0]["linked_issues"], [1])


if __name__ == "__main__":
    unittest.main()
