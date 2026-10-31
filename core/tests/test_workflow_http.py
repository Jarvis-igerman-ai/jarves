"""Real loopback HTTP/auth/SSE and owner-wait integration, no external services."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from jarves.workflow.server import Server


class WorkflowHTTPTests(unittest.TestCase):
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

    def request(self, path, data=None, headers=None):
        h = {"Cookie": self.cookie, "Origin": self.server.origin}
        if data is not None:
            h.update({"Content-Type": "application/json", "X-Workflow-Request": "1"})
        h.update(headers or {})
        req = urllib.request.Request(self.server.origin + path,
                                     data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.code, dict(response.headers), json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw

    def login(self):
        status, headers, data = self.request("/api/session", {"token": self.server.token})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def test_private_state_requires_private_session(self):
        self.assertEqual(self.request("/api/state")[0], 401)
        self.assertEqual(self.request("/api/session", {"token": "wrong"})[0], 401)
        self.login()
        status, _, data = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(data["agents"], [])
        self.assertEqual(data["autopilot"]["work"], [])
        self.assertFalse(data["autopilot"]["policy"]["enabled"])
        self.assertNotIn(self.server.token, json.dumps(data))

    def test_origin_host_and_request_marker_fail_closed(self):
        self.login()
        self.assertEqual(self.request("/api/owner", {"mode": "present"}, {"Origin": "https://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("/api/owner", {"mode": "present"}, {"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request("/api/owner", {"mode": "present"}, {"X-Workflow-Request": ""})[0], 403)
        self.assertEqual(self.server.store.snapshot()["owner"]["effective_mode"], "away")

    def test_static_csp_and_no_path_traversal(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'lang="he"', body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.login()
        self.assertEqual(self.request("/../access.token")[0], 404)
        self.assertEqual(self.request("/api/state")[1]["Cache-Control"], "no-store")

    def test_unanswered_work_is_saved_while_worker_claims_independent_task(self):
        store = self.server.store
        for ident in ("needs-owner", "independent"):
            store.create_task({"id": ident, "title": ident, "lane": "bugs"})
        store.register_agent({"id": "bugs", "lane": "bugs"})
        store.claim("bugs", "needs-owner")
        q = store.ask({"task_id": "needs-owner", "title": "Which behavior?", "checkpoint": "commit A"})
        self.assertEqual(store.claim("bugs")["id"], "independent")
        self.login()
        self.assertEqual(self.request(f"/api/questions/{q['id']}/defer", {})[0], 200)
        waiting = next(t for t in store.snapshot()["tasks"] if t["id"] == "needs-owner")
        self.assertEqual(waiting["state"], "waiting_owner")
        self.assertEqual(self.request(f"/api/questions/{q['id']}/answer", {"answer": "Keep existing behavior"})[0], 200)
        state = self.request("/api/state")[2]
        resumed = next(t for t in state["tasks"] if t["id"] == "needs-owner")
        self.assertEqual(resumed["state"], "queued")
        self.assertEqual(state["questions"][0]["answer"], "Keep existing behavior")
        self.assertEqual(resumed["validation"], "not_run")

    def test_dispatch_uses_existing_gated_dispatcher_not_fake_heartbeat(self):
        self.login()
        with patch("jarves.workflow.control.dispatch", return_value=[{"status": "submitted"}]) as called:
            status, _, result = self.request("/api/dispatch", {})
            self.assertEqual(status, 200)
            called.assert_called_once()
            self.assertEqual(result["result"]["assignments"][0]["status"], "submitted")
        self.assertEqual(self.server.store.snapshot()["supervisor"]["status"], "offline")

    def test_web_cannot_authorize_imported_scope_or_recover_unknown_writer(self):
        self.server.store.create_task({"id": "scope", "title": "Imported", "execution_allowed": False})
        self.login()
        self.assertEqual(self.request("/api/tasks/scope/transition", {"execution_allowed": True})[0], 400)
        self.assertEqual(self.request("/api/tasks/scope/transition", {"state": "done", "delivery": "beta"})[0], 400)
        self.assertFalse(self.server.store.snapshot()["tasks"][0]["execution_allowed"])

    def test_sse_initial_revision_and_event_type(self):
        self.login()
        req = urllib.request.Request(self.server.origin + "/api/events", headers={"Cookie": self.cookie})
        with urllib.request.urlopen(req, timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
            self.assertEqual(response.readline().strip(), b"event: change")
            self.assertTrue(response.readline().startswith(b"data: "))

    def test_action_history_and_retro_keep_existing_auth_and_csrf_boundaries(self):
        from jarves.workflow.trace import record
        self.assertEqual(self.request("/api/trace")[0], 401)
        self.login()
        actor = self.server.store.register_agent({"id": "worker"})
        record(self.server.store, actor, "PreToolUse", tool="Read", call_id="real-call")
        status, _, data = self.request("/api/trace?agent_id=worker")
        self.assertEqual(status, 200)
        self.assertEqual(data["events"][0]["call_id"], "real-call")
        self.assertEqual(self.request("/api/trace?since=nan")[0], 400)
        self.assertEqual(self.request("/api/trace/settings", {"mode": "details"}, {"X-Workflow-Request": ""})[0], 403)
        self.assertEqual(self.request("/api/trace/settings", {"mode": "details"})[0], 200)
        status, _, result = self.request("/api/trace/retro", {"agent_id": "worker"})
        self.assertEqual(status, 200)
        self.assertFalse(result["result"]["executed"])
        self.assertEqual(self.request("/api/trace/retro", {"output": "../../public.json"})[0], 400)


if __name__ == "__main__":
    unittest.main()
