"""T4 autopilot watchdog and steward actions, driven by a fake clock; no processes, no network."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from jarves.workflow import autopilot_watch as aw
from jarves.workflow.store import Store

POLICY = {"enabled": True, "kill": False, "versions": ["V4.5.1", "V5"],
          "lanes": {"bugs": {"workers": 1}, "features": {"workers": 1},
                    "steward": {"on": True, "model": "claude-haiku-5-5"}, "review": {"model": "claude-opus-5-5"}},
          "watchdog": {"idle_s": 300, "max_restarts_per_task": 1}}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.store = Store(Path(self.temp.name) / "w.sqlite3", clock=lambda: self.now[0])
        self.started, self.stopped, self.notes = [], [], []

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def step(self):
        self.store.tick()
        return aw.watchdog_step(self.store, POLICY, start=lambda lane, t: self.started.append((lane, t["id"])),
                                stop=lambda a: self.stopped.append(a["id"]),
                                notify=lambda k, t: self.notes.append(k))

    def task(self, ident, **extra):
        return self.store.create_task({"id": ident, "title": ident, "lane": "features", "version": "V4.5.1",
                                       "acceptance": [{"id": "C1", "label": "ok"}], **extra})

    def agent(self, ident, lane="features"):
        return self.store.register_agent({"id": ident, "lane": lane})

    def state(self, ident):
        return next(t for t in self.store.snapshot()["tasks"] if t["id"] == ident)


class WatchdogTests(Base):
    def test_watchdog_idle_lane_starts_after_idle_s(self):
        self.task("a")
        self.assertEqual(self.step(), [])
        self.now[0] += 299
        self.assertEqual(self.step(), [])
        self.now[0] += 2
        self.assertEqual(self.step(), ["start:features:a"])
        self.assertEqual(self.started, [("features", "a")])

    def test_watchdog_kill_switch_blocks_start(self):
        self.task("a")
        self.step()
        self.now[0] += 400
        self.store.tick()
        self.assertEqual(aw.watchdog_step(self.store, {**POLICY, "kill": True}, start=lambda *a: 1 / 0,
                                          stop=lambda a: None), [])

    def test_watchdog_next_task_by_version_order(self):
        self.task("late", version="V5")
        self.task("early", version="V4.5.1")
        self.step()
        self.now[0] += 301
        self.assertEqual(self.step(), ["start:features:early"])

    def test_watchdog_stall_restarts_once_then_blocks(self):
        self.task("a")
        self.agent("ap-features-a")
        self.store.claim("ap-features-a", "a")
        for expected in ("restart:a", "blocked:a"):
            self.now[0] += 2000
            self.store.heartbeat("ap-features-a", "alive")
            self.now[0] += 1900
            self.store.heartbeat("ap-features-a", "alive")  # heartbeat only: no progress
            actions = self.step()
            self.assertIn(expected, actions)
            if expected == "restart:a":
                self.assertEqual(self.state("a")["state"], "queued")
                self.agent("ap-features-a")
                self.assertIsNotNone(self.store.claim("ap-features-a", "a"))
        task = self.state("a")
        self.assertEqual(task["state"], "blocked")
        self.assertIn("stalled twice", task["blocker"])
        self.assertEqual(self.stopped, ["ap-features-a"])
        self.assertIn("task_blocked", {a["kind"] for a in aw.autopilot_alerts(self.store)})

    def test_watchdog_foreign_session_alert_only(self):
        self.task("a")
        self.agent("native-1")
        self.store.claim("native-1", "a")
        self.now[0] += 2000
        actions = self.step()
        self.assertEqual(actions, ["alert_foreign:native-1"])
        self.assertEqual(self.stopped, [])
        self.assertNotEqual(self.state("a")["state"], "queued")

    def test_watchdog_permission_wait_alert_only_then_away_alert_once(self):
        self.agent("ap-features-a")
        with self.store._tx() as db:
            agent = self.store._get(db, "agent", "ap-features-a")
            agent.update(status="blocked", heartbeat_at=self.now[0] - 500)
            self.store._put(db, "agent", agent)
        self.assertEqual(self.step(), [])
        self.assertEqual(self.notes, [])
        self.now[0] += 601
        with self.store._tx() as db:
            agent = self.store._get(db, "agent", "ap-features-a")
            agent.update(status="blocked", heartbeat_at=self.now[0] - 500)
            self.store._put(db, "agent", agent)
        self.assertEqual(self.step(), ["away_alert:ap-features-a"])
        self.assertEqual(self.step(), [])
        self.assertEqual(self.notes, ["permission_wait_away"])
        self.assertEqual(self.stopped, [])


class StewardTests(Base):
    FILES = {1: ["docs/readme.md"], 2: ["core/src/jarves/approvals_gate.py"], 3: ["core/src/jarves/foo.py"]}

    def prs(self, labels=()):
        return [{"number": n, "head_sha": f"abcdef{n}0", "labels": list(labels)} for n in self.FILES]

    def test_tier_r_review_not_steward(self):
        made = aw.steward_step(self.store, POLICY, self.prs(), lambda pr: self.FILES[pr["number"]])
        self.assertIn("review-pr-2-abcdef20", made)
        self.assertIn("steward-pr-1-abcdef10", made)
        self.assertIn("steward-pr-3-abcdef30", made)
        self.assertFalse([m for m in made if m.startswith("steward-pr-2")])
        self.assertEqual(self.state("review-pr-2-abcdef20")["scope"]["model"], "claude-opus-5-5")
        self.assertEqual(aw.steward_step(self.store, POLICY, self.prs(), lambda pr: self.FILES[pr["number"]]), [])

    def test_unsure_and_label_and_autopilot_code_are_tier_r(self):
        self.assertTrue(aw.is_tier_r([], []))
        self.assertTrue(aw.is_tier_r(["docs/x.md"], ["no-automerge"]))
        self.assertTrue(aw.is_tier_r(["core/src/jarves/workflow/autopilot.py"], []))
        self.assertFalse(aw.is_tier_r(["docs/x.md"], []))

    def test_beta_verify_once_daily(self):
        self.assertEqual(aw.beta_step(self.store, POLICY, "a" * 40, True, "2026-10-31"), ["beta-verify-2026-10-31"])
        self.assertEqual(aw.beta_step(self.store, POLICY, "b" * 40, True, "2026-10-31"), [])
        self.assertEqual(aw.beta_step(self.store, POLICY, "c" * 40, False, "2026-11-01"), [])
        self.assertEqual(aw.beta_step(self.store, POLICY, "d" * 40, True, "2026-11-01", contains_p01_fix=True),
                         ["beta-verify-dddddddd"])
        task = self.state("beta-verify-2026-10-31")
        self.assertEqual(task["resources"], ["owner-pc"])
        self.assertEqual(task["lane"], "release")

    def test_manual_test_owner_item(self):
        self.task("phone", required_validation="device")
        self.task("other")
        self.store.transition("phone", {"state": "review"})
        self.assertEqual(aw.manual_test_step(self.store), ["phone"])
        self.assertEqual(aw.manual_test_step(self.store), [])
        self.assertEqual(self.state("phone")["state"], "waiting_owner")
        snap = self.store.snapshot()
        self.assertTrue(next(t for t in snap["tasks"] if t["id"] == "other")["ready"])

    def test_no_merge_or_tag_calls(self):
        """AST + token scan of every autopilot*.py plus control.py and sync.py: no push/tag/merge/PR/label/API writes,
        in string commands or list-form argv. The only allowed git write is the local `merge --ff-only`."""
        import ast
        shell = re.compile(r"pr\s+merge|git\s+tag|git\s+push|\.merge\(|auto-merge|--auto\b|push\s+--force|"
                           r"release\s+signoff|gh\s+(pr|issue|label|release)\s+(merge|edit|close|create|comment|delete)|"
                           r"gh\s+api\b.*(-X|--method)|gh\s+label|--add-label|--remove-label", re.I)
        verbs = {"push", "tag", "merge", "label", "release", "signoff", "--add-label", "--remove-label", "--method", "-X"}
        gh_writes = {"merge", "edit", "close", "create", "comment", "delete", "reopen", "ready"}
        root = Path(aw.__file__).parent
        files = sorted({*root.glob("autopilot*.py"), root / "control.py", root / "sync.py"})
        self.assertGreaterEqual(len(files), 5)
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 300:
                    self.assertIsNone(shell.search(node.value), f"{path.name}: {node.value!r}")
                if isinstance(node, (ast.List, ast.Tuple)):
                    items = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    if len(items) != len(node.elts) or not items:
                        continue
                    argv = items[1:] if items[0] in {"git", "gh"} else items
                    if items[0] == "gh" and len(argv) > 1 and argv[1] in gh_writes:
                        self.fail(f"{path.name}: gh write {items}")
                    if items[0] == "gh" and argv[:1] == ["api"] and {"-X", "--method", "-f", "-F"} & set(argv):
                        self.fail(f"{path.name}: gh api write {items}")
                    if argv and argv[0] in verbs and items[0] in {"git", "gh"} | verbs:
                        if argv[0] == "merge" and "--ff-only" in argv:
                            continue
                        if argv[0] in {"push", "tag", "label", "merge", "release"}:
                            self.fail(f"{path.name}: write command {items}")


if __name__ == "__main__":
    unittest.main()
