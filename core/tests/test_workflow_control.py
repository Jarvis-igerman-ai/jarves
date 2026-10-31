"""Integration regressions for the optional bridge dispatcher and external watcher.

Real SQLite ledgers and concurrent handles; injected clocks/processes/network
callbacks. No live agent, account, network, polling sleep or paid call is used.
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import control
from jarves.workflow.store import Store


class WorkflowControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.now = [1000.0]
        self.store = Store(self.home / "workflow.db", clock=lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def task(self, ident="task", **extra):
        return self.store.create_task({"id": ident, "title": ident, "lane": "features",
                                       "acceptance": [{"id": "C1", "label": "A user scenario works"}], **extra})

    def worker(self, ident="worker", **extra):
        return self.store.register_agent({"id": ident, "label": ident, "lane": "features",
                                           "session_id": "session-" + ident, "auto_dispatch": True, **extra})

    def test_same_real_session_cannot_be_attached_to_two_workers(self):
        self.worker("first", session_id="same-session")
        with self.assertRaisesRegex(ValueError, "session already attached"):
            self.worker("second", session_id="same-session")
        self.task("one")
        self.task("two")
        sent = []
        result = control.dispatch(self.store, self.home,
                                  tell=lambda _home, sid, _text: sent.append(sid) or {"ok": True})
        self.assertEqual(sent, ["same-session"])
        self.assertEqual(len(result), 1)
        self.assertEqual(sum(t["state"] == "queued" for t in self.store.snapshot()["tasks"]), 1)

    def test_ambiguous_delivery_keeps_gui_lock_and_writer_quarantined(self):
        self.worker("first")
        self.worker("second")
        self.task("one", priority=0, resources=["windows-gui"])
        self.task("two", priority=2, resources=["windows-gui"])
        result = control.dispatch(self.store, self.home, tell=lambda *_: None)
        self.assertEqual(result, [{"task_id": "one", "status": "delivery_unconfirmed"}])
        tasks = {t["id"]: t for t in self.store.snapshot()["tasks"]}
        self.assertEqual(tasks["one"]["state"], "blocked")
        self.assertTrue(tasks["one"]["quarantined"])
        self.assertEqual(tasks["one"]["agent_id"], "first")
        self.assertEqual(tasks["two"]["state"], "queued")
        self.assertIsNone(self.store.claim("first"))
        self.assertIsNone(self.store.claim("second"))

    def test_bridge_exception_is_not_retried_as_a_second_writer(self):
        self.worker()
        self.task(resources=["worktree"])
        calls = []

        def disconnected(*args):
            calls.append(args)
            raise OSError("acknowledgement lost")

        first = control.dispatch(self.store, self.home, tell=disconnected)
        second = control.dispatch(self.store, self.home, tell=disconnected)
        self.assertEqual(first[0]["status"], "delivery_unconfirmed")
        self.assertEqual(second, [])
        self.assertEqual(len(calls), 1)

    def test_answered_task_resumes_with_answer_checkpoint_identity_and_home(self):
        self.worker("feature-worker")
        self.task()
        self.assertIsNotNone(self.store.claim("feature-worker"))
        question = self.store.ask({"task_id": "task", "title": "Which transport?",
                                   "checkpoint": "Commit abc123; local browser closed"})
        self.store.defer(question["id"])
        self.store.answer(question["id"], "Use the existing secure phone transport")
        sent = []
        control.dispatch(self.store, self.home,
                         tell=lambda _home, _sid, text: sent.append(text) or {"ok": True})
        self.assertEqual(len(sent), 1)
        self.assertNotIn("YOUR_ID", sent[0])
        context = json.loads(sent[0].split("\n", 1)[1])
        self.assertEqual(context["workflow_agent_id"], "feature-worker")
        self.assertEqual(Path(context["workflow_home"]), self.home)
        self.assertEqual(context["owner_decisions"][0]["answer"], "Use the existing secure phone transport")
        self.assertEqual(context["owner_decisions"][0]["checkpoint"], "Commit abc123; local browser closed")
        self.assertIn("never as permission grants", sent[0])

    def test_opt_out_and_native_telemetry_workers_do_not_receive_bridge_work(self):
        self.worker("opt-out", auto_dispatch=False)
        self.worker("native", provider="claude-native", session_id="", auto_dispatch=False)
        self.task()
        sent = []
        self.assertEqual(control.dispatch(self.store, self.home,
                                         tell=lambda *args: sent.append(args) or {"ok": True}), [])
        self.assertEqual(sent, [])
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "queued")

    def test_stale_workers_and_unapproved_imported_scope_are_not_dispatched(self):
        self.worker()
        self.task("approved")
        self.now[0] += 121
        sent = []
        tell = lambda *args: sent.append(args) or {"ok": True}
        self.assertEqual(control.dispatch(self.store, self.home, tell=tell), [])
        self.store.heartbeat("worker")
        self.store.transition("approved", {"state": "blocked"})
        self.store.seed_plan({"versions": [{"id": "V7", "requirements": [{
            "id": "V7-SCOPE", "title": "Imported future scope", "lane": "features"}]}]})
        self.assertEqual(control.dispatch(self.store, self.home, tell=tell), [])
        self.assertEqual(sent, [])

    def test_two_dispatchers_cannot_send_two_tasks_to_one_worker(self):
        self.worker()
        self.task("one")
        self.task("two")
        second_store = Store(self.store.path, clock=lambda: self.now[0])
        ready = threading.Barrier(2)
        sent = []
        lock = threading.Lock()

        def tell(_home, sid, text):
            with lock:
                sent.append((sid, text))
            return {"ok": True}

        def run(store):
            ready.wait(timeout=5)
            return control.dispatch(store, self.home, tell=tell)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, [self.store, second_store]))
            self.assertEqual(sum(len(result) for result in results), 1)
            self.assertEqual(len(sent), 1)
        finally:
            second_store.close()

    def test_tick_alone_does_not_claim_external_supervisor_is_alive(self):
        self.worker()
        self.task()
        before = self.store.snapshot()["supervisor"]
        alerts = self.store.tick()
        after = self.store.snapshot()["supervisor"]
        self.assertTrue(any(a["kind"] == "idle_with_work" for a in alerts))
        self.assertEqual(before, after)
        self.assertEqual(after["heartbeat_at"], 0)
        self.assertEqual(after["status"], "offline")

    def test_supervisor_uses_checkout_entry_and_monitors_during_slow_sync(self):
        # A blocked metadata read must not prevent another local monitoring pass.
        # Events control ordering; timeouts are only deadlock guards, not sleeps.
        plan = self.home / "plan.json"
        plan.write_text(json.dumps({"versions": []}), encoding="utf-8")
        stop, sync_started, release_sync = threading.Event(), threading.Event(), threading.Event()
        supervisor_finished, sync_finished, background_closed = threading.Event(), threading.Event(), threading.Event()
        ticks, commands, failures = [], [], []
        real_tick = Store.tick
        real_close = Store.close

        class Child:
            pid = 314
            stopped = False

            def poll(self):
                return 0 if self.stopped else None

            def terminate(self):
                self.stopped = True

            def wait(self, timeout):
                return 0

        child = Child()

        def popen(command, **kwargs):
            commands.append(command)
            return child

        def tick(store, *args, **kwargs):
            result = real_tick(store, *args, **kwargs)
            ticks.append(len(ticks) + 1)
            if len(ticks) == 2:
                stop.set()
            return result

        def slow_sync(_store):
            sync_started.set()
            if not release_sync.wait(timeout=5):
                failures.append("sync blocked the supervisor")
            sync_finished.set()
            return {}

        def close(store):
            real_close(store)
            if threading.current_thread().name == "workflow-github-read":
                background_closed.set()

        def run():
            try:
                control.supervisor(self.home, self.home / "profile", port=8188, interval=0,
                                   stop=stop, popen=popen, clock=lambda: self.now[0])
            except BaseException as exc:
                failures.append(exc)
            finally:
                supervisor_finished.set()

        with mock.patch("jarves.workflow.sync.PLAN", plan), mock.patch("jarves.workflow.sync.sync", slow_sync), \
                mock.patch.object(Store, "tick", tick), mock.patch.object(Store, "close", close), \
                mock.patch.object(control, "Store", side_effect=lambda path: Store(path, clock=lambda: self.now[0])):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(sync_started.wait(timeout=5), "background read never started")
                self.assertTrue(supervisor_finished.wait(timeout=5), "metadata read suspended local monitoring")
                self.assertFalse(sync_finished.is_set(), "test read was expected to remain blocked")
                self.assertEqual(ticks, [1, 2])
            finally:
                stop.set()
                release_sync.set()
                thread.join(timeout=5)
                self.assertTrue(sync_finished.wait(timeout=5))
                self.assertTrue(background_closed.wait(timeout=5), "background SQLite handle did not close")
        self.assertEqual(failures, [])
        self.assertEqual(len(commands), 1)
        command = commands[0]
        entry = Path(command[1])
        self.assertEqual(entry.name, "workflow_console.py")
        self.assertTrue(entry.is_file(), "checkout startup must not depend on an installed package")
        self.assertNotIn("-m", command)
        self.assertEqual(command[command.index("--profile-home") + 1], str(self.home / "profile"))
        self.assertTrue(child.stopped)


if __name__ == "__main__":
    unittest.main()
