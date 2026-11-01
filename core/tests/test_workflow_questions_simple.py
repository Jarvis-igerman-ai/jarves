"""Simple, focused owner questions: plain Hebrew templates, a one-shot rewrite and 'Help from Claude'."""
import io
import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from jarves.workflow import autopilot_watch as aw
from jarves.workflow import discuss
from jarves.workflow import questions_text as qt
from jarves.workflow.__main__ import main as cli
from jarves.workflow.server import Server
from jarves.workflow.store import Store

STATIC = Path(__file__).resolve().parents[1] / "src/jarves/workflow/static"


def plan_task(store, ident, version="V5", **extra):
    data = {"id": ident, "title": "ממשק PWA בעברית ובאנגלית", "lane": "features", "version": version,
            "owner_gate": "מכשיר Android אמיתי.", "execution_allowed": False,
            "acceptance": [{"id": "A1", "label": "Required source test evidence: assembleDebug in CI"}], **extra}
    return store.create_task(data)


class Templates(unittest.TestCase):
    """Every generated template: short Hebrew title, at most one 3+ English-word run outside Details."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "w.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def assert_plain(self, question):
        runs = [r for text in qt.visible_texts(question) for r in qt.english_runs(text)]
        self.assertLessEqual(len(runs), 1, (question["id"] if "id" in question else question["title"], runs))
        self.assertFalse(qt.english_runs(question["title"]), question["title"])
        self.assertTrue(qt.HEBREW.search(question["title"]), question["title"])
        self.assertLessEqual(len(question["title"].split()), 12, question["title"])
        choices = question.get("choices") or []
        if choices:
            self.assertTrue(2 <= len(choices) <= 3)
            self.assertTrue(choices[0].get("recommended"))
            self.assertTrue(question.get("recommendation"))
        self.assertLessEqual(len(question.get("steps") or []), 3)

    def test_gate_template_is_plain_hebrew_even_for_english_tasks(self):
        english = {"id": "V7-G1", "title": "Read-only GitHub client and protected token store", "version": "V7",
                   "owner_gate": "Owner creates a read-only fine-grained token (guide)",
                   "acceptance": [{"label": "Fake API tests, redaction tests and rate-limit handling"}]}
        for task in (english, {"id": "V5-PWA", "title": "ממשק PWA בעברית ובאנגלית", "version": "V5",
                               "owner_gate": "מכשיר Android אמיתי."}):
            q = aw.gate_question(task)
            self.assert_plain(q)
            self.assertEqual(q["group"], f"gate:{task['version']}")
            self.assertIn(task["owner_gate"], q["details"])
        self.assertIn("Read-only GitHub client", aw.gate_question(english)["details"])

    def test_manual_test_and_live_rewrites_are_plain(self):
        self.assert_plain({**qt.manual_test_text({"id": "t", "title": "Phone pairing on a real device"}), "choices": []})
        for spec in qt.LIVE_REWRITES.values():
            self.assert_plain({k: v for k, v in spec.items() if k != "original"})

    def test_gate_choice_ids_did_not_change(self):
        self.assertEqual([c["id"] for c in qt.GATE_CHOICES], ["later-today", "tell-me", "skip-version"])


class Rewrite(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "w.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def old_gate(self, ident, version="V5"):
        plan_task(self.store, ident, version)
        self.store.ask({"id": f"gate-{ident}", "task_id": ident, "blocks": False, "kind": "owner_gate", "minutes": 15,
                        "title": f"Your step is needed for: {ident}", "why": "Task cannot be finished by an agent",
                        "choices": [{"id": "later-today", "label": "Schedule it for later today", "recommended": True},
                                    {"id": "tell-me", "label": "Tell me when ready"},
                                    {"id": "skip-version", "label": "Skip for this version"}]})

    def frozen(self):
        return {q["id"]: (q["status"], q.get("answer"), q["created_at"], q.get("answered_at"), q["task_id"], q["blocks"],
                          sorted(c["id"] for c in q.get("choices", [])))
                for q in self.store.snapshot()["questions"]}

    def test_rewrite_changes_display_only_skips_answered_and_is_idempotent(self):
        for ident in ("a", "b", "c"):
            self.old_gate(ident)
        self.old_gate("d", "V6")
        self.store.answer("gate-b", "Tell me when ready", channel="dashboard")
        self.store.answer("gate-c", "ok", channel="agent")          # needs owner confirmation: untouched
        self.store.defer("gate-d")
        before = self.frozen()
        revision = self.store.snapshot()["revision"]
        rows = qt.rewrite_pending_questions(self.store)
        self.assertEqual(sorted(r["id"] for r in rows), ["gate-a", "gate-d"])
        self.assertTrue(all(r["applied"] for r in rows))
        self.assertEqual(self.frozen(), before)                     # status, answers, timestamps, ids untouched
        self.assertGreater(self.store.snapshot()["revision"], revision)  # live views refresh
        by_id = {q["id"]: q for q in self.store.snapshot()["questions"]}
        self.assertEqual(by_id["gate-a"]["title"], "צריך אותך: ממשק PWA בעברית ובאנגלית")
        self.assertEqual(by_id["gate-a"]["choices"][0]["label"], "לתזמן להיום")
        self.assertEqual(by_id["gate-d"]["status"], "deferred")
        self.assertEqual(by_id["gate-b"]["title"], "Your step is needed for: b")
        self.assertEqual(by_id["gate-c"]["title"], "Your step is needed for: c")
        self.assertEqual(qt.rewrite_pending_questions(self.store), [])

    def test_dry_run_changes_nothing(self):
        self.old_gate("a")
        snap = json.dumps(self.store.snapshot()["questions"], sort_keys=True)
        rows = qt.rewrite_pending_questions(self.store, apply=False)
        self.assertEqual([r["id"] for r in rows], ["gate-a"])
        self.assertEqual(json.dumps(self.store.snapshot()["questions"], sort_keys=True), snap)

    def test_live_rewrite_matches_id_and_original_title_only(self):
        plan_task(self.store, "gh-58", execution_allowed=True)
        spec = qt.LIVE_REWRITES["q-bf7f4acd789f"]
        self.store.ask({"id": "q-bf7f4acd789f", "task_id": "gh-58", "title": spec["original"], "context": "Code merged (PR #68).",
                        "choices": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"}]})
        self.store.ask({"id": "q-a92cdde685b5", "task_id": "gh-58", "title": "A different question reused this id"})
        rows = qt.rewrite_pending_questions(self.store)
        self.assertEqual([r["id"] for r in rows], ["q-bf7f4acd789f"])
        q = next(q for q in self.store.snapshot()["questions"] if q["id"] == "q-bf7f4acd789f")
        self.assertEqual(q["title"], spec["title"])
        self.assertEqual(q["choices"][0]["id"], "b")                 # recommended first, ids kept
        self.assertIn(spec["original"], q["details"])
        self.assertIn("Code merged (PR #68).", q["details"])
        self.assertEqual((q["status"], q["answer"], q["blocks"]), ("pending", None, True))

    def test_store_refuses_non_display_fields_and_changed_choice_ids(self):
        self.old_gate("a")
        with self.assertRaises(ValueError):
            self.store.rewrite_display("gate-a", {"status": "answered"})
        with self.assertRaises(ValueError):
            self.store.rewrite_display("gate-a", {"answer": "x"})
        with self.assertRaises(ValueError):
            self.store.rewrite_display("gate-a", {"choices": [{"id": "approve", "label": "x"}]})

    def test_cli_dry_run_then_apply_prints_a_before_after_table(self):
        home = Path(self.temp.name) / "home"
        store = Store(home / "workflow.db")
        plan_task(store, "a")
        store.ask({"id": "gate-a", "task_id": "a", "blocks": False, "title": "Your step is needed for: a",
                   "choices": [{"id": "later-today", "label": "x"}, {"id": "tell-me", "label": "y"},
                               {"id": "skip-version", "label": "z"}]})
        store.close()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli(["questions-rewrite", "--dry-run", "--home", str(home)]), 0)
        self.assertIn("dry run: 1 question(s)", out.getvalue())
        self.assertIn("before: Your step is needed for: a", out.getvalue())
        self.assertIn("after:  צריך אותך:", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli(["questions-rewrite", "--apply", "--home", str(home)]), 0)
        self.assertIn("applied: 1", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            cli(["questions-rewrite", "--apply", "--home", str(home)])
        self.assertIn("nothing to rewrite", out.getvalue())


class FakeLauncher:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, *, cwd, env):
        self.calls.append((argv, cwd, env))


class DiscussHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(Path(self.temp.name), 0)
        self.launcher = FakeLauncher()
        self.now = [100.0]
        helper = self.server.discuss
        helper.launcher, helper.platform = self.launcher, "win32"
        helper.which = lambda name: r"C:\Users\owner\AppData\Roaming\npm\claude.cmd"
        helper.clock = lambda: self.now[0]
        helper.repo = Path(r"C:\work\jarves-main")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, headers, _ = self.request("/api/session", {"token": self.server.token}, cookie="")
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.server.unlocked[self.cookie.split("=", 1)[1]] = 4e9  # owner-answer unlock (#750): see test_workflow_unlock.py
        store = self.server.store
        plan_task(store, "V5-PWA")
        plan_task(store, "V5-PAIRING")
        aw.gate_questions_step(store)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None, cookie=None, origin=None, marker=True):
        h = {"Cookie": self.cookie if cookie is None else cookie, "Origin": origin or self.server.origin}
        if data is not None:
            h["Content-Type"] = "application/json"
            if marker:
                h["X-Workflow-Request"] = "1"
        req = urllib.request.Request(self.server.origin + path,
                                     data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.code, dict(response.headers), json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw

    def test_discuss_writes_the_prompt_and_launches_a_fixed_visible_window_without_the_token(self):
        code, _, body = self.request("/api/questions/gate-V5-PWA/discuss", {})
        self.assertEqual(code, 200, body)
        result = body["result"]
        self.assertTrue(result["launched"])
        argv, cwd, env = self.launcher.calls[0]
        self.assertEqual(argv[:6], ["cmd", "/c", "start", discuss.WINDOW_TITLE, "/D", r"C:\work\jarves-main"])
        self.assertEqual(argv[6:10], [r"C:\Users\owner\AppData\Roaming\npm\claude.cmd", "--permission-mode", "plan", "--add-dir"])
        self.assertTrue(all(discuss.SAFE_ARG.fullmatch(a) for a in argv[3:]))
        self.assertEqual(env["JARVES_WORKFLOW_AGENT"], "1")
        self.assertEqual(env["JARVES_WORKFLOW_HELPER"], "1")
        path = Path(result["prompt_file"])
        self.assertEqual(path, Path(self.temp.name) / "discuss" / "gate-V5-PWA.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn(discuss.RULE, text)
        self.assertIn("צריך אותך: ממשק PWA בעברית ובאנגלית", text)
        self.assertIn("לתזמן להיום (recommended)", text)
        self.assertIn("gate-V5-PAIRING", text)                      # group members listed
        self.assertIn("docs/workflow/OPERATING_MODEL.md", text)
        for part in (text, " ".join(argv), json.dumps(env), result["command"]):
            self.assertNotIn(self.server.token, part)
        self.assertIn(str(path), result["command"])
        q = next(q for q in self.server.store.snapshot()["questions"] if q["id"] == "gate-V5-PWA")
        self.assertEqual((q["status"], q["answer"]), ("pending", None))   # helper never answers

    def test_failed_launch_does_not_use_the_rate_limit_slot(self):
        def boom(argv, *, cwd, env):
            raise OSError("no window")
        original = self.server.discuss.launcher
        self.server.discuss.launcher = boom
        self.request("/api/questions/gate-V5-PWA/discuss", {})
        self.assertIsNone(self.server.discuss.last)
        self.server.discuss.launcher = original

    def test_rate_limit_one_launch_per_ten_seconds(self):
        self.assertEqual(self.request("/api/questions/gate-V5-PWA/discuss", {})[0], 200)
        code, _, body = self.request("/api/questions/gate-V5-PAIRING/discuss", {})
        self.assertEqual(code, 429)
        self.assertIn("wait 10 seconds", body["error"])
        self.now[0] += 10.5
        self.assertEqual(self.request("/api/questions/gate-V5-PAIRING/discuss", {})[0], 200)
        self.assertEqual(len(self.launcher.calls), 2)

    def test_copy_only_and_refusals_return_the_same_command_and_launch_nothing(self):
        code, _, body = self.request("/api/questions/gate-V5-PWA/discuss", {"launch": False})
        self.assertEqual((code, body["result"]["launched"]), (200, False))
        self.assertTrue(body["result"]["command"].startswith('cd "C:\\work\\jarves-main"; claude --permission-mode plan'))
        self.server.discuss.platform = "linux"
        body = self.request("/api/questions/gate-V5-PWA/discuss", {})[2]
        self.assertFalse(body["result"]["launched"])
        self.assertIn("only on Windows", body["result"]["reason"])
        self.server.discuss.platform = "win32"
        self.server.discuss.which = lambda name: None
        body = self.request("/api/questions/gate-V5-PWA/discuss", {})[2]
        self.assertIn("not found", body["result"]["reason"])
        self.server.discuss.which = lambda name: r'C:\evil"&calc.exe'
        body = self.request("/api/questions/gate-V5-PWA/discuss", {})[2]
        self.assertIn("unsafe", body["result"]["reason"])
        self.assertEqual(self.launcher.calls, [])

    def test_csrf_origin_session_and_id_are_checked(self):
        path = "/api/questions/gate-V5-PWA/discuss"
        self.assertEqual(self.request(path, {}, origin="http://evil.example")[0], 403)
        self.assertEqual(self.request(path, {}, marker=False)[0], 403)
        self.assertEqual(self.request(path, {}, cookie="")[0], 401)
        self.assertEqual(self.request("/api/questions/..%5Cx/discuss", {})[0], 404)
        self.assertEqual(self.request("/api/questions/unknown-q/discuss", {})[0], 400)
        self.assertEqual(self.request(path, {"launch": "yes"})[0], 400)
        self.assertEqual(self.request(path, {"argv": ["calc"]})[0], 400)
        self.assertEqual(self.launcher.calls, [])

    def test_answered_question_gets_no_helper_and_answer_endpoint_is_unchanged(self):
        code, _, body = self.request("/api/questions/gate-V5-PWA/answer", {"answer": "לתזמן להיום"})
        self.assertEqual(code, 200)
        self.assertEqual((body["result"]["channel"], body["result"]["answered_by"]), ("dashboard", "owner (dashboard session)"))
        self.assertEqual(self.request("/api/questions/gate-V5-PWA/discuss", {})[0], 400)
        self.assertEqual(self.launcher.calls, [])

    def test_child_env_drops_workflow_variables_and_the_token(self):
        env = discuss.child_env({"PATH": "x", "JARVES_WORKFLOW_TOKEN": "t", "OTHER": "has abc123 inside"}, token="abc123")
        self.assertEqual(env, {"PATH": "x", "JARVES_WORKFLOW_AGENT": "1", "JARVES_WORKFLOW_HELPER": "1"})


class ConsoleStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_help_button_and_copy_fallback_on_every_open_card(self):
        self.assertIn('data-discuss="${esc(id)}"', self.js)
        self.assertIn('data-discuss-copy="${esc(id)}"', self.js)
        self.assertIn("/api/questions/${encodeURIComponent(id)}/discuss`, {launch}", self.js)
        self.assertEqual(self.js.count("${discussHTML("), 2)      # single card and group card
        self.assertIn("if (said) toast(said", self.js)            # one polite live region (#toast) announces it
        self.assertIn("ממתין, שום דבר לא זז.", self.js)            # no default: silence approves nothing
        self.assertIn('<bdi class="mono" dir="ltr">${esc(id)}</bdi>', self.js)
        self.assertNotRegex(self.js, r"\bonclick=|javascript:")  # CSP-safe: listeners only

    def test_group_card_answers_each_ticked_question_through_the_normal_endpoint(self):
        self.assertIn("function questionUnits(pending)", self.js)
        self.assertIn('/${deferOnly ? "defer" : "answer"}`', self.js)
        self.assertIn('type="checkbox" name="item"', self.js)

    def test_locale_labels_exist_in_both_languages(self):
        block = self.js[self.js.index("const I18N = {"):self.js.index("const L = I18N")]
        for key in ("discuss:", "discussFallback:", "discussCopy:", "groupTitle:", "groupSubmit:"):
            self.assertEqual(block.count(key), 2, key)
        self.assertIn('discuss: "עזרה מ־Claude"', block)

    def test_mobile_styles(self):
        self.assertIn(".discuss-cmd{", self.css)
        self.assertRegex(self.css, re.compile(r"@media\(max-width:760px\)\{\.discuss-open"))


if __name__ == "__main__":
    unittest.main()
