"""Autopilot part 1: policy, version order, bug label scope and the work list. Hermetic, no network, no workers."""
import json
import tempfile
import unittest
from pathlib import Path

from jarves.workflow import autopilot
from jarves.workflow.store import Store
from jarves.workflow.sync import apply_snapshot


def issue(n, labels, state="open"):
    return {"number": n, "title": f"Issue {n}", "state": state, "url": f"https://x/{n}", "labels": labels}


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = Store(self.home / "workflow.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def sync(self, issues, prs=(), plan=None):
        apply_snapshot(self.store, {"sha": "m", "issues": issues, "prs": list(prs)}, plan or {"versions": []})

    def tasks(self):
        return {t["id"]: t for t in self.store.snapshot()["tasks"]}

    def test_policy_without_agent_retention_stays_valid(self):
        # #730: a policy file saved before the retention key existed still loads (autopilot does not fail closed).
        import json
        old = json.loads(json.dumps(autopilot.DEFAULTS))
        del old["watchdog"]["agent_retention_s"]
        old["enabled"] = True
        (self.home / "autopilot.json").write_text(json.dumps(old), encoding="utf-8")
        policy, error = autopilot.load(self.home)
        self.assertEqual((error, policy["enabled"], autopilot.agent_retention_s(self.home)), ("", True, 86400))
        autopilot.set_key(self.home, "watchdog.agent_retention_s", "3600")
        self.assertEqual(autopilot.agent_retention_s(self.home), 3600)
        with self.assertRaises(ValueError):
            autopilot.set_key(self.home, "watchdog.agent_retention_s", str(31 * 86400))

    def test_policy_validation(self):
        policy, error = autopilot.load(self.home)
        self.assertEqual((error, policy["enabled"]), ("", False))
        autopilot.set_key(self.home, "lanes.bugs.workers", "2")
        self.assertEqual(autopilot.load(self.home)[0]["lanes"]["bugs"]["workers"], 2)
        for key, value in (("max_concurrent", "99"), ("nope", "1"), ("lanes.bugs.model", '"bad model!"'),
                           ("authorized_versions", '["V9"]'), ("enabled", "1")):
            with self.assertRaises(ValueError, msg=key):
                autopilot.set_key(self.home, key, value)
        (self.home / "autopilot.json").write_text("{broken", encoding="utf-8")
        policy, error = autopilot.load(self.home)
        self.assertTrue(error)
        self.assertFalse(policy["enabled"])
        self.assertEqual(autopilot.step(self.store, self.home), {"skipped": error})

    def test_kill_switch_blocks_import_and_authorisation(self):
        autopilot.command(self.store, self.home, "set", ["enabled", "true"])
        autopilot.command(self.store, self.home, "stop", [])
        self.sync([issue(5, ["bug"])])
        self.assertIn("skipped", autopilot.command(self.store, self.home, "once", []))
        self.assertNotIn("gh-5", self.tasks())
        autopilot.command(self.store, self.home, "start", [])
        self.assertEqual(autopilot.command(self.store, self.home, "once", [])["created"], ["gh-5"])

    def test_bug_label_scope(self):
        self.sync([issue(1, ["P1"]), issue(2, ["showstopper"]), issue(3, ["docs"]), issue(4, ["bug", "P3"]),
                   issue(6, ["P2"], state="closed"), issue(7, ["v5"]), issue(8, ["bug", "5.0"])])
        self.assertEqual(self.store.snapshot()["source"]["open_bugs"], 4)
        autopilot.command(self.store, self.home, "once", [])
        tasks = self.tasks()
        self.assertEqual(sorted(k for k in tasks if k.startswith("gh-")), ["gh-1", "gh-2", "gh-4", "gh-7", "gh-8"])
        self.assertEqual((tasks["gh-1"]["lane"], tasks["gh-1"]["priority"], tasks["gh-1"]["version"]), ("bugs", 1, "V4.5.1"))
        self.assertEqual(tasks["gh-2"]["priority"], 0)
        self.assertEqual((tasks["gh-7"]["lane"], tasks["gh-7"]["version"]), ("features", "V5"))
        self.assertEqual(tasks["gh-8"]["lane"], "bugs")
        # Imported rows are not runnable until the policy is enabled and their version authorised.
        self.assertFalse(any(tasks[k]["execution_allowed"] for k in tasks))
        autopilot.command(self.store, self.home, "set", ["enabled", "true"])
        autopilot.command(self.store, self.home, "once", [])
        tasks = self.tasks()
        self.assertTrue(tasks["gh-1"]["execution_allowed"])
        self.assertFalse(tasks["gh-7"]["execution_allowed"])
        self.assertFalse(tasks["gh-8"]["execution_allowed"])
        autopilot.command(self.store, self.home, "set", ["authorized_versions", '["V4.5.1","V5"]'])
        autopilot.command(self.store, self.home, "once", [])
        self.assertTrue(self.tasks()["gh-7"]["execution_allowed"])
        self.assertTrue(self.tasks()["gh-8"]["execution_allowed"])
        # Re-running creates nothing new.
        self.assertEqual(autopilot.step(self.store, self.home)["created"], [])

    def test_version_order(self):
        plan = {"versions": [
            {"id": "V5", "requirements": [{"id": "V5-A", "title": "a", "priority": 0}]},
            {"id": "V4.5.1", "requirements": [{"id": "V451-A", "title": "a", "lane": "features", "priority": 3}]}]}
        self.sync([], plan=plan)
        for ident in ("V5-A", "V451-A"):
            self.store.update_metadata(ident, {"execution_allowed": True}, authorize_execution=True)
        policy = autopilot.load(self.home)[0]
        # P3 in the earlier version beats P0 in the later one.
        self.assertEqual(autopilot.next_task(self.store.snapshot(), policy, "features"), "V451-A")
        self.store.update_metadata("V451-A", {"execution_allowed": False}, authorize_execution=True)
        # The earlier version still has open required work, so the later one is not picked.
        self.assertIsNone(autopilot.next_task(self.store.snapshot(), policy, "features"))

    def test_work_list_and_cli_status(self):
        self.sync([issue(1, ["P0"]), issue(2, ["bug"])],
                  prs=[{"number": 9, "title": "Fix", "state": "open", "labels": ["no-automerge"], "linked_issues": [1],
                        "url": "u"}])
        autopilot.command(self.store, self.home, "once", [])
        view = autopilot.command(self.store, self.home, "status", [])
        self.assertEqual([(w["kind"], w["number"]) for w in view["work"]], [("issue", 1), ("issue", 2), ("pr", 9)])
        self.assertEqual(view["work"][2]["task_id"], "gh-1")
        self.assertEqual(view["work"][2]["ci"], "unknown")
        autopilot.command(self.store, self.home, "set", ["max_starts_per_hour", "3"])
        self.assertEqual(autopilot.command(self.store, self.home, "status", [])["policy"]["max_starts_per_hour"], 3)
        json.dumps(view)


if __name__ == "__main__":
    unittest.main()
