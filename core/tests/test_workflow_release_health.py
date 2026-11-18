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


def kinds(source):
    return sorted(f["kind"] for f in release_health(source, NOW))


class ReleaseHealthTests(unittest.TestCase):
    def kinds(self, source):
        return kinds(source)

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

    def test_main_state_follows_newest_required_gate(self):
        """JAR-406: a nightly/beta job or a superseded failed attempt on the main sha is not a red main."""
        from jarves.workflow import sync
        gate = sync.REQUIRED_CHECK

        def state(gate_runs, others=(), total=None, gate_total=None):
            every = list(gate_runs) + list(others)
            paths = []

            def get(path):
                paths.append(path)
                if "check_name=" in path:
                    return {"total_count": gate_total or len(gate_runs), "check_runs": list(gate_runs)}
                return {"total_count": total or len(every), "check_runs": every}
            result = sync.main_ci_state(get, "s")
            # The full quoted gate name is asked first; the unfiltered list only as a fallback.
            self.assertEqual(paths[0], "/repos/Jarvis-igerman-ai/jarves/commits/s/check-runs"
                             "?check_name=CI%20result%20%28single%20required%20check%29&per_page=100")
            self.assertEqual(len(paths), 1 if gate_runs else 2)
            return result

        def run(i, conclusion, status="completed", name=gate):
            return {"id": i, "name": name, "status": status, "conclusion": conclusion}
        flake = run(9, "failure", name="Flake detector (3x suite, Linux)")   # main 85aa2aae, 17 Nov
        beta = run(8, "failure", name="beta")                                  # main f4390920 / 5e2e365d
        self.assertEqual(state([run(2, "success")], [flake, beta]), "success")
        self.assertEqual(state([run(1, "failure"), run(5, "success")]), "success")      # re-run passed
        self.assertEqual(state([run(5, "failure"), run(1, "success")]), "failure")      # newest failed
        self.assertEqual(state([run(1, "success"), run(5, None, "in_progress")]), "pending")
        self.assertEqual(state([run(5, "cancelled")]), "unknown")
        self.assertEqual(state([run(5, "success")], total=178), "success")  # main fef9ae06: >100 checks
        self.assertEqual(state([{"id": "x", "name": gate, "status": "completed", "conclusion": "success"}]), "unknown")
        self.assertEqual(state([{"id": True, "name": gate, "status": "completed", "conclusion": "success"}]), "unknown")
        self.assertEqual(state([], [flake]), "failure")  # no gate run seen: unchanged all-checks rule
        self.assertEqual(state([run(5, "success")], gate_total=101), "unknown")  # gate list truncated
        self.assertEqual(state([run(5, "skipped")]), "unknown")                  # skipped gate is not green

    def test_beta_state_of_the_newest_beta_run(self):
        """JAR-406: main_ci_state ignores the beta job, so the beta run is judged on its own."""
        from jarves.workflow import sync

        def state(runs, total=None):
            def get(path):
                self.assertIn("?check_name=beta&", path)
                return {"check_runs": runs} if runs is None or total is None else {"total_count": total, "check_runs": runs}
            return sync.beta_state(get, "s")

        def run(i, conclusion, status="completed", name="beta"):
            return {"id": i, "name": name, "status": status, "conclusion": conclusion}
        self.assertEqual(state([run(1, "failure")]), "failure")
        self.assertEqual(state([run(1, "failure"), run(2, "success")]), "success")   # a passing re-run decides
        self.assertEqual(state([run(1, None, "in_progress")]), "pending")
        self.assertEqual(state([run(1, None, "queued")]), "pending")
        self.assertEqual(state([run(1, "cancelled")]), "unknown")
        self.assertEqual(state([run(1, "skipped")]), "none")                         # beta publish off
        self.assertEqual(state([run(1, "skipped"), run(2, None, "in_progress")]), "pending")
        # No beta run yet: package.yml starts only after the gate passed, so it may still come (JAR-406 R1).
        self.assertEqual(state([run(1, "failure", name="beta-docs")]), "pending")     # exact job name only
        self.assertEqual(state([]), "pending")
        self.assertEqual(state([{"id": "x", "name": "beta", "status": "completed", "conclusion": "success"},
                                run(2, "skipped")]), "unknown")                       # bad id before skipped
        self.assertEqual(state([run(2, "skipped")], total=101), "unknown")            # beta list truncated
        self.assertEqual(state(None), "unknown")
        for exc in (OSError("down"), TypeError("bad"), KeyError("x"), AttributeError("y")):
            def boom(path, exc=exc):
                raise exc
            self.assertEqual(sync.beta_state(boom, "s"), "unknown")   # never breaks collect

    def test_failed_beta_raises_release_alert(self):
        self.assertEqual(kinds({"sha": "c" * 40, "beta_state": "failure"}), ["beta_failed"])
        self.assertEqual(kinds({"sha": "c" * 40, "beta_state": "failure", "error": "x"}), [])
        for st in ("pending", "unknown", "none", "success"):
            self.assertEqual(kinds({"sha": "c" * 40, "beta_state": st}), [])

    def test_beta_verify_task_only_for_a_finished_or_absent_beta(self):
        from jarves.workflow import autopilot_loop
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "w.db")
            self.addCleanup(store.close)
            src = {"sha": "d" * 40, "synced_at": 1, "open_prs": [], "open_issues": [], "ci_green": True}

            def betas(**extra):
                store.set_source({**src, **extra})
                out = autopilot_loop._watch(store, Path(tmp), {"enabled": True, "kill": False},
                                            stop=lambda *a: None, notify=lambda *a: None)
                return [o for o in out if o["action"] == "beta_verify_task"]
            for st in ("failure", "pending", "unknown"):
                self.assertEqual(betas(beta_state=st), [], st)
            self.assertEqual(betas(), [])                                    # old source without the field
            self.assertEqual(len(betas(beta_state="success")), 1)           # same sha, once the beta passed
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "w.db")
            self.addCleanup(store.close)
            store.set_source({**src, "beta_state": "none"})                  # beta publish off: beta run skipped
            out = autopilot_loop._watch(store, Path(tmp), {"enabled": True, "kill": False},
                                        stop=lambda *a: None, notify=lambda *a: None)
            self.assertEqual(len([o for o in out if o["action"] == "beta_verify_task"]), 1)

    def test_collect_reads_beta_only_on_a_green_main(self):
        from jarves.workflow import sync
        gate = {"id": 1, "name": sync.REQUIRED_CHECK, "status": "completed", "conclusion": "success"}
        paths = []

        def get(path):
            paths.append(path)
            if "/branches/main" in path:
                return {"commit": {"sha": "m" * 40, "commit": {}}}
            if "check_name=beta" in path:
                return {"check_runs": [{"id": 2, "name": "beta", "status": "in_progress", "conclusion": None}]}
            if "check-runs" in path:
                return {"check_runs": [gate]}
            return []
        snap = sync.collect(get)
        self.assertEqual((snap["ci_state"], snap["beta_state"]), ("success", "pending"))
        self.assertEqual(sum("check-runs" in p for p in paths), 2)          # gate + beta, no all-checks read

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
        self.assertEqual(snap["beta_state"], "unknown")                 # not green: beta never read


if __name__ == "__main__":
    unittest.main()
