"""Simulation of the quiet wake queue (#762): coalescing, clean-prompt-only delivery, no retry timer."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarves.workflow import wake
from jarves.workflow.store import Store


class Chan:
    def __init__(self, **state):
        self.s = {"known": True, "idle": True, "draft": False, "permission_prompt": False, "mid_turn": False}
        self.s.update(state)
        self.sent = []
        self.ok = True
        self.calls = 0

    def state(self):
        self.calls += 1
        return self.s

    def inject(self, line):
        self.sent.append(line)
        return self.ok


def _store():
    return Store(Path(tempfile.mkdtemp()) / "w.db", clock=lambda: 1000.0)


def _kinds(store):
    with store._tx() as db:
        return [r[0] for r in db.execute("SELECT kind FROM workflow_events ORDER BY rowid")]


class Wake(unittest.TestCase):
    def test_requests_coalesce_into_one_line(self):
        s, c = _store(), Chan()
        for i in range(5):
            wake.request_wake(s, "mgr", f"r{i}")
        self.assertEqual(wake.pending(s, "mgr")["count"], 5)
        self.assertEqual(wake.try_deliver(s, "mgr", c), "delivered")
        self.assertEqual(c.sent, [wake.WAKE_LINE])
        self.assertIsNone(wake.pending(s, "mgr"))
        self.assertEqual(wake.try_deliver(s, "mgr", c), "none")
        self.assertEqual(len(c.sent), 1)

    def test_unclean_prompt_defers_and_keeps_pending(self):
        for state, why in [({"draft": True}, "draft_present"), ({"permission_prompt": True}, "permission_prompt"),
                           ({"mid_turn": True}, "mid_turn"), ({"idle": False}, "not_idle"),
                           ({"known": False}, "unknown_state")]:
            s, c = _store(), Chan(**state)
            wake.request_wake(s, "mgr", "x")
            self.assertEqual(wake.try_deliver(s, "mgr", c), "deferred:" + why)
            self.assertEqual(c.sent, [])
            self.assertIsNotNone(wake.pending(s, "mgr"))
            self.assertIn("wake_deferred", _kinds(s))

    def test_no_retry_without_new_event_then_delivers_when_clean(self):
        s, c = _store(), Chan(draft=True)
        wake.request_wake(s, "mgr", "x")
        wake.try_deliver(s, "mgr", c)
        self.assertEqual(c.calls, 1)          # nothing re-probes on its own
        c.s["draft"] = False                  # manager reaches a clean prompt; the idle event triggers one attempt
        self.assertEqual(wake.try_deliver(s, "mgr", c), "delivered")

    def test_inject_failure_and_probe_error_are_logged_and_stay_pending(self):
        s, c = _store(), Chan()
        c.ok = False
        wake.request_wake(s, "mgr", "x")
        self.assertEqual(wake.try_deliver(s, "mgr", c), "failed")
        self.assertIsNotNone(wake.pending(s, "mgr"))
        c.state = lambda: 1 / 0
        self.assertEqual(wake.try_deliver(s, "mgr", c), "deferred:state_error")
        self.assertIn("wake_failed", _kinds(s))


if __name__ == "__main__":
    unittest.main()
