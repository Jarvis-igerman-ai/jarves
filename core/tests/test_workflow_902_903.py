"""#902 held typed answer hidden from agent views and blocked outside the project; #903 mutating routes need the unlock."""
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from jarves.workflow import unlock
from jarves.workflow.__main__ import main as cli
from jarves.workflow.hooks import observe
from jarves.workflow.server import Server
from jarves.workflow.store import HELD_TEXT, Store, hide_held_answers


class HeldAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.outside = Path(self.temp.name) / "elsewhere"
        self.outside.mkdir()
        self.home = Path(self.temp.name) / "private"
        self.store = Store(self.home / "workflow.db")
        self.store.create_task({"id": "t1", "title": "T", "lane": "bugs"})
        self.q = self.store.ask({"task_id": "t1", "title": "Which?"})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_held_answer_hidden_in_agent_view_but_kept_in_store(self):
        self.store.answer(self.q["id"], "SECRET-ANSWER", answered_by="unverified", channel="claude-window")
        view = hide_held_answers(self.store.snapshot())
        self.assertNotIn("SECRET-ANSWER", json.dumps(view))
        self.assertEqual(view["questions"][0]["answer"], HELD_TEXT)
        self.assertIn("SECRET-ANSWER", json.dumps(self.store.snapshot()))  # the dashboard still shows it

    def test_cli_status_hides_held_answer(self):
        self.store.answer(self.q["id"], "SECRET-ANSWER", answered_by="unverified", channel="claude-window")
        self.store.close()
        out = io.StringIO()
        with redirect_stdout(out):
            cli(["--home", str(self.home), "status"])
        self.assertNotIn("SECRET-ANSWER", out.getvalue())
        self.store = Store(self.home / "workflow.db")

    def test_confirmed_answer_is_visible(self):
        self.store.answer(self.q["id"], "OPEN-ANSWER", answered_by="owner", channel="dashboard")
        self.assertIn("OPEN-ANSWER", json.dumps(hide_held_answers(self.store.snapshot())))

    def test_answer_prompt_outside_project_is_blocked_not_stored(self):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": str(self.outside),
                   "prompt": f"ANSWER {self.q['id']} SECRET-ANSWER"}
        result = observe(self.store, payload, self.root)
        self.assertEqual(result["decision"], "block")
        self.assertNotIn("SECRET-ANSWER", json.dumps(result))
        self.assertEqual(self.store.snapshot()["questions"][0]["status"], "pending")

    def test_ordinary_prompt_outside_project_ignored(self):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": str(self.outside), "prompt": "hello"}
        self.assertEqual(observe(self.store, payload, self.root), {})


class GatedRoutesTests(unittest.TestCase):
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

    def request(self, path, data):
        req = urllib.request.Request(self.server.origin + path, data=json.dumps(data).encode(), headers={
            "Cookie": self.cookie, "Origin": self.server.origin, "Content-Type": "application/json", "X-Workflow-Request": "1"})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, response.headers, json.loads(response.read())

    def test_locked_session_gets_403_on_each_mutating_route(self):
        self.server.store.create_task({"id": "t", "title": "T", "lane": "bugs"})
        status, headers, _ = self.request("/api/session", {"token": self.server.token})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        routes = {"/api/owner": {"mode": "present"}, "/api/tasks": {"id": "n", "title": "N", "lane": "bugs"},
                  "/api/tasks/t/transition": {"state": "done"}, "/api/dispatch": {}}
        for path, body in routes.items():
            self.assertEqual(self.request(path, body)[0], 403, path)
        tasks = {t["id"]: t for t in self.server.store.snapshot()["tasks"]}
        self.assertNotIn("n", tasks)
        self.assertNotEqual(tasks["t"]["state"], "done")
        self.assertEqual(self.request("/api/unlock", {"code": unlock.issue(self.server.home)})[0], 200)
        self.assertEqual(self.request("/api/owner", {"mode": "present"})[0], 200)
        self.assertEqual(self.request("/api/tasks", {"id": "n", "title": "N", "lane": "bugs"})[0], 200)


if __name__ == "__main__":
    unittest.main()
