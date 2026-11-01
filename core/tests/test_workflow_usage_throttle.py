"""Autopilot usage throttle (#776): below 80 normal, 80-95 one at a time, >=95 none, resume, unknown never blocks."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarves.workflow import autopilot, observability, usage
from jarves.workflow import autopilot_loop as ap
from jarves.workflow.autopilot_watch import autopilot_alerts
from jarves.workflow.store import Store

POLICY = {"enabled": True, "kill": False, "project": "jarves", "versions": ["V4.5.1"], "authorized_versions": ["V4.5.1"],
          "max_concurrent": 4, "max_starts_per_hour": 50,
          "lanes": {"bugs": {"workers": 2, "model": "claude-sonnet-5-5"},
                    "features": {"workers": 2, "model": "claude-sonnet-5-5"}}}


def fake_usage(pct, estimate=False):
    return lambda store, policy: {"pct": pct, "source": "estimate" if estimate else "statusline", "estimate": estimate,
                                  "text": "unknown" if pct is None else f"{pct:.0f}% weekly" + (" (ESTIMATE)" if estimate else "")}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = Store(self.home / "workflow.db")
        ap._LAST_SIG.clear()
        self.started, self.notes = [], []
        for i in range(3):
            self.task(f"b{i}", "bugs")
            self.task(f"f{i}", "features")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def task(self, ident, lane):
        return self.store.create_task({"id": ident, "title": ident, "lane": lane, "version": "V4.5.1",
                                       "acceptance": [{"id": "C1", "label": "ok"}]})

    def start(self, _home, **req):
        self.started.append(req["env"]["JARVES_WORKFLOW_AGENT"])
        return {"ok": True, "session": {"id": f"s{len(self.started)}"}}

    def step(self, pct, policy=None):
        ap._LAST_SIG.clear()
        return ap.step(self.store, self.home, policy or POLICY, start=self.start, stop=lambda *a: {"ok": True},
                       notify=lambda _h, k, t: self.notes.append((k, t)), force=True, usage=fake_usage(pct))

    def count(self, actions):
        return len([a for a in actions if a["action"] == "started"])


class ThrottleTests(Base):
    def test_below_80_runs_normally(self):
        self.assertEqual(self.count(self.step(79.9)), 4)

    def test_80_to_95_one_at_a_time(self):
        self.assertEqual(self.count(self.step(80)), 1)
        self.assertEqual(self.count(self.step(94.9)), 0)       # one is running: nothing else starts
        self.assertEqual(len(self.started), 1)

    def test_95_starts_nothing_alerts_once_and_notifies(self):
        self.assertEqual(self.count(self.step(95)), 0)
        self.step(97)
        alerts = [a for a in autopilot_alerts(self.store) if a["kind"] == "usage_stop"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual([k for k, _ in self.notes], ["usage_stop"])
        self.assertIn("USAGE_STOP", observability._idle_reason(self.store, POLICY, [], fake_usage(95)))

    def test_running_tasks_finish_and_resume_below_80(self):
        self.step(50)
        self.assertEqual(len(self.started), 4)
        self.assertEqual(self.count(self.step(96)), 0)         # running ones are not stopped, none added
        self.assertEqual(self.store.snapshot()["agents"][0]["status"] != "", True)
        self.assertEqual([a for a in autopilot_alerts(self.store) if a["kind"] == "usage_stop"][0]["kind"], "usage_stop")
        self.step(40)                                           # drops: alert re-armed
        self.assertEqual([a for a in autopilot_alerts(self.store) if a["kind"] == "usage_stop"], [])

    def test_owner_clear_turns_stop_into_slow_until_below_80(self):
        self.assertEqual(self.count(self.step(96)), 0)
        autopilot.command(self.store, self.home, "usage-resume", [])
        self.assertEqual(self.count(self.step(96)), 1)          # one at a time
        self.assertEqual(self.count(self.step(96)), 0)
        self.step(60)
        self.assertFalse(usage.override_active(self.store))
        self.assertEqual(usage.level(self.store, POLICY, {"pct": 96}), "stop")

    def test_unknown_source_never_blocks(self):
        self.assertEqual(self.count(self.step(None)), 4)

    def test_idle_reasons(self):
        self.step(85)
        reason = observability._idle_reason(self.store, POLICY, [], fake_usage(85))
        self.assertTrue(reason.startswith("USAGE_SLOW"), reason)


class SourceTests(Base):
    def test_statusline_snapshot_and_stale_is_unknown(self):
        path = self.home / "usage.json"
        path.write_text(json.dumps({"weekly_pct": 83.0, "ts": self.store.clock()}), encoding="utf-8")
        got = usage.current(self.store, POLICY, path)
        self.assertEqual((got["pct"], got["source"], got["estimate"]), (83.0, "statusline", False))
        path.write_text(json.dumps({"weekly_pct": 83.0, "ts": self.store.clock() - 13 * 3600}), encoding="utf-8")
        self.assertIsNone(usage.current(self.store, POLICY, path)["pct"])
        self.assertIn("DISABLED", usage.current(self.store, POLICY, path)["notice"])

    def test_extract_from_statusline_json(self):
        got = usage.extract({"rate_limits": {"seven_day": {"used_percentage": 41.5}, "five_hour": {"used_percentage": 9}}})
        self.assertEqual(got, {"weekly_pct": 41.5, "five_hour_pct": 9.0})
        self.assertIsNone(usage.extract({"context_window": {}}))

    def test_estimate_is_labelled_and_needs_a_budget(self):
        missing = self.home / "none.json"
        self.assertEqual(usage.current(self.store, POLICY, missing)["source"], "unknown")   # no budget: throttle off
        usage.record_tokens(self.store, 4_000_000)
        got = usage.current(self.store, {**POLICY, "weekly_token_budget": 5_000_000}, missing)
        self.assertEqual((got["pct"], got["source"], got["estimate"]), (80.0, "estimate", True))
        self.assertIn("ESTIMATE", got["text"])

    def test_policy_validation(self):
        autopilot.validate({**autopilot.DEFAULTS, "usage_slow_pct": 70, "usage_stop_pct": 90, "weekly_token_budget": 1000})
        for bad in ({"usage_slow_pct": 96}, {"usage_stop_pct": 0}, {"usage_slow_pct": "80"}, {"weekly_token_budget": -1}):
            with self.assertRaises(ValueError):
                autopilot.validate({**autopilot.DEFAULTS, **bad})

    def test_health_shows_usage_field(self):
        got = observability.health(self.store)["usage"]
        self.assertEqual(got["source"] if got["pct"] is None else "x", got["source"])
        self.assertIn("text", got)
        self.assertIn("level", got)


if __name__ == "__main__":
    unittest.main()
