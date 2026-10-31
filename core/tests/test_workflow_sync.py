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

    def _fake(self, files_by_pr, runs):
        calls = []

        def get(path):
            calls.append(path)
            if "/issues?" in path:
                return []
            if "/pulls?" in path:
                return [{"number": n, "state": "open", "head": {"sha": f"sha{n}"}, "labels": []} for n in files_by_pr]
            if "/files?" in path:
                n = int(path.split("/pulls/")[1].split("/")[0])
                return [{"filename": f} for f in files_by_pr[n]]
            if "/check-runs" in path:
                return {"total_count": len(runs), "check_runs": runs}
            return {"commit": {"sha": "mainsha"}}
        return get, calls

    def test_pr_files_drive_tier_r_and_are_cached_by_sha(self):
        from jarves.workflow import autopilot_watch as watch, sync as sync_mod
        sync_mod._FILES_CACHE.clear()
        get, calls = self._fake({9001: ["docs/guide.md"], 9002: ["core/src/jarves/foo_plain.py"],
                                 9003: [".claude/settings.json"], 9004: ["core/src/jarves/workflow/hooks.py"]}, [])
        result = collect(get)
        by = {p["number"]: p for p in result["prs"]}
        self.assertEqual(by[9001]["files"], ["docs/guide.md"])
        self.assertFalse(watch.is_tier_r(by[9001]["files"], []))
        self.assertFalse(watch.is_tier_r(by[9002]["files"], []))
        self.assertTrue(watch.is_tier_r(by[9003]["files"], []))
        self.assertTrue(watch.is_tier_r(by[9004]["files"], []))
        self.assertTrue(watch.is_tier_r(by[9001]["files"], ["no-automerge"]))
        collect(get)
        self.assertEqual(sum("/files?" in c for c in calls), 4)  # second sync used the cache

    def test_ci_green_from_main_check_runs(self):
        ok = [{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "skipped"}]
        self.assertTrue(collect(self._fake({}, ok)[0])["ci_green"])
        self.assertFalse(collect(self._fake({}, ok + [{"status": "in_progress", "conclusion": None}])[0])["ci_green"])
        self.assertFalse(collect(self._fake({}, ok + [{"status": "completed", "conclusion": "failure"}])[0])["ci_green"])
        self.assertFalse(collect(self._fake({}, [])[0])["ci_green"])
        apply_snapshot(self.store, collect(self._fake({}, ok)[0]))
        self.assertTrue(self.store.snapshot()["source"]["ci_green"])


if __name__ == "__main__":
    unittest.main()
