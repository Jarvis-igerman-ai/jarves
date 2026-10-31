"""Questions are understandable (#744): plain-language gate questions and a one-click 'Get more info'."""
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
from jarves.workflow.__main__ import main as cli
from jarves.workflow.server import Server
from jarves.workflow.store import Store

STATIC = Path(__file__).resolve().parents[1] / "src/jarves/workflow/static"
INFO_TEXT = ("Explain this question: why am I asked, what each option does, what you recommend, "
             "and what happens if I skip it.")


class GateQuestionTemplate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "w.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def gated(self, ident="g", **extra):
        data = {"id": ident, "title": "Live voice check", "lane": "features", "version": "V5",
                "owner_gate": "מדידת קול חיה במחשב הבעלים ובמסגרת התקציב.", "execution_allowed": False,
                "acceptance": [{"id": "A1", "label": "Run jarvis voice test on your PC"},
                               {"id": "A2", "label": "Say three sentences and rate them"}], **extra}
        return self.store.create_task(data)

    def test_gate_question_is_plain_and_keeps_raw_text_in_details(self):
        self.gated(issue_numbers=[744], scope={"owner_minutes": 10})
        self.assertEqual(aw.gate_questions_step(self.store), ["g"])
        q = next(q for q in self.store.snapshot()["questions"] if q["id"] == "gate-g")
        self.assertEqual(q["kind"], "owner_gate")
        self.assertEqual(q["title"], "Your step is needed for: Live voice check")
        self.assertIn("Task g in V5", q["why"])
        self.assertTrue(q["link"].endswith("/issues/744"))
        self.assertEqual(q["steps"], ["Run jarvis voice test on your PC", "Say three sentences and rate them"])
        self.assertEqual(q["minutes"], 10)
        self.assertIn("stays queued", q["skip_effect"])
        self.assertIn("takes about 10 min", q["recommendation"])
        self.assertEqual(q["details"], "מדידת קול חיה במחשב הבעלים ובמסגרת התקציב.")  # Hebrew kept as is
        self.assertEqual(q["choices"][0], {**aw.GATE_CHOICES[0], "id": "later-today"})
        self.assertTrue(q["choices"][0]["recommended"])
        self.assertTrue(all(c.get("description") for c in q["choices"]))
        self.assertFalse(q["blocks"])
        self.assertEqual(self.store.snapshot()["tasks"][0]["state"], "queued")

    def test_gate_without_acceptance_has_no_invented_steps_and_default_minutes(self):
        self.gated(acceptance=[])
        aw.gate_questions_step(self.store)
        q = self.store.snapshot()["questions"][0]
        self.assertEqual((q["steps"], q["minutes"]), ([], 15))

    def test_old_pending_gate_question_is_rewritten_once_but_not_its_status(self):
        self.gated()
        self.gated("h")
        # Older format: raw gate text as title, no kind.
        for ident in ("g", "h"):
            self.store.ask({"id": f"gate-{ident}", "task_id": ident, "blocks": False, "title": "raw gate",
                            "context": "Task: ...", "choices": [{"id": "later-today", "label": "Schedule (Recommended)"},
                                                                {"id": "tell-me", "label": "Tell me when ready"},
                                                                {"id": "skip-version", "label": "Skip for this version"}]})
        self.store.answer("gate-h", "Tell me when ready")
        self.assertEqual(aw.gate_questions_step(self.store), [])
        by_id = {q["id"]: q for q in self.store.snapshot()["questions"]}
        self.assertEqual(by_id["gate-g"]["kind"], "owner_gate")
        self.assertEqual(by_id["gate-g"]["status"], "pending")
        self.assertEqual(by_id["gate-g"]["details"], "מדידת קול חיה במחשב הבעלים ובמסגרת התקציב.")
        self.assertEqual(by_id["gate-g"]["context"], "")
        self.assertEqual(by_id["gate-h"]["title"], "raw gate")          # answered: untouched
        self.assertEqual(by_id["gate-h"]["answer"], "Tell me when ready")
        self.assertFalse(self.store.refresh_gate_question("gate-g", aw.gate_question(self.store.snapshot()["tasks"][0])))

    def test_refresh_is_limited_to_gate_questions(self):
        self.gated()
        self.store.ask({"id": "q-1", "task_id": "g", "blocks": False, "title": "Other"})
        self.assertFalse(self.store.refresh_gate_question("q-1", {"title": "x"}))
        self.assertEqual(self.store.snapshot()["questions"][0]["title"], "Other")

    def test_explanation_fields_are_validated(self):
        self.gated()
        with self.assertRaises(ValueError):
            self.store.ask({"task_id": "g", "title": "x", "steps": "do it"})
        with self.assertRaises(ValueError):
            self.store.ask({"task_id": "g", "title": "x", "steps": ["a"] * 13})
        with self.assertRaises(ValueError):
            self.store.ask({"task_id": "g", "title": "x", "kind": "approval"})
        q = self.store.ask({"task_id": "g", "title": "x", "blocks": False})
        self.assertEqual((q["steps"], q["kind"], q["skip_effect"], q["details"]), ([], "", "", ""))

    def test_cli_ask_accepts_steps_skip_effect_and_details(self):
        self.gated()
        home = Path(self.temp.name) / "home"
        Store(home / "workflow.db").close()
        out = io.StringIO()
        with redirect_stdout(out):
            cli(["create", "--id", "t", "--title", "T", "--acceptance", "ok", "--home", str(home)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli(["ask", "--task", "t", "--title", "Plug in the phone", "--step", "Connect USB", "--step",
                        "Unlock it", "--skip-effect", "Phone tests wait", "--details", "raw", "--home", str(home)])
        q = json.loads(out.getvalue())
        self.assertEqual((code, q["steps"], q["skip_effect"], q["details"]),
                         (0, ["Connect USB", "Unlock it"], "Phone tests wait", "raw"))


class GetMoreInfoConsole(unittest.TestCase):
    """Static checks of the console: the button, its fixed text, the waiting/idle states and both locales."""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_button_sends_the_fixed_follow_up_through_ask_more(self):
        self.assertIn(json.dumps(INFO_TEXT), self.js)
        self.assertIn('data-more-info="${esc(id)}"', self.js)
        self.assertIn("/api/questions/${encodeURIComponent(infoButton.dataset.moreInfo)}/ask-more`, {text: INFO_TEXT}", self.js)

    def test_waiting_idle_and_reply_states_do_not_overpromise(self):
        self.assertIn("const INFO_IDLE_S = 180;", self.js)
        self.assertIn("Sent, waiting for the manager since ${n} min", self.js)
        self.assertIn("The manager is idle. Switch to the Claude window or wait for the next worker event.", self.js)
        self.assertNotRegex(self.js, re.compile(r"wak(e|ing) (up )?the manager", re.I))

    def test_locales_have_the_same_labels_and_hebrew_has_no_long_english_runs(self):
        block = self.js[self.js.index("const I18N = {"):self.js.index("const L = I18N")]
        he = block[block.index("he: {"):block.index("en: {")]
        en = block[block.index("en: {"):]
        keys = lambda part: set(re.findall(r"(?<![^\s{,])([A-Za-z]+): ", part)) - {"he", "en"}
        self.assertEqual(keys(he), keys(en))
        for text in re.findall(r'"([^"]*)"|`([^`]*)`', he):
            words = " ".join(text)
            self.assertIsNone(re.search(r"\b[A-Za-z]+ [A-Za-z]+ [A-Za-z]+\b", words), words)

    def test_explanation_blocks_and_collapsed_details_fit_a_phone(self):
        for marker in ('class="question-explain"', 'class="question-steps"', '<details class="question-details">'):
            self.assertIn(marker, self.js)
        self.assertIn(".explain-row{grid-template-columns:1fr", self.css)


class GetMoreInfoHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(Path(self.temp.name), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, headers, _ = self.request("/api/session", {"token": self.server.token}, cookie="")
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None, cookie=None):
        h = {"Cookie": self.cookie if cookie is None else cookie, "Origin": self.server.origin}
        if data is not None:
            h.update({"Content-Type": "application/json", "X-Workflow-Request": "1"})
        req = urllib.request.Request(self.server.origin + path,
                                     data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.code, dict(response.headers), json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw

    def test_get_more_info_then_manager_reply_shows_in_the_thread_and_question_stays_open(self):
        store = self.server.store
        store.create_task({"id": "g", "title": "Gate", "lane": "features", "owner_gate": "צריך טלפון",
                           "execution_allowed": False})
        aw.gate_questions_step(store)
        self.assertEqual(self.request("/api/questions/gate-g/ask-more", {"text": INFO_TEXT})[0], 200)
        state = self.request("/api/state")[2]
        item = state["followups"][0]
        self.assertEqual((item["question_id"], item["text"], item["channel"]), ("gate-g", INFO_TEXT, "dashboard"))
        store.reply_followup(item["id"], "You plug in the phone; I recommend today.")
        state = self.request("/api/state")[2]
        self.assertEqual(state["followups"][0]["reply"], "You plug in the phone; I recommend today.")
        self.assertEqual(state["questions"][0]["status"], "pending")
        self.assertEqual(state["questions"][0]["kind"], "owner_gate")


if __name__ == "__main__":
    unittest.main()
