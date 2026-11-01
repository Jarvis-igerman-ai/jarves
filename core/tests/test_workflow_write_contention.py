"""Hook write contention (issue #786): many agents plus a supervisor on one ledger lose no events and raise nothing."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from jarves.workflow.hooks import SPOOL_NAME, drain_spool, run_hook
from jarves.workflow.store import Store

AGENTS = 6
ROUNDS = 12
FLOW = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Notification", "PreToolUse", "PostToolUse", "Stop")


class WriteContentionTest(unittest.TestCase):
    def test_parallel_hooks_and_supervisor_lose_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            home = Path(tmp) / "home"
            home.mkdir()
            Store(home / "workflow.db").close()
            errors, statuses, ticks = [], [], []
            stop = threading.Event()

            def worker(n):
                for r in range(ROUNDS):
                    for i, ev in enumerate(FLOW):
                        payload = {"hook_event_name": ev, "session_id": f"s{n}", "cwd": str(root),
                                   "tool_name": "Bash", "tool_use_id": f"t{n}-{r}-{i}"}
                        if n:
                            payload.update(agent_id=f"sub{n}", agent_type="workflow-current")
                        try:
                            statuses.append(run_hook(home, root, payload, budget_s=1.2)[1])
                        except Exception as exc:  # noqa: BLE001
                            errors.append(repr(exc))

            def supervisor():
                store = Store(home / "workflow.db")
                try:
                    while not stop.is_set():
                        t0 = time.monotonic()
                        drain_spool(store)
                        store.tick()
                        store.set_supervisor({"status": "running"})
                        store.record_event("supervisor", "tick")
                        ticks.append(time.monotonic() - t0)
                        time.sleep(0.05)
                finally:
                    store.close()

            sup = threading.Thread(target=supervisor)
            sup.start()
            threads = [threading.Thread(target=worker, args=(n,)) for n in range(AGENTS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            stop.set()
            sup.join()
            self.assertEqual(errors, [])
            self.assertNotIn("lost", statuses)
            store = Store(home / "workflow.db")
            try:
                while drain_spool(store):
                    pass
                spool = home / SPOOL_NAME
                self.assertFalse(spool.exists() and spool.stat().st_size)
                self.assertFalse((home / (SPOOL_NAME + ".draining")).exists())
                expected = AGENTS * ROUNDS * len(FLOW)
                # every event is recorded exactly once: none lost (spooled ones replayed), none doubled
                got = store.read(lambda db: db.execute("SELECT COUNT(*) FROM workflow_trace").fetchone()[0])
                self.assertEqual(got, expected)
            finally:
                store.close()
            print(f"\ncontention: {len(statuses)} hooks, spooled={statuses.count('spooled')}, "
                  f"max tick {max(ticks):.3f}s over {len(ticks)} ticks")

    def test_busy_database_spools_instead_of_raising_and_drains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            home = Path(tmp) / "home"
            home.mkdir()
            holder = Store(home / "workflow.db")
            payload = {"hook_event_name": "SessionStart", "session_id": "sx", "cwd": str(root)}
            with holder.batch():                       # a writer that stays longer than the hook budget
                result, status = run_hook(home, root, payload, budget_s=0.4)
            self.assertEqual((result, status), ({}, "spooled"))
            lines = (home / SPOOL_NAME).read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0])["payload"]["session_id"], "sx")
            self.assertEqual(drain_spool(holder), 1)
            self.assertFalse((home / SPOOL_NAME).exists())
            self.assertEqual(len(holder.snapshot()["agents"]), 1)
            holder.close()


if __name__ == "__main__":
    unittest.main()
