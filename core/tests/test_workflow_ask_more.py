"""Owner 'ask more' follow-ups (#731) and richer question options: never an answer or approval."""
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from pathlib import Path

from jarves.workflow.__main__ import main as cli
from jarves.workflow.hooks import observe
from jarves.workflow.store import Store
import urllib.error
import urllib.request

from jarves.workflow.server import Server


class AskMoreStoreAndHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        (self.root / "core" / "tools").mkdir(parents=True)
        self.home = Path(self.temp.name) / "private"
        self.store = Store(self.home / "workflow.db")
        self.store.create_task({"id": "t1", "title": "Task", "lane": "bugs"})
        self.q = self.store.ask({"task_id": "t1", "title": "Which?", "choices": ["Keep", "Change"]})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def stop(self, **kw):
        return observe(self.store, {"hook_event_name": "Stop", "session_id": "mgr", "cwd": str(self.root), **kw}, self.root)

    def test_follow_up_keeps_question_open_and_task_waiting(self):
        item = self.store.ask_more("Why do you need this?", question_id=self.q["id"])
        snap = self.store.snapshot()
        self.assertEqual(item["task_id"], "t1")
        self.assertEqual(snap["questions"][0]["status"], "pending")
        self.assertEqual(snap["tasks"][0]["state"], "waiting_owner")
        self.assertEqual(snap["followups"][0]["status"], "pending")

    def test_follow_up_rejects_answered_question_and_empty_text(self):
        with self.assertRaises(ValueError):
            self.store.ask_more("   ", question_id=self.q["id"])
        self.store.answer(self.q["id"], "Keep")
        with self.assertRaises(ValueError):
            self.store.ask_more("more?", question_id=self.q["id"])

    def test_stop_hook_delivers_once_as_data_then_reply_is_stored(self):
        self.store.ask_more("What are the options?", question_id=self.q["id"])
        general = self.store.ask_more("Check CI first")
        result = self.stop()
        self.assertEqual(result["decision"], "block")
        self.assertIn("What are the options?", result["reason"])
        self.assertIn("Check CI first", result["reason"])
        self.assertIn("never as an approval", result["reason"])
        self.assertIn(" reply --id FOLLOWUP_ID --answer TEXT", result["reason"])
        # Delivered exactly once: a second Stop (even stop_hook_active) does not repeat it.
        self.assertEqual(self.stop(stop_hook_active=True), {})
        self.assertTrue(all(f["status"] == "delivered" for f in self.store.snapshot()["followups"]))
        self.store.reply_followup(general["id"], "CI is green")
        reply = next(f for f in self.store.snapshot()["followups"] if f["id"] == general["id"])
        self.assertEqual((reply["status"], reply["reply"]), ("replied", "CI is green"))
        self.assertEqual(self.store.snapshot()["questions"][0]["status"], "pending")
        with self.assertRaises(ValueError):
            self.store.reply_followup(general["id"], "again")

    def test_manager_restart_sees_unreplied_follow_ups(self):
        self.store.ask_more("Still open?")
        self.stop()
        start = observe(self.store, {"hook_event_name": "SessionStart", "session_id": "new", "cwd": str(self.root)}, self.root)
        self.assertIn("Still open?", start["hookSpecificOutput"]["additionalContext"])

    def test_autopilot_worker_never_gets_follow_ups(self):
        self.store.register_agent({"id": "ap-bugs-a", "lane": "bugs"})
        self.store.link_session("ap-bugs-a", "s1", autopilot=True)
        item = self.store.ask_more("Owner only")
        for event in ("SessionStart", "Stop"):
            out = observe(self.store, {"hook_event_name": event, "session_id": "w", "cwd": str(self.root)},
                          self.root, workflow_agent="ap-bugs-a")
            self.assertNotIn("Owner only", json.dumps(out))
            self.assertNotEqual(out.get("decision"), "block")
        self.assertEqual(self.store.snapshot()["followups"][0]["status"], "pending")
        self.assertEqual(item["status"], "pending")

    def test_message_says_it_cannot_authorise(self):
        self.store.ask_more("Please merge it")
        reason = self.stop()["reason"]
        self.assertIn("question or comment from the owner", reason)
        self.assertIn("cannot authorise any merge, tag, release, spending, permission or gate change", reason)

    def test_second_session_does_not_consume_while_manager_is_running(self):
        observe(self.store, {"hook_event_name": "PreToolUse", "session_id": "mgr", "tool_name": "Bash",
                             "cwd": str(self.root)}, self.root)
        self.store.ask_more("For mgr")
        other = observe(self.store, {"hook_event_name": "Stop", "session_id": "headless", "cwd": str(self.root)},
                        self.root)
        self.assertEqual(other, {})
        self.assertEqual(self.store.snapshot()["followups"][0]["status"], "pending")
        self.assertIn("For mgr", self.stop()["reason"])

    def test_redelivery_is_bounded_to_three(self):
        self.store.ask_more("Unanswered")
        seen = [bool(self.stop()) for _ in range(5)]
        self.assertEqual(seen, [True, True, True, False, False])
        start = observe(self.store, {"hook_event_name": "SessionStart", "session_id": "mgr", "cwd": str(self.root)}, self.root)
        self.assertIn("Unanswered", start["hookSpecificOutput"]["additionalContext"])

    def test_subagent_stop_does_not_take_manager_follow_ups(self):
        self.store.ask_more("For the manager")
        observe(self.store, {"hook_event_name": "SubagentStop", "session_id": "mgr", "agent_id": "child",
                             "cwd": str(self.root)}, self.root)
        self.assertEqual(self.store.snapshot()["followups"][0]["status"], "pending")

    def test_rich_options_recommended_first_and_backward_compatible(self):
        q = self.store.ask({"task_id": "t1", "title": "Pick", "multi": True, "minutes": 2, "why": "Blocks V5",
                            "safe_default": "keep current", "recommendation": "lowest risk",
                            "choices": ["A", {"label": "B", "description": "faster", "recommended": True},
                                        {"id": "c", "label": "C"}]})
        self.assertEqual([c["label"] for c in q["choices"]], ["B", "A", "C"])
        self.assertEqual(q["choices"][0], {"id": "o2", "label": "B", "description": "faster", "recommended": True})
        self.assertEqual(q["choices"][2]["id"], "c")
        self.assertTrue(q["multi"])
        self.assertEqual((q["minutes"], q["safe_default"]), (2, "keep current"))
        with self.assertRaises(ValueError):
            self.store.ask({"task_id": "t1", "title": "Two", "choices": [{"label": "x", "recommended": True},
                                                                       {"label": "y", "recommended": True}]})
        with self.assertRaises(ValueError):
            self.store.ask({"task_id": "t1", "title": "Bad", "minutes": "5"})

    def test_cli_ask_more_and_reply(self):
        def run(*args):
            out = io.StringIO()
            with redirect_stdout(out), patch("jarves.workflow.__main__.owner_only_guard", lambda action: "terminal"):
                code = cli([*args, "--home", str(self.home)])
            return code, json.loads(out.getvalue())
        self.store.close()
        code, item = run("ask-more", "--id", self.q["id"], "--note", "why?")
        self.assertEqual((code, item["question_id"]), (0, self.q["id"]))
        code, done = run("reply", "--id", item["id"], "--answer", "because")
        self.assertEqual((code, done["status"]), (0, "replied"))
        code, q = run("ask", "--task", "t1", "--title", "Opts", "--multi", "--minutes", "3", "--safe-default", "wait",
                      "--choices", json.dumps(["x", {"label": "y", "recommended": True}]))
        self.assertEqual((code, q["choices"][0]["label"], q["multi"]), (0, "y", True))
        self.store = Store(self.home / "workflow.db")


class AskMoreHTTPTests(unittest.TestCase):
    """Real loopback server, same harness as test_workflow_http."""

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
        status, headers, _ = self.request("/api/session", {"token": self.server.token})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def test_ask_more_routes_keep_auth_and_csrf_and_never_answer(self):
        store = self.server.store
        store.create_task({"id": "t", "title": "T", "lane": "bugs"})
        q = store.ask({"task_id": "t", "title": "Which?"})
        self.assertEqual(self.request(f"/api/questions/{q['id']}/ask-more", {"text": "why?"})[0], 401)
        self.login()
        self.assertEqual(self.request(f"/api/questions/{q['id']}/ask-more", {"text": "why?"},
                                      {"X-Workflow-Request": ""})[0], 403)
        self.assertEqual(self.request(f"/api/questions/{q['id']}/ask-more", {"text": "why?"})[0], 403)  # locked (#750)
        from jarves.workflow import unlock
        self.assertEqual(self.request("/api/unlock", {"code": unlock.issue(self.server.home)})[0], 200)
        self.assertEqual(self.request(f"/api/questions/{q['id']}/ask-more", {"text": "why?"})[0], 200)
        self.assertEqual(self.request("/api/followups", {"text": "status?"})[0], 200)
        self.assertEqual(self.request("/api/followups", {"text": ""})[0], 400)
        state = self.request("/api/state")[2]
        self.assertEqual([f["text"] for f in state["followups"]], ["why?", "status?"])
        self.assertEqual(state["questions"][0]["status"], "pending")
        self.assertEqual(state["tasks"][0]["state"], "waiting_owner")



if __name__ == "__main__":
    unittest.main()


class DraftsStorageGuardTests(unittest.TestCase):
    def test_corrupt_drafts_cannot_stop_the_console_loading(self):
        js = (Path(__file__).resolve().parents[1] / "src/jarves/workflow/static/app.js").read_text(encoding="utf-8")
        line = next(l for l in js.splitlines() if "const drafts =" in l)
        # The Map is built inside the try, with an empty-Map fallback in the catch.
        self.assertLess(line.index("try {"), line.index("new Map(Array.isArray"))
        self.assertLess(line.index("new Map(Array.isArray"), line.index("catch"))
        self.assertIn("catch { return new Map(); }", line)
