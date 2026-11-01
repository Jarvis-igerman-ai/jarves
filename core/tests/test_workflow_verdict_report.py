"""#758 one factory verdict and #761 daily report. Fake clock, fake gh, no network, no processes."""
import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jarves.workflow import autopilot_loop as loop, observability as obs, report
from jarves.workflow.store import Store

IL = ZoneInfo("Asia/Jerusalem")
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=IL).timestamp()      # 09:00 Israel (UTC+2 in November)
STATIC = Path(__file__).resolve().parents[1] / "src" / "jarves" / "workflow" / "static"


def fake_gh(since):
    return True, [{"number": 7, "title": "Fix the thing", "merged_at": since + 60}]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.now = [T0]
        self.store = Store(self.home / "workflow.db", clock=lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def advance(self, seconds):
        self.now[0] += seconds

    def task(self, ident="t1"):
        self.store.create_task({"id": ident, "title": f"Task {ident}", "lane": "bugs"})

    def alive(self):
        for layer in ("supervisor", "autopilot", "core", "bridge"):
            self.store.beat(layer, "ok")


class VerdictTests(Base):
    def kind(self):
        v = obs.verdict(self.store)
        self.assertEqual(set(v), {"verdict", "reason", "since", "since_min", "detail"})
        self.assertIn(v["verdict"], obs.VERDICTS)
        return v["verdict"], v

    def test_unknown_without_any_heartbeat(self):
        self.assertEqual(self.kind()[0], "unknown")

    def test_supervisor_dead_and_late(self):
        self.alive()
        self.advance(250)
        kind, v = self.kind()
        self.assertEqual((kind, v["detail"]["code"]), ("unknown", "supervisor_late"))
        self.advance(200)
        kind, v = self.kind()
        self.assertEqual((kind, v["reason"], v["since_min"]), ("stopped", "supervisor dead", 7))

    def test_no_core(self):
        self.alive()
        obs._save(self.store, obs.DECISION_KEY, {"reason": "NO_CORE", "since": T0 - 600, "logged_at": T0})
        kind, v = self.kind()
        self.assertEqual(kind, "stopped")
        self.assertIn("NO_CORE", v["reason"])
        self.assertEqual(v["since_min"], 10)

    def test_usage_stop(self):
        self.alive()
        loop._save(self.store, {"paused_until": T0 + 600, "pause_reason": "USAGE_LIMIT"})
        kind, v = self.kind()
        self.assertEqual(kind, "stopped")
        self.assertEqual(v["reason"], "usage stop: USAGE_LIMIT")

    def test_budget_reached_is_the_worker_limit_not_usage(self):
        self.alive()
        self.task()
        loop._save(self.store, {"paused_until": T0 + 600, "pause_reason": "BUDGET_REACHED"})
        kind, v = self.kind()
        self.assertEqual((kind, v["detail"]["code"]), ("idle_with_work", "worker_limit"))
        self.assertEqual(v["reason"], "worker limit reached: 0 of 3 running")
        self.assertNotIn("usage", v["reason"])
        self.store.register_agent({"id": "w1", "label": "W1", "lane": "bugs"})
        self.store.heartbeat("w1")
        self.store.claim("w1")
        kind, v = self.kind()
        self.assertEqual((kind, v["detail"]["code"]), ("working", "worker_limit"))
        self.assertEqual(v["reason"], "worker limit reached: 1 of 3 running")

    def test_stale_no_core_is_ignored_after_a_worker_started(self):
        self.alive()
        self.task()
        loop._save(self.store, {"paused_until": T0 + 600, "pause_reason": "NO_CORE"})
        obs._save(self.store, obs.DECISION_KEY, {"reason": "NO_CORE", "since": T0 - 60, "logged_at": T0})
        self.assertEqual(self.kind()[1]["detail"]["code"], "no_core")
        self.advance(30)
        self.store.register_agent({"id": "w1", "label": "W1", "lane": "bugs"})
        self.store.link_session("w1", "s1", provider="claude", autopilot=True)
        self.store.heartbeat("w1")
        self.store.claim("w1")
        self.assertEqual(self.kind()[0], "working")

    def test_usage_file_stops_the_factory(self):
        self.alive()
        self.task()
        (self.home / "usage.json").write_text(json.dumps({"ts": T0, "weekly_pct": 100.0}), encoding="utf-8")
        kind, v = self.kind()
        self.assertEqual((kind, v["reason"]), ("stopped", "usage stop: 100% used"))

    def test_idle_with_ready_work(self):
        self.alive()
        self.task()
        kind, v = self.kind()
        self.assertEqual((kind, v["reason"]), ("idle_with_work", "idle with 1 ready task(s)"))

    def test_owner_wait_only(self):
        self.alive()
        self.task()
        self.store.ask({"task_id": "t1", "title": "Pick one"})
        self.advance(120)
        kind, v = self.kind()
        self.assertEqual((kind, v["detail"]["questions"], v["since_min"]), ("owner_wait", 1, 2))

    def test_working_with_active_worker(self):
        self.alive()
        self.task()
        self.store.register_agent({"id": "w1", "label": "W1", "lane": "bugs"})
        self.store.heartbeat("w1")
        self.store.claim("w1")
        self.assertEqual(self.kind()[0], "working")
        self.advance(1000)                      # worker silent past the freshness window
        self.alive()
        self.assertNotEqual(self.kind()[0], "working")

    def test_nothing_queued(self):
        self.alive()
        kind, v = self.kind()
        self.assertEqual((kind, v["reason"]), ("stopped", "nothing is queued"))

    def test_health_status_and_http_carry_the_same_verdict(self):
        self.alive()
        self.task()
        self.assertEqual(obs.health(self.store)["verdict"], obs.verdict(self.store))

    def test_overview_reads_the_remote_verdict_with_fallback(self):
        js = (STATIC / "overview.js").read_text(encoding="utf-8")
        self.assertIn('fetch("/api/health"', js)
        self.assertIn("const rv = remoteVerdict();", js)
        self.assertIn("rv ? rv.text : verdictText(f)", js)
        for code in obs.VERDICT_TEXT:
            self.assertRegex(js, rf"\b{code}:", code)
        self.assertIn('fetch("/api/report"', js)
        self.assertIn('id="ov-report-text"', (STATIC / "index.html").read_text(encoding="utf-8"))


class ReportTests(Base):
    def test_report_sections_from_fixture_events(self):
        self.alive()
        self.store.create_task({"id": "t1", "title": "Ship it", "lane": "bugs"})
        self.store.create_task({"id": "t2", "title": "Next up", "lane": "features", "priority": 1})
        self.store.register_agent({"id": "w1", "label": "W1", "lane": "bugs"})
        self.store.heartbeat("w1")
        self.store.claim("w1", "t1")
        self.store.transition("t1", {"state": "review"})
        self.store.ask({"task_id": "t2", "title": "Need a decision", "minutes": 3})
        self.store.record_event("incident_opened", "supervisor_dead: silent")
        self.advance(1800)
        rep = report.build(self.store, 24, gh=fake_gh)
        self.assertEqual(rep["shipped"]["merged_prs"][0]["number"], 7)
        self.assertEqual(rep["workers"], {"started": 1, "finished": 1, "blocked": 0})
        self.assertEqual(rep["questions"]["count"], 1)
        self.assertEqual(rep["questions"]["oldest_wait_min"], 30)
        self.assertEqual(rep["questions"]["cards"][0]["title"], "Need a decision")
        self.assertEqual(len(rep["incidents"]), 1)
        self.assertIn("window", rep)
        self.assertEqual(rep["tz"], "Asia/Jerusalem")
        he = rep["he"]
        for word in ("דוח בוקר", "מה שוחרר", "מה נתקע", "שאלות שמחכות לך: 1", "PR 7", "מה המפעל יעשה הלאה"):
            self.assertIn(word, he)
        self.assertRegex(he, r"02\.11 09:30")                    # Israel local time, not UTC
        json.dumps(rep)

    def test_stalled_gap_has_a_reason_and_gh_failure_is_tolerated(self):
        self.store.beat("supervisor", "ok")
        self.task()
        self.advance(3 * 3600)
        self.store.beat("supervisor", "ok")
        rep = report.build(self.store, 24, gh=lambda since: (False, []))
        self.assertTrue(rep["stalled"])
        self.assertEqual(rep["stalled"][0]["layer"], "supervisor")
        self.assertIn("נתוני GitHub לא זמינים", rep["he"])

    def test_gh_runner_failures_return_empty(self):
        def boom(*a, **k):
            raise FileNotFoundError("gh")
        self.assertEqual(report.gh_merged(T0, run=boom), (False, []))

        class Out:
            returncode, stdout = 0, json.dumps([{"number": 3, "title": "t", "mergedAt": "2026-11-02T06:00:00Z"}])
        ok, rows = report.gh_merged(T0 - 86400, run=lambda *a, **k: Out())
        self.assertTrue(ok)
        self.assertEqual(rows[0]["number"], 3)

    def test_daily_schedule_at_0800_israel_is_idempotent(self):
        calls = []

        def gh(since):
            calls.append(since)
            return True, []
        before = datetime(2026, 11, 2, 7, 59, tzinfo=IL).timestamp()
        self.assertEqual(report.daily_tick(self.store, self.home, before, gh), "")
        at = datetime(2026, 11, 2, 8, 0, tzinfo=IL).timestamp()
        path = report.daily_tick(self.store, self.home, at, gh)
        self.assertTrue(path.endswith("2026-11-02.md"))
        self.assertTrue(Path(path).read_text(encoding="utf-8").startswith("דוח בוקר"))
        self.assertEqual(report.daily_tick(self.store, self.home, at + 3600, gh), "")      # same day: nothing again
        Path(path).unlink()                                                                  # even without the file
        self.assertEqual(report.daily_tick(self.store, self.home, at + 7200, gh), "")      # the ledger event decides
        self.assertEqual(len(calls), 1)
        events = [e for e in self.store.snapshot()["events"] if e["kind"] == "daily_report"]
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["summary"].startswith("2026-11-02 "))
        msgs = [r for r in self.store._db.execute("SELECT summary FROM workflow_events WHERE kind='agent_message'")]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(json.loads(msgs[0][0])["to"], "manager")
        self.assertLessEqual(len(json.loads(msgs[0][0])["body"].splitlines()), 5)
        nxt = datetime(2026, 11, 3, 8, 5, tzinfo=IL).timestamp()
        self.assertTrue(report.daily_tick(self.store, self.home, nxt, gh).endswith("2026-11-03.md"))

    def test_schedule_follows_summer_time(self):
        summer = datetime(2026, 7, 1, 8, 0, tzinfo=IL).timestamp()          # UTC+3 in July
        self.assertEqual(report.due_date(summer), "2026-07-01")
        self.assertIsNone(report.due_date(summer - 60))
        self.assertIsNone(re.match(r"x", "y"))


if __name__ == "__main__":
    unittest.main()
