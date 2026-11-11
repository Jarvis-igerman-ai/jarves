"""#786 / JAR-214: concurrent SubagentStart hooks must not lose events to 'database is locked' (temp home only)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.store import Store

CORE_SRC = Path(__file__).resolve().parents[1] / "src"
# Each real commit is slowed to a Windows-like synced write (synchronous=FULL, antivirus), applied to every
# write transaction the hook opens; Linux tmpfs commits are too fast to show the queue otherwise.
SLOW_COMMIT = """
import contextlib, sys, time
from jarves.workflow import hooks, store
real = store.Store._tx
@contextlib.contextmanager
def slow(self):
    with real(self) as db:
        yield db
        time.sleep(0.1)
store.Store._tx = slow
sys.exit(hooks.main(sys.argv[1:]))
"""


class ConcurrentSubagentStartTests(unittest.TestCase):
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
        cmd = [sys.executable, "-c", SLOW_COMMIT, "--home", str(self.home), "--repo-root", str(self.root)]
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
