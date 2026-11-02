"""#789 item 5: Overview 'now' data (workers, supervisor stop/start strip, manager log). Fake clock, no services."""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarves.workflow import observability as obs
from jarves.workflow.server import STATIC, Server
from jarves.workflow.store import Store
from jarves.workflow.trace import record, settings

T0 = 1_800_000_000.0


class NowViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [T0]
        self.store = Store(Path(self.temp.name) / "workflow.db", clock=lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_empty_ledger_is_a_clear_empty_view(self):
        view = obs.now_view(self.store)
        self.assertEqual(view["workers"], [])
        self.assertIsNone(view["manager"])
        self.assertEqual(view["restarts"], {"events": [], "gaps": [], "starts": 0, "stops": 0})
        self.assertEqual(view["since"], T0 - 86400)

    def test_worker_task_and_one_redacted_bounded_line(self):
        self.store.create_task({"id": "BUG-1", "title": "Fix the strip", "lane": "bugs"})
        worker = self.store.register_agent({"id": "w1", "label": "Claude · workflow-bugs", "lane": "bugs"})
        self.store.claim("w1", "BUG-1")
        self.store.register_agent({"id": "idle", "lane": "bugs"})            # idle, no task: not running
        settings(self.store, "details")
        secret = "sk-proj-" + "a" * 30
        record(self.store, worker, "PreToolUse", tool="Bash", call_id="c1",
               details={"tool_input": {"description": f"Run tests with token={secret} " + "x" * 300,
                                       "command": "cat ~/.ssh/id_rsa"}})
        view = obs.now_view(self.store)
        self.assertEqual([w["agent_id"] for w in view["workers"]], ["w1"])
        w = view["workers"][0]
        self.assertEqual((w["task_id"], w["task_title"]), ("BUG-1", "Fix the strip"))
        self.assertEqual((w["last"]["event"], w["last"]["tool"]), ("PreToolUse", "Bash"))
        hint = w["last"]["hint"]
        self.assertNotIn(secret, json.dumps(view))
        self.assertNotIn("id_rsa", json.dumps(view))                         # only description/summary, never the command
        self.assertLessEqual(len(hint), obs.NOW_HINT + 1)
        self.assertNotIn("\n", hint)
        # Metadata mode (the default): no hint at all.
        settings(self.store, "metadata")
        record(self.store, worker, "PostToolUse", tool="Bash", call_id="c1", details={"tool_input": {"description": "visible?"}})
        self.assertEqual(obs.now_view(self.store)["workers"][0]["last"]["hint"], "")

    def test_worker_list_is_bounded_and_stale_workers_drop(self):
        for i in range(obs.NOW_WORKERS + 3):
            self.store.register_agent({"id": f"w{i}", "lane": "bugs"})
            self.store.observe_agent(f"w{i}", "running")
        view = obs.now_view(self.store)
        self.assertEqual(len(view["workers"]), obs.NOW_WORKERS)
        self.assertEqual(view["workers_total"], obs.NOW_WORKERS + 3)
        self.now[0] += obs.ACTIVE_FRESH_S + 1
        self.assertEqual(obs.now_view(self.store)["workers"], [])

    def test_manager_log_last_20_actions_one_line_per_call(self):
        manager = self.store.register_agent({"id": "mgr", "lane": "ops", "provider": "claude-native"})
        self.store.observe_agent("mgr", "running")
        self.assertTrue(self.store.claim_followup_manager("mgr"))
        for i in range(30):
            self.now[0] += 1
            record(self.store, manager, "PreToolUse", tool="Read", call_id=f"c{i}")
            self.now[0] += 1
            record(self.store, manager, "PostToolUse", tool="Read", call_id=f"c{i}")
        record(self.store, manager, "PreToolUse", tool="Bash", call_id="open")   # still in flight
        view = obs.now_view(self.store)
        self.assertEqual([w["agent_id"] for w in view["workers"]], [])           # the manager is not listed as a worker
        actions = view["manager"]["actions"]
        self.assertEqual(len(actions), 20)
        self.assertEqual((actions[0]["event"], actions[0]["tool"]), ("PreToolUse", "Bash"))
        self.assertTrue(all(a["event"] == "PostToolUse" for a in actions[1:]))
        self.assertEqual(actions[1]["duration_ms"], 1000)
        self.assertEqual(set(actions[0]), {"ts", "event", "tool", "status", "duration_ms"})   # metadata only

    def test_manager_falls_back_to_top_level_native_session(self):
        self.store.register_agent({"id": "sub", "lane": "bugs", "provider": "claude-native", "parent_id": "top"})
        self.store.register_agent({"id": "top", "lane": "ops", "provider": "claude-native"})
        self.assertEqual(obs.now_view(self.store)["manager"]["agent_id"], "top")

    def test_restart_strip_events_and_gaps(self):
        obs.on_start(self.store, Path(self.temp.name), "supervise")
        obs.beat(self.store, "supervisor", "ok")
        self.now[0] += 60
        obs.on_stop(Path(self.temp.name), "owner stop", self.store)
        self.now[0] += 3600
        obs.on_start(self.store, Path(self.temp.name), "login")
        obs.beat(self.store, "supervisor", "ok")
        r = obs.now_view(self.store)["restarts"]
        self.assertEqual([e["kind"] for e in r["events"]], ["start", "stop", "start"])
        self.assertEqual((r["starts"], r["stops"]), (2, 1))
        self.assertEqual(len(r["gaps"]), 1)
        self.assertGreaterEqual(r["gaps"][0]["seconds"], 3600)
        # Events older than 24 h leave the strip.
        self.now[0] += 86400 + 1
        self.assertEqual(obs.now_view(self.store)["restarts"]["events"], [])


class NowEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(Path(self.temp.name), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def get(self, path, cookie=""):
        req = urllib.request.Request(self.server.origin + path, headers={"Cookie": cookie, "Origin": self.server.origin})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, json.loads(response.read() or b"null")

    def test_requires_session_and_is_read_only_json(self):
        self.assertEqual(self.get("/api/now")[0], 401)
        req = urllib.request.Request(self.server.origin + "/api/session", data=json.dumps({"token": self.server.token}).encode(),
                                     headers={"Content-Type": "application/json", "X-Workflow-Request": "1", "Origin": self.server.origin})
        with urllib.request.urlopen(req, timeout=5) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        revision = self.server.store.snapshot()["revision"]
        status, data = self.get("/api/now", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"now", "since", "trace_mode", "workers", "workers_total", "manager", "restarts"})
        self.assertNotIn(self.server.token, json.dumps(data))
        self.assertEqual(self.server.store.snapshot()["revision"], revision)


class NowStaticContractTests(unittest.TestCase):
    def setUp(self):
        self.js = (Path(STATIC) / "overview.js").read_text(encoding="utf-8")
        self.html = (Path(STATIC) / "index.html").read_text(encoding="utf-8")

    def test_panels_exist_with_states_and_accessibility(self):
        for needle in ('id="ov-workers"', 'id="ov-strip"', 'id="ov-manager-log" tabindex="0" role="region"', 'href="#timeline"'):
            self.assertIn(needle, self.html)
        self.assertEqual(self.html.count('aria-busy="true"'), 3)
        self.assertIn('fetch("/api/now"', self.js)
        self.assertIn('role="img" aria-label="${esc(label)}"', self.js)
        for banned in ("style=", "onclick", "method:", "X-Workflow-Request"):
            self.assertNotIn(banned, self.js)
        # Every server string is escaped; free text keeps its own direction.
        self.assertIn('<span dir="auto">${esc(a.hint)}</span>', self.js)
        self.assertIn('${esc(w.task_title)}', self.js)

    def test_both_locales_have_the_same_keys_and_hebrew_has_no_english_runs(self):
        en = self.js[self.js.index("    now: {\n      noWorkers"):self.js.index("  } : {")]
        he = self.js[self.js.index("    now: {\n      noWorkers", self.js.index("  } : {")):self.js.index("  const signatures")]
        keys = lambda part: set(re.findall(r"(?:^|[{,])\s*([A-Za-z]+): ", part, re.M))
        self.assertEqual(keys(en), keys(he))
        for text in re.findall(r'"([^"]*)"|`([^`]*)`', he):
            words = re.sub(r"\$\{[^}]*\}", "", " ".join(text))
            self.assertIsNone(re.search(r"\b[A-Za-z]+ [A-Za-z]+ [A-Za-z]+\b", words), words)
        for key in ("now_title", "now_loading", "restarts_title", "restarts_more", "manager_title", "manager_note"):
            self.assertEqual(self.js.count(f"{key}: "), 2, key)
            self.assertIn(f'data-i18n="{key}"', self.html)

    def test_overview_script_parses_and_renders(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        self.assertEqual(subprocess.run([node, "--check", str(Path(STATIC) / "overview.js")], capture_output=True).returncode, 0)
