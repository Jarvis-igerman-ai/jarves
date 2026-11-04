"""#784 review: the worker allow rule covers the whole workflow_console.py script, so owner-only actions refuse in an agent env."""
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.store import Store
from test_workflow_owner_answer_guard import run_cli

AGENT_ENV = {"JARVES_WORKFLOW_AGENT": "claude-test"}


class WorkerOwnerOnlyActionsTests(unittest.TestCase):
    def test_owner_only_actions_refused_for_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for args in (("autopilot", "set", "worker_allowed_tools", '["Bash(*)"]'), ("autopilot", "start"),
                         ("autopilot", "grant"), ("authorize", "--task", "t1"), ("bootstrap", "--apply"),
                         ("unlock",), ("dispatch",), ("create", "--title", "x"), ("recover", "--task", "t1"),
                         ("trace", "--capture", "details"), ("release", "--resource", "pr:1", "--force"),
                         ("questions-rewrite", "--apply"), ("close-superseded", "1"), ("sync",), ("seed",), ("handover",)):
                code, _out, err = run_cli(home, *args, tty=True, env=AGENT_ENV)
                self.assertEqual(code, 3, args)
                self.assertIn("owner-only", err)
            self.assertFalse((home / "autopilot.json").exists())

    def test_worker_actions_and_autopilot_status_still_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            Store(home / "workflow.db").close()
            for args in (("autopilot", "status"), ("check-touch", "--resource", "pr:1")):
                _code, _out, err = run_cli(home, *args, tty=False, env=AGENT_ENV)
                self.assertNotIn("owner-only", err, args)


if __name__ == "__main__":
    unittest.main()
