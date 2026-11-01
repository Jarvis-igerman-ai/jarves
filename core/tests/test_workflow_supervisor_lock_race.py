"""#754: a restart that overlaps the old supervisor's shutdown must wait, not exit."""
import threading
import time

import pytest

from jarves.workflow.control import exclusive


def test_held_lock_without_wait_fails_fast(tmp_path):
    lock = tmp_path / "supervisor.lock"
    with exclusive(lock):
        with pytest.raises(OSError):
            with exclusive(lock):
                pass


def test_restart_waits_for_old_instance_to_release(tmp_path):
    lock = tmp_path / "supervisor.lock"
    held, release, got = threading.Event(), threading.Event(), []

    def old():
        with exclusive(lock):
            held.set()
            release.wait(10)

    t = threading.Thread(target=old)
    t.start()
    assert held.wait(5)
    threading.Timer(0.5, release.set).start()
    start = time.monotonic()
    with exclusive(lock, wait_s=10):
        got.append(time.monotonic() - start)
    t.join()
    assert 0.3 < got[0] < 8


def test_wait_is_bounded(tmp_path):
    lock = tmp_path / "supervisor.lock"
    with exclusive(lock):
        with pytest.raises(OSError):
            with exclusive(lock, wait_s=0.6):
                pass
