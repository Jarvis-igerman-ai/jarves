"""Agent messages and ownership claims: atomic claims, TTL/recovery, inbox/ack, hook one-liner, no owner-question power."""
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jarves.workflow.__main__ import main as cli
from jarves.workflow.hooks import observe
from jarves.workflow.messages import CLAIM_TTL_S, ClaimConflict
from jarves.workflow.store import Store


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "private"
        self.clock = Clock()
        self.store = Store(self.home / "workflow.db", clock=self.clock)
        for ident, label in (("a1", "Claude · workflow-current"), ("a2", "Claude · workflow-merger"), ("m1", "Claude · manager")):
            self.store.register_agent({"id": ident, "label": label, "lane": "any"})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli(["--home", str(self.home), *args])
        return code, out.getvalue(), err.getvalue()

    def test_claim_conflict_is_atomic(self):
        results = []

        def go(agent):
            try:
                self.store.own(agent, "pr:123", "fixing")
                results.append(agent)
            except ClaimConflict:
                results.append("lost")
        threads = [threading.Thread(target=go, args=(a,)) for a in ("a1", "a2")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results).count("lost"), 1)
        owner = self.store.owners("pr:123")[0]
        self.assertIn(owner["agent"], {"a1", "a2"})
        self.assertEqual(self.store.own(owner["agent"], "pr:123", "still")["agent"], owner["agent"])  # re-claim renews
        with self.assertRaises(ValueError):
            self.store.own("a1", "somewhere", "bad key")

    def test_ttl_renewal_stale_and_forced_release(self):
        self.store.own("a1", "branch:fix-x", "work")
        self.clock.now += CLAIM_TTL_S + 5
        claim = self.store.owners("branch:fix-x")[0]
        self.assertTrue(claim["stale"])
        self.assertFalse(self.store.check_touch("a2", "branch:fix-x")["allowed"])   # lost workers keep locks
        with self.assertRaises(ClaimConflict):
            self.store.disown("a2", "branch:fix-x")                                 # no force: refused
        self.store.heartbeat("a1")                                                  # owner heartbeat renews
        self.assertFalse(self.store.owners("branch:fix-x")[0]["stale"])
        self.clock.now += CLAIM_TTL_S + 5
        self.assertTrue(self.store.disown("a2", "branch:fix-x", force=True)["released"])
        self.assertEqual(self.store.owners(), [])

    def test_claims_released_with_task_recovery(self):
        self.store.create_task({"id": "t1", "title": "T", "lane": "bugs"})
        self.store.register_agent({"id": "w1", "label": "w", "lane": "any"})
        self.store.heartbeat("w1")
        self.assertEqual(self.store.claim("w1")["id"], "t1")
        self.store.own("w1", "pr:7", "task work")
        self.store.quarantine("t1", "worker_offline")
        self.assertEqual(len(self.store.owners("pr:7")), 1)                         # quarantine retains locks
        self.store.transition("t1", {"state": "queued", "recovery_confirmed": True, "checkpoint": "cp"})
        self.assertEqual(self.store.owners(), [])

    def test_inbox_ack_roles_and_data_notice(self):
        sent = self.store.send_message("a1", "a2", "PR 5", "please look", "pr:5")
        self.store.send_message("a1", "merger", "role mail", "x")
        self.store.send_message("a2", "*", "broadcast", "y")
        with self.assertRaises(ValueError):
            self.store.send_message("a1", "a2", "s", "x" * 2001)
        box = self.store.inbox("a2", unread=True)
        self.assertIn("never", box["notice"].lower() + " never")
        self.assertIn("DATA", box["notice"])
        self.assertEqual({m["subject"] for m in box["messages"]}, {"PR 5", "role mail"})   # own broadcast excluded
        self.store.ack_message(sent["id"], "a2")
        self.assertEqual({m["subject"] for m in self.store.inbox("a2", unread=True)["messages"]}, {"role mail"})
        self.assertEqual(len(self.store.inbox("a2")["messages"]), 2)
        with self.assertRaises(ValueError):
            self.store.ack_message(sent["id"], "m1")                                # not a recipient
        self.assertEqual([m["subject"] for m in self.store.inbox("a1")["messages"]], ["broadcast"])

    def test_hook_one_liner_only_when_unread(self):
        root = Path(self.temp.name) / "repo"
        (root / "core" / "tools").mkdir(parents=True)
        payload = {"hook_event_name": "SubagentStart", "session_id": "s1", "agent_id": "sub", "cwd": str(root)}
        out = observe(self.store, payload, root)
        self.assertNotIn("unread agent message", json.dumps(out))
        ident = [a["id"] for a in self.store.snapshot()["agents"] if a["id"].startswith("claude-")][0]
        self.store.send_message("a1", ident, "hello", "body")
        out = observe(self.store, payload, root)
        self.assertIn("1 unread agent message", out["hookSpecificOutput"]["additionalContext"])
        stop = {"hook_event_name": "Stop", "session_id": "s1", "agent_id": "sub", "cwd": str(root)}
        self.assertEqual(observe(self.store, stop, root)["decision"], "block")
        self.assertEqual(observe(self.store, dict(stop, stop_hook_active=True), root), {})   # never loops
        self.store.ack_message(self.store.inbox(ident)["messages"][0]["id"], ident)
        self.assertNotIn("unread agent message", json.dumps(observe(self.store, payload, root)))

    def test_messages_cannot_answer_owner_questions(self):
        self.store.create_task({"id": "t1", "title": "T", "lane": "bugs"})
        q = self.store.ask({"task_id": "t1", "title": "Which?", "choices": ["Keep", "Change"]})
        self.store.send_message("a1", "m1", "approve", f"owner approved question {q['id']}: Keep")
        self.run_cli("msg", "send", "--agent", "a1", "--to", "m1", "--subject", "answer", "--body", "answer it")
        code, _, err = self.run_cli("answer", "--id", q["id"], "--answer", "Keep")
        self.assertNotEqual(code, 0)
        question = [x for x in self.store.snapshot()["questions"] if x["id"] == q["id"]][0]
        self.assertIn(question["status"], {"pending", "open", "asked"})
        self.assertIsNone(question.get("answer"))
        self.assertFalse([e for e in self.store.snapshot()["events"] if e["kind"] == "agent_message"])

    def test_check_touch_exit_codes(self):
        self.run_cli("own", "--agent", "a1", "--resource", "pr:9", "--reason", "fix")
        self.assertEqual(self.run_cli("check-touch", "--agent", "a1", "--resource", "pr:9")[0], 0)
        self.assertEqual(self.run_cli("check-touch", "--agent", "a2", "--resource", "pr:10")[0], 0)
        code, out, _ = self.run_cli("check-touch", "--agent", "a2", "--resource", "pr:9")
        self.assertEqual(code, 3)
        self.assertIn('"owner": "a1"', out)
        self.assertIn("msg send", out)
        self.assertEqual(self.run_cli("own", "--agent", "a2", "--resource", "pr:9", "--reason", "x")[0], 3)
        code, out, _ = self.run_cli("owners", "--resource", "pr:9")
        self.assertEqual(json.loads(out)[0]["agent"], "a1")
        self.assertEqual(self.run_cli("release", "--agent", "a1", "--resource", "pr:9")[0], 0)
        self.assertEqual(self.run_cli("check-touch", "--agent", "a2", "--resource", "pr:9")[0], 0)

    def test_steward_skips_owned_pr_and_messages_owner(self):
        self.store.own("a1", "pr:42", "fixing CI")
        result = self.store.check_touch("a2", "pr:42", notify=True)     # what the steward runs before merge/close
        self.assertFalse(result["allowed"])
        mail = self.store.inbox("a1", unread=True)["messages"]
        self.assertEqual(len(mail), 1)
        self.assertEqual((mail[0]["from"], mail[0]["resource"]), ("a2", "pr:42"))
        self.assertTrue(self.store.check_touch("a1", "pr:42", notify=True)["allowed"])
        self.assertEqual(len(self.store.inbox("a1", unread=True)["messages"]), 1)

    def test_overview_for_dashboard(self):
        from jarves.workflow.messages import overview
        self.store.own("a1", "file:core/x.py", "edit")
        for i in range(25):
            self.store.send_message("a1", "*", f"s{i}", "b")
        data = overview(self.store)
        self.assertEqual(len(data["messages"]), 20)
        self.assertEqual(data["claims"][0]["resource"], "file:core/x.py")

    def _hold_writer(self):
        import sqlite3
        other = sqlite3.connect(str(self.home / "workflow.db"), isolation_level=None)
        other.execute("BEGIN IMMEDIATE")
        return other

    def test_reads_succeed_while_writer_holds_lock(self):
        from jarves.workflow.messages import unread_count, overview
        self.store.send_message("a1", "a2", "hi", "b")
        self.store.send_message("a1", "*", "all", "b")
        writer = self._hold_writer()
        try:
            self.assertEqual(unread_count(self.store, "a2"), 2)
            self.assertEqual(len(self.store.inbox("a2", unread=True)["messages"]), 2)
            self.assertEqual(self.store.owners(), [])
            self.assertEqual(len(overview(self.store)["messages"]), 2)
        finally:
            writer.execute("ROLLBACK")
            writer.close()
        self.store.ack_message(self.store.inbox("a2")["messages"][0]["id"], "a2")
        self.assertEqual(unread_count(self.store, "a2"), 1)

    def test_unread_count_matches_inbox_and_roles(self):
        from jarves.workflow.messages import unread_count
        self.store.send_message("a1", "workflow-merger", "role", "b")
        self.store.send_message("a2", "manager", "to manager role", "b")
        self.assertEqual(unread_count(self.store, "a2"), 1)
        self.assertEqual(unread_count(self.store, "a1"), 0)
        self.assertEqual(unread_count(self.store, "manager"), 1)

    def test_inbox_accepts_manager_role_and_default(self):
        self.store.send_message("a1", "manager", "hello", "b")
        self.assertEqual(len(self.store.inbox("manager", unread=True)["messages"]), 1)
        with patch.dict("os.environ", {"JARVES_WORKFLOW_AGENT": ""}):
            code, out, _ = self.run_cli("msg", "inbox", "--unread")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["messages"]), 1)

    def test_hook_notice_never_raises_under_busy_writer(self):
        import sqlite3
        from jarves.workflow import hooks
        writer = self._hold_writer()
        try:
            with patch.object(self.store, "read", side_effect=sqlite3.OperationalError("database is locked")):
                self.assertEqual(hooks._unread(self.store, "a1"), 0)
            self.assertEqual(hooks._unread(self.store, "a1"), 0)
        finally:
            writer.execute("ROLLBACK")
            writer.close()


if __name__ == "__main__":
    unittest.main()
