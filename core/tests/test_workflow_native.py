"""Native Claude lifecycle is observation, not approval or acceptance."""
import json
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.bootstrap import prepare, settings_with_hooks
from jarves.workflow.hooks import observe
from jarves.workflow.store import Store


class WorkflowNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "AGENTS.md").write_text("Policy", encoding="utf-8")
        tool = self.root / "core" / "tools" / "workflow_hook.py"
        tool.parent.mkdir(parents=True)
        tool.write_text("# hook", encoding="utf-8")
        self.home = Path(self.temp.name) / "private"
        self.store = Store(self.home / "workflow.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def event(self, event, **kw):
        return observe(self.store, {"hook_event_name": event, "session_id": "real-session",
                                    "cwd": str(self.root), **kw}, self.root)

    def test_actual_subagent_id_and_home_are_injected_without_parsing_transcript(self):
        result = self.event("SubagentStart", agent_id="actual-child", agent_type="workflow-bugs",
                            last_assistant_message="private message that must not be retained")
        agent = self.store.snapshot()["agents"][0]
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertIn(agent["id"], context)
        self.assertIn(str(self.home), context)
        self.assertEqual(agent["lane"], "bugs")
        self.assertFalse(agent["auto_dispatch"])
        self.assertNotIn("private message", json.dumps(self.store.snapshot()))

    def test_native_stop_does_not_finish_task_or_release_unreconciled_resources(self):
        self.event("SubagentStart", agent_id="child", agent_type="workflow-bugs")
        aid = self.store.snapshot()["agents"][0]["id"]
        self.store.create_task({"id": "fix", "title": "Fix", "lane": "bugs", "resources": ["windows-gui"]})
        self.assertEqual(self.store.claim(aid, "fix")["id"], "fix")
        self.event("SubagentStop", agent_id="child", agent_type="workflow-bugs")
        task = self.store.snapshot()["tasks"][0]
        self.assertEqual(task["state"], "blocked")
        self.assertTrue(task["quarantined"])
        self.assertEqual(task["validation"], "not_run")

    def test_permission_notification_does_not_return_permission_decision(self):
        result = self.event("Notification", notification_type="permission_prompt")
        self.assertEqual(result, {})
        self.assertEqual(self.store.snapshot()["agents"][0]["status"], "blocked")

    def test_manager_stop_continuation_only_for_ready_authorised_work_and_is_bounded(self):
        self.store.create_task({"id": "ready", "title": "Approved task"})
        self.assertEqual(self.event("Stop")["decision"], "block")
        self.assertEqual(self.event("Stop", stop_hook_active=True), {})
        self.assertEqual(self.event("Stop")["decision"], "block")
        self.assertEqual(self.event("Stop"), {})

    def test_owner_wait_and_unapproved_scope_do_not_keep_manager_in_a_loop(self):
        self.store.create_task({"id": "scope", "title": "Scope only", "execution_allowed": False})
        self.store.create_task({"id": "wait", "title": "Needs owner"})
        self.store.ask({"task_id": "wait", "title": "Choose?"})
        self.assertEqual(self.event("Stop"), {})

    def test_hook_install_preserves_permissions_and_unrelated_hooks_idempotently(self):
        settings = {"permissions": {"deny": ["Bash(rm:*)"]}, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing-observer"}]}]}}
        updated = settings_with_hooks(settings, self.root, self.home, "/python")
        again = settings_with_hooks(updated, self.root, self.home, "/python")
        self.assertEqual(updated, again)
        self.assertEqual(updated["permissions"], settings["permissions"])
        self.assertEqual(updated["hooks"]["Stop"][0], settings["hooks"]["Stop"][0])
        self.assertNotIn("asyncRewake", json.dumps(updated))

    def test_preview_migration_preserves_unrelated_local_instructions_and_writes_nothing(self):
        local = self.root / "CLAUDE.local.md"
        old = "Use concise Hebrew.\n\nRequire 5 nightly runs for a JARVES release.\n"
        local.write_text(old, encoding="utf-8")
        changes = prepare(self.root, self.home, "/python")
        self.assertIn("Use concise Hebrew.", changes[0][2])
        self.assertNotIn("Require 5 nightly", changes[0][2])
        self.assertIn("@AGENTS.md", changes[0][2])
        self.assertEqual(local.read_text(encoding="utf-8"), old)
        self.assertFalse((self.root / ".claude" / "settings.local.json").exists())


if __name__ == "__main__":
    unittest.main()
