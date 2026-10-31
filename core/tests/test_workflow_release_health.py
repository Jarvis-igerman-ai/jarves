"""V451-WATCHDOG: release-health findings are explicit, deduplicated and never act."""
import datetime
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.release_health import release_health, watch_release_health
from jarves.workflow.store import Store

NOW = datetime.datetime(2026, 11, 1, 12, 0, tzinfo=datetime.timezone.utc).timestamp()


def iso(hours_ago):
    return datetime.datetime.fromtimestamp(NOW - hours_ago * 3600, datetime.timezone.utc).isoformat()


def pr(n, hours, **kw):
    return {"number": n, "updated_at": iso(hours), "head_sha": "abcdef012345", "labels": [], **kw}


class ReleaseHealthTests(unittest.TestCase):
    def kinds(self, source):
        return sorted(f["kind"] for f in release_health(source, NOW))

    def test_red_main(self):
        self.assertEqual(self.kinds({"sha": "a" * 40, "ci_green": False}), ["main_not_green"])
        self.assertEqual(self.kinds({"sha": "a" * 40, "ci_green": False, "error": "sync down"}), [])

    def test_stuck_and_dropped_queue(self):
        src = {"open_prs": [pr(1, 30), pr(2, 3, auto_merge=True), pr(3, 1, auto_merge=True), pr(4, 1)]}
        self.assertEqual(self.kinds(src), ["pr_stuck", "queue_stalled"])

    def test_tier_r_and_draft_prs_are_not_flagged(self):
        src = {"open_prs": [pr(1, 99, labels=["no-automerge"]), pr(2, 99, draft=True)]}
        self.assertEqual(self.kinds(src), [])

    def test_missing_beta(self):
        base = {"sha": "b" * 40, "ci_green": True, "main_committed_at": iso(8)}
        self.assertEqual(self.kinds({**base, "latest_beta": {"tag": "v1-beta.1", "published_at": iso(20)}}), ["beta_missing"])
        self.assertEqual(self.kinds({**base, "latest_beta": {"tag": "v1-beta.2", "published_at": iso(2)}}), [])
        self.assertEqual(self.kinds({**base, "latest_beta": None}), [])          # unknown: no claim
        self.assertEqual(self.kinds({**base, "main_committed_at": iso(1), "latest_beta": {"tag": "", "published_at": ""}}), [])

    def test_alert_once_and_no_actions(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "w.sqlite3", clock=lambda: NOW)
            src = {"sha": "c" * 40, "ci_green": False}
            seen = []
            self.assertEqual(watch_release_health(store, src, notify=lambda k, t: seen.append(k)), ["main_not_green"])
            self.assertEqual(watch_release_health(store, src, notify=lambda k, t: seen.append(k)), [])
            self.assertEqual(seen, ["release_main_not_green"])
            store.close()


if __name__ == "__main__":
    unittest.main()
