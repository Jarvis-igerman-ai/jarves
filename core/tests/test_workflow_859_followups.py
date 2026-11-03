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
        # Fake clock: 252557.90562814512 + 0.6 - 252557.90562814512 > 0.6 in floating point, which failed
        # on Windows with the real clock. The timeouts must still stay inside (0, budget].
        with mock.patch.object(hooks.subprocess, "run", fake_run), \
                mock.patch.object(hooks.time, "monotonic", side_effect=[252557.90562814512] + [252557.9056281452] * 4):
            first = hooks.git_base_note("C:/nowhere")
            second = hooks.git_base_note("C:/nowhere")  # database retry path
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 4)  # not re-run
        self.assertTrue(all(0 < t <= hooks.GIT_NOTE_BUDGET_S for t in calls))
        self.assertLessEqual(hooks.GIT_NOTE_BUDGET_S, 1.0)

    def test_exhausted_budget_degrades_without_a_third_call(self):
        with mock.patch.object(hooks.time, "monotonic", side_effect=[0.0, 0.1, 5.0]), \
                mock.patch.object(hooks.subprocess, "run", return_value=mock.Mock(stdout="x")) as run:
            note = hooks.git_base_note("C:/other")
        self.assertIn("could not be verified", note)
        self.assertEqual(run.call_count, 1)


class GitNoteBehindTests(unittest.TestCase):
    """#852: a worktree base behind jarves/main is warned about with the exact tip and command."""

    def setUp(self):
        hooks._GIT_NOTE_CACHE.clear()

    def _note(self, tip, behind):
        def fake_run(cmd, **kw):
            if "remote" in cmd:
                return mock.Mock(stdout="jarves\thttps://x/jarves.git\n")
            if "--count" in cmd:
                return mock.Mock(stdout=behind)
            return mock.Mock(stdout=tip if "refs/remotes/jarves/main" in cmd else "a13b56d0\n")
        with mock.patch.object(hooks.subprocess, "run", fake_run):
            return hooks.git_base_note("C:/wt")

    def test_behind_names_tip_and_command(self):
        note = self._note("7e6db095\n", "42\n")
        self.assertIn("WARNING", note)
        self.assertIn("42 commit(s) behind jarves/main 7e6db095", note)
        self.assertIn("git switch -C", note)

    def test_current_or_missing_ref_keeps_plain_note(self):
        self.assertNotIn("WARNING", self._note("7e6db095\n", "0\n"))
        hooks._GIT_NOTE_CACHE.clear()
        self.assertNotIn("WARNING", self._note("", ""))


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


class ReportFlagTests(unittest.TestCase):
    """`report` without --agent is the owner's morning report; with --agent it is a heartbeat."""

    def test_report_with_and_without_agent(self):
        import contextlib
        import io
        from jarves.workflow import __main__ as wf
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = wf.main(["report", "--json", "--home", tmp])
            self.assertEqual(rc, 0, err.getvalue())
            json.loads(out.getvalue())
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = wf.main(["agent", "--agent", "a1", "--home", tmp])
                rc2 = wf.main(["report", "--agent", "a1", "--note", "hi", "--home", tmp])
            self.assertEqual((rc, rc2), (0, 0), err.getvalue())
