"""Real SQLite concurrency and state transitions; no clock sleeps or services."""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.store import Store


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.path = Path(self.temp.name) / "workflow.sqlite3"
        self.store = Store(self.path, clock=lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def task(self, ident="one", **extra):
        return self.store.create_task({"id": ident, "title": ident, "lane": "features", "version": "V5",
                                       "acceptance": [{"id": "C1", "label": "User scenario works"}], **extra})

    def worker(self, ident="worker"):
        return self.store.register_agent({"id": ident, "label": ident, "lane": "features"})

    def test_two_process_handles_cannot_claim_same_task(self):
        self.task(resources=["windows-gui"])
        self.worker("a")
        self.worker("b")
        other = Store(self.path, clock=lambda: self.now[0])
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                claims = list(pool.map(lambda x: x[0].claim(x[1]), [(self.store, "a"), (other, "b")]))
            self.assertEqual(sum(c is not None for c in claims), 1)
            self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "running")
        finally:
            other.close()

    def test_auto_dispatch_requires_explicit_boolean_opt_in(self):
        agent = self.worker()
        self.assertFalse(agent["auto_dispatch"])
        agent = self.store.register_agent({"id": "worker", "auto_dispatch": True})
        self.assertTrue(agent["auto_dispatch"])
        self.assertTrue(self.store.register_agent({"id": "worker"})["auto_dispatch"])
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.store.register_agent({"id": "worker", "auto_dispatch": "yes"})

    def test_claim_refuses_expired_heartbeat_before_supervisor_tick(self):
        self.task()
        self.worker()
        self.now[0] += 121
        self.assertIsNone(self.store.claim("worker"))

    def test_resource_claim_blocks_another_task_and_priority_is_respected(self):
        self.task("low", resources=["phone"], priority=3)
        self.task("high", resources=["phone"], priority=0)
        self.worker("a")
        self.worker("b")
        self.assertEqual(self.store.claim("a")["id"], "high")
        self.assertIsNone(self.store.claim("b"))

    def test_waiting_question_releases_worker_only_at_explicit_checkpoint(self):
        self.task("needs-owner", resources=["windows-gui"], priority=0)
        self.task("independent", resources=["windows-gui"], priority=2)
        self.worker()
        self.store.claim("worker")
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.store.ask({"task_id": "needs-owner", "title": "Which design?"})
        q = self.store.ask({"task_id": "needs-owner", "title": "Which design?", "checkpoint": "Changes committed; GUI closed"})
        self.store.defer(q["id"])
        self.assertEqual(self.store.claim("worker")["id"], "independent")
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "waiting_owner")
        self.store.answer(q["id"], "Keep the existing design")
        first = self.store.snapshot()["tasks"][0]
        self.assertEqual(first["state"], "queued")
        self.assertEqual(first["implementation"], "in_progress")
        self.assertFalse(first["acceptance"][0]["passed"])

    def test_multiple_questions_all_need_answers(self):
        self.task()
        q1 = self.store.ask({"task_id": "one", "title": "First?"})
        q2 = self.store.ask({"task_id": "one", "title": "Second?"})
        self.store.answer(q1["id"], "yes")
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "waiting_owner")
        self.store.answer(q2["id"], "no")
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "queued")

    def test_unfinished_dependencies_do_not_get_skipped(self):
        self.task("base")
        self.task("dependent", depends_on=["base"], priority=0)
        self.worker()
        self.assertEqual(self.store.claim("worker")["id"], "base")
        self.store.ask({"task_id": "base", "title": "Decision?", "checkpoint": "No work in flight"})
        self.assertIsNone(self.store.claim("worker"))
        self.assertEqual(self.store.snapshot()["tasks"][1]["state"], "queued")

    def test_stale_worker_quarantine_keeps_locks_and_never_duplicate_dispatches(self):
        self.task("a", resources=["windows-gui"], priority=0)
        self.task("b", resources=["windows-gui"], priority=2)
        self.worker("a")
        self.worker("b")
        self.store.claim("a")
        self.now[0] += 121
        self.store.heartbeat("b")
        alerts = self.store.tick()
        self.assertTrue(any(a["kind"] == "worker_offline" for a in alerts))
        self.assertIsNone(self.store.claim("b"))
        task = self.store.snapshot()["tasks"][0]
        self.assertTrue(task["quarantined"])
        with self.assertRaisesRegex(ValueError, "recovery"):
            self.store.transition("a", {"state": "queued"})
        self.store.transition("a", {"state": "queued", "recovery_confirmed": True,
                                    "checkpoint": "Old process stopped and worktree inspected"})
        self.assertEqual(self.store.claim("b")["id"], "a")

    def test_live_but_not_progressing_worker_is_stalled(self):
        self.task()
        self.worker()
        self.store.claim("worker")
        self.now[0] += 1801
        self.store.heartbeat("worker", "Still running")
        self.assertTrue(any(a["kind"] == "worker_stalled" for a in self.store.tick()))
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "blocked")

    def test_ambiguous_dispatch_quarantine_retains_resources(self):
        self.task("one", resources=["worktree"], priority=0)
        self.task("two", resources=["worktree"], priority=2)
        self.worker("a")
        self.worker("b")
        self.store.claim("a")
        quarantined = self.store.quarantine("one", "Delivery timed out; worker may already be writing")
        self.assertTrue(quarantined["quarantined"])
        self.assertEqual(quarantined["agent_id"], "a")
        self.assertIsNone(self.store.claim("a"))
        self.assertIsNone(self.store.claim("b"))

    def test_done_fails_closed_without_acceptance_device_proof_and_delivery(self):
        self.task(required_validation="device")
        self.store.transition("one", {"code_revision": "abc"})
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.transition("one", {"state": "verified", "implementation": "merged", "validation": "device"})
        with self.assertRaises(ValueError):
            self.store.record_check("one", "C1", "")
        self.store.record_check("one", "C1", "Real phone run artifact #42, build abc")
        with self.assertRaisesRegex(ValueError, "validation"):
            self.store.transition("one", {"state": "verified", "implementation": "merged", "validation": "automated"})
        self.store.transition("one", {"state": "verified", "implementation": "merged", "validation": "device"})
        with self.assertRaisesRegex(ValueError, "delivery"):
            self.store.transition("one", {"state": "done"})
        with self.assertRaisesRegex(ValueError, "artifact_ref"):
            self.store.transition("one", {"state": "done", "delivery": "beta"})
        self.store.transition("one", {"state": "done", "delivery": "beta", "artifact_ref": "beta build 42, sha256:123"})
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "done")
        self.store.record_check("one", "C1", "Regression on build def", passed=False)
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "review")

    def test_reopening_invalidates_passing_flags(self):
        self.task(required_validation="automated")
        self.store.transition("one", {"code_revision": "abc"})
        self.store.record_check("one", "C1", "CI artifact for build abc")
        self.store.transition("one", {"state": "done", "implementation": "merged", "validation": "automated", "delivery": "beta", "artifact_ref": "beta-abc"})
        task = self.store.transition("one", {"state": "queued"})
        self.assertEqual(task["validation"], "not_run")
        self.assertFalse(task["acceptance"][0]["passed"])

    def test_presence_expires_without_answering_or_granting_anything(self):
        self.task()
        self.store.ask({"task_id": "one", "title": "Question?"})
        self.store.set_owner("present")
        self.assertEqual(self.store.snapshot()["owner"]["effective_mode"], "present")
        self.now[0] += 601
        state = self.store.snapshot()
        self.assertEqual(state["owner"]["effective_mode"], "away")
        self.assertEqual(state["questions"][0]["status"], "pending")
        self.assertEqual(state["tasks"][0]["state"], "waiting_owner")

    def test_seed_plan_is_idempotent_and_does_not_overwrite_progress(self):
        plan = {"baseline_sha": "abc", "scope_note": "scope only", "versions": [{"id": "V5", "requirements": [
            {"id": "V5-PHONE", "title": "Phone", "issue_numbers": [193], "acceptance": [{"id": "A", "label": "Pair"}]}]}]}
        self.assertEqual(self.store.seed_plan(plan)["created"], 1)
        self.worker()
        self.assertIsNone(self.store.claim("worker"))
        self.store.transition("V5-PHONE", {"execution_allowed": True})
        self.store.claim("worker")
        self.assertEqual(self.store.seed_plan(plan)["created"], 0)
        state = self.store.snapshot()
        self.assertEqual(state["tasks"][0]["state"], "running")
        self.assertEqual(state["tasks"][0]["issue_numbers"], [193])

    def test_seed_plan_orders_cross_version_dependencies_and_marks_mapping_gaps(self):
        plan = {"baseline_sha": "abc", "versions": [{"id": "V7", "requirements": [
            {"id": "dependent", "title": "Dependent", "depends_on": ["base"], "issue_numbers": [1]},
            {"id": "base", "title": "Base"}]}]}
        self.assertEqual(self.store.seed_plan(plan)["created"], 2)
        self.assertEqual(self.store.snapshot()["source"]["unmapped"], ["base"])

    def test_scope_import_does_not_reset_failed_or_live_github_snapshot(self):
        self.store.set_source({"mode": "github", "sha": "live-sha", "synced_at": 900,
                               "error": "rate limited", "unmapped": ["old-gap"]})
        plan = {"baseline_sha": "scope-sha", "versions": [{"id": "V5", "requirements": [
            {"id": "new-gap", "title": "Unmapped"}]}]}
        self.store.seed_plan(plan)
        source = self.store.snapshot()["source"]
        self.assertEqual(source["mode"], "github")
        self.assertEqual(source["sha"], "live-sha")
        self.assertEqual(source["synced_at"], 900)
        self.assertEqual(source["error"], "rate limited")
        self.assertEqual(source["unmapped"], ["old-gap", "new-gap"])

    def test_cyclic_seed_plan_is_rejected_before_partial_import(self):
        plan = {"versions": [{"id": "V5", "requirements": [
            {"id": "a", "title": "A", "depends_on": ["b"]},
            {"id": "b", "title": "B", "depends_on": ["a"]}]}]}
        with self.assertRaisesRegex(ValueError, "cyclic"):
            self.store.seed_plan(plan)
        self.assertEqual(self.store.snapshot()["tasks"], [])

    def test_changed_code_revision_invalidates_previous_evidence(self):
        self.task(required_validation="automated")
        self.store.transition("one", {"code_revision": "abc"})
        self.store.record_check("one", "C1", "CI for abc")
        self.store.transition("one", {"state": "done", "implementation": "merged", "validation": "automated", "delivery": "beta", "artifact_ref": "beta-abc"})
        task = self.store.transition("one", {"code_revision": "def"})
        self.assertEqual(task["state"], "review")
        self.assertEqual(task["validation"], "not_run")
        self.assertEqual(task["artifact_ref"], "")
        self.assertFalse(task["acceptance"][0]["passed"])
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.transition("one", {"state": "verified", "validation": "automated"})

    def test_missing_revision_and_pre_revision_evidence_never_verify(self):
        self.task(required_validation="automated")
        self.store.record_check("one", "C1", "An unbound old test log")
        with self.assertRaisesRegex(ValueError, "revision"):
            self.store.transition("one", {"state": "verified", "implementation": "merged", "validation": "automated"})
        self.store.transition("one", {"code_revision": "abc", "implementation": "merged", "validation": "automated"})
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.transition("one", {"state": "verified"})
        self.store.record_check("one", "C1", "CI log explicitly checked on abc")
        self.store.transition("one", {"state": "verified"})

    def test_acceptance_regression_blocks_downstream_claims(self):
        self.task("base", required_validation="automated")
        self.task("dependent", depends_on=["base"])
        self.store.transition("base", {"code_revision": "abc"})
        self.store.record_check("base", "C1", "CI artifact on base commit")
        self.store.transition("base", {"state": "done", "implementation": "merged", "validation": "automated", "delivery": "beta", "artifact_ref": "beta-abc"})
        self.worker()
        self.assertTrue(next(t for t in self.store.snapshot()["tasks"] if t["id"] == "dependent")["ready"])
        self.store.record_check("base", "C1", "Regression in real app", passed=False)
        self.assertIsNone(self.store.claim("worker"))

    def test_imported_heartbeat_does_not_refresh_a_stale_observation(self):
        self.worker()
        self.now[0] += 200
        agent = self.store.heartbeat("worker", observed_at=1000)
        self.assertEqual(agent["heartbeat_at"], 1000)
        self.assertIsNone(self.store.claim("worker"))
        agent = self.store.heartbeat("worker", observed_at=100000)
        self.assertEqual(agent["heartbeat_at"], self.now[0])

    def test_quarantined_question_cannot_release_the_old_process_locks(self):
        self.task(resources=["windows-gui"])
        self.worker()
        self.store.claim("worker")
        self.now[0] += 121
        self.store.tick()
        with self.assertRaisesRegex(ValueError, "recovery"):
            self.store.ask({"task_id": "one", "title": "Continue?", "checkpoint": "A statement alone is not recovery"})

    def test_metadata_sync_cannot_turn_closed_issues_into_proof_or_execution(self):
        self.task(execution_allowed=False)
        self.store.update_metadata("one", {"source_closed": True, "source_issues": [{"number": 193, "state": "closed"}]})
        task = self.store.snapshot()["tasks"][0]
        self.assertEqual(task["state"], "queued")
        self.assertEqual(task["validation"], "not_run")
        self.assertFalse(task["execution_allowed"])
        with self.assertRaisesRegex(ValueError, "operator"):
            self.store.update_metadata("one", {"execution_allowed": True})
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.store.update_metadata("one", {"state": "done"})
        task = self.store.update_metadata("one", {"execution_allowed": True}, authorize_execution=True)
        self.assertTrue(task["execution_allowed"])

    def test_task_and_question_payloads_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "large"):
            self.task(scope_note="a" * 65000)
        self.task()
        with self.assertRaisesRegex(ValueError, "large"):
            self.store.ask({"task_id": "one", "title": "Question?", "context": "a" * 25000})

    def test_empty_system_shows_no_fake_workers_and_no_work_is_invented(self):
        self.assertEqual(self.store.snapshot()["agents"], [])
        self.store.tick()
        self.assertEqual(self.store.snapshot()["tasks"], [])
        self.task()
        self.assertTrue(any(a["kind"] == "no_workers" for a in self.store.tick()))


if __name__ == "__main__":
    unittest.main()
