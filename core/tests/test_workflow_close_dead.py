"""close-dead: provably dead blocked review/steward tasks become cancelled (temp homes, fake clock, no network)."""
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.store import Store


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(tmp, open_prs=(7,), **source):
    clock = Clock()
    store = Store(Path(tmp) / "workflow.db", clock=clock)
    clock.now = 1100.0
    store.set_source({"mode": "github", "synced_at": 5000.0, "error": "", "truncated": False,
                      "open_prs": [{"number": n} for n in open_prs], **source})
    clock.now = 6000.0
    return store, clock


def dead_task(store, ident, pr, **extra):
    store.create_task({"id": ident, "title": ident, "scope": {"kind": "steward", "pr": pr}, "execution_allowed": False,
                       "version": "V4.5.1", **extra})
    store.transition(ident, {"state": "blocked", "blocker": f"Superseded: PR #{pr} is no longer open"})


class CloseDeadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def build(self, **kw):
        store, clock = make(self.tmp.name, **kw)
        self.addCleanup(store.close)
        return store, clock

    def _stamp(self, store, clock):
        # tasks were blocked before the sync snapshot (synced_at 5000)
        clock.now = 4000.0

    def test_merged_pr_task_is_closed_and_open_pr_task_is_not(self):
        store, clock = self.build()
        self._stamp(store, clock)
        dead_task(store, "gone", 5)
        dead_task(store, "still-open", 7)
        clock.now = 6000.0
        dry = store.dead_blocked()
        self.assertEqual((dry["blocked"], dry["closable"], dry["ids"], dry["closed"]), (2, 1, ["gone"], []))
        self.assertEqual({t["id"]: t["state"] for t in store.snapshot()["tasks"]}["gone"], "blocked")  # dry run changes nothing
        done = store.dead_blocked(apply=True)
        self.assertEqual(done["closed"], ["gone"])
        tasks = {t["id"]: t for t in store.snapshot()["tasks"]}
        self.assertEqual(tasks["gone"]["state"], "cancelled")
        self.assertEqual(tasks["gone"]["validation"], "not_run")
        self.assertEqual(tasks["gone"]["delivery"], "none")
        self.assertIn("confirmed by GitHub sync", tasks["gone"]["closed_reason"])
        self.assertFalse(tasks["gone"]["ready"])
        self.assertEqual(tasks["still-open"]["state"], "blocked")
        self.assertEqual(store.dead_blocked(apply=True)["closed"], [])  # idempotent
        self.assertIn("dead_blocked_closed", [e["kind"] for e in store.snapshot()["events"]])

    def test_task_with_recorded_evidence_is_not_cancelled(self):
        store, clock = self.build()
        self._stamp(store, clock)
        dead_task(store, "proved", 30, acceptance=[{"id": "A1", "label": "reviewed"}])
        store.record_check("proved", "A1", "review passed, run link")
        clock.now = 6000.0
        self.assertEqual(store.dead_blocked()["ids"], [])
        self.assertEqual(store.dead_blocked(apply=True)["closed"], [])
        self.assertEqual({t["id"]: t["state"] for t in store.snapshot()["tasks"]}["proved"], "blocked")

    def test_malformed_open_pr_number_does_not_crash_snapshot(self):
        store, clock = self.build()
        self._stamp(store, clock)
        dead_task(store, "gone2", 31)
        store.set_source({"mode": "github", "synced_at": 5000.0, "error": "", "truncated": False,
                          "open_prs": [{"number": "abc"}, {"number": 7}]})
        clock.now = 6000.0
        result = store.dead_blocked(apply=True)
        self.assertEqual((result["sync_ok"], result["closed"]), (False, []))
        self.assertTrue(store.snapshot()["tasks"])

    def test_bounded_per_run(self):
        store, clock = self.build()
        self._stamp(store, clock)
        for n in range(30):
            dead_task(store, f"t{n}", 100 + n)
        clock.now = 6000.0
        result = store.dead_blocked(apply=True, limit=25)
        self.assertEqual((len(result["closed"]), result["skipped"]), (25, 5))

    def test_protected_cases_are_never_closed(self):
        store, clock = self.build()
        self._stamp(store, clock)
        dead_task(store, "v5", 20, version="V5")
        dead_task(store, "gate", 21, owner_gate="x")
        dead_task(store, "asked", 22)
        store.ask({"task_id": "asked", "title": "Q?", "blocks": True})
        dead_task(store, "base", 23)
        store.create_task({"id": "child", "title": "child", "depends_on": ["base"]})
        store.create_task({"id": "other", "title": "other", "scope": {"kind": "steward", "pr": 24}, "execution_allowed": False})
        store.transition("other", {"state": "blocked", "blocker": "waiting for the owner"})
        clock.now = 6000.0
        result = store.dead_blocked(apply=True)
        self.assertNotIn("v5", result["closed"])
        self.assertNotIn("gate", result["closed"])
        self.assertNotIn("asked", result["closed"])
        self.assertNotIn("base", result["closed"])
        self.assertNotIn("other", result["closed"])

    def test_untrusted_or_stale_sync_closes_nothing(self):
        for source in ({"error": "GitHub sync unavailable"}, {"truncated": True}, {"synced_at": 1.0}, {"open_prs": []}):
            with tempfile.TemporaryDirectory() as tmp:
                store, clock = make(tmp)
                self.addCleanup(lambda: None)
                clock.now = 4000.0
                dead_task(store, "gone", 5)
                clock.now = 6000.0
                store.set_source({"synced_at": 5000.0, **source} if "synced_at" not in source else source)
                if "synced_at" in source:
                    clock.now = 5000.0 + 25 * 3600
                result = store.dead_blocked(apply=True)
                self.assertEqual(result["closed"], [], source)
                self.assertFalse(result["sync_ok"], source)
                store.close()

    def test_task_blocked_after_the_snapshot_is_not_closed(self):
        store, clock = self.build()
        clock.now = 5500.0
        dead_task(store, "late", 5)
        self.assertEqual(store.dead_blocked(apply=True)["closed"], [])

    def test_cancelled_only_through_the_sweep(self):
        store, clock = self.build()
        self._stamp(store, clock)
        dead_task(store, "x", 5)
        with self.assertRaises(ValueError):
            store.transition("x", {"state": "cancelled"})
        clock.now = 6000.0
        store.dead_blocked(apply=True)
        with self.assertRaises(ValueError):
            store.transition("x", {"state": "verified"})
        self.assertEqual(store.transition("x", {"state": "queued"})["state"], "queued")  # explicit reopen

    def test_snapshot_reports_closable_count(self):
        store, clock = self.build()
        self._stamp(store, clock)
        dead_task(store, "x", 5)
        clock.now = 6000.0
        self.assertEqual(store.snapshot()["dead_blocked"], {"blocked": 1, "sync_ok": True, "closable": 1})


if __name__ == "__main__":
    unittest.main()
