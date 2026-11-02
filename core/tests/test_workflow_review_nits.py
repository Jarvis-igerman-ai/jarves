"""#861 review nits (temp homes only): PR number validation, hostile policy file, git budget, native claim."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarves.workflow import hooks
from jarves.workflow.hooks import observe
from jarves.workflow.store import Store


class ReviewNitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.home = Path(self.temp.name) / "wf"
        self.store = Store(self.home / "workflow.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_close_superseded_refuses_non_positive_and_bool(self):
        self.store.create_task({"id": "r", "title": "R", "scope": {"pr": 5, "kind": "steward"}})
        for bad in (["0"], [True], [-1], ["5", "0"]):
            with self.assertRaises(ValueError):
                self.store.close_superseded(bad)
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "queued")

    def test_deeply_nested_policy_file_falls_back_and_stop_hook_exits_zero(self):
        (self.home / "autopilot.json").write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
        self.assertEqual(self.store._worker_limit(), 3)
        payload = {"hook_event_name": "Stop", "session_id": "s", "cwd": str(self.root)}
        stdin = mock.Mock(buffer=io.BytesIO(json.dumps(payload).encode()))
        with mock.patch.object(sys, "stdin", stdin), mock.patch("builtins.print"), \
                mock.patch.object(hooks.Store, "manager_continue", side_effect=RecursionError("deep")):
            self.assertEqual(hooks.main(["--home", str(self.home), "--repo-root", str(self.root)]), 0)

    def test_git_note_budget_is_well_under_the_hook_limit(self):
        self.assertLessEqual(hooks.GIT_NOTE_BUDGET_S, 0.6)

    def test_subagent_registration_is_native_and_claimable(self):
        observe(self.store, {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(self.root)}, self.root)
        observe(self.store, {"hook_event_name": "SubagentStart", "session_id": "s", "agent_id": "kid",
                             "agent_type": "workflow-bugs", "cwd": str(self.root)}, self.root)
        agent = next(a for a in self.store.snapshot()["agents"] if a.get("parent_id"))
        self.assertEqual((agent["provider"], agent["status"]), ("claude-native", "running"))
        self.store.create_task({"id": "t", "title": "T", "lane": "bugs"})
        self.assertEqual(self.store.claim(agent["id"], "t")["id"], "t")

    def test_agent_with_native_session_but_empty_provider_can_claim(self):
        self.store.register_agent({"id": "legacy", "lane": "bugs", "provider": "", "session_id": "native:abc"})
        self.store.observe_agent("legacy", "running", "hook event")
        self.store.create_task({"id": "t", "title": "T", "lane": "bugs"})
        self.assertEqual(self.store.claim("legacy", "t")["id"], "t")


if __name__ == "__main__":
    unittest.main()
