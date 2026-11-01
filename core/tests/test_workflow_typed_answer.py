"""#797: only the owner's typed prompt in his interactive Claude window answers a question."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarves.workflow import bootstrap
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
                os.environ.pop("CLAUDE_CODE_ENTRYPOINT")
            os.environ.pop("JARVES_WORKFLOW_AGENT", None) if "JARVES_WORKFLOW_AGENT" not in merged else None
            return observe(self.store, payload, ROOT)

    def status(self):
        return next(q for q in self.store.snapshot()["questions"] if q["id"] == self.q)

    def test_owner_typed_answer_is_stored_and_confirmed(self):
        out = self.fire(f"ANSWER {self.q}: go left")
        self.assertEqual(out["decision"], "block")
        q = self.status()
        self.assertEqual((q["status"], q["answer"], q["channel"]), ("answered", "go left", "terminal"))
        self.assertFalse(q["needs_owner_confirmation"])

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

    def test_hook_is_synchronous_so_it_can_block(self):
        self.assertNotIn("UserPromptSubmit", bootstrap.ASYNC_EVENTS)


if __name__ == "__main__":
    unittest.main()
