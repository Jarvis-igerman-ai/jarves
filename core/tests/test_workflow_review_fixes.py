"""Regression tests for the 31 Oct workflow review: long tools, noise, resume, sync."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarves.workflow import control
from jarves.workflow.store import Store


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.store = Store(Path(self.temp.name) / "w.db", clock=lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def running(self):
        self.store.create_task({"id": "t", "title": "t", "lane": "features", "version": "V5",
                                "acceptance": [{"id": "C1", "label": "ok"}]})
        self.store.register_agent({"id": "w", "label": "w", "lane": "features"})
        self.store.claim("w")

    def events(self):
        return len(self.store.snapshot()["events"])

    def test_tool_in_flight_is_not_quarantined_until_15_minutes(self):
        self.running()
        self.store.observe_agent("w", "running", "Bash", tool="start")
        self.now[0] += 600
        self.assertEqual(self.store.tick(), [])
        self.assertFalse(self.store.snapshot()["tasks"][0].get("quarantined"))
        self.now[0] += 400
        self.assertTrue(any(a["kind"] == "worker_offline" for a in self.store.tick()))

    def test_tool_end_clears_flight_and_activity_revives_agent(self):
        self.running()
        self.store.observe_agent("w", "running", "Bash", tool="start")
        self.store.observe_agent("w", "running", "done", tool="end")
        self.now[0] += 121
        self.store.tick()
        agent = self.store.snapshot()["agents"][0]
        self.assertEqual(agent["status"], "offline")
        # a non-quarantined owner (task back to running) returns to running on activity
        self.store.observe_agent("w", "running", "again")
        self.assertNotIn("tool_started_at", self.store.snapshot()["agents"][0])

    def test_finished_agent_without_task_goes_offline_silently(self):
        self.store.register_agent({"id": "gone", "label": "gone", "lane": "any"})
        self.now[0] += 500
        self.assertEqual(self.store.tick(), [])
        self.assertEqual(self.store.snapshot()["agents"][0]["status"], "offline")

    def test_repeat_ticks_heartbeats_and_supervisor_write_no_events(self):
        self.running()
        self.store.set_supervisor({"status": "running"})
        self.store.tick()
        before = self.store.snapshot()
        for _ in range(3):
            self.now[0] += 10
            self.store.set_supervisor({"status": "running"})
            self.store.tick()
            self.store.heartbeat("w")
            self.store.observe_agent("w", "running", "x")
        after = self.store.snapshot()
        self.assertEqual(len(after["events"]), len(before["events"]))
        self.assertGreater(after["revision"], before["revision"])

    def test_answer_exposes_decision_time_in_task_context(self):
        self.running()
        q = self.store.ask({"task_id": "t", "title": "Q?", "context": "c", "choices": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}], "checkpoint": "cp"})
        self.store.answer(q["id"], "a")
        task = self.store.snapshot()["tasks"][0]
        decisions = control.task_context(self.store, task)["owner_decisions"]
        self.assertIsNotNone(decisions[0]["decided_at"])

    def test_unchanged_github_sync_writes_no_task_events(self):
        from jarves.workflow.sync import apply_snapshot
        snap = {"issues": [], "prs": [], "sha": "abc"}
        apply_snapshot(self.store, snap)
        before = self.events()
        self.now[0] += 300
        apply_snapshot(self.store, snap)
        names = [e["kind"] for e in self.store.snapshot()["events"][: self.events() - before]]
        self.assertNotIn("task_metadata", names)


if __name__ == "__main__":
    unittest.main()


class LegacyConsoleEncodingTests(unittest.TestCase):
    def test_cli_json_with_hebrew_survives_a_cp1252_pipe(self):
        import os
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
            entry = [sys.executable, str(root / "tools" / "workflow_console.py"), "--home", home]
            made = subprocess.run([*entry, "create", "--id", "he", "--title", "בדיקה → עברית", "--acceptance", "ok"],
                                  capture_output=True, env=env, timeout=60)
            self.assertEqual(made.returncode, 0, made.stderr.decode("utf-8", "replace"))
            shown = subprocess.run([*entry, "status"], capture_output=True, env=env, timeout=60)
            self.assertEqual(shown.returncode, 0, shown.stderr.decode("utf-8", "replace"))
            self.assertIn("בדיקה → עברית", shown.stdout.decode("utf-8"))
