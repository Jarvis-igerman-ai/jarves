"""Autopilot wiring with the REAL Store and the REAL policy loader (file on disk); only the worker is fake.

Regression for the live bug: `autopilot watch failed: AttributeError` every step (an imported task carries
scope "required", a string) and for the unconfirmed-start / unconfirmed-stop / notify paths.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import autopilot, autopilot_loop as ap, autopilot_watch as aw
from jarves.workflow.store import Store


class Wiring(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        policy, error = autopilot.load(self.home)
        self.assertEqual(error, "")
        policy.update(enabled=True, project="jarves", authorized_versions=["V4.5.1"], max_concurrent=3,
                      max_starts_per_hour=6)
        autopilot.save(self.home, policy)
        self.store = Store(self.home / "workflow.db")
        ap._LAST_SIG.clear()
        self.starts, self.stops = [], []

    def tearDown(self):
        self.store.close()
        getattr(self, "extra", self.store).close()
        self.temp.cleanup()

    def task(self, ident, **extra):
        return self.store.create_task({"id": ident, "title": ident, "lane": "bugs", "version": "V4.5.1",
                                       "acceptance": [{"id": "C1", "label": "ok"}], **extra})

    def start(self, profile_home, **req):
        self.starts.append(req)
        return {"ok": True, "session": {"id": f"s{len(self.starts)}"}}

    def stop(self, profile_home, sid):
        self.stops.append(sid)
        return {"ok": True}

    def step(self, **kw):
        kw.setdefault("start", self.start)
        kw.setdefault("stop", self.stop)
        kw.setdefault("notify", lambda *a: None)
        return ap.step(self.store, self.home, force=True, **kw)

    def events(self, kind):
        return [e for e in self.store.snapshot()["events"] if e["kind"] == kind]

    def test_string_scope_task_does_not_break_the_watch_and_a_worker_starts(self):
        self.task("imported", scope="required", required_validation="app")        # the live ledger shape
        self.task("phone", scope="required", required_validation="device")
        self.store.transition("phone", {"state": "review"})
        actions = self.step()
        self.assertEqual(self.events("autopilot_error"), [])
        self.assertIn("started", [a["action"] for a in actions])
        self.assertEqual(len(self.starts), 1)
        self.assertTrue(any(q["id"] == "manual-phone" for q in self.store.snapshot()["questions"]))

    def test_watch_failure_is_logged_with_traceback_and_message(self):
        self.task("a")
        with mock.patch.object(aw, "manual_test_step", side_effect=ValueError("boom")):
            with self.assertLogs("jarves.workflow.autopilot_loop", "ERROR") as cm:
                self.step()
        self.assertTrue(any(r.exc_info for r in cm.records))
        self.assertIn("boom", self.events("autopilot_error")[0]["summary"])
        self.assertEqual(len(self.starts), 1)                                      # the start stage still ran

    def test_unconfirmed_start_adopts_the_live_session(self):
        self.task("a")
        found = []
        self.step(start=lambda h, **r: None, find=lambda h, agent: found.append(agent) or "live-1")
        self.assertEqual(found, ["ap-bugs-a"])
        agent = next(x for x in self.store.snapshot()["agents"] if x["id"] == "ap-bugs-a")
        self.assertEqual(agent["session_id"], "live-1")
        self.assertEqual(next(t for t in self.store.snapshot()["tasks"] if t["id"] == "a")["state"], "running")

    def test_refused_start_does_not_consume_the_hourly_start_budget(self):
        # #763: a clean refusal (NO_CORE) launched nothing, so it must not count toward max_starts_per_hour.
        self.task("a")
        actions = self.step(start=lambda h, **r: {"ok": False, "code": "NO_CORE"})
        self.assertIn("refused", [x["action"] for x in actions])
        self.assertEqual(ap._load(self.store).get("starts", []), [])

    def test_pre_start_refusals_do_not_count_but_post_start_ones_do(self):
        # SESSION_CLOSED / AUTH_REQUIRED are returned after a worker process exists: they still use the budget.
        for code in ("NOT_ALLOWED_PROJECT", "BUDGET_REACHED", "MODEL_NOT_ALLOWED", "SECOND_WRITER", "NOT_APPROVED",
                     "CONFIG_NOT_VERIFIED", "CORE_NOT_RUNNING", "PROVIDER_MISSING"):
            self.assertIn(code, ap.PRE_START_REFUSALS)
        self.assertNotIn("error", ap.PRE_START_REFUSALS)
        rows = (("SESSION_CLOSED", "", 1), ("AUTH_REQUIRED", "", 1), ("NO_CORE", "", 0), ("PROVIDER_MISSING", "", 0),
                ("error", "start failed (RuntimeError)", 1), ("error", "worktree failed: boom", 0),
                ("error", "Invalid autopilot environment.", 0), ("error", "Invalid model identifier.", 0))
        for code, text, counted in rows:
            ap._LAST_SIG.clear()
            if not any(t["id"] == "a" for t in self.store.snapshot()["tasks"]):
                self.task("a")
            state = ap._load(self.store)
            state.update(starts=[], paused_until=0)
            ap._save(self.store, state)
            self.step(start=lambda h, c=code, t=text, **r: {"ok": False, "code": c, "text": t})
            self.assertEqual(len(ap._load(self.store).get("starts", [])), counted, (code, text))

    def test_ok_and_unconfirmed_starts_still_count(self):
        self.task("a")
        self.step()
        self.assertEqual(len(ap._load(self.store).get("starts", [])), 1)

    def test_unconfirmed_start_none_result_counts(self):
        self.task("b")
        self.step(start=lambda h, **r: None, find=lambda h, agent: "")
        self.assertEqual(len(ap._load(self.store).get("starts", [])), 1)

    def test_unconfirmed_start_without_a_session_is_quarantined_not_retried(self):
        self.task("a")
        actions = self.step(start=lambda h, **r: None, find=lambda h, agent: "")
        self.assertIn("start_unconfirmed", [x["action"] for x in actions])
        self.assertNotEqual(next(t for t in self.store.snapshot()["tasks"] if t["id"] == "a")["state"], "running")
        self.step()
        self.assertEqual(self.starts, [])

    def test_unconfirmed_stop_keeps_the_link_and_starts_no_second_worker(self):
        self.task("a")
        self.store.register_agent({"id": "ap-bugs-a", "lane": "bugs"})
        self.store.link_session("ap-bugs-a", "s-old", autopilot=True, provider="claude")
        actions = self.step(stop=lambda h, sid: None)
        self.assertIn("stop_unconfirmed", [x["action"] for x in actions])
        self.assertEqual(self.starts, [])
        agent = next(x for x in self.store.snapshot()["agents"] if x["id"] == "ap-bugs-a")
        self.assertEqual(agent["session_id"], "s-old")
        self.step()                                                                # the stop now works: link cleared
        self.assertEqual(self.stops, ["s-old"])

    def test_watchdog_requeues_only_after_a_confirmed_stop(self):
        now = [1000.0]
        store = Store(self.home / "w2.db", clock=lambda: now[0])
        self.extra = store
        store.create_task({"id": "a", "title": "a", "lane": "bugs", "version": "V4.5.1",
                           "acceptance": [{"id": "C1", "label": "ok"}]})
        store.register_agent({"id": "ap-bugs-a", "lane": "bugs"})
        store.link_session("ap-bugs-a", "s1", autopilot=True)
        store.claim("ap-bugs-a", "a")
        now[0] += 2000
        store.heartbeat("ap-bugs-a", "alive")
        now[0] += 1900
        store.heartbeat("ap-bugs-a", "alive")
        store.tick()
        policy, _ = autopilot.load(self.home)
        state = lambda: next(t for t in store.snapshot()["tasks"] if t["id"] == "a")["state"]
        out = aw.watchdog_step(store, policy, start=lambda *a: None, stop=lambda agent: False)
        self.assertIn("stop_unconfirmed:a", out)
        self.assertNotEqual(state(), "queued")
        out = aw.watchdog_step(store, policy, start=lambda *a: None, stop=lambda agent: True)
        self.assertIn("restart:a", out)

    def test_bridge_notify_logs_when_not_delivered(self):
        with mock.patch("jarves.orchestrator.bridge.try_request", return_value=None):
            with self.assertLogs("jarves.workflow.autopilot_loop", "WARNING"):
                ap.bridge_notify(self.home, "alert", "x")


if __name__ == "__main__":
    unittest.main()
