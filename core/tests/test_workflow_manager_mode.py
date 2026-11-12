"""Manager mode (#715 slice 1). Hermetic: temp dir, injected liveness (fake pids) and clock."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import manager_mode as mm
from jarves.workflow.__main__ import owner_only_guard

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
        rec = mm.acquire(self.home, "native", "claude-1", pid=111, alive=alive)
        with self.assertRaises(mm.ManagerConflict):
            mm.set_mode(self.home, "jarves", alive=alive)
        with self.assertRaises(mm.ManagerConflict):
            mm.release(self.home, "someone-else")
        with self.assertRaises(mm.ManagerConflict):            # right name printed by status, no token: refused
            mm.release(self.home, "claude-1")
        with self.assertRaises(mm.ManagerConflict):
            mm.release(self.home, "claude-1", token="wrong")
        mm.release(self.home, "claude-1", token=rec["token"])
        mm.set_mode(self.home, "jarves", alive=alive)
        mm.acquire(self.home, "jarves", "orch", pid=222, alive=alive)
        self.assertEqual(mm.status(self.home, alive)["active_scheduler"], "jarves")

    def test_dead_holder_lock_is_recovered(self):
        mm.acquire(self.home, "native", "claude-1", pid=999, alive=lambda p: True)
        mm.set_mode(self.home, "jarves", alive=alive)          # pid 999 is not alive in the fake table
        rec = mm.acquire(self.home, "jarves", "orch", pid=222, alive=alive)
        self.assertEqual(rec["holder"], "orch")

    def test_empty_lock_is_held_while_being_created_and_stale_after_grace(self):
        lock = self.home / mm.LOCK_NAME
        lock.write_text("", encoding="utf-8")                  # fake: holder between create and write
        with self.assertRaises(mm.ManagerConflict):
            mm.acquire(self.home, "native", "B", pid=222, alive=alive)
        self.assertTrue(lock.exists())                         # not stolen
        later = lambda: lock.stat().st_mtime + mm.GRACE + 1    # noqa: E731  (fake clock)
        self.assertEqual(mm.acquire(self.home, "native", "B", pid=222, alive=alive, now=later)["holder"], "B")

    def test_status_and_conflict_never_leak_the_token(self):
        mm.acquire(self.home, "native", "A", pid=111, alive=alive)
        self.assertNotIn("token", mm.status(self.home, alive)["owner"])
        with self.assertRaises(mm.ManagerConflict) as cm:
            mm.acquire(self.home, "native", "B", pid=222, alive=alive)
        self.assertNotIn("token", cm.exception.owner)
        leftovers = [p.name for p in self.home.iterdir() if p.name.endswith((".stale", ".new", ".recover"))]
        self.assertEqual(leftovers, [])

    def test_recover_restores_a_lock_replaced_between_judging_and_moving(self):
        lock = self.home / mm.LOCK_NAME
        mm.acquire(self.home, "native", "old", pid=111, alive=lambda p: True)
        fresh = {"mode": "native", "holder": "new", "pid": 222, "since": 1, "token": "t"}
        orig = mm._read
        state = {"swapped": False}

        def read_then_swap(path):                              # fake: a fresh holder appears after the judgment
            data = orig(path)
            if path == lock and not state["swapped"]:
                state["swapped"] = True
                lock.write_text(json.dumps(fresh), encoding="utf-8")
            return data
        with mock.patch.object(mm, "_read", read_then_swap):
            mm._recover_stale(lock, lambda p: False, time.time)
        self.assertEqual(orig(lock)["holder"], "new")

    def test_acquire_requires_live_explicit_pid(self):
        for bad in (0, -1, 999):
            with self.assertRaises(mm.ManagerConflict):
                mm.acquire(self.home, "native", "cli", pid=bad, alive=alive)
        with self.assertRaises(ValueError):                    # CLI form needs HOLDER PID
            mm.command(self.home, ["acquire", "cli"])

    def test_forced_release_and_agent_refusal(self):
        mm.acquire(self.home, "native", "claude-1", pid=111, alive=alive)
        mm.release(self.home, "x", force=True)                 # owner path: the CLI wrapper applies owner_only_guard
        self.assertIsNone(mm.lock_owner(self.home, alive))
        with mock.patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": "fake-agent"}):   # fake agent environment
            for action in ("manager set", "manager release"):
                with self.assertRaises(PermissionError):
                    owner_only_guard(action)

    def test_cli_wrapper_refuses_set_for_agent_environment(self):
        from jarves.workflow.__main__ import main
        with mock.patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": "fake-agent"}):   # fake agent environment
            self.assertEqual(main(["manager", "set", "jarves", "--home", str(self.home)]), 3)
        self.assertEqual(mm.get_mode(self.home), "native")

    def test_release_never_deletes_a_lock_replaced_after_validation(self):
        lock = self.home / mm.LOCK_NAME
        rec = mm.acquire(self.home, "native", "A", pid=111, alive=alive)
        fresh = {"mode": "native", "holder": "B", "pid": 222, "since": 1, "token": "tb"}
        orig = mm._read
        state = {"swapped": False}

        def read_then_swap(path):                              # fake: A dies, B recovers and acquires after A's check
            data = orig(path)
            if path == lock and not state["swapped"]:
                state["swapped"] = True
                lock.write_text(json.dumps(fresh), encoding="utf-8")
            return data
        with mock.patch.object(mm, "_read", read_then_swap), self.assertRaises(mm.ManagerConflict):
            mm.release(self.home, "A", token=rec["token"])
        self.assertEqual(orig(lock)["holder"], "B")
        with self.assertRaises(mm.ManagerConflict):            # a second release with the old token is refused too
            mm.release(self.home, "A", token=rec["token"])
        self.assertEqual(orig(lock)["holder"], "B")

    def test_repeated_release_is_harmless(self):
        rec = mm.acquire(self.home, "native", "A", pid=111, alive=alive)
        mm.release(self.home, "A", token=rec["token"])
        self.assertEqual(mm.release(self.home, "A", token=rec["token"]), {"released": True})
        self.assertFalse(any(p.name.endswith(".stale") for p in self.home.iterdir()))

    def test_tokenless_release_refuses_a_fresh_unreadable_lock(self):
        lock = self.home / mm.LOCK_NAME
        lock.write_text("", encoding="utf-8")                  # fake: holder between create and write
        with self.assertRaises(mm.ManagerConflict):
            mm.release(self.home, "x")
        self.assertTrue(lock.exists())
        mm.release(self.home, "x", force=True)
        self.assertFalse(lock.exists())

    def test_set_mode_reverts_when_the_old_mode_acquires_concurrently(self):
        orig = mm._write

        def write_after_acquire(path, data):                   # fake: a native scheduler acquires mid-switch
            mm.acquire(self.home, "native", "claude-1", pid=111, alive=alive)
            mm._write = orig
            orig(path, data)
        with mock.patch.object(mm, "_write", write_after_acquire), self.assertRaises(mm.ManagerConflict):
            mm.set_mode(self.home, "jarves", alive=alive)
        self.assertEqual(mm.get_mode(self.home), "native")

    def test_acquire_backs_out_when_the_mode_switches_concurrently(self):
        with mock.patch.object(mm, "get_mode", side_effect=["native", "jarves"]), self.assertRaises(mm.ManagerConflict):
            mm.acquire(self.home, "native", "claude-1", pid=111, alive=alive)
        self.assertFalse((self.home / mm.LOCK_NAME).exists())

    def test_cli_acquire_pid_must_be_the_calling_scheduler(self):
        with self.assertRaises(mm.ManagerConflict):            # alive, but not the CLI's parent process
            mm.command(self.home, ["acquire", "cli", str(os.getpid())])
        self.assertFalse((self.home / mm.LOCK_NAME).exists())

    def test_filesystem_errors_become_conflicts(self):
        with mock.patch.object(mm.os, "link", side_effect=OSError("no hard links")), self.assertRaises(mm.ManagerConflict):
            mm.acquire(self.home, "native", "A", pid=111, alive=alive)
        rec = mm.acquire(self.home, "native", "A", pid=111, alive=alive)
        with mock.patch.object(mm.os, "replace", side_effect=PermissionError("in use")), self.assertRaises(mm.ManagerConflict):
            mm.release(self.home, "A", token=rec["token"])

    def test_guard_text_for_manager_does_not_mention_questions(self):
        with mock.patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": "fake-agent"}), self.assertRaises(PermissionError) as cm:
            owner_only_guard("manager set")
        self.assertNotIn("question", str(cm.exception))

    def test_cli_commands(self):
        self.assertEqual(mm.command(self.home, [])["mode"], "native")
        self.assertEqual(mm.command(self.home, ["set", "jarves"])["mode"], "jarves")
        with self.assertRaises(ValueError):
            mm.command(self.home, ["bogus"])


if __name__ == "__main__":
    unittest.main()
