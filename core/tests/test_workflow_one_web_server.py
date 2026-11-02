"""#789: exactly one dashboard web server per supervisor; the port bind is exclusive."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import control, webchild
from jarves.workflow.server import Server, serve
from jarves.workflow.store import Store


def _home() -> Path:
    return Path(tempfile.mkdtemp())


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class StopRecorded(unittest.TestCase):
    def test_matching_record_is_stopped_by_pid(self):
        home, killed = _home(), []
        webchild.record(home, 8188, pid=4242, started=lambda pid: 100.0)
        outcome = webchild.stop_recorded(home, 8188, started=lambda pid: 100.0, kill=lambda pid: killed.append(pid) or True)
        self.assertEqual(outcome, ("stopped", 4242))
        self.assertEqual(killed, [4242])
        self.assertEqual(webchild.read(home), {})

    def test_reused_pid_is_never_touched(self):
        home, killed = _home(), []
        webchild.record(home, 8188, pid=4242, started=lambda pid: 100.0)
        outcome = webchild.stop_recorded(home, 8188, started=lambda pid: 500.0, kill=lambda pid: killed.append(pid) or True)
        self.assertEqual(outcome, ("stale", 4242))
        self.assertEqual(killed, [])
        self.assertEqual(webchild.read(home), {})

    def test_other_port_and_self_are_left_alone(self):
        home = _home()
        never = mock.Mock(side_effect=AssertionError("must not kill"))
        webchild.record(home, 9000, pid=4242, started=lambda pid: 1.0)
        self.assertEqual(webchild.stop_recorded(home, 8188, started=lambda pid: 1.0, kill=never)[0], "other-port")
        webchild.record(home, 8188, started=lambda pid: 1.0)
        self.assertEqual(webchild.stop_recorded(home, 8188, started=lambda pid: 1.0, kill=never)[0], "self")

    def test_failed_stop_keeps_record_and_is_logged(self):
        home, events = _home(), []
        webchild.record(home, 8188, pid=4242, started=lambda pid: 1.0)
        with mock.patch.object(webchild, "started_at", lambda pid: 1.0), mock.patch.object(webchild, "terminate", lambda pid: False):
            self.assertEqual(webchild.reap(home, 8188, lambda kind, text: events.append(text)), ("failed", 4242))
        self.assertEqual(webchild.read(home)["pid"], 4242)
        self.assertIn("4242", events[0])

    def test_real_process_is_stopped(self):
        home, proc = _home(), _sleeper()
        try:
            webchild.record(home, 8188, pid=proc.pid)
            self.assertEqual(webchild.listening_pid(home, 8188), proc.pid)
            self.assertEqual(webchild.stop_recorded(home, 8188), ("stopped", proc.pid))
            self.assertIsNotNone(proc.wait(timeout=10))
            self.assertEqual(webchild.listening_pid(home, 8188), 0)
        finally:
            if proc.poll() is None:
                proc.kill()


class ExclusiveBind(unittest.TestCase):
    def test_second_server_cannot_share_the_port(self):
        first = Server(_home(), 0)
        try:
            with self.assertRaises(OSError):
                Server(_home(), first.server_port).server_close()
        finally:
            first.server_close()

    def test_serve_records_its_own_listening_pid_and_clears_it(self):
        home, box, started = _home(), [], threading.Event()

        def ready(server):
            box.append(server)
            started.set()
        thread = threading.Thread(target=serve, args=(home, 0), kwargs={"ready": ready}, daemon=True)
        thread.start()
        self.assertTrue(started.wait(10))
        rec = webchild.read(home)
        self.assertEqual((rec["pid"], rec["port"]), (os.getpid(), box[0].server_port))
        box[0].shutdown()
        thread.join(10)
        self.assertEqual(webchild.read(home), {})


class SupervisorRestartLoop(unittest.TestCase):
    """Simulation: five supervisor restarts, each leaving a live server behind, end with no orphan."""

    class FakeChild:
        pid = 1
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0

    def _run_once(self, home: Path, plan: Path) -> None:
        stop = threading.Event()
        with mock.patch.object(control, "dispatch", lambda *a, **k: stop.set()), \
                mock.patch("jarves.workflow.autopilot_loop.step_safe", lambda *a, **k: None), \
                mock.patch("jarves.workflow.autopilot.step", lambda *a, **k: None), \
                mock.patch("jarves.workflow.sync.PLAN", plan), \
                mock.patch.object(threading.Event, "wait", lambda self, t=None: self.is_set()):
            control.supervisor(home, home / "profile", port=8188, interval=0, sync_github=False, stop=stop,
                               popen=lambda *a, **k: self.FakeChild())

    def test_five_restarts_leave_no_orphan_server(self):
        home = _home()
        plan = home / "plan.json"
        plan.write_text('{"versions": []}', encoding="utf-8")
        for _ in range(5):
            orphan = _sleeper()  # the real server a crashed supervisor left on the port
            try:
                webchild.record(home, 8188, pid=orphan.pid)
                self._run_once(home, plan)
                self.assertIsNotNone(orphan.wait(timeout=10))
            finally:
                if orphan.poll() is None:
                    orphan.kill()
        store = Store(home / "workflow.db")
        stopped = [e for e in store.snapshot().get("events", []) if "Stopped the previous dashboard server" in e.get("summary", e.get("text", str(e)))]
        store.close()
        self.assertEqual(len(stopped), 5)
        self.assertEqual(webchild.read(home), {})


if __name__ == "__main__":
    unittest.main()
