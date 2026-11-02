"""Usage-aware throttle (#776) simulated with a fake usage source file. No paid API, no real session."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from jarves.workflow import autopilot, autopilot_loop as ap
from jarves.workflow.store import Store

POLICY = {"enabled": True, "kill": False, "project": "jarves", "versions": ["V4.5.1"], "authorized_versions": ["V4.5.1"],
          "max_concurrent": 3, "max_starts_per_hour": 50, "usage_slow_pct": 80, "usage_stop_pct": 95,
          "lanes": {"bugs": {"workers": 2, "model": "claude-sonnet-5-5"},
                    "features": {"workers": 2, "model": "claude-sonnet-5-5"}}}


class UsageThrottleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = Store(self.home / "workflow.db")
        ap._LAST_SIG.clear()
        self.started, self.notes = [], []
        for i in range(4):
            self.store.create_task({"id": f"b{i}", "title": f"b{i}", "lane": "bugs", "version": "V4.5.1",
                                    "acceptance": [{"id": "C1", "label": "x"}]})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def usage(self, pct, at=None):
        (self.home / "usage.json").write_text(json.dumps({"pct": pct, "at": at or time.time(), "source": "fake", "estimated": True}))

    def start(self, profile_home, **req):
        self.started.append(req["env"]["JARVES_WORKFLOW_AGENT"])
        return {"ok": True, "session": {"id": f"s{len(self.started)}"}}

    def step(self):
        return ap.step(self.store, self.home, POLICY, start=self.start, stop=lambda *a: {"ok": True},
                       notify=lambda h, k, t: self.notes.append((k, t)), find=lambda *a: "", force=True)

    def test_below_slow_runs_normally(self):
        self.usage(40)
        self.step()
        self.assertEqual(len(self.started), 2)             # two bug workers

    def test_unknown_usage_runs_normally(self):
        self.step()
        self.assertEqual(len(self.started), 2)

    def test_slow_starts_one_at_a_time(self):
        self.usage(85)
        self.step()
        self.assertEqual(len(self.started), 1)
        self.step()
        self.assertEqual(len(self.started), 1)             # one still running: nothing else starts

    def test_stop_starts_nothing_and_notifies_once(self):
        self.usage(96)
        self.step()
        self.step()
        self.assertEqual(self.started, [])
        self.assertEqual([k for k, _ in self.notes], ["usage_stop"])

    def test_resume_command_allows_slow_start_then_week_reset_clears(self):
        self.usage(96)
        self.step()
        autopilot.command(self.store, self.home, "resume", [])
        self.step()
        self.assertEqual(len(self.started), 1)
        self.usage(10)
        self.step()
        self.assertNotIn("usage_resume", ap._load(self.store))

    def test_week_reset_resumes_without_command(self):
        self.usage(96)
        self.step()
        self.usage(5)
        self.step()
        self.assertEqual(len(self.started), 2)

    def test_stale_usage_is_unknown(self):
        self.usage(99, at=time.time() - 7 * 3600)
        self.step()
        self.assertEqual(len(self.started), 2)

    def test_policy_validation_and_old_files(self):
        pol = {k: v for k, v in autopilot.DEFAULTS.items() if k not in autopilot.USAGE_KEYS}
        self.assertEqual(autopilot.validate(pol), pol)     # older policy file stays valid
        with self.assertRaises(ValueError):
            autopilot.validate({**autopilot.DEFAULTS, "usage_slow_pct": 96})

    def test_status_shows_usage(self):
        self.usage(83)
        v = autopilot.command(self.store, self.home, "status", [])["usage"]
        self.assertEqual((v["pct"], v["mode"]), (83.0, "slow"))


if __name__ == "__main__":
    unittest.main()
