"""Focus (dynamic prioritisation): each rule, hysteresis, bounded borrowing, steward never 0, main-red wins, usage stop wins."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarves.workflow import autopilot, focus
from jarves.workflow import autopilot_loop as ap
from jarves.workflow.store import Store

POLICY = {"enabled": True, "kill": False, "project": "jarves", "versions": ["V4.5.1"], "authorized_versions": ["V4.5.1"],
          "max_concurrent": 4, "max_starts_per_hour": 50,
          "lanes": {"bugs": {"workers": 1, "model": "claude-sonnet-5-5"}, "features": {"workers": 2, "model": "claude-sonnet-5-5"}}}


def pr(n, ci="SUCCESS", mergeable="MERGEABLE", labels=(), draft=False):
    return {"number": n, "isDraft": draft, "mergeable": mergeable, "labels": [{"name": x} for x in labels],
            "statusCheckRollup": [{"conclusion": ci}]}


def issue(n, *labels):
    return {"number": n, "labels": [{"name": x} for x in labels]}


class FakeGh:
    def __init__(self, prs=(), issues=()):
        self.prs, self.issues, self.calls, self.fail = list(prs), list(issues), 0, False

    def __call__(self, kind):
        self.calls += 1
        if self.fail:
            raise RuntimeError("gh down")
        return self.prs if kind == "prs" else self.issues


def inputs(total=0, green=0, red=0, conflicted=0, tier_r=0, pressure=0, main_red=False, bugs=3, usage="ok", blocked=()):
    return {"prs": {"total": total, "green": green, "red": red, "conflicted": conflicted, "tier_r_waiting": tier_r},
            "issues": {"showstopper": pressure, "p0": 0, "p1": 0, "p2": 0, "main_fix": 0, "pressure": pressure},
            "main_red": main_red, "queued": {"bugs": bugs, "features": 3}, "questions_waiting": 0,
            "blocked_lanes": list(blocked), "usage_level": usage, "source": "gh"}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.now = [1000.0]
        self.store = Store(self.home / "workflow.db", lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def ev(self, gh, **kw):
        return focus.evaluate(self.store, POLICY, gh, **kw)

    def tick(self, gh, seconds=301):
        self.now[0] += seconds
        return self.ev(gh)

    def events(self):
        with self.store._tx() as db:
            return [r[0] for r in db.execute("SELECT kind FROM workflow_events WHERE kind='focus_changed'")]


class RuleTests(unittest.TestCase):
    def test_balanced_keeps_static_plan(self):
        r = focus.compute_focus(inputs(total=2), POLICY)
        self.assertEqual(r["focus"], "balanced")
        self.assertEqual(r["lanes"], {"bugs": 1, "features": 2, "steward": 1, "review": 0})

    def test_many_prs_means_merge(self):
        r = focus.compute_focus(inputs(total=9, green=2, tier_r=3, red=1, conflicted=1), POLICY)
        self.assertEqual(r["focus"], "merge")
        self.assertEqual(r["lanes"]["features"], 1)
        self.assertEqual(r["lanes"]["bugs"], 1)
        self.assertEqual(r["lanes"]["review"], 2)
        self.assertIn("9 open PRs", " ".join(r["reasons"]))
        self.assertEqual(focus.line_he(r), "מיקוד: מיזוג — 9 PR פתוחים")

    def test_three_green_waiting_means_merge(self):
        self.assertEqual(focus.compute_focus(inputs(total=3, green=3), POLICY)["focus"], "merge")

    def test_showstoppers_mean_fix_and_borrow_from_features(self):
        r = focus.compute_focus(inputs(total=1, pressure=2, bugs=5), POLICY)
        self.assertEqual(r["focus"], "fix")
        self.assertEqual(r["lanes"]["bugs"], 3)                       # cap 4 - steward 1
        self.assertEqual(r["lanes"]["features"], 0)
        self.assertEqual(r["lanes"]["steward"], 1)

    def test_borrowing_is_bounded_by_max_concurrent(self):
        for cap in range(0, 9):
            policy = {**POLICY, "max_concurrent": cap}
            for case in (inputs(pressure=3, bugs=9), inputs(total=9), inputs(main_red=True), inputs()):
                lanes = focus.compute_focus(case, policy)["lanes"]
                self.assertLessEqual(lanes["bugs"] + lanes["features"], cap, (cap, lanes))
                self.assertEqual(lanes["steward"], 1)

    def test_steward_never_zero_even_on_usage_stop(self):
        r = focus.compute_focus(inputs(total=9, usage="stop"), POLICY)
        self.assertEqual(r["lanes"], {"bugs": 0, "features": 0, "steward": 1, "review": 0})

    def test_main_red_wins_over_everything(self):
        r = focus.compute_focus(inputs(total=20, green=9, pressure=5, main_red=True), POLICY)
        self.assertEqual(r["focus"], "main-fix")
        self.assertEqual(r["lanes"]["features"], 0)
        self.assertEqual(r["lanes"]["review"], 0)
        self.assertGreaterEqual(r["lanes"]["bugs"], 1)
        self.assertGreaterEqual(len(r["reasons"]), 3)

    def test_fix_beats_merge(self):
        self.assertEqual(focus.compute_focus(inputs(total=20, pressure=2), POLICY)["focus"], "fix")

    def test_usage_slow_limits_to_one(self):
        lanes = focus.compute_focus(inputs(pressure=2, usage="slow"), POLICY)["lanes"]
        self.assertEqual(lanes["bugs"] + lanes["features"], 1)

    def test_blocked_lane_gets_no_borrowed_workers(self):
        r = focus.compute_focus(inputs(pressure=2, bugs=5, blocked=["bugs"]), POLICY)
        self.assertEqual(r["lanes"]["bugs"], 1)
        self.assertIn("owner question blocks lane", " ".join(r["reasons"]))

    def test_policy_validation(self):
        base = autopilot.DEFAULTS
        autopilot.validate({**base, "focus_pr_high": 4, "focus_enabled": False})
        for bad in ({"focus_pr_high": 0}, {"focus_pr_high": "6"}, {"focus_enabled": 1}, {"focus_showstopper_high": 101},
                    {"focus_green_high": True}):
            with self.assertRaises(ValueError, msg=str(bad)):
                autopilot.validate({**base, **bad})

    def test_classify(self):
        c = focus.classify_prs([pr(1), pr(2, ci="FAILURE"), pr(3, mergeable="CONFLICTING"), pr(4, labels=["no-automerge"]),
                                pr(5, draft=True), pr(6, ci="IN_PROGRESS")])
        self.assertEqual(c, {"total": 6, "green": 1, "red": 1, "conflicted": 1, "tier_r_waiting": 1})
        i = focus.classify_issues([issue(1, "showstopper", "P0"), issue(2, "P1"), issue(3, "main-fix", "P2")])
        self.assertEqual((i["showstopper"], i["p0"], i["p1"], i["p2"], i["main_fix"], i["pressure"]), (1, 1, 1, 1, 1, 1))


class StateTests(Base):
    def test_hysteresis_and_one_event_per_change(self):
        busy = FakeGh([pr(n) for n in range(8)])
        first = self.ev(busy)
        self.assertEqual(first["focus"], "balanced")                  # seen once: not yet
        self.assertEqual(first["pending"]["seen"], 1)
        self.assertEqual(self.events(), [])
        second = self.tick(busy)
        self.assertEqual(second["focus"], "merge")
        self.assertEqual(len(self.events()), 1)
        self.tick(busy)
        self.tick(busy)
        self.assertEqual(len(self.events()), 1, "no new event while the focus holds")
        calm = FakeGh()
        self.assertEqual(self.tick(calm)["focus"], "merge")           # one calm look is not enough
        self.assertEqual(self.tick(calm)["focus"], "balanced")
        self.assertEqual(len(self.events()), 2)

    def test_flapping_does_not_change_focus(self):
        busy, calm = FakeGh([pr(n) for n in range(8)]), FakeGh()
        for i in range(8):
            self.assertEqual(self.tick(busy if i % 2 == 0 else calm)["focus"], "balanced")
        self.assertEqual(self.events(), [])

    def test_read_only_view_does_not_advance_state(self):
        busy = FakeGh([pr(n) for n in range(8)])
        for _ in range(5):
            self.now[0] += 301
            self.ev(busy, commit=False)
        self.assertEqual(self.ev(busy, commit=False)["focus"], "balanced")
        self.assertEqual(self.events(), [])

    def test_gh_cached_five_minutes_and_fail_soft(self):
        gh = FakeGh([pr(1)], [issue(1, "showstopper")])
        self.ev(gh)
        calls = gh.calls
        self.ev(gh)
        self.assertEqual(gh.calls, calls, "second look inside 5 minutes uses the cache")
        gh.fail = True
        self.now[0] += 301
        r = self.ev(gh)                                               # gh down: last good data, no exception
        self.assertEqual(r["inputs"]["issues"]["showstopper"], 1)
        self.assertEqual(r["inputs"]["source"], "stale-cache")

    def test_gh_down_without_cache_uses_sync_snapshot(self):
        gh = FakeGh()
        gh.fail = True
        r = self.ev(gh)
        self.assertEqual(r["inputs"]["source"], "snapshot")
        self.assertEqual(r["focus"], "balanced")

    def test_main_red_from_sync_snapshot(self):
        with self.store._tx() as db:
            self.store._set_meta(db, "source", {"sha": "abcdef12", "ci_state": "failure", "open_prs": [], "open_issues": []})
        self.assertTrue(focus.gather_inputs(self.store, FakeGh())["main_red"])


class LoopTests(Base):
    def run_step(self, gh, policy=None, usage_pct=10):
        started = []

        def start(_h, **req):
            started.append(req["env"]["JARVES_WORKFLOW_AGENT"])
            return {"ok": True, "session": {"id": f"s{len(started)}"}}
        ap._LAST_SIG.clear()
        ap.step(self.store, self.home, policy or POLICY, start=start, stop=lambda *a: {"ok": True}, notify=lambda *a: None,
                force=True, usage=lambda s, p: {"pct": usage_pct, "text": "x"}, gh_focus=gh)
        return started

    def tasks(self):
        for i in range(4):
            self.store.create_task({"id": f"b{i}", "title": f"b{i}", "lane": "bugs", "version": "V4.5.1",
                                    "acceptance": [{"id": "C1", "label": "ok"}]})
            self.store.create_task({"id": f"f{i}", "title": f"f{i}", "lane": "features", "version": "V4.5.1",
                                    "acceptance": [{"id": "C1", "label": "ok"}]})

    def lane_counts(self, started):
        return sum(a.startswith("autopilot-bugs") or "bugs" in a for a in started), sum("features" in a for a in started)

    def test_static_policy_when_focus_disabled(self):
        self.tasks()
        started = self.run_step(FakeGh([pr(n) for n in range(9)]), {**POLICY, "focus_enabled": False})
        self.assertEqual(self.lane_counts(started), (1, 2))

    def test_focus_plan_drives_workers_after_two_evaluations(self):
        self.tasks()
        busy = FakeGh([pr(n) for n in range(9)])
        self.run_step(busy)                                           # evaluation 1: still balanced -> 1 bug + 2 features
        self.assertEqual(self.ev(busy, commit=False)["focus"], "balanced")
        self.now[0] += 301
        self.run_step(busy)                                           # evaluation 2: merge focus is committed
        self.assertEqual(focus.evaluate(self.store, POLICY, busy, commit=False)["focus"], "merge")
        self.assertEqual(len(self.events()), 1)

    def hold(self, name):
        focus._put(self.store, focus.STATE_KEY, {"current": name, "candidate": None, "streak": 0})

    def test_merge_focus_drops_features_to_one(self):
        self.tasks()
        self.hold("merge")
        started = self.run_step(FakeGh([pr(n) for n in range(9)]))
        self.assertEqual(self.lane_counts(started), (1, 1))

    def test_fix_focus_gives_bugs_the_workers(self):
        self.tasks()
        self.hold("fix")
        started = self.run_step(FakeGh([], [issue(1, "showstopper"), issue(2, "P0")]))
        self.assertEqual(self.lane_counts(started), (3, 0))

    def test_main_fix_pauses_features(self):
        self.tasks()
        self.hold("main-fix")
        with self.store._tx() as db:
            self.store._set_meta(db, "source", {"sha": "abcdef12", "ci_state": "failure", "open_prs": [], "open_issues": []})
        started = self.run_step(FakeGh())
        self.assertEqual(self.lane_counts(started), (1, 0))

    def test_usage_stop_starts_nothing_even_with_fix_focus(self):
        self.tasks()
        gh = FakeGh([], [issue(1, "showstopper"), issue(2, "P0")])
        self.assertEqual(self.run_step(gh, usage_pct=99), [])

    def test_focus_failure_falls_back_to_static(self):
        self.tasks()

        def boom(kind):
            raise RuntimeError("x")
        started = self.run_step(boom)
        self.assertEqual(self.lane_counts(started), (1, 2))


if __name__ == "__main__":
    unittest.main()
