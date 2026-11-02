"""#855: ready work excludes non-runnable tasks; superseded review tasks can be closed (temp home only)."""
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.store import Store


class ReadyFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "workflow.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def ready(self):
        return {t["id"] for t in self.store.snapshot()["tasks"] if t["ready"]}

    def test_unrunnable_tasks_are_not_ready_or_pushed_by_stop(self):
        self.store.create_task({"id": "ok", "title": "Runnable"})
        self.store.create_task({"id": "scope", "title": "Scope only", "execution_allowed": False})
        self.store.create_task({"id": "merged", "title": "Already on main"})
        self.store.transition("merged", {"implementation": "merged"})
        self.store.create_task({"id": "blocked", "title": "Blocked"})
        self.store.transition("blocked", {"state": "blocked", "blocker": "missing tool"})
        self.assertEqual(self.ready(), {"ok"})
        self.assertEqual(self.store.manager_continue("mgr"), ["ok"])

    def test_close_superseded_blocks_only_review_tasks_of_closed_prs(self):
        for n in (10, 11):
            self.store.create_task({"id": f"review-pr-{n}-abc", "title": f"Review {n}",
                                    "scope": {"pr": n, "kind": "tier_r_review"}})
        self.store.create_task({"id": "plain", "title": "Plain task"})
        closed = self.store.close_superseded(["11"])
        self.assertEqual(closed, ["review-pr-10-abc"])
        self.assertEqual(self.ready(), {"review-pr-11-abc", "plain"})
        task = next(t for t in self.store.snapshot()["tasks"] if t["id"] == "review-pr-10-abc")
        self.assertEqual(task["state"], "blocked")
        self.assertIn("Superseded", task["blocker"])


if __name__ == "__main__":
    unittest.main()
