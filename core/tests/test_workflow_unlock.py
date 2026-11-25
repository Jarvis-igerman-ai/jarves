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
        self.login()                                                   # the read-only token holder's one window
        results = [self.wrong_try(wrong) for _ in range(6)]
        self.assertEqual(results, [(403, None)] * 3 + [(403, "session_exhausted")] * 3)
        self.assertEqual(self.wrong_try(shown), (403, "session_exhausted"))  # no 4th guess, even a right one
        self.assertIn("another window", self.request("/api/unlock", {"code": shown})[2]["error"])  # names both ways out
        self.assertTrue((self.home / unlock.FILE).exists())           # the owner's live code survives
        self.cookie = ""
        self.login()                                                   # the owner's own window
        self.server.unlock_attempts.clear()
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 200)

    def test_wrong_tries_over_many_sessions_cancel_the_code_at_the_backstop(self):
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        statuses = []
        for i in range(5):                                             # new sessions give no extra guesses past 5
            if i % 3 == 0:
                self.cookie = ""
                self.login()
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

    def login_owner(self, owner):
        status, headers, _ = self.request("/api/session", {"token": self.server.token, "owner": owner})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        return self.cookie

    def test_token_holder_windows_cannot_cancel_the_code_open_bound_to_the_owner_window_1416(self):
        shown = unlock.issue(self.home, owner="nonce-1416")
        wrong = "0000" if shown != "0000" else "1111"
        self.assertNotIn("nonce-1416", (self.home / unlock.FILE).read_text(encoding="ascii"))  # only its digest
        owner_cookie = self.login_owner("nonce-1416")                  # the window 'jarvis workflow open' launched
        self.cookie = ""
        self.login()                                                   # token holder, window A
        self.assertEqual([self.wrong_try(wrong) for _ in range(3)], [(403, None)] * 2 + [(403, "owner_window_only")])
        self.cookie = ""
        self.login()                                                   # token holder, window B: no fresh tries
        self.assertEqual([self.wrong_try(wrong) for _ in range(3)], [(403, "owner_window_only")] * 3)
        self.assertEqual(self.wrong_try(shown), (403, "owner_window_only"))  # not even a right guess
        self.assertTrue((self.home / unlock.FILE).exists())           # the owner's code survives the 3 + 2 path
        self.assertLessEqual(self.server.unlock_wrong.total, 5)
        self.cookie = owner_cookie
        self.assertEqual(self.wrong_try(wrong), (403, None))           # the owner keeps typo room
        self.server.unlock_attempts.clear()
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 200)

    def test_owner_value_binds_once_and_only_to_the_live_code_1416(self):
        shown = unlock.issue(self.home, owner="nonce-a")
        wrong = "0000" if shown != "0000" else "1111"
        self.cookie = ""
        self.login_owner("guess")                                      # a wrong owner value binds nothing
        self.assertEqual(self.server.unlock_wrong.owner, "")
        first = self.login_owner("nonce-a")
        bound = self.server.unlock_wrong.owner
        self.login_owner("nonce-a")                                    # a second login with it is an ordinary window
        self.assertEqual(self.server.unlock_wrong.owner, bound)
        self.assertEqual(first.split("=", 1)[1], bound)
        # The owner's own typos still count toward the 5-try total, so the guess bound stays 5 of 10,000.
        self.cookie = first
        self.assertEqual([self.wrong_try(wrong) for _ in range(3)], [(403, None)] * 3)
        self.cookie = ""
        self.login()
        self.assertEqual([self.wrong_try(wrong) for _ in range(2)], [(403, None)] * 2)
        self.assertFalse((self.home / unlock.FILE).exists())          # 3 + 2 = 5: cancelled
        self.assertEqual(unlock.owner_salt(self.home, "nonce-a"), "")
        # A code issued without an owner value (plain 'jarvis workflow unlock') keeps the #1265 rules.
        unlock.issue(self.home)
        self.assertEqual(unlock.owner_salt(self.home, "nonce-a"), "")

    def test_open_issues_the_code_bound_to_the_link_it_launches_1416(self):
        links = []
        out, err = io.StringIO(), io.StringIO()
        with (patch.object(cli, "owner_only_guard", return_value="terminal"),
              patch("jarves.workflow.opener.listening", return_value=True),
              patch("webbrowser.open", side_effect=lambda url: links.append(url) or True),
              redirect_stdout(out), redirect_stderr(err)):
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "open"]), 0)
        self.assertEqual(len(links), 1)
        owner = links[0].split("&owner=", 1)[1]
        self.assertTrue(unlock.owner_salt(self.home, owner))
        self.assertNotIn(owner, out.getvalue() + err.getvalue())       # the owner value is not printed
        self.assertIn("Unlock code", err.getvalue())

    def test_open_no_open_prints_no_owner_value_and_binds_nothing_1416(self):
        out, err = io.StringIO(), io.StringIO()
        with (patch.object(cli, "owner_only_guard", return_value="terminal"),
              patch("jarves.workflow.opener.listening", return_value=True),
              patch("webbrowser.open", side_effect=AssertionError("--no-open must not launch a browser")),
              redirect_stdout(out), redirect_stderr(err)):
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "open", "--no-open"]), 0)
        self.assertIn("#token=", out.getvalue())
        self.assertNotIn("owner=", out.getvalue() + err.getvalue())   # a printed link never carries the owner value
        self.assertIn("Unlock code", err.getvalue())
        record = json.loads((self.home / unlock.FILE).read_text(encoding="ascii"))
        self.assertNotIn("owner", record)                              # no binding: the #1265 rules apply

    def test_printed_link_never_carries_an_owner_value_1416(self):
        from jarves.workflow.opener import open_dashboard
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            open_dashboard(self.home, 1, show_link=True, token=lambda h: "t", probe=lambda p: True, owner="secret-v")
        self.assertNotIn("secret-v", out.getvalue())

    def test_browser_failure_message_never_carries_the_owner_value_1416(self):
        from jarves.workflow.opener import open_dashboard
        def boom(url):
            raise OSError("no browser")
        for browser in (lambda url: False, boom):                      # not started, and launcher error
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = open_dashboard(self.home, 1, show_link=False, token=lambda h: "t", probe=lambda p: True,
                                    browser=browser, owner="secret-v")
            self.assertEqual(rc, 1)
            self.assertNotIn("secret-v", out.getvalue() + err.getvalue())
            self.assertNotIn("#token=", out.getvalue() + err.getvalue())  # no link at all, it points to --no-open

    def test_open_with_the_server_down_issues_no_code_1416(self):
        out, err = io.StringIO(), io.StringIO()
        with (patch.object(cli, "owner_only_guard", return_value="terminal"),
              patch("jarves.workflow.opener.listening", return_value=False),
              patch("webbrowser.open", side_effect=AssertionError("no browser for a down server")),
              redirect_stdout(out), redirect_stderr(err)):
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "open"]), 2)
        self.assertFalse((self.home / unlock.FILE).exists())          # no code replaced for nothing
        self.assertNotIn("Unlock code", err.getvalue())

    def test_open_probes_the_server_once_so_a_failed_open_issues_no_code_1416(self):
        out, err = io.StringIO(), io.StringIO()
        with (patch.object(cli, "owner_only_guard", return_value="terminal"),
              patch("jarves.workflow.opener.listening", side_effect=[True, False]) as probe,  # stops between probes
              patch("webbrowser.open", return_value=True),
              redirect_stdout(out), redirect_stderr(err)):
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "open"]), 0)
        self.assertEqual(probe.call_count, 1)                         # one answer decides both the code and the open
        self.assertIn("Unlock code", err.getvalue())

    def test_owner_compare_never_raises_on_a_non_ascii_binding_1416(self):
        shown = unlock.issue(self.home, owner="nonce-x")
        wrong = "0000" if shown != "0000" else "1111"
        self.login_owner("nonce-x")
        self.server.unlock_wrong.owner = "é" * 64                  # str compare_digest raises TypeError here
        self.cookie = ""
        self.login()
        self.assertEqual(self.wrong_try(wrong), (403, None))           # an ordinary window, not a 500

    def test_a_login_flood_cannot_push_out_the_bound_owner_window_1416(self):
        import time
        from jarves.workflow.server import MAX_SESSIONS
        shown = unlock.issue(self.home, owner="nonce-flood")
        owner_cookie = self.login_owner("nonce-flood")
        bound = self.server.unlock_wrong.owner
        end = time.time() + 3600
        for n in range(MAX_SESSIONS - len(self.server.sessions)):     # a token holder fills every session slot
            self.server.sessions[f"{n:064x}"] = end
        self.server.attempts.clear()
        self.cookie = ""
        self.assertEqual(self.request("/api/session", {"token": self.server.token})[0], 429)  # refused, nobody evicted
        self.assertIn(bound, self.server.sessions)
        self.assertEqual(self.server.unlock_wrong.owner, bound)
        self.cookie = owner_cookie
        self.server.unlock_attempts.clear()
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 200)

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
