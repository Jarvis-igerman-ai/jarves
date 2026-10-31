import tempfile
import unittest
from pathlib import Path

from jarves.workflow import autopilot_update as up
from jarves.workflow.autopilot_watch import autopilot_alerts
from jarves.workflow.store import Store


class FakeGit:
    def __init__(self, dirty="", head="aaa", remote="bbb", ff_ok=True, fetch_ok=True):
        self.dirty, self.head, self.remote, self.ff_ok, self.fetch_ok, self.calls = dirty, head, remote, ff_ok, fetch_ok, []

    def __call__(self, repo, args):
        self.calls.append(args)
        if args[0] == "fetch":
            return (0 if self.fetch_ok else 1), ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main"
        if args[0] == "status":
            return 0, self.dirty
        if args == ["rev-parse", "HEAD"]:
            return 0, self.head
        if args[0] == "rev-parse":
            return 0, self.remote
        if args[0] == "merge":
            assert args == ["merge", "--ff-only", "jarves/main"]      # only ever fast-forward
            if self.ff_ok:
                self.head = self.remote
            return (0 if self.ff_ok else 1), ""
        raise AssertionError(args)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "w.db")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)
        self.policy = {"enabled": True, "kill": False}

    def sync(self, sha):
        self.store.set_source({"sha": sha, "synced_at": 1, "open_prs": [], "open_issues": []})

    def test_fast_forward_then_idempotent(self):
        git = FakeGit()
        self.sync("s1")
        self.assertEqual(up.update_step(self.store, self.policy, repo=Path("."), git=git), "updated")
        self.assertEqual(git.head, "bbb")
        self.assertEqual(up.update_step(self.store, self.policy, repo=Path("."), git=git), "skipped")  # same sha

    def test_dirty_checkout_alerts_once_and_never_merges(self):
        git = FakeGit(dirty=" M core/x.py")
        self.sync("s1")
        self.assertEqual(up.update_step(self.store, self.policy, repo=Path("."), git=git), "blocked_dirty")
        self.assertFalse(any(c[0] == "merge" for c in git.calls))
        self.sync("s1")
        up.update_step(self.store, self.policy, repo=Path("."), git=git)
        self.assertEqual(len([a for a in autopilot_alerts(self.store) if a["kind"] == "update_blocked_dirty"]), 1)

    def test_non_fast_forward_alerts_and_does_not_reset(self):
        git = FakeGit(ff_ok=False)
        self.sync("s2")
        self.assertEqual(up.update_step(self.store, self.policy, repo=Path("."), git=git), "blocked_non_ff")
        self.assertEqual(git.head, "aaa")
        self.assertFalse(any(c[0] in {"reset", "checkout", "pull"} for c in git.calls))

    def test_disabled_or_killed_does_nothing(self):
        git = FakeGit()
        self.sync("s3")
        self.assertEqual(up.update_step(self.store, {"enabled": False}, repo=Path("."), git=git), "skipped")
        self.assertEqual(up.update_step(self.store, {"enabled": True, "kill": True}, repo=Path("."), git=git), "skipped")
        self.assertEqual(git.calls, [])


if __name__ == "__main__":
    unittest.main()
