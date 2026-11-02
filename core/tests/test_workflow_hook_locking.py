"""#786: inbox reads take no write lock; registration hooks retry a busy DB (temp home only)."""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import hooks
from jarves.workflow.store import Store


class HookLockingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "wf"
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_open_followups_works_while_another_connection_holds_the_write_lock(self):
        store = Store(self.home / "workflow.db", timeout_s=0.1)
        other = sqlite3.connect(str(self.home / "workflow.db"), isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            self.assertEqual(store.open_followups("anyone"), [])
        finally:
            other.execute("ROLLBACK")
            other.close()
            store.close()

    def test_subagent_start_retries_once_when_database_is_busy(self):
        calls = []
        real = hooks.observe

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(*a, **k)

        payload = {"hook_event_name": "SubagentStart", "session_id": "s", "agent_id": "child",
                   "agent_type": "workflow-bugs", "cwd": str(self.root)}
        stdin = mock.Mock(buffer=io.BytesIO(json.dumps(payload).encode()))
        with mock.patch.object(hooks, "observe", flaky), mock.patch.object(sys, "stdin", stdin), \
                mock.patch.object(hooks.time, "sleep"), mock.patch("builtins.print"):
            code = hooks.main(["--home", str(self.home), "--repo-root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        store = Store(self.home / "workflow.db")
        try:
            self.assertEqual(len(store.snapshot()["agents"]), 1)  # the worker was registered
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
