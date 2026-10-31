"""Regression (#727): a task the watchdog restarts must be started again exactly once, even when the Core no
longer has a live session to stop (stop_session finds live sessions only). Real Store, Core-like fake stop."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarves.workflow import autopilot, autopilot_loop as ap
from jarves.workflow.store import Store


class RestartAfterStop(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        policy, _ = autopilot.load(self.home)
        policy.update(enabled=True, project="jarves", authorized_versions=["V4.5.1"], max_concurrent=3,
                      max_starts_per_hour=60)
        autopilot.save(self.home, policy)
        self.now = [1000.0]
        self.store = Store(self.home / "workflow.db", clock=lambda: self.now[0])
        self.store.create_task({"id": "a", "title": "a", "lane": "bugs", "version": "V4.5.1",
                                "acceptance": [{"id": "C1", "label": "ok"}]})
        self.live, self.starts, self.stuck = set(), [], set()
        self.kw = dict(start=self.start, stop=self.stop, notify=lambda *a: None, find=lambda h, a: "", force=True)

    def start(self, h, **req):
        sid = f"s{len(self.starts) + 1}"
        self.starts.append(sid)
        self.live.add(sid)
        return {"ok": True, "session": {"id": sid}}

    def stop(self, h, sid):     # like the Core: only LIVE sessions resolve; a failing live stop stays live
        if sid in self.stuck:
            return {"ok": False, "code": "SERVER_ERROR"}
        if sid in self.live:
            self.live.discard(sid)
            return {"ok": True}
        return {"ok": False, "code": "NO_SUCH_SESSION"}

    def stall(self):
        self.now[0] += 2000
        self.store.heartbeat("ap-bugs-a", "alive")
        self.now[0] += 1900
        self.store.heartbeat("ap-bugs-a", "alive")
        self.store.tick()

    def run_steps(self, n=4):
        out = []
        for _ in range(n):
            self.now[0] += 60
            out += [str(a) for a in ap.step(self.store, self.home, **self.kw)]
        return out

    def agent(self):
        return next(x for x in self.store.snapshot()["agents"] if x["id"] == "ap-bugs-a")

    def test_restarted_task_started_again_exactly_once_for_dead_session(self):
        ap.step(self.store, self.home, **self.kw)
        self.live.clear()                       # the worker died; the Core has no live session for it
        self.stall()
        log = self.run_steps()
        self.assertEqual(self.starts, ["s1", "s2"], log)
        self.assertFalse(any("stop_unconfirmed" in x for x in log), log)

    def test_dead_worker_restarted_once_then_blocked(self):
        ap.step(self.store, self.home, **self.kw)
        self.live.clear()
        self.stall()
        self.run_steps()
        self.live.clear()
        self.stall()
        self.run_steps()
        self.assertEqual(self.starts, ["s1", "s2"])
        task = next(x for x in self.store.snapshot()["tasks"] if x["id"] == "a")
        self.assertEqual(task["state"], "blocked")
        self.assertFalse(self.agent().get("session_id"))

    def test_failed_stop_of_live_session_keeps_task_and_alerts(self):
        ap.step(self.store, self.home, **self.kw)
        self.stuck.add("s1")
        self.stall()
        log = self.run_steps()
        self.assertEqual(self.starts, ["s1"], log)
        self.assertTrue(any("stop_unconfirmed" in x for x in log), log)
        self.assertEqual(self.agent().get("session_id"), "s1")
        self.assertEqual(self.agent().get("task_id"), "a")


if __name__ == "__main__":
    unittest.main()
