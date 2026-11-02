"""#754: a restart that overlaps the old supervisor's shutdown must wait, not exit."""
import threading

import pytest

from jarves.workflow import control
from jarves.workflow.control import exclusive


class FakeTime:
    def __init__(self, on_sleep=None):
        self.now, self.sleeps, self.on_sleep = 0.0, [], on_sleep

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


def test_held_lock_without_wait_fails_fast(tmp_path):
    lock = tmp_path / "supervisor.lock"
    with exclusive(lock):
        with pytest.raises(OSError):
            with exclusive(lock):
                pass


def test_restart_waits_for_old_instance_to_release(tmp_path):
    lock = tmp_path / "supervisor.lock"
    held, release = threading.Event(), threading.Event()

    def old():
        with exclusive(lock):
            held.set()
            release.wait(10)

    t = threading.Thread(target=old)
    t.start()
    assert held.wait(5)
    fake = FakeTime(on_sleep=lambda n: release.set() if n == 2 else (t.join(5) if n > 2 else None))
    with exclusive(lock, wait_s=10, sleep=fake.sleep, clock=fake.clock):
        pass
    t.join()
    assert len(fake.sleeps) >= 2


def test_wait_is_bounded(tmp_path):
    lock = tmp_path / "supervisor.lock"
    fake = FakeTime()
    with exclusive(lock):
        with pytest.raises(OSError):
            with exclusive(lock, wait_s=1.0, sleep=fake.sleep, clock=fake.clock):
                pass
    assert fake.sleeps == [0.25] * 4


def test_supervisor_passes_lock_wait(tmp_path, monkeypatch):
    seen = {}

    class Boom(Exception):
        pass

    def fake_exclusive(path, **kw):
        seen.update(kw)
        raise Boom

    monkeypatch.setattr(control, "exclusive", fake_exclusive)
    with pytest.raises(Boom):
        control.supervisor(tmp_path, tmp_path / "profile", port=8188)
    assert seen["wait_s"] == control.SUPERVISOR_LOCK_WAIT_S


def test_supervisor_exits_cleanly_when_lock_stays_held(tmp_path, caplog):
    fake = FakeTime()
    with exclusive(tmp_path / "supervisor.lock"):
        control.supervisor(tmp_path, tmp_path / "profile", port=8188, sync_github=False,
                           lock_sleep=fake.sleep, lock_clock=fake.clock)
    assert fake.now >= control.SUPERVISOR_LOCK_WAIT_S
    assert "another supervisor holds the lock" in caplog.text
