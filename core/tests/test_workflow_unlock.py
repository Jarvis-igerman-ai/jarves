"""#750: the private link alone is read-only; owner answers need the terminal code (unlock)."""
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jarves.workflow import __main__ as cli
from jarves.workflow import autopilot, unlock
from jarves.workflow.server import Handler, Server, UnlockWrong


class UnlockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.server = Server(self.home, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""
        self.store = self.server.store
        self.store.create_task({"id": "t", "title": "T", "lane": "bugs"})
        self.q = self.store.ask({"task_id": "t", "title": "Which?"})["id"]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None):
        h = {"Cookie": self.cookie, "Origin": self.server.origin}
        if data is not None:
            h.update({"Content-Type": "application/json", "X-Workflow-Request": "1"})
        req = urllib.request.Request(self.server.origin + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.code, dict(response.headers), json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw

    def login(self):
        status, headers, _ = self.request("/api/session", {"token": self.server.token})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        return self.cookie.split("=", 1)[1]

    def test_locked_session_reads_but_cannot_answer_defer_reopen_or_discuss(self):
        self.login()
        status, _, state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["unlock"]["remaining_s"], 0)
        for action, body in (("answer", {"answer": "A"}), ("defer", {}), ("reopen", {}), ("discuss", {"launch": False})):
            code, _, data = self.request(f"/api/questions/{self.q}/{action}", body)
            self.assertEqual(code, 403, action)
            self.assertTrue(data["locked"], action)
        self.assertEqual(self.store.snapshot()["questions"][0]["status"], "pending")

    def test_locked_session_cannot_confirm_a_forged_held_answer(self):
        self.store.answer(self.q, "forged")
        self.login()
        code, _, data = self.request(f"/api/questions/{self.q}/confirm", {})
        self.assertEqual(code, 403)
        self.assertTrue(data["locked"])
        q = self.store.snapshot()["questions"][0]
        self.assertTrue(q["held"])
        task = [t for t in self.store.snapshot()["tasks"] if t["id"] == "t"][0]
        self.assertEqual(task["state"], "waiting_owner")

    def test_ask_more_and_followups_are_locked_until_unlock_then_work(self):
        self.login()
        for path in (f"/api/questions/{self.q}/ask-more", "/api/followups"):
            code, _, data = self.request(path, {"text": "More please"})
            self.assertEqual(code, 403, path)
            self.assertTrue(data["locked"], path)
        self.assertEqual(self.store.snapshot().get("followups", []), [])
        self.assertEqual(self.request("/api/unlock", {"code": unlock.issue(self.home)})[0], 200)
        for path in (f"/api/questions/{self.q}/ask-more", "/api/followups"):
            self.assertEqual(self.request(path, {"text": "More please"})[0], 200, path)
        self.assertEqual(len(self.store.snapshot()["followups"]), 2)

    def test_correct_code_unlocks_only_that_session_once_and_default_is_1_week(self):
        sid = self.login()
        shown = unlock.issue(self.home)
        self.assertRegex(shown, r"^\d{4}$")
        self.assertEqual(self.request("/api/unlock", {"code": "0000" if shown != "0000" else "1111"})[0], 403)
        code, _, data = self.request("/api/unlock", {"code": shown})
        self.assertEqual(code, 200)
        self.assertEqual(data["result"]["remaining_s"], 7 * 86400)
        self.assertEqual(self.request("/api/state")[2]["unlock"]["remaining_s"] // 3600, 167)
        self.assertEqual(self.request(f"/api/questions/{self.q}/answer", {"answer": "A"})[0], 200)
        self.assertFalse((self.home / unlock.FILE).exists())          # single use
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 403)
        self.assertIn(sid, self.server.unlocked)
        self.cookie = ""
        other = self.login()                                           # a second session stays locked
        self.assertNotEqual(sid, other)
        self.assertEqual(self.request(f"/api/questions/{self.q}/reopen", {})[0], 403)

    def test_unlock_expires_and_policy_value_applies(self):
        sid = self.login()
        self.server.unlocked[sid] = __import__("time").time() - 1
        self.assertEqual(self.request(f"/api/questions/{self.q}/defer", {})[0], 403)
        self.assertEqual(self.request("/api/state")[2]["unlock"]["remaining_s"], 0)
        self.assertEqual(autopilot.owner_unlock_s(self.home), 7 * 86400)
        policy = autopilot.load(self.home)[0]
        policy["watchdog"]["owner_unlock_s"] = 600
        autopilot.save(self.home, policy)
        unlock.issue(self.home)
        code = unlock.issue(self.home)
        self.assertEqual(self.request("/api/unlock", {"code": code})[2]["result"]["remaining_s"], 600)

    def test_session_lives_as_long_as_the_unlock_window(self):
        sid = self.login()
        code, headers, data = self.request("/api/unlock", {"code": unlock.issue(self.home)})
        self.assertEqual(code, 200)
        self.assertGreaterEqual(self.server.sessions[sid], self.server.unlocked[sid])
        self.assertGreater(self.server.sessions[sid] - __import__("time").time(), 7 * 86400 - 60)
        self.assertIn("Max-Age=604800", headers["Set-Cookie"])
        self.assertEqual(len(self.server.sessions), 1)

    def test_cli_unlock_prints_hours_when_under_a_day(self):
        policy = autopilot.load(self.home)[0]
        policy["watchdog"]["owner_unlock_s"] = 6 * 3600
        autopilot.save(self.home, policy)
        out = io.StringIO()
        with patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": ""}), patch.object(cli, "owner_only_guard", return_value="terminal"), redirect_stdout(out):
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "unlock"]), 0)
        self.assertIn("unlocked for 6 hours", out.getvalue())
        self.assertNotIn("0 days", out.getvalue())

    def test_unlock_limit_is_one_week(self):
        policy = autopilot.load(self.home)[0]
        policy["watchdog"]["owner_unlock_s"] = 7 * 86400
        autopilot.validate(policy)
        policy["watchdog"]["owner_unlock_s"] = 7 * 86400 + 1
        with self.assertRaises(ValueError):
            autopilot.validate(policy)

    def test_code_expiry_attempt_limit_and_no_token_leak(self):
        self.login()
        code = unlock.issue(self.home, now=1.0)          # issued long ago
        self.assertFalse(unlock.consume(self.home, code))
        self.assertFalse((self.home / unlock.FILE).exists())
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        self.assertEqual([self.request("/api/unlock", {"code": wrong})[0] for _ in range(5)], [403] * 5)
        self.assertTrue((self.home / unlock.FILE).exists())           # #1265: one session cannot cancel the code
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 429)
        self.server.unlock_attempts.clear()
        unlock.issue(self.home)
        text = (self.home / unlock.FILE).read_text(encoding="ascii")
        self.assertNotIn(self.server.token, text)
        self.assertNotRegex(text, r'"\d{4}"')

    def wrong_try(self, code):
        self.server.unlock_attempts.clear()                            # keep the 5-per-minute limit out of the way
        status, _, data = self.request("/api/unlock", {"code": code})
        return status, data.get("reason")

    def test_one_session_cannot_cancel_the_owner_code_1265(self):
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        self.login()                                                   # the owner's own window, already open
        owner = self.cookie
        self.cookie = ""
        self.login()                                                   # the read-only token holder's one window
        results = [self.wrong_try(wrong) for _ in range(6)]
        self.assertEqual(results, [(403, None)] * 3 + [(403, "session_exhausted")] * 3)
        self.assertEqual(self.wrong_try(shown), (403, "session_exhausted"))  # no 4th guess, even a right one
        self.assertIn("another window", self.request("/api/unlock", {"code": shown})[2]["error"])  # names both ways out
        self.assertTrue((self.home / unlock.FILE).exists())           # the owner's live code survives
        self.cookie = owner                                            # #1416: a window opened before the wrong tries
        self.server.unlock_attempts.clear()
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 200)

    def test_login_after_a_wrong_try_cannot_spend_the_shared_tries_1416(self):
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        self.login()                                                   # the owner's window, open before the code
        owner = self.cookie
        self.cookie = ""
        self.login()                                                   # token holder's window A
        self.assertEqual([self.wrong_try(wrong) for _ in range(3)], [(403, None)] * 3)
        self.cookie = ""
        self.login()                                                   # log in again for a fresh window B
        self.assertEqual([self.wrong_try(wrong) for _ in range(2)], [(403, "session_too_new")] * 2)
        self.assertEqual(self.wrong_try(shown), (403, "session_too_new"))   # no guess at all, even a right one
        self.assertEqual(self.server.unlock_wrong.total, 3)            # B's refusals use none of the shared tries
        self.assertTrue((self.home / unlock.FILE).exists())           # the owner's live code survives 3 + 2
        self.cookie = owner
        self.assertEqual(self.wrong_try(shown), (200, None))
        shown = unlock.issue(self.home)                                # a new code: B may guess it
        self.cookie = ""
        self.login()
        self.assertEqual(self.wrong_try(shown), (200, None))

    def test_restored_session_keeps_its_tries_after_a_later_login_1416(self):
        sid = self.login()                                             # the owner's window, stored for a restart
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.server = Server(self.home, 0)                             # restart: the window is restored, login 0
        self.store = self.server.store
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        restored = f"{self.server.cookie}={sid}"
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        self.cookie = ""
        self.login()                                                   # a fresh login after the restart
        self.assertEqual(self.wrong_try(wrong), (403, None))           # its wrong try starts the rule
        self.cookie = restored
        self.assertEqual([self.wrong_try(wrong) for _ in range(2)], [(403, None)] * 2)   # counted, not too new
        self.assertEqual(self.server.unlock_wrong.by_session.get(sid), 2)
        self.assertEqual(self.wrong_try(shown), (200, None))

    def test_wrong_tries_over_many_sessions_cancel_the_code_at_the_backstop(self):
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        statuses = []
        windows = []
        for _ in range(2):                                             # windows open before the first wrong try
            self.cookie = ""
            self.login()
            windows.append(self.cookie)
        for i in range(5):                                             # many sessions give no extra guesses past 5
            if i % 3 == 0:
                self.cookie = windows[i // 3]
            statuses.append(self.wrong_try(wrong)[0])
            self.assertEqual((self.home / unlock.FILE).exists(), i < 4, i)
            if i == 2:
                exhausted = self.cookie                                # this window used all 3 of its tries
        self.assertEqual(statuses, [403] * 5)
        # The backstop itself cleared every count (checked before any new code, whose new salt would also reset them).
        self.assertEqual(self.server.unlock_wrong, UnlockWrong())
        self.assertEqual(self.wrong_try(shown), (403, None))           # cancelled after 5 in total (guess bound unchanged)
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        self.cookie = exhausted                                        # a new code gives the exhausted window its 3 tries
        self.assertEqual([self.wrong_try(wrong) for _ in range(3)], [(403, None)] * 3)

    def test_wrong_tries_without_a_live_code_are_not_counted(self):
        self.login()
        self.assertFalse((self.home / unlock.FILE).exists())
        self.assertEqual([self.wrong_try("1234") for _ in range(4)], [(403, None)] * 4)
        self.assertEqual((self.server.unlock_wrong.total, self.server.unlock_wrong.by_session), (0, {}))

    def test_exhausted_window_refusals_count_against_the_shared_minute_limit(self):
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        self.login()
        self.server.unlock_attempts.clear()
        tries = [self.request("/api/unlock", {"code": wrong})[2].get("reason") for _ in range(5)]
        self.assertEqual(tries, [None] * 3 + ["session_exhausted"] * 2)
        self.cookie = ""
        self.login()                                                   # a fresh window in the same minute
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 429)
        self.assertTrue((self.home / unlock.FILE).exists())

    def test_unlock_without_a_live_session_is_refused_before_counting(self):
        unlock.issue(self.home)
        self.cookie = "jarves_bogus=nope"
        self.assertEqual(self.request("/api/unlock", {"code": "0000"})[0], 401)   # no or bogus cookie
        # The session ends between the auth check and _unlock: the try is refused, not counted under "".
        with patch.object(Handler, "_authenticated", return_value=True):
            self.assertEqual(self.request("/api/unlock", {"code": "0000"})[0], 401)
        self.assertEqual((self.server.unlock_wrong.total, self.server.unlock_wrong.by_session), (0, {}))
        self.assertEqual(self.server.unlock_attempts, [])

    def test_new_code_resets_an_exhausted_session(self):
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        self.login()
        for _ in range(3):
            self.wrong_try(wrong)
        self.assertEqual(self.wrong_try(shown), (403, "session_exhausted"))
        self.server.unlock_attempts.clear()
        self.assertEqual(self.request("/api/unlock", {"code": unlock.issue(self.home)})[0], 200)

    def test_two_simultaneous_correct_requests_unlock_once(self):
        self.login()
        shown = unlock.issue(self.home)
        results = []
        def go():
            results.append(self.request("/api/unlock", {"code": shown})[0])
        threads = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(sorted(results), [200, 403])
        self.assertEqual(len(self.server.unlocked), 1)

    def test_unlock_needs_a_session(self):
        unlock.issue(self.home)
        self.assertEqual(self.request("/api/unlock", {"code": "1234"})[0], 401)

    def test_cli_unlock_refuses_agents_and_non_terminals_and_open_prints_no_code(self):
        argv = ["--home", str(self.home), "--profile-home", str(self.home), "unlock"]
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": "1"}), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.main(argv), 3)
        self.assertFalse((self.home / unlock.FILE).exists())
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": ""}), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.main(argv), 3)            # stdin/stdout are not terminals under test
        self.assertFalse((self.home / unlock.FILE).exists())
        with patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": "1"}), redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            cli.main(["--home", str(self.home), "--profile-home", str(self.home), "open", "--no-open"])
        self.assertNotIn("Unlock code", out.getvalue() + err.getvalue())
        self.assertFalse((self.home / unlock.FILE).exists())

    def test_cli_unlock_in_terminal_shows_code_that_the_server_accepts(self):
        argv = ["--home", str(self.home), "--profile-home", str(self.home), "unlock"]
        out = io.StringIO()
        with patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": ""}), patch.object(cli, "owner_only_guard", return_value="terminal"), redirect_stdout(out):
            self.assertEqual(cli.main(argv), 0)
        shown = out.getvalue().split(": ")[-1].strip()
        self.login()
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 200)
        self.assertNotIn(self.server.token, out.getvalue())

    def test_dashboard_has_unlock_control_in_both_locales(self):
        js = (Path(cli.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        html = (Path(cli.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="unlock-bar"', html)
        self.assertIn("פתח מענה", js)
        self.assertIn("Unlock answers", js)
        self.assertIn('"/api/unlock"', js)


if __name__ == "__main__":
    unittest.main()
