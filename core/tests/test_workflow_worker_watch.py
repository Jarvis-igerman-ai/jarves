"""Worker-count monitor, Stop continuity and handover signal (temp home, fake clock)."""
import tempfile
import unittest
from pathlib import Path

from jarves.workflow import watch
from jarves.workflow.hooks import observe
from jarves.workflow.store import NATIVE_GRACE_S, Store

POLICY = {"lanes": {"bugs": {"workers": 2}, "features": {"workers": 2}}}


class WorkerWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.now = [10000.0]
        self.store = Store(Path(self.temp.name) / "private" / "workflow.db", clock=lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def event(self, name, **kw):
        return observe(self.store, {"hook_event_name": name, "session_id": "s", "cwd": str(self.root), **kw}, self.root)

    def start(self, n):
        self.event("SessionStart")  # the manager
        for i in range(n):
            self.event("SubagentStart", agent_id=f"w{i}", agent_type="workflow-bugs")

    def test_pure_decision_and_dedupe(self):
        n, action, st = watch.evaluate(1, 4, ["a"], {}, 1000)
        self.assertEqual((n["shortfall"], action), (3, "short"))
        self.assertIsNone(watch.evaluate(1, 4, ["a"], st, 1100)[1])  # inside 5 minutes
        self.assertEqual(watch.evaluate(1, 4, ["a"], st, 1301)[1], "short")  # persists past the interval
        self.assertEqual(watch.evaluate(5, 4, [], {}, 1000)[1], "over")
        self.assertEqual(watch.evaluate(4, 4, ["a"], st, 1400)[1], "recovered")
        self.assertIsNone(watch.evaluate(4, 4, ["a"], {}, 1400)[1])
        self.assertEqual(watch.evaluate(0, 4, [], {}, 1000)[0]["shortfall"], 0)  # nothing runnable: no shortfall

    def test_one_of_four_with_ready_work_sends_one_message_and_stop_continues(self):
        self.start(1)
        self.store.create_task({"id": "t1", "title": "Ready"})
        first = watch.watch(self.store, POLICY)
        self.assertEqual((first["running"], first["allowed"], first["shortfall"], first["action"]), (1, 4, 3, "short"))
        again = watch.watch(self.store, POLICY)  # same minute: deduplicated
        self.assertEqual(again["action"], "none")
        stop = self.event("Stop")
        self.assertEqual(stop["decision"], "block")
        self.assertIn("WORKERS_SHORT: running 1 of 4", stop["reason"])
        self.assertEqual(self.store.take_manager_notes(), [])  # shown once
        self.assertEqual(self.event("Stop", stop_hook_active=True), {})  # loop guard stays

    def test_full_workers_send_nothing_and_recovery_is_recorded(self):
        self.start(1)
        self.store.create_task({"id": "t1", "title": "Ready"})
        watch.watch(self.store, POLICY)
        for i in range(1, 4):
            self.event("SubagentStart", agent_id=f"w{i}", agent_type="workflow-bugs")
        self.store.take_manager_notes()
        result = watch.watch(self.store, POLICY)
        self.assertEqual((result["running"], result["shortfall"], result["action"]), (4, 0, "recovered"))
        self.assertEqual(self.store.take_manager_notes(), [])
        self.assertTrue(any("WORKERS_OK" in e["summary"] for e in self.store.snapshot()["events"]))

    def test_unrunnable_work_and_stale_workers(self):
        self.start(1)
        self.store.create_task({"id": "scope", "title": "Scope", "execution_allowed": False})
        self.assertEqual(watch.watch(self.store, POLICY)["action"], "none")
        self.store.create_task({"id": "t1", "title": "Ready"})
        self.now[0] += 4000  # worker heartbeat is stale: it no longer counts
        self.assertEqual(watch.watch(self.store, POLICY)["running"], 0)

    def test_over_limit_is_reported(self):
        self.start(3)
        result = watch.watch(self.store, {"lanes": {"bugs": {"workers": 1}, "features": {"workers": 1}}})
        self.assertEqual((result["over"], result["action"]), (1, "over"))

    def test_monitor_never_raises(self):
        self.store.close()
        self.assertEqual(watch.watch(self.store, POLICY)["action"], "error")
        self.store = Store(Path(self.temp.name) / "private" / "workflow.db")

    def test_handover_waits_for_checkpoint_or_grace(self):
        self.event("SessionStart")
        self.assertFalse(watch.handover_state(self.store)["ready"])
        self.now[0] += 60
        self.event("PostToolUse", tool_name="Bash")  # manager still active
        self.assertFalse(watch.handover_state(self.store)["ready"])
        self.now[0] += 60
        watch.write_handover(self.store, "state: waiting on PR 859")
        ready = watch.handover_state(self.store)
        self.assertTrue(ready["ready"])
        self.assertIn("handover", ready["reason"])
        self.now[0] += 5
        self.event("PostToolUse", tool_name="Bash")  # real activity after the handover
        self.now[0] += 100
        self.event("PostToolUse", tool_name="Bash")
        self.assertFalse(watch.handover_state(self.store)["ready"])
        self.now[0] += 700  # a long wait (permission prompt / long tool call) is not silence yet
        self.assertFalse(watch.handover_state(self.store)["ready"])
        self.now[0] += NATIVE_GRACE_S
        state = watch.handover_state(self.store)
        self.assertTrue(state["ready"])
        self.assertIn("grace", state["reason"])

    def test_notes_without_ready_work_respect_the_hourly_cap(self):
        self.event("SessionStart")
        blocked = 0
        for i in range(14):
            self.store.post_manager_note("WORKERS_SHORT", f"n{i}")
            out = self.event("Stop")
            blocked += out.get("decision") == "block"
            self.now[0] += 10
        self.assertEqual(blocked, 12)  # the shared cap (no busy workers); notes 13 and 14 are held back
        self.assertEqual(self.store.take_manager_notes()[-1]["body"], "n13")  # held back, not lost
        self.now[0] += 3600
        self.store.post_manager_note("WORKERS_SHORT", "later")
        self.assertEqual(self.event("Stop").get("decision"), "block")

    def seed_source(self):
        self.store.set_source({"mode": "github", "synced_at": 1,
                               "open_issues": [{"number": 11, "state": "open", "labels": ["bug", "P2"], "title": "a"},
                                               {"number": 12, "state": "open", "labels": ["docs"], "title": "b"},
                                               {"number": 919, "state": "open", "labels": ["agent-doable"], "title": "c"}],
                               "open_prs": [{"number": 5, "labels": ["signed-off"], "draft": False},
                                            {"number": 6, "labels": [], "draft": True}]})

    def test_open_work_counts_issues_without_task_and_prs(self):
        self.seed_source()
        self.store.create_task({"id": "gh-919", "title": "Has task", "execution_allowed": False})
        work = watch.open_work(self.store.snapshot())
        self.assertEqual((work["issues"], work["prs"], work["signed_prs"]), ([11], [5], [5]))

    def test_idle_alarm_notifies_once_and_is_rate_limited(self):
        self.seed_source()
        self.start(0)
        pushed = []
        notify = lambda kind, text: pushed.append((kind, text))
        self.assertFalse(watch.watch(self.store, POLICY, notify=notify)["idle_alarm"])  # activity is fresh
        self.now[0] += watch.IDLE_ALARM_S + 1
        res = watch.watch(self.store, POLICY, notify=notify)
        self.assertTrue(res["idle_alarm"])
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0][0], "idle_work")
        self.assertTrue(any("IDLE_WORK" in n["subject"] for n in self.store.take_manager_notes()))
        self.now[0] += 400  # inside the rate limit
        watch.watch(self.store, POLICY, notify=notify)
        self.assertEqual(len(pushed), 1)
        self.now[0] += watch.IDLE_ALARM_S
        watch.watch(self.store, POLICY, notify=notify)
        self.assertEqual(len(pushed), 2)

    def test_no_idle_alarm_without_free_slots_or_work(self):
        self.start(0)
        self.now[0] += watch.IDLE_ALARM_S + 1
        self.assertFalse(watch.watch(self.store, POLICY, notify=lambda *a: None)["idle_alarm"])  # no work
        self.seed_source()
        self.assertFalse(watch.watch(self.store, {})["idle_alarm"])  # allowed 0: no free slot

    def test_run_age_and_dead_wrapper_incident(self):
        from jarves.workflow import observability
        self.assertEqual(observability.health(self.store)["worker_watch"]["state"], "never")
        watch.watch(self.store, POLICY)
        self.now[0] += 60
        self.assertEqual(observability.health(self.store)["worker_watch"]["age_s"], 60.0)
        self.now[0] += watch.RUN_DEAD_S
        health = observability.health(self.store)
        self.assertEqual(health["worker_watch"]["state"], "dead")
        self.assertTrue(any(i["kind"] == "worker_watch_dead" for i in health["incidents"]))


if __name__ == "__main__":
    unittest.main()
