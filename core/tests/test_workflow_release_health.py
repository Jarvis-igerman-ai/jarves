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
        self.assertEqual(self.kinds({"sha": "a" * 40, "ci_state": "failure"}), ["main_not_green"])
        self.assertEqual(self.kinds({"sha": "a" * 40, "ci_state": "failure", "error": "sync down"}), [])

    def test_running_or_unknown_ci_is_silent(self):
        for st in ("pending", "unknown", "success"):
            self.assertEqual(self.kinds({"sha": "a" * 40, "ci_state": st, "ci_green": False}), [])

    def test_stuck_and_dropped_queue(self):
        src = {"open_prs": [pr(1, 30), pr(2, 3, auto_merge=True), pr(3, 1, auto_merge=True), pr(4, 1)]}
        self.assertEqual(self.kinds(src), ["pr_stuck", "queue_stalled"])

    def test_tier_r_and_draft_prs_are_not_flagged(self):
        src = {"open_prs": [pr(1, 99, labels=["no-automerge"]), pr(2, 99, draft=True)]}
        self.assertEqual(self.kinds(src), [])

    def test_missing_beta(self):
        base = {"sha": "b" * 40, "ci_state": "success", "main_committed_at": iso(8)}
        self.assertEqual(self.kinds({**base, "latest_beta": {"tag": "v1-beta.1", "published_at": iso(20)}}), ["beta_missing"])
        self.assertEqual(self.kinds({**base, "latest_beta": {"tag": "v1-beta.2", "published_at": iso(2)}}), [])
        self.assertEqual(self.kinds({**base, "latest_beta": None}), [])          # unknown: no claim
        self.assertEqual(self.kinds({**base, "main_committed_at": iso(1), "latest_beta": {"tag": "", "published_at": ""}}), [])

    def test_alert_once_and_no_actions(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "w.sqlite3", clock=lambda: NOW)
            src = {"sha": "c" * 40, "ci_state": "failure"}
            seen = []
            self.assertEqual(watch_release_health(store, src, notify=lambda k, t: seen.append(k)), ["main_not_green"])
            self.assertEqual(watch_release_health(store, src, notify=lambda k, t: seen.append(k)), [])
            self.assertEqual(seen, ["release_main_not_green"])
            store.close()

    def test_dedup_not_evicted_by_other_alerts(self):
        from jarves.workflow import autopilot_watch as w
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "w.sqlite3", clock=lambda: NOW)
            src = {"sha": "c" * 40, "ci_state": "failure"}
            seen = []
            watch_release_health(store, src, notify=lambda k, t: seen.append(k))
            for i in range(60):
                w.raise_alert(store, "other", "x", task_id=str(i))
            self.assertEqual(watch_release_health(store, src, notify=lambda k, t: seen.append(k)), [])
            self.assertEqual(seen, ["release_main_not_green"])
            self.assertEqual(watch_release_health(store, {"sha": "c" * 40, "ci_state": "success"}), [])
            self.assertEqual(watch_release_health(store, src), ["main_not_green"])  # cleared, then red again
            store.close()

    def test_raise_cap_per_tick(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "w.sqlite3", clock=lambda: NOW)
            src = {"open_prs": [pr(n, 30) for n in range(1, 12)]}
            self.assertEqual(len(watch_release_health(store, src)), 5)
            self.assertEqual(len(watch_release_health(store, src)), 5)
            store.close()


class SyncFieldTests(unittest.TestCase):
    def test_ci_state_and_beta(self):
        from jarves.workflow import sync

        def runs(*pairs):
            return {"total_count": len(pairs), "check_runs": [{"status": s, "conclusion": c} for s, c in pairs]}
        cases = [([("completed", "success")], "success"), ([("in_progress", None), ("completed", "success")], "pending"),
                 ([("in_progress", None), ("completed", "failure")], "failure"), ([], "unknown"),
                 ([("completed", "cancelled")], "unknown")]
        for pairs, want in cases:
            self.assertEqual(sync.main_ci_state(lambda p, r=runs(*pairs): r, "s"), want)
        many = {"total_count": 150, "check_runs": [{"status": "completed", "conclusion": "success"}]}
        self.assertEqual(sync.main_ci_state(lambda p: many, "s"), "unknown")

        def boom(p):
            raise OSError("down")
        self.assertEqual(sync.main_ci_state(boom, "s"), "unknown")
        rel = [{"prerelease": False, "tag_name": "v1"}, {"prerelease": True, "draft": True, "tag_name": "d"},
               {"prerelease": True, "tag_name": "b1", "published_at": "2026-11-01T00:00:00Z"}]
        self.assertEqual(sync.latest_beta(lambda p: rel), {"tag": "b1", "published_at": "2026-11-01T00:00:00Z"})
        self.assertIsNone(sync.latest_beta(boom))

    def test_collect_carries_draft_auto_merge_and_ci_state(self):
        from jarves.workflow import sync

        def get(path):
            if "/pulls?" in path:
                return [{"number": 7, "title": "t", "state": "open", "labels": [], "draft": True,
                         "auto_merge": {"x": 1}, "head": {"sha": "abc"}}] if "page=1" in path else []
            if "/branches/main" in path:
                return {"commit": {"sha": "m" * 40, "commit": {"committer": {"date": "2026-11-01T00:00:00Z"}}}}
            if "check-runs" in path:
                return {"total_count": 1, "check_runs": [{"status": "in_progress"}]}
            return []
        snap = sync.collect(get)
        p = snap["prs"][0]
        self.assertTrue(p["draft"])
        self.assertTrue(p["auto_merge"])
        self.assertEqual(snap["ci_state"], "pending")
        self.assertFalse(snap["ci_green"])


if __name__ == "__main__":
    unittest.main()
