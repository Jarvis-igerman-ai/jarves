"""#786 / JAR-214: concurrent SubagentStart hooks must not lose events to 'database is locked' (temp home only)."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import hooks
from jarves.workflow.store import Store

CORE_SRC = Path(__file__).resolve().parents[1] / "src"


class OneTransactionTests(unittest.TestCase):
    """Deterministic: count the write transactions a registration hook opens (no clock, no races)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "wf"
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        Store(self.home / "workflow.db").close()
        hooks._GIT_NOTE_CACHE.clear()
        hooks._IN_PROJECT_CACHE.clear()
        self.stores = []

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, event, agent_id=None, cwd=None):
        """Run hooks.main in-process; returns (statements per store, printed output)."""
        statements = []
        test = self
        self.stores = []

        class Traced(Store):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self._db.set_trace_callback(statements.append)
                test.stores.append(self)

        payload = {"hook_event_name": event, "session_id": "s", "cwd": cwd or str(self.root)}
        if agent_id:
            payload.update(agent_id=agent_id, agent_type="workflow-bugs")
        stdin = mock.Mock(buffer=io.BytesIO(json.dumps(payload).encode()))
        out = io.StringIO()
        with mock.patch.object(hooks, "Store", Traced), mock.patch.object(sys, "stdin", stdin), \
                mock.patch.object(hooks.time, "sleep"), mock.patch.object(sys, "stdout", out):
            self.assertEqual(hooks.main(["--home", str(self.home), "--repo-root", str(self.root)]), 0)
        return statements, out.getvalue()

    def _agents(self):
        store = Store(self.home / "workflow.db")
        try:
            return store.snapshot()["agents"]
        finally:
            store.close()

    def test_subagent_and_session_start_each_open_exactly_one_write_transaction(self):
        for event, agent in (("SubagentStart", "child"), ("SessionStart", None)):
            with self.subTest(event=event):
                statements, out = self._run(event, agent)
                begins = [s for s in statements if s.startswith("BEGIN")]
                self.assertEqual(begins, ["BEGIN IMMEDIATE"])   # main: about four per event
                self.assertIn("additionalContext", out)
        self.assertEqual(len(self._agents()), 2)   # the subagent and the manager were both registered

    def test_error_mid_event_rolls_back_everything_restores_tx_and_the_retry_succeeds(self):
        real = Store.observe_agent
        seen = []

        def flaky(store, *a, **k):
            seen.append([a["id"] for a in store.snapshot()["agents"]])   # register_agent already wrote, uncommitted
            if len(seen) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(store, *a, **k)

        with mock.patch.object(Store, "observe_agent", flaky):
            statements, out = self._run("SubagentStart", "child")
        self.assertEqual(len(seen), 2)                          # failed once, retried once
        self.assertIn("ROLLBACK", statements)
        self.assertNotIn("_tx", vars(self.stores[0]))           # the instance override was removed
        self.assertEqual(len(self._agents()), 1)
        self.assertIn("additionalContext", out)

    def test_rollback_leaves_no_partial_rows(self):
        store = Store(self.home / "workflow.db")
        try:
            with self.assertRaises(sqlite3.OperationalError), hooks._one_transaction(store):
                store.register_agent({"id": "half-written", "lane": "bugs"})
                raise sqlite3.OperationalError("database is locked")
            self.assertNotIn("_tx", vars(store))
            self.assertEqual(store.snapshot()["agents"], [])
        finally:
            store.close()

    def test_no_subprocess_runs_while_the_write_lock_is_held(self):
        outside = Path(self.temp.name) / "worktree"   # outside the root: in_project must ask git
        outside.mkdir()
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append([s._db.in_transaction for s in self.stores])
            common = str(self.root / ".git") if "--git-common-dir" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=common, stderr="")

        for event, agent in (("SubagentStart", "child"), ("SessionStart", None)):
            with self.subTest(event=event), mock.patch.object(hooks.subprocess, "run", fake_run):
                hooks._GIT_NOTE_CACHE.clear()
                hooks._IN_PROJECT_CACHE.clear()
                calls.clear()
                _, out = self._run(event, agent, cwd=str(outside))
                self.assertTrue(calls)                                       # git was asked (in_project, base note)
                self.assertFalse(any(any(c) for c in calls), calls)          # never inside the transaction
                self.assertIn("additionalContext", out)


class ConcurrentSubagentStartTests(unittest.TestCase):
    """Smoke test with eight real hook processes and no injected delays or timing assumptions.

    OneTransactionTests is the deterministic regression guard, since commits here are too fast to queue.
    """
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "wf"
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        Store(self.home / "workflow.db").close()   # the ledger already exists, as on the owner PC

    def tearDown(self):
        self.temp.cleanup()

    def test_eight_concurrent_subagent_starts_register_without_lock_errors(self):
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(CORE_SRC), os.environ.get("PYTHONPATH")]))}
        for key in ("JARVES_WORKFLOW_AGENT", "JARVES_WORKFLOW_HELPER"):
            env.pop(key, None)
        cmd = [sys.executable, "-m", "jarves.workflow.hooks", "--home", str(self.home), "--repo-root", str(self.root)]
        procs = []
        for n in range(8):
            payload = {"hook_event_name": "SubagentStart", "session_id": "s", "agent_id": f"child-{n}",
                       "agent_type": "workflow-bugs", "cwd": str(self.root)}
            stdin = Path(self.temp.name) / f"payload-{n}.json"   # a file: every hook gets its input at once
            stdin.write_text(json.dumps(payload), encoding="utf-8")
            with stdin.open("rb") as fh:
                procs.append(subprocess.Popen(cmd, stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env))
        outputs = [proc.communicate(timeout=60) for proc in procs]
        errors_log = self.home / "hook_errors.log"
        errors = errors_log.read_text(encoding="utf-8") if errors_log.exists() else ""
        self.assertNotIn("OperationalError", errors)   # main() logs a 'database is locked' failure by type name
        for out, err in outputs:
            self.assertNotIn(b"database is locked", err)
            self.assertNotIn(b"observer unavailable", err)
            self.assertIn("additionalContext", out.decode())
        store = Store(self.home / "workflow.db")
        try:
            self.assertEqual(len(store.snapshot()["agents"]), 8)   # every worker was registered
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
