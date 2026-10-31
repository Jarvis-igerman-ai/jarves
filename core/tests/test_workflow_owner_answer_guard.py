"""Owner questions are answered only by the owner: dashboard session or an interactive terminal."""
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from jarves.workflow import __main__ as cli
from jarves.workflow.server import Server
from jarves.workflow.store import Store


def run_cli(home, *args, tty, env=None):
    out, err = io.StringIO(), io.StringIO()
    out.isatty = err.isatty = lambda: tty
    stdin = io.StringIO()
    stdin.isatty = lambda: tty
    with patch.object(cli.sys, "stdin", stdin), patch.object(cli.sys, "stdout", out), patch.object(cli.sys, "stderr", err), \
         patch.object(cli, "utf8_streams", lambda: None), patch.dict(os.environ, env or {}):
        os.environ.pop("JARVES_WORKFLOW_AGENT", None) if not env else None
        code = cli.main(["--home", str(home), "--profile-home", str(home), *args])
    return code, out.getvalue(), err.getvalue()


class OwnerAnswerGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        store = Store(self.home / "workflow.db")
        store.create_task({"id": "t1", "title": "t1", "lane": "bugs"})
        self.q = store.ask({"task_id": "t1", "title": "Which way?"})["id"]
        store.close()

    def tearDown(self):
        self.temp.cleanup()

    def question(self):
        store = Store(self.home / "workflow.db")
        try:
            return next(q for q in store.snapshot()["questions"] if q["id"] == self.q)
        finally:
            store.close()

    def test_non_tty_refused_and_question_stays_open(self):
        code, _, err = run_cli(self.home, "answer", "--id", self.q, "--answer", "ok", tty=False)
        self.assertEqual(code, 1)
        self.assertIn("owner questions are answered in the dashboard or in your own terminal", err)
        self.assertEqual(self.question()["status"], "pending")

    def test_agent_env_refused_even_with_tty(self):
        code, _, err = run_cli(self.home, "answer", "--id", self.q, "--answer", "ok", tty=True,
                               env={"JARVES_WORKFLOW_AGENT": "agent-1"})
        self.assertEqual(code, 1)
        self.assertIn("agent environment", err)
        self.assertEqual(self.question()["status"], "pending")

    def test_tty_terminal_answer_is_recorded_as_terminal(self):
        code, _, _ = run_cli(self.home, "answer", "--id", self.q, "--answer", "A", tty=True)
        self.assertEqual(code, 0)
        q = self.question()
        self.assertEqual((q["status"], q["channel"], q["answered_by"]), ("answered", "terminal", "owner (terminal)"))
        self.assertFalse(q["needs_owner_confirmation"])

    def test_store_default_is_unknown_and_flagged_and_reopen_keeps_history(self):
        store = Store(self.home / "workflow.db")
        store.answer(self.q, "ok")
        q = next(x for x in store.snapshot()["questions"] if x["id"] == self.q)
        self.assertEqual((q["channel"], q["answered_by"], q["needs_owner_confirmation"]), ("unknown", "unknown", True))
        store.close()
        code, _, err = run_cli(self.home, "reopen", "--id", self.q, tty=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.question()["status"], "answered")
        code, _, _ = run_cli(self.home, "reopen", "--id", self.q, tty=True)
        self.assertEqual(code, 0)
        q = self.question()
        self.assertEqual(q["status"], "pending")
        self.assertIsNone(q["answer"])
        self.assertEqual(q["history"][0]["answer"], "ok")
        self.assertEqual(q["history"][0]["channel"], "unknown")
        store = Store(self.home / "workflow.db")
        self.assertEqual(next(t for t in store.snapshot()["tasks"] if t["id"] == "t1")["state"], "waiting_owner")
        store.close()

    def test_old_row_without_provenance_reads_as_unknown(self):
        db = sqlite3.connect(self.home / "workflow.db")
        row = db.execute("SELECT data FROM workflow_records WHERE kind='question' AND id=?", (self.q,)).fetchone()
        data = json.loads(row[0])
        data.update(status="answered", answer="ok", answered_at=1.0)
        db.execute("UPDATE workflow_records SET data=? WHERE kind='question' AND id=?", (json.dumps(data), self.q))
        db.commit()
        db.close()
        q = self.question()
        self.assertEqual((q["channel"], q["answered_by"], q["needs_owner_confirmation"]), ("unknown", "unknown", True))

    def test_invalid_channel_is_stored_as_unknown(self):
        store = Store(self.home / "workflow.db")
        self.assertEqual(store.answer(self.q, "x", channel="root")["channel"], "unknown")
        store.close()


class DashboardAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(Path(self.temp.name), 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def post(self, path, data):
        h = {"Cookie": self.cookie, "Origin": self.server.origin, "Content-Type": "application/json", "X-Workflow-Request": "1"}
        req = urllib.request.Request(self.server.origin + path, data=json.dumps(data).encode(), headers=h)
        try:
            r = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            r = e
        with r:
            return r.code, dict(r.headers), json.loads(r.read())

    def test_dashboard_answers_with_session_and_unauthenticated_refused(self):
        store = self.server.store
        store.create_task({"id": "t1", "title": "t1", "lane": "bugs"})
        q = store.ask({"task_id": "t1", "title": "Which?"})["id"]
        self.assertEqual(self.post(f"/api/questions/{q}/answer", {"answer": "ok"})[0], 401)
        _, headers, _ = self.post("/api/session", {"token": self.server.token})
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.post(f"/api/questions/{q}/answer", {"answer": "A"})[0], 200)
        row = store.snapshot()["questions"][0]
        self.assertEqual((row["channel"], row["needs_owner_confirmation"]), ("dashboard", False))
        # an agent-channel answer is flagged and the owner can reopen it from the dashboard
        store.create_task({"id": "t2", "title": "t2", "lane": "bugs"})
        q2 = store.ask({"task_id": "t2", "title": "Again?"})["id"]
        store.answer(q2, "ok", channel="agent")
        self.assertTrue(next(x for x in store.snapshot()["questions"] if x["id"] == q2)["needs_owner_confirmation"])
        self.assertEqual(self.post(f"/api/questions/{q2}/reopen", {})[0], 200)
        self.assertEqual(next(x for x in store.snapshot()["questions"] if x["id"] == q2)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
