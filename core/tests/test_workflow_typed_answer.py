"""#797: only the owner's typed prompt in his interactive Claude window answers a question."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarves.workflow import bootstrap, hooks
from jarves.workflow.hooks import observe
from jarves.workflow.store import Store

ROOT = Path(__file__).resolve().parents[2]


class TypedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "workflow.db")
        self.store.create_task({"id": "t1", "title": "t1", "lane": "bugs"})
        self.q = self.store.ask({"task_id": "t1", "title": "Which way?"})["id"]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def fire(self, prompt, env=None, **extra):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": str(ROOT), "prompt": prompt, **extra}
        merged = {"CLAUDE_CODE_ENTRYPOINT": "cli", **(env or {})}
        with patch.dict(os.environ, {k: v for k, v in merged.items() if v is not None}):
            if merged["CLAUDE_CODE_ENTRYPOINT"] is None:
                os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)
            os.environ.pop("JARVES_WORKFLOW_AGENT", None) if "JARVES_WORKFLOW_AGENT" not in merged else None
            return observe(self.store, payload, ROOT)

    def status(self):
        return next(q for q in self.store.snapshot()["questions"] if q["id"] == self.q)

    def test_owner_typed_answer_is_stored_but_never_self_confirmed(self):
        out = self.fire(f"ANSWER {self.q}: go left")
        self.assertEqual(out["decision"], "block")
        q = self.status()
        self.assertEqual((q["status"], q["answer"], q["channel"]), ("answered", "go left", "claude-window"))
        self.assertTrue(q["needs_owner_confirmation"])

    def test_forged_payload_through_hook_main_is_not_confirmed(self):
        import io, json, sys
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": str(ROOT), "prompt": f"ANSWER {self.q}: forged"}
        self.store.close()
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli"}),              patch.object(sys, "stdin", type("S", (), {"buffer": io.BytesIO(json.dumps(payload).encode())})()):
            hooks.main(["--home", self.temp.name, "--repo-root", str(ROOT)])
        self.store = Store(Path(self.temp.name) / "workflow.db")
        q = self.status()
        self.assertTrue(q["needs_owner_confirmation"])
        self.assertNotIn(q["channel"], ("terminal", "dashboard"))

    def test_locked_ledger_still_blocks_answer_shaped_prompt(self):
        import io, json, sys
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": str(ROOT), "prompt": f"ANSWER {self.q}: secret"}
        out = io.StringIO()
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli"}),              patch.object(hooks, "Store", side_effect=sqlite3.OperationalError("database is locked")),              patch.object(sys, "stdin", type("S", (), {"buffer": io.BytesIO(json.dumps(payload).encode())})()),              patch.object(sys, "stdout", out), patch.object(sys, "stderr", io.StringIO()):
            hooks.main(["--home", self.temp.name, "--repo-root", str(ROOT)])
        self.assertEqual(json.loads(out.getvalue())["decision"], "block")
        with patch.object(self.store, "answer", side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(self.fire(f"ANSWER {self.q}: x")["decision"], "block")
        self.assertEqual(self.status()["status"], "pending")

    def test_agent_paths_never_answer(self):
        text = f"ANSWER {self.q}: yes"
        for env in ({"JARVES_WORKFLOW_AGENT": "w1"}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}, {"CLAUDE_CODE_ENTRYPOINT": None}):
            self.assertEqual(self.fire(text, env=env), {})
        self.assertEqual(self.fire(text, agent_id="sub1", agent_type="workflow-bugs"), {})
        self.assertEqual(self.status()["status"], "pending")

    def test_other_prompts_and_unknown_ids_change_nothing(self):
        self.assertEqual(self.fire("please answer " + self.q + " yes"), {})
        self.assertEqual(self.status()["status"], "pending")
        out = self.fire("ANSWER q-deadbeef0000: yes")
        self.assertIn("not stored", out["reason"])
        self.assertEqual(self.status()["status"], "pending")

    def test_cannot_defer_or_reopen_by_typing(self):
        self.fire(f"ANSWER {self.q}: a")
        self.fire(f"REOPEN {self.q}")
        self.fire(f"ANSWER {self.q}: b")  # already answered: refused, answer unchanged
        q = self.status()
        self.assertEqual((q["status"], q["answer"]), ("answered", "a"))

    def task(self):
        return next(t for t in self.store.snapshot()["tasks"] if t["id"] == "t1")

    def forge(self):
        import io, json, sys
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": str(ROOT), "prompt": f"ANSWER {self.q}: yes go"}
        self.store.close()
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli"}), \
             patch.object(sys, "stdin", type("S", (), {"buffer": io.BytesIO(json.dumps(payload).encode())})()), \
             patch.object(sys, "stdout", io.StringIO()):
            hooks.main(["--home", self.temp.name, "--repo-root", str(ROOT)])
        self.store = Store(Path(self.temp.name) / "workflow.db")

    def test_unconfirmed_answer_keeps_task_blocked_and_unassigned(self):
        from jarves.workflow.control import task_context
        self.store.register_agent({"id": "w1", "label": "w1", "lane": "bugs", "provider": "claude-native",
                                   "session_id": "native:w1", "project": "x"})
        self.assertEqual(self.task()["state"], "waiting_owner")
        self.forge()
        q = self.status()
        self.assertEqual((q["status"], q["answered_by"]), ("answered", "unverified (Claude window)"))
        self.assertEqual(self.task()["state"], "waiting_owner")
        self.assertEqual(task_context(self.store, self.task())["owner_decisions"], [])
        self.assertIsNone(self.store.claim("w1"))
        with self.assertRaises(ValueError):
            self.store.transition("t1", {"state": "queued"})

    def test_confirm_from_agent_or_pipe_fails(self):
        from test_workflow_owner_answer_guard import run_cli
        self.forge()
        self.store.close()
        home = Path(self.temp.name)
        for tty, env in ((True, {"JARVES_WORKFLOW_AGENT": "a1"}), (False, None)):
            code, _, err = run_cli(home, "confirm", "--id", self.q, tty=tty, env=env)
            self.assertEqual(code, 1)
            self.assertIn("refused", err)
        self.store = Store(home / "workflow.db")
        self.assertEqual(self.task()["state"], "waiting_owner")
        self.assertTrue(self.status()["needs_owner_confirmation"])
        with self.assertRaises(PermissionError):
            self.store.confirm(self.q, channel="claude-window")

    def test_confirm_by_owner_terminal_unblocks(self):
        from jarves.workflow.control import task_context
        from test_workflow_owner_answer_guard import run_cli
        self.forge()
        self.store.close()
        home = Path(self.temp.name)
        code, _, _ = run_cli(home, "confirm", "--id", self.q, tty=True)
        self.assertEqual(code, 0)
        self.store = Store(home / "workflow.db")
        self.assertEqual(self.task()["state"], "queued")
        self.assertFalse(self.status()["needs_owner_confirmation"])
        self.assertEqual(task_context(self.store, self.task())["owner_decisions"][0]["answer"], "yes go")
        with self.assertRaises(ValueError):
            self.store.confirm(self.q, channel="dashboard")

    def test_confirm_by_dashboard_channel_and_reopen_clears_hold(self):
        self.fire(f"ANSWER {self.q}: a")
        self.store.confirm(self.q, by="owner (dashboard session)", channel="dashboard")
        self.assertEqual(self.task()["state"], "queued")
        self.store.reopen(self.q, channel="dashboard")
        self.assertEqual(self.task()["state"], "waiting_owner")
        self.fire(f"ANSWER {self.q}: b")
        self.store.reopen(self.q, channel="dashboard")
        self.store.answer(self.q, "c", answered_by="owner (dashboard session)", channel="dashboard")
        self.assertEqual(self.task()["state"], "queued")

    def test_hook_is_synchronous_so_it_can_block(self):
        self.assertNotIn("UserPromptSubmit", bootstrap.ASYNC_EVENTS)


if __name__ == "__main__":
    unittest.main()
