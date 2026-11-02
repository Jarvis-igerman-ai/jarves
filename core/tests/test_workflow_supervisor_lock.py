"""#754: a successor supervisor waits for the exiting one instead of dying on the lock (temp dir only)."""
import contextlib
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.control import exclusive


class SupervisorLockTests(unittest.TestCase):
    def test_waiter_acquires_after_the_holder_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "supervisor.lock"
            stack = contextlib.ExitStack()
            stack.enter_context(exclusive(lock))
            sleeps = []

            def fake_sleep(_):
                sleeps.append(1)
                if len(sleeps) == 2:
                    stack.close()  # the old supervisor finishes exiting

            with exclusive(lock, wait_s=5, sleep=fake_sleep):
                pass
            self.assertEqual(len(sleeps), 2)

    def test_live_holder_still_fails_after_the_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "supervisor.lock"
            with exclusive(lock):
                with self.assertRaises(OSError):
                    with exclusive(lock, wait_s=1, sleep=lambda _: None):
                        pass
                with self.assertRaises(OSError):
                    with exclusive(lock):
                        pass


if __name__ == "__main__":
    unittest.main()
