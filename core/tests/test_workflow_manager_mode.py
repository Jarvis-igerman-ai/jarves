"""Manager mode (#715 slice 1). Hermetic: temp dir, injected liveness (fake pids) and clock."""
import tempfile
import unittest
from pathlib import Path

from jarves.workflow import manager_mode as mm

LIVE = {111, 222}
alive = lambda pid: pid in LIVE  # noqa: E731  (fake process table)


class ManagerModeTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.home = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def test_default_native_and_damaged_file_fails_closed(self):
        self.assertEqual(mm.get_mode(self.home), "native")
        (self.home / mm.STATE_NAME).write_text("{not json", encoding="utf-8")
        self.assertEqual(mm.get_mode(self.home), "native")
        with self.assertRaises(ValueError):
            mm.set_mode(self.home, "other")

    def test_acquire_requires_chosen_mode(self):
        with self.assertRaises(mm.ManagerConflict):
            mm.acquire(self.home, "jarves", "orch", pid=111, alive=alive)

    def test_only_one_live_scheduler_and_no_duplicate_after_restart(self):
        mm.acquire(self.home, "native", "claude-1", pid=111, alive=alive)
        with self.assertRaises(mm.ManagerConflict):
            mm.acquire(self.home, "native", "claude-2", pid=222, alive=alive)
        self.assertEqual(mm.status(self.home, alive)["active_scheduler"], "native")

    def test_switch_refused_while_other_runs_then_allowed_after_release(self):
        mm.acquire(self.home, "native", "claude-1", pid=111, alive=alive)
        with self.assertRaises(mm.ManagerConflict):
            mm.set_mode(self.home, "jarves", alive=alive)
        with self.assertRaises(mm.ManagerConflict):
            mm.release(self.home, "someone-else")
        mm.release(self.home, "claude-1")
        mm.set_mode(self.home, "jarves", alive=alive)
        mm.acquire(self.home, "jarves", "orch", pid=222, alive=alive)
        self.assertEqual(mm.status(self.home, alive)["active_scheduler"], "jarves")

    def test_dead_holder_lock_is_recovered(self):
        mm.acquire(self.home, "native", "claude-1", pid=999, alive=lambda p: True)
        mm.set_mode(self.home, "jarves", alive=alive)          # pid 999 is not alive in the fake table
        rec = mm.acquire(self.home, "jarves", "orch", pid=222, alive=alive)
        self.assertEqual(rec["holder"], "orch")

    def test_cli_commands(self):
        self.assertEqual(mm.command(self.home, [])["mode"], "native")
        self.assertEqual(mm.command(self.home, ["set", "jarves"])["mode"], "jarves")
        with self.assertRaises(ValueError):
            mm.command(self.home, ["bogus"])


if __name__ == "__main__":
    unittest.main()
