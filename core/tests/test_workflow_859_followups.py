"""#859 follow-ups: empty close-superseded refusal, bounded git note, policy worker limit (temp homes only)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import hooks
from jarves.workflow.store import Store


class CloseSupersededEmptyTests(unittest.TestCase):
    def test_empty_list_refuses_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "workflow.db")
            try:
                store.create_task({"id": "r1", "title": "Review", "scope": {"kind": "tier_r_review", "pr": 5}})
                for empty in ([], [""] and []):
                    with self.assertRaises(ValueError):
                        store.close_superseded(empty)
                task = next(t for t in store.snapshot()["tasks"] if t["id"] == "r1")
                self.assertEqual(task["state"], "queued")
                self.assertEqual(store.close_superseded(["9"]), ["r1"])
            finally:
                store.close()


class GitNoteTests(unittest.TestCase):
    def setUp(self):
        hooks._GIT_NOTE_CACHE.clear()

    def test_timeouts_share_one_second_budget_and_note_is_cached(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(kw["timeout"])
            return mock.Mock(stdout="jarves\thttps://x/jarves.git\n" if "remote" in cmd else "abc1234\n")
        with mock.patch.object(hooks.subprocess, "run", fake_run):
            first = hooks.git_base_note("C:/nowhere")
            second = hooks.git_base_note("C:/nowhere")  # database retry path
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 2)  # not re-run
        self.assertTrue(all(0 < t <= hooks.GIT_NOTE_BUDGET_S for t in calls))
        self.assertLessEqual(hooks.GIT_NOTE_BUDGET_S, 1.0)

    def test_exhausted_budget_degrades_without_a_third_call(self):
        with mock.patch.object(hooks.time, "monotonic", side_effect=[0.0, 0.1, 5.0]), \
                mock.patch.object(hooks.subprocess, "run", return_value=mock.Mock(stdout="x")) as run:
            note = hooks.git_base_note("C:/other")
        self.assertIn("could not be verified", note)
        self.assertEqual(run.call_count, 1)


class WorkerLimitTests(unittest.TestCase):
    def test_default_three_and_policy_max_concurrent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "workflow.db")
            try:
                self.assertEqual(store._worker_limit(), 3)
                from jarves.workflow import autopilot
                policy, _ = autopilot.load(Path(tmp))
                policy["max_concurrent"] = 1
                autopilot.policy_path(Path(tmp)).write_text(json.dumps(policy), encoding="utf-8")
                self.assertEqual(store._worker_limit(), 1)
                autopilot.policy_path(Path(tmp)).write_text("{bad", encoding="utf-8")
                self.assertEqual(store._worker_limit(), 3)  # invalid policy falls back
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
