"""Fake-worker end-to-end for the autopilot loop (plan T3). No paid API, no real session, no network.

A fake `start` spawns a fake worker thread that drives the REAL workflow CLI (next, report --progress,
ask, transition --state review). Proves ledger logic only; real `claude -p` workers are UNVERIFIED.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from jarves.workflow import __main__ as cli
from jarves.workflow import autopilot_loop as ap
from jarves.workflow import hooks
from jarves.workflow.store import Store

POLICY = {"enabled": True, "kill": False, "project": "jarves", "versions": ["V4.5.1", "V5", "V6"],
          "authorized_versions": ["V4.5.1", "V5"], "max_concurrent": 3, "max_starts_per_hour": 50,
          "lanes": {"bugs": {"workers": 1, "model": "claude-sonnet-5-5"},
                    "features": {"workers": 1, "model": "claude-sonnet-5-5"}}}


def run_cli(home, *argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(["--home", str(home), *argv])
    return code, out.getvalue()


class Fake:
    """Records starts/stops; each started session runs a scripted worker thread over the real CLI."""

    def __init__(self, home, ask_on=()):
        self.home, self.ask_on = home, set(ask_on)
        self.started, self.stopped, self.threads, self.errors = [], [], [], []
        self.lock = threading.Lock()

    def start(self, profile_home, **req):
        agent = req["env"]["JARVES_WORKFLOW_AGENT"]
        with self.lock:
            self.started.append(req)
            sid = f"sess-{len(self.started)}"
        thread = threading.Thread(target=self.work, args=(agent, req), daemon=True)
        self.threads.append(thread)
        thread.start()
        return {"ok": True, "session": {"id": sid}}

    def stop(self, profile_home, session_id):
        self.stopped.append(session_id)
        return {"ok": True}

    def work(self, agent, req):
        try:
            task = json.loads(req["task"].split("\n", 1)[1])["id"]
            time.sleep(0.2)                       # let the autopilot link the session first
            code, _ = run_cli(self.home, "report", "--agent", agent, "--progress", "--note", "working")
            assert code == 0
            if task in self.ask_on:
                code, _ = run_cli(self.home, "ask", "--task", task, "--title", "Which option?",
                                  "--checkpoint", "wip-commit")
            else:
                code, _ = run_cli(self.home, "transition", "--task", task, "--state", "review")
            assert code == 0
        except Exception as exc:  # noqa: BLE001
            self.errors.append(repr(exc))

    def join(self):
        for t in self.threads:
            t.join(10)
        self.assertNoErrors()

    def assertNoErrors(self):
        assert not self.errors, self.errors


class AutopilotLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = Store(self.home / "workflow.db")
        ap._LAST_SIG.clear()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def task(self, ident, lane, version, **extra):
        return self.store.create_task({"id": ident, "title": ident, "lane": lane, "version": version,
                                       "acceptance": [{"id": "C1", "label": "works"}], **extra})

    def tasks(self):
        return {t["id"]: t for t in self.store.snapshot()["tasks"]}

    def step(self, fake, **kw):
        return ap.step(self.store, self.home, kw.pop("policy", POLICY), start=fake.start, stop=fake.stop, force=True)

    def test_one_bug_two_features_across_versions(self):
        self.task("gh-1", "bugs", "V4.5.1", priority=0)
        self.task("f-v5", "features", "V5")                      # created first, but later version
        self.task("f-451", "features", "V4.5.1")
        fake = Fake(self.home)
        first = self.step(fake)
        # one worker per lane, started in one step; version order picks V4.5.1 before V5
        self.assertEqual({a["task_id"] for a in first if a["action"] == "started"}, {"gh-1", "f-451"})
        self.assertEqual(self.step(fake), [])                    # no duplicate start while both run
        fake.join()
        second = self.step(fake)                                 # finished: stop sessions + next task, one step
        self.assertEqual(sorted(fake.stopped), ["sess-1", "sess-2"])
        self.assertEqual([a["task_id"] for a in second if a["action"] == "started"], ["f-v5"])
        fake.join()
        self.step(fake)
        self.assertEqual(len(fake.stopped), 3)
        order = [json.loads(r["task"].split("\n", 1)[1])["id"] for r in fake.started]
        self.assertLess(order.index("f-451"), order.index("f-v5"))
        self.assertTrue(all(t["state"] == "review" for t in self.tasks().values()))
        self.assertTrue(all(r["model"] == "claude-sonnet-5-5" and r["provider"] == "claude" for r in fake.started))

    def test_unauthorized_version_does_not_start(self):
        self.task("f-v6", "features", "V6")
        fake = Fake(self.home)
        self.assertEqual(self.step(fake), [])
        self.assertEqual(fake.started, [])

    def test_question_on_a_does_not_stop_b_and_answer_resumes_a(self):
        self.task("f-a", "features", "V4.5.1", priority=0)
        self.task("f-b", "features", "V4.5.1", priority=1)
        fake = Fake(self.home, ask_on={"f-a"})
        self.step(fake)
        fake.join()
        self.step(fake)                                          # A waits -> B starts in the same step
        fake.join()
        tasks = self.tasks()
        self.assertEqual(tasks["f-a"]["state"], "waiting_owner")
        self.assertEqual(tasks["f-b"]["state"], "review")
        question = next(q for q in self.store.snapshot()["questions"] if q["task_id"] == "f-a")
        self.store.answer(question["id"], "option 2")
        fake.ask_on.clear()
        self.step(fake)                                          # old sessions stopped, A resumes
        resumed = fake.started[-1]
        self.assertIn("option 2", resumed["task"])
        self.assertIn("wip-commit", resumed["task"])             # checkpoint travels with the task
        fake.join()
        self.step(fake)
        self.assertEqual(self.tasks()["f-a"]["state"], "review")

    def test_concurrent_steps_start_once(self):
        self.task("f-1", "features", "V4.5.1")
        fake = Fake(self.home)
        barrier = threading.Barrier(4)

        def go():
            other = Store(self.home / "workflow.db")
            try:
                barrier.wait()
                ap.step(other, self.home, POLICY, start=fake.start, stop=fake.stop, force=True)
            finally:
                other.close()
        threads = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in threads]
        [t.join(15) for t in threads]
        self.assertEqual(len(fake.started), 1)
        fake.join()

    def test_clean_refusal_releases_claim_and_pauses(self):
        self.task("f-1", "features", "V4.5.1")
        calls = []

        def refuse(_home, **req):
            calls.append(req)
            return {"ok": False, "code": "BUDGET_REACHED"}
        actions = ap.step(self.store, self.home, POLICY, start=refuse, stop=lambda *a: {"ok": True}, force=True)
        self.assertEqual(actions[0]["action"], "refused")
        task = self.tasks()["f-1"]
        self.assertEqual((task["state"], task["agent_id"]), ("queued", None))
        self.assertIn("BUDGET_REACHED", task["blocker"])
        ap.step(self.store, self.home, POLICY, start=refuse, stop=lambda *a: {"ok": True}, force=True)
        self.assertEqual(len(calls), 1)                          # paused: no hammering

    def test_ambiguous_start_quarantines_and_is_not_retried(self):
        self.task("f-1", "features", "V4.5.1")
        calls = []
        start = lambda _home, **req: calls.append(req)           # returns None: unknown outcome
        ap.step(self.store, self.home, POLICY, start=start, stop=lambda *a: {"ok": True}, force=True)
        task = self.tasks()["f-1"]
        self.assertTrue(task["quarantined"])
        ap.step(self.store, self.home, POLICY, start=start, stop=lambda *a: {"ok": True}, force=True)
        self.assertEqual(len(calls), 1)

    def test_restart_with_unlinked_claim_is_quarantined_not_restarted(self):
        self.task("f-1", "features", "V4.5.1")
        started = []
        # crash between claim and start: claimed by an autopilot agent, no session
        self.store.register_agent({"id": "ap-features-f-1", "lane": "features", "provider": "claude"})
        self.store.link_session("ap-features-f-1", "", autopilot=True)
        self.store.claim("ap-features-f-1", task_id="f-1")
        later = Store(self.home / "workflow.db", clock=lambda: time.time() + ap.START_GRACE_S + 5)
        try:
            ap.step(later, self.home, POLICY, start=lambda *a, **k: started.append(1) or {"ok": True},
                    stop=lambda *a: {"ok": True}, force=True)
        finally:
            later.close()
        self.assertEqual(started, [])
        self.assertTrue(self.tasks()["f-1"]["quarantined"])

    def test_kill_switch_and_disabled_block_starts(self):
        self.task("f-1", "features", "V4.5.1")
        fake = Fake(self.home)
        self.assertEqual(self.step(fake, policy={**POLICY, "kill": True}), [])
        self.assertEqual(self.step(fake, policy={**POLICY, "enabled": False}), [])
        self.assertEqual(fake.started, [])

    def test_hook_attribution_uses_autopilot_agent_not_native_identity(self):
        self.task("f-1", "features", "V4.5.1")
        fake = Fake(self.home)
        self.step(fake)
        fake.join()
        root = Path(__file__).resolve().parents[2]
        payload = {"hook_event_name": "PostToolUse", "session_id": "native-s", "cwd": str(root), "tool_name": "Bash"}
        before = {a["id"] for a in self.store.snapshot()["agents"]}
        hooks.observe(self.store, payload, root, "ap-features-f-1")
        after = {a["id"] for a in self.store.snapshot()["agents"]}
        self.assertEqual(before, after)                          # no second identity
        hooks.observe(self.store, payload, root, None)
        self.assertEqual(len({a["id"] for a in self.store.snapshot()["agents"]} - before), 1)


if __name__ == "__main__":
    unittest.main()
