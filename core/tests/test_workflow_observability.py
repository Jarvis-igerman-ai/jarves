"""Factory observability: layer beats, gaps, incidents, timeline shape, endpoints. Fake clock, no services."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarves.workflow import autopilot, observability as obs
from jarves.workflow.server import Server
from jarves.workflow.store import Store

T0 = 1_800_000_000.0


class ObservabilityTests(unittest.TestCase):
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

    def row(self, layer):
        return next(r for r in self.store.layers() if r["layer"] == layer)

    def test_layer_staleness_thresholds(self):
        self.assertEqual(self.row("wrapper")["status"], "dead")
        self.assertEqual(self.row("wrapper")["state"], "never")
        self.store.beat("wrapper", "ok", "hello")
        self.assertEqual(self.row("wrapper")["status"], "ok")
        self.advance(181)
        self.assertEqual(self.row("wrapper")["status"], "stale")
        self.advance(200)
        self.assertEqual(self.row("wrapper")["status"], "dead")
        self.assertEqual(self.row("wrapper")["note"], "hello")
        self.store.beat("autopilot", "ok")
        self.advance(901)
        self.assertEqual(self.row("autopilot")["status"], "stale")
        self.store.beat("supervisor", "stopped", "clean stop")
        self.assertEqual(self.row("supervisor")["status"], "dead")
        with self.assertRaises(ValueError):
            self.store.beat("nope")

    def test_manager_layer_follows_hook_activity(self):
        self.store.register_agent({"id": "mgr", "label": "Manager", "lane": "any", "provider": "claude-native"})
        self.assertEqual(self.row("manager")["status"], "ok")
        self.advance(1801)
        self.assertEqual(self.row("manager")["status"], "stale")

    def test_beats_are_throttled_but_state_change_is_recorded(self):
        for _ in range(5):
            self.store.beat("core", "ok")
            self.advance(10)
        self.store.beat("core", "down", "NO_CORE")
        kinds = [r[0] for r in self.store._db.execute("SELECT summary FROM workflow_events WHERE kind='layer_beat'")]
        self.assertEqual(len(kinds), 2)
        self.assertNotIn("layer_beat", [e["kind"] for e in self.store.snapshot()["events"]])

    def test_gap_detection_with_ready_work(self):
        self.store.create_task({"id": "t1", "title": "Ready work", "lane": "bugs"})
        for _ in range(4):
            self.store.beat("supervisor", "ok")
            self.advance(60)
        self.advance(3 * 3600)               # supervisor silent for 3 h while t1 waits
        self.store.beat("supervisor", "ok")
        self.advance(30)
        tl = self.store.timeline(since=T0 - 10)
        gaps = [g for g in tl["gaps"] if g["layer"] == "supervisor"]
        self.assertEqual(len(gaps), 1)
        self.assertGreater(gaps[0]["seconds"], 3 * 3600)
        self.assertEqual(gaps[0]["ready_work"], 1)
        segs = [s for s in tl["segments"] if s["layer"] == "supervisor"]
        self.assertEqual(len(segs), 2)
        self.assertEqual({"layer", "state", "start", "end"}, set(segs[0]))

    def test_open_gap_when_layer_is_silent_now(self):
        self.store.beat("core", "ok")
        self.advance(3600)
        tl = self.store.timeline(since=T0 - 10)
        self.assertEqual([g["layer"] for g in tl["gaps"]], ["core"])
        self.assertAlmostEqual(tl["gaps"][0]["end"], self.now[0])

    def test_timeline_shape_marks_and_pipeline(self):
        self.store.create_task({"id": "t1", "title": "Ship it", "lane": "bugs"})
        self.store.register_agent({"id": "w1", "label": "W1", "lane": "bugs"})
        self.store.heartbeat("w1")
        self.store.claim("w1")
        self.store.transition("t1", {"state": "review"})
        tl = self.store.timeline(since=T0 - 10)
        self.assertEqual(set(tl), {"since", "until", "segments", "marks", "gaps", "pipeline"})
        self.assertEqual([(p["from_state"], p["to_state"]) for p in tl["pipeline"]], [("queued", "running"), ("running", "review")])
        self.assertEqual(set(tl["pipeline"][0]), {"ts", "task_id", "title", "lane", "from_state", "to_state", "agent"})
        self.assertEqual(set(tl["marks"][0]), {"ts", "kind", "layer", "summary"})
        self.assertIn("task_transition", {m["kind"] for m in tl["marks"]})
        text = obs.timeline_text(tl, self.store.layers())
        self.assertIn("Gaps", text)
        self.assertIn("t1", text)
        self.assertNotIn("task_flow", [e["kind"] for e in self.store.snapshot()["events"]])

    def enable_autopilot(self):
        policy, _ = autopilot.load(self.home)
        autopilot.save(self.home, {**policy, "enabled": True})

    def test_ops_lane_default_is_off_and_old_policy_files_load(self):
        policy, error = autopilot.load(self.home)
        self.assertEqual((error, policy["lanes"]["ops"]["workers"]), ("", 0))
        old = {**policy, "lanes": {k: v for k, v in policy["lanes"].items() if k != "ops"}}
        (self.home / autopilot.POLICY_NAME).write_text(json.dumps(old), encoding="utf-8")
        loaded, error = autopilot.load(self.home)
        self.assertEqual((error, loaded["lanes"]["ops"]["workers"]), ("", 0))

    def test_incident_dedupe_and_cooldown(self):
        self.enable_autopilot()
        self.store.create_task({"id": "t1", "title": "Waiting", "lane": "bugs"})
        self.advance(25 * 60)
        found = self.store.detect_incidents()
        self.assertEqual([i["key"] for i in found], ["factory_idle_with_ready_work"])
        self.assertEqual(len(obs.process_incidents(self.store)), 1)
        self.advance(60)
        self.assertEqual(obs.process_incidents(self.store), [])          # same key inside the cooldown
        tasks = [t for t in self.store.snapshot()["tasks"] if t["lane"] == "ops"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "Incident triage: factory_idle_with_ready_work")
        self.assertIn("showstopper", json.dumps(tasks[0]["acceptance"]))
        self.assertIn("Timeline", tasks[0]["scope_note"])
        self.assertTrue(tasks[0]["execution_allowed"])
        self.assertIn("incident", {a["kind"] for a in self.store.snapshot()["alerts"]} | {
            a["kind"] for a in __import__("jarves.workflow.autopilot_watch", fromlist=["x"]).autopilot_alerts(self.store)})
        self.assertEqual(obs.health(self.store)["incidents"][0]["task_id"], tasks[0]["id"])
        self.advance(4 * 3600)
        self.assertEqual(len(obs.process_incidents(self.store)), 1)       # cooldown over: a new task
        self.assertEqual(len([t for t in self.store.snapshot()["tasks"] if t["lane"] == "ops"]), 2)

    def test_no_incident_when_autopilot_off_or_no_work(self):
        self.advance(3600)
        self.assertEqual(self.store.detect_incidents(), [])
        self.store.create_task({"id": "t1", "title": "Waiting", "lane": "bugs"})
        self.assertEqual(self.store.detect_incidents(), [])               # autopilot disabled

    def test_supervisor_and_wrapper_incidents_and_error_burst(self):
        self.store.beat("supervisor", "ok")
        self.store.beat("wrapper", "ok")
        self.advance(400)
        keys = {i["key"] for i in self.store.detect_incidents()}
        self.assertEqual(keys, {"supervisor_dead", "wrapper_dead"})
        self.store.beat("wrapper", "stopped", "owner closed it")
        self.assertNotIn("wrapper_dead", {i["key"] for i in self.store.detect_incidents()})
        for _ in range(5):
            self.store.record_event("autopilot_error", "boom")
        self.assertIn("repeated_autopilot_error", {i["key"] for i in self.store.detect_incidents()})

    def test_supervisor_tick_beats_and_watches_wrapper(self):
        probe = lambda _home: (True, False, "bridge down")   # noqa: E731
        self.store.beat("wrapper", "ok")
        obs.supervisor_tick(self.store, self.home, self.home, probe=probe)
        self.assertEqual(self.row("supervisor")["status"], "ok")
        self.assertEqual((self.row("core")["state"], self.row("bridge")["state"]), ("ok", "down"))
        self.assertEqual(self.row("autopilot")["state"], "off")
        self.advance(301)
        obs.supervisor_tick(self.store, self.home, self.home, probe=probe)
        from jarves.workflow.autopilot_watch import autopilot_alerts
        self.assertIn("watcher_dead:wrapper", {a["kind"] for a in autopilot_alerts(self.store)})

    def test_idle_decision_is_deduplicated(self):
        policy, _ = autopilot.load(self.home)
        self.assertEqual(obs.record_decision(self.store, policy, []), "POLICY_OFF")
        for _ in range(5):
            self.advance(10)
            obs.record_decision(self.store, policy, [])
        self.assertEqual(self.idle_events(), 1)
        self.advance(1800)
        obs.record_decision(self.store, policy, [])
        self.assertEqual(self.idle_events(), 2)                            # logged again after 30 min
        policy = {**policy, "enabled": True}
        self.assertEqual(obs.record_decision(self.store, policy, []), "NO_READY_TASK")
        self.assertEqual(self.idle_events(), 3)                            # reason changed
        self.assertEqual(obs.record_decision(self.store, policy, [{"action": "started"}]), "")

    def idle_events(self):
        return self.store._db.execute("SELECT COUNT(*) FROM workflow_events WHERE kind='autopilot_idle'").fetchone()[0]

    def test_supervisor_log_rotates_and_start_records_previous_crash(self):
        obs.log_line(self.home, "first")
        self.assertRegex((self.home / "supervisor.log").read_text(encoding="utf-8"), r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d first")
        (self.home / "supervisor.log").write_bytes(b"x" * obs.LOG_MAX)
        obs.log_line(self.home, "after rotation")
        self.assertTrue((self.home / "supervisor.log.1").exists())
        self.store.set_supervisor({"heartbeat_at": T0, "status": "running"})
        self.advance(900)
        obs.on_start(self.store, self.home)
        self.assertIn("without a clean stop", (self.home / "supervisor.log").read_text(encoding="utf-8"))
        self.assertEqual([t["title"] for t in self.store.snapshot()["tasks"] if t["lane"] == "ops"][0][:16], "Incident triage:")


class ObservabilityHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(Path(self.temp.name), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def get(self, path):
        req = urllib.request.Request(self.server.origin + path, headers={"Cookie": self.cookie, "Origin": self.server.origin})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, json.loads(response.read())

    def login(self):
        req = urllib.request.Request(self.server.origin + "/api/session", data=json.dumps({"token": self.server.token}).encode(),
                                     headers={"Origin": self.server.origin, "Content-Type": "application/json", "X-Workflow-Request": "1"})
        with urllib.request.urlopen(req, timeout=5) as response:
            self.cookie = response.headers["Set-Cookie"].split(";", 1)[0]

    def test_endpoints_require_the_session_and_return_the_contract(self):
        self.assertEqual(self.get("/api/health")[0], 401)
        self.assertEqual(self.get("/api/timeline?hours=24")[0], 401)
        self.login()
        status, health = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(set(health), {"layers", "incidents", "now", "verdict", "usage", "focus"})
        self.assertEqual([r["layer"] for r in health["layers"]], list(obs.LAYERS))
        self.assertEqual(set(health["layers"][0]), {"layer", "status", "state", "last_beat_at", "age_s", "note"})
        status, tl = self.get("/api/timeline?hours=24")
        self.assertEqual(status, 200)
        self.assertEqual(set(tl), {"since", "until", "segments", "marks", "gaps", "pipeline"})
        self.assertEqual(self.get("/api/timeline?hours=abc")[0], 400)
        self.assertEqual(self.get("/api/timeline?hours=99999")[0], 400)


if __name__ == "__main__":
    unittest.main()
