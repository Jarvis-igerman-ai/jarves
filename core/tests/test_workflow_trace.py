"""Recorded action history, privacy modes and manual retro export; no model calls."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from jarves.workflow import trace
from jarves.workflow.hooks import observe
from jarves.workflow.store import Store


class WorkflowTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1000.0]
        self.store = Store(self.root / "workflow.db", clock=lambda: self.now[0])
        self.agent = self.store.register_agent({"id": "real-worker", "parent_id": "real-manager"})
        self.store.create_task({"id": "T", "title": "An approved task"})
        self.agent = self.store.claim("real-worker", "T")
        self.agent = self.store.snapshot()["agents"][0]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_default_metadata_has_call_identity_but_no_inputs_or_results(self):
        trace.record(self.store, self.agent, "PreToolUse", tool="Bash", call_id="call-1",
                     details={"tool_input": {"command": "private command", "token": "secret-value"}})
        result = trace.history(self.store)
        row = result["events"][0]
        self.assertEqual((row["agent_id"], row["task_id"], row["parent_id"], row["call_id"]),
                         ("real-worker", "T", "real-manager", "call-1"))
        self.assertNotIn("private command", json.dumps(result))
        self.assertNotIn("secret-value", json.dumps(result))

    def test_opt_in_details_redacts_credentials_and_matches_duration_and_failure(self):
        trace.settings(self.store, "details")
        trace.record(self.store, self.agent, "PreToolUse", tool="Bash", call_id="call-1", details={
            "tool_input": {"password": "swordfish", "command": "curl -H 'Authorization: Bearer abc-secret'",
                           "environment": {"API_KEY": "provider-key"}}, "thinking": "not an action"})
        self.now[0] += 0.25
        trace.record(self.store, self.agent, "PostToolUseFailure", tool="Bash", call_id="call-1", status="failed",
                     details={"error": "token=never-export-this"})
        result = trace.history(self.store)
        self.assertAlmostEqual(result["events"][0]["duration_ms"], 250)
        self.assertEqual(result["events"][0]["status"], "failed")
        encoded = json.dumps(result)
        for secret in ("swordfish", "abc-secret", "provider-key", "never-export-this", "not an action"):
            self.assertNotIn(secret, encoded)

    def test_common_credential_headers_environment_and_private_keys_never_reach_export(self):
        trace.settings(self.store, "details")
        secret_text = ("Authorization: Basic dXNlcjpwYXNz\nCookie: session=private-cookie\n"
                       "AWS_SECRET_ACCESS_KEY=aws-secret-value\n"
                       "-----BEGIN PRIVATE KEY-----\nprivate-key-body\n-----END PRIVATE KEY-----\n"
                       "https://person:private-password@example.invalid/path")
        trace.record(self.store, self.agent, "PostToolUse", details={"tool_response": secret_text})
        exported = Path(trace.retro_bundle(self.store)["path"]).read_text(encoding="utf-8")
        for secret in ("dXNlcjpwYXNz", "private-cookie", "aws-secret-value", "private-key-body", "private-password"):
            self.assertNotIn(secret, exported)

    def test_filters_pagination_and_no_invented_previous_history(self):
        self.assertEqual(trace.history(self.store)["events"], [])
        for index in range(4):
            self.now[0] += 1
            trace.record(self.store, self.agent, "PreToolUse", tool="Read", call_id=str(index))
        first = trace.history(self.store, agent_id="real-worker", task_id="T", limit=2)
        second = trace.history(self.store, before=first["before"], limit=2)
        self.assertTrue(first["more"])
        self.assertEqual(len({r["id"] for r in first["events"] + second["events"]}), 4)
        self.assertEqual(trace.history(self.store, agent_id="x' OR 1=1 --")["events"], [])
        self.assertEqual(len(trace.history(self.store, since=1003, until=1003)["events"]), 1)
        with self.assertRaises(ValueError):
            trace.history(self.store, since=float("nan"))

    def test_recording_is_bounded_and_off_does_not_capture(self):
        with patch.object(trace, "MAX_EVENTS", 3):
            for index in range(6):
                trace.record(self.store, self.agent, "PreToolUse", call_id=str(index))
            self.assertEqual(len(trace.history(self.store)["events"]), 3)
        trace.settings(self.store, "off")
        trace.record(self.store, self.agent, "PreToolUse", call_id="not-recorded")
        self.assertEqual(len(trace.history(self.store)["events"]), 3)
        trace.settings(self.store, "metadata")
        self.now[0] += trace.RETENTION_S + 1
        trace.record(self.store, self.agent, "PreToolUse", call_id="new")
        self.assertEqual(len(trace.history(self.store)["events"]), 1)

    def test_retro_is_private_selected_evidence_and_never_executes_or_changes_rules(self):
        with self.assertRaisesRegex(ValueError, "no recorded"):
            trace.retro_bundle(self.store)
        trace.record(self.store, self.agent, "PreToolUse", tool="Read", call_id="one")
        result = trace.retro_bundle(self.store, task_id="T")
        self.assertTrue(result["prepared"])
        self.assertFalse(result["executed"])
        path = Path(result["path"])
        self.assertEqual(path.parent, self.root / "retros")
        exported = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(exported["events"][0]["call_id"], "one")
        self.assertIn("UNTRUSTED", exported["warning"])
        self.assertIn("/retro", result["prompt"])
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_native_subagent_actions_keep_session_group_and_do_not_read_transcripts(self):
        payload = {"hook_event_name": "PreToolUse", "cwd": str(self.root), "session_id": "session-real",
                   "agent_id": "child-real", "agent_type": "workflow-bugs", "tool_name": "Read",
                   "tool_use_id": "native-call", "transcript_path": "/do/not/read/me",
                   "last_assistant_message": "private chat", "tool_input": {"file_path": "source.py"}}
        observe(self.store, payload, self.root)
        row = trace.history(self.store)["events"][0]
        self.assertEqual(row["call_id"], "native-call")
        self.assertTrue(row["parent_id"].startswith("claude-"))
        self.assertNotIn("private chat", json.dumps(trace.history(self.store)))


def test_existing_orchestrator_events_join_the_same_journal_without_a_second_dispatcher(tmp_path):
    from jarves.orchestrator.db import AgentDB
    profile = tmp_path / "profile"
    ledger = Store(profile / "workflow" / "workflow.db")
    db = AgentDB(profile / "agents.db")
    try:
        db.upsert_session("codex:real-id", provider="codex", project="jarves", state="RUNNING")
        ledger.register_agent({"id": "bugs", "session_id": "codex:real-id", "lane": "bugs", "auto_dispatch": True})
        db.add_event("codex:real-id", "progress", "private summary")
        history = trace.history(ledger)
        assert len(history["events"]) == 1
        assert history["events"][0]["event"] == "orchestrator:progress"
        assert "private summary" not in json.dumps(history)
        actor = ledger.snapshot()["agents"][0]
        assert actor["session_id"] == "codex:real-id" and actor["auto_dispatch"]
        assert actor["lane"] == "bugs" and actor["id"] == "bugs"
    finally:
        db.close()
        ledger.close()


def test_optional_observer_contention_does_not_hold_agent_delivery_for_fifteen_seconds(tmp_path):
    from jarves.orchestrator.db import AgentDB
    profile = tmp_path / "profile"
    ledger = Store(profile / "workflow" / "workflow.db")
    db = AgentDB(profile / "agents.db")
    finished = threading.Event()
    db.upsert_session("codex:real", provider="codex", project="jarves", state="RUNNING")

    def progress():
        try:
            db.add_event("codex:real", "progress", "agent progressed")
        finally:
            finished.set()

    worker = threading.Thread(target=progress)
    try:
        with ledger._tx():
            worker.start()
            assert finished.wait(3), "optional observation blocked actual agent progress behind the ledger lock"
        worker.join(3)
        assert len(db.events("codex:real")) == 1
    finally:
        worker.join(3)
        db.close()
        ledger.close()
