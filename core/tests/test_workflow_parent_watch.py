"""#789: a dashboard server whose supervisor is gone stops itself (no orphan), by its own PID only."""
from __future__ import annotations

import threading
import unittest
from pathlib import Path

from jarves.workflow import webchild


class _Server:
    def __init__(self):
        self.stopped = threading.Event()

    def shutdown(self):
        self.stopped.set()


def _run(readings):
    """Drive the watcher with scripted parent start times; wait() returns at once (clock-free)."""
    server, seq = _Server(), iter(readings)
    thread = webchild.watch_parent(server, 4242, 0, started=lambda pid: next(seq), wait=lambda s: None)
    thread.join(5)
    return server


class ParentWatch(unittest.TestCase):
    def test_server_shuts_down_when_parent_exits(self):
        self.assertTrue(_run([100.0, 100.0, None]).stopped.is_set())

    def test_reused_parent_pid_counts_as_gone(self):
        self.assertTrue(_run([100.0, 100.0, 900.0]).stopped.is_set())

    def test_unreadable_parent_start_time_never_stops_the_server(self):
        self.assertFalse(_run([None]).stopped.is_set())

    def test_supervisor_passes_its_pid_to_the_web_child(self):
        src = (Path(webchild.__file__).parent / "control.py").read_text(encoding="utf-8")
        self.assertIn('"--parent-pid", str(os.getpid())', src)


if __name__ == "__main__":
    unittest.main()
