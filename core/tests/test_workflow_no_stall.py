"""Factory never stalls: autopilot reuses a leftover offline agent, a locked DB does not end the supervisor,
the Stop hook retries, and a hook-observed external-* stub yields its session to the autopilot."""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import autopilot, autopilot_loop as ap, control, hooks
from jarves.workflow.store import Store


def _store_with_task():
    home = Path(tempfile.mkdtemp())
    policy, _ = autopilot.load(home)
    policy.update(enabled=True, project="jarves", authorized_versions=["V4.5.1"], max_concurrent=3, max_starts_per_hour=60)
    autopilot.save(home, policy)
    now = [1000.0]
    store = Store(home / "workflow.db", clock=lambda: now[0])
    store.create_task({"id": "a", "title": "a", "lane": "bugs", "version": "V4.5.1",
                       "acceptance": [{"id": "C1", "label": "ok"}]})
    return home, now, store


class AutopilotReuse(unittest.TestCase):
    def test_leftover_offline_agent_is_reset_and_task_starts(self):
        home, now, store = _store_with_task()
        store.register_agent({"id": "ap-bugs-a", "label": "old", "lane": "bugs", "provider": "claude"})
        store.link_session("ap-bugs-a", "", autopilot=True)
        with store._tx() as db:                       # simulate tick() having marked it offline
            agent = store._get(db, "agent", "ap-bugs-a")
            agent["status"] = "offline"
            store._put(db, "agent", agent)
        starts = []

        def start(h, **req):
            starts.append(req)
            return {"ok": True, "session": {"id": "s1"}}
        now[0] += 10
        actions = ap.step(store, home, start=start, stop=lambda h, s: {"ok": True},
                          notify=lambda *a: None, find=lambda h, a: "", force=True)
        self.assertEqual(len(starts), 1)
        self.assertTrue(any(a.get("action") == "started" for a in actions))

    def test_failed_claim_is_recorded_and_does_not_break(self):
        home, now, store = _store_with_task()
        with mock.patch.object(Store, "claim", return_value=None):
            ap.step(store, home, start=lambda h, **r: {"ok": True}, stop=lambda h, s: {"ok": True},
                    notify=lambda *a: None, find=lambda h, a: "", force=True)
        self.assertTrue(any(e.get("kind") == "autopilot_error" for e in store.snapshot().get("events", [])))


class LinkSessionAdopts(unittest.TestCase):
    def test_external_stub_yields_session(self):
        home, now, store = _store_with_task()
        store.register_agent({"id": "external-abc", "label": "ext", "lane": "bugs", "session_id": "S9"})
        store.register_agent({"id": "ap-bugs-a", "label": "ap", "lane": "bugs"})
        agent = store.link_session("ap-bugs-a", "S9", autopilot=True)
        self.assertEqual(agent["session_id"], "S9")

    def test_other_worker_session_still_refused(self):
        home, now, store = _store_with_task()
        store.register_agent({"id": "w1", "label": "w", "lane": "bugs", "session_id": "S9"})
        store.register_agent({"id": "ap-bugs-a", "label": "ap", "lane": "bugs"})
        with self.assertRaises(ValueError):
            store.link_session("ap-bugs-a", "S9", autopilot=True)


class SupervisorSurvives(unittest.TestCase):
    def test_locked_db_tick_does_not_end_supervisor(self):
        home = Path(tempfile.mkdtemp())
        stop = threading.Event()
        calls = []
        real_tick = Store.tick

        def flaky(self):
            calls.append(1)
            if len(calls) < 3:
                raise sqlite3.OperationalError("database is locked")
            if len(calls) >= 4:
                stop.set()
            return real_tick(self)

        class FakeChild:
            pid = 1
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0

        plan = home / "plan.json"
        plan.write_text('{"versions": []}', encoding="utf-8")
        with mock.patch.object(Store, "tick", flaky), mock.patch.object(control, "dispatch", lambda *a, **k: None), \
                mock.patch("jarves.workflow.autopilot_loop.step_safe", lambda *a, **k: None), \
                mock.patch("jarves.workflow.autopilot.step", lambda *a, **k: None), \
                mock.patch("jarves.workflow.sync.PLAN", plan), \
                mock.patch.object(threading.Event, "wait", lambda self, t=None: self.is_set()):
            control.supervisor(home, home / "profile", port=8188, interval=0, sync_github=False, stop=stop,
                               popen=lambda *a, **k: FakeChild())
        self.assertGreaterEqual(len(calls), 4)
        events = Store(home / "workflow.db").snapshot().get("events", [])
        self.assertTrue(any(e.get("kind") == "supervisor_error" for e in events))


class StopHookRetry(unittest.TestCase):
    def test_stop_retries_once_on_locked_db(self):
        home = Path(tempfile.mkdtemp())
        n = []

        def flaky(store, payload, root, agent):
            n.append(1)
            if len(n) == 1:
                raise sqlite3.OperationalError("database is locked")
            return None
        import io, sys
        payload = b'{"hook_event_name": "Stop"}'
        fake = mock.Mock()
        fake.buffer = io.BytesIO(payload)
        with mock.patch.object(hooks, "observe", flaky), mock.patch.object(sys, "stdin", fake), \
                mock.patch.object(hooks.time, "sleep", lambda s: None):
            hooks.main(["--home", str(home), "--repo-root", str(home)])
        self.assertEqual(len(n), 2)


if __name__ == "__main__":
    unittest.main()
