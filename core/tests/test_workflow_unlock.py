"""#750: the private link alone is read-only; owner answers need the terminal code (unlock)."""
import hmac
import io
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jarves.workflow import __main__ as cli
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from jarves.workflow import autopilot, unlock
from jarves.workflow.server import Server


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
        self.assertFalse(unlock.consume(self.home, code, key=self.server.unlock_key))
        self.assertFalse((self.home / unlock.FILE).exists())
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        self.assertEqual([self.request("/api/unlock", {"code": wrong})[0] for _ in range(5)], [403] * 5)
        self.assertFalse((self.home / unlock.FILE).exists())          # 5 wrong tries cancel the code
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 429)
        self.server.unlock_attempts.clear()
        unlock.issue(self.home)
        text = (self.home / unlock.FILE).read_text(encoding="ascii")
        self.assertNotIn(self.server.token, text)
        self.assertNotRegex(text, r'"\d{4}"')

    def test_wrong_tries_count_across_all_sessions_and_cancel_the_code(self):
        shown = unlock.issue(self.home)
        wrong = "0000" if shown != "0000" else "1111"
        statuses = []
        for _ in range(5):                                             # a different session for every try
            self.cookie = ""
            self.login()
            statuses.append(self.request("/api/unlock", {"code": wrong})[0])
        self.assertEqual(statuses, [403] * 5)
        self.assertFalse((self.home / unlock.FILE).exists())          # cancelled after 5 in total
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 429)  # shared 5-per-minute limit
        self.server.unlock_attempts.clear()
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 403)

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

    def test_750_home_files_alone_cannot_check_a_code_offline(self):
        # #750: an agent that reads every file in the workflow home (access.token, unlock.json, ...) must not be able
        # to test the 10 000 codes offline; only the running server's in-memory key can check one.
        shown = unlock.issue(self.home)
        copy = Path(self.temp.name + "-agent-copy")
        __import__("shutil").copytree(self.home, copy, ignore=lambda d, names: [n for n in names if n.startswith("workflow.db")])
        self.addCleanup(__import__("shutil").rmtree, copy, True)
        self.assertFalse(unlock.consume(copy, shown))
        self.assertFalse(unlock.consume(copy, shown, key=X25519PrivateKey.generate()))  # any other private key fails
        record = json.loads((copy / unlock.FILE).read_text(encoding="ascii"))
        want, expires = bytes.fromhex(record["hash"]), record["expires"]
        for readable in (bytes.fromhex((copy / unlock.PUB).read_text(encoding="ascii")), bytes.fromhex(record["salt"])):
            self.assertFalse(hmac.compare_digest(unlock._mac(readable, shown, expires), want))  # not MACed under disk bytes
        self.assertTrue((self.home / unlock.FILE).exists())
        self.login()
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 200)

    def test_750_code_needs_a_running_server_key(self):
        (self.home / unlock.PUB).unlink()
        with self.assertRaises(FileNotFoundError):
            unlock.issue(self.home)
        err = io.StringIO()
        with patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": ""}), patch.object(cli, "owner_only_guard", return_value="terminal"), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "unlock"]), 1)
        self.assertIn("start the dashboard first", err.getvalue())
        self.assertFalse((self.home / unlock.FILE).exists())
        (self.home / unlock.PUB).write_text("not a key", encoding="ascii")
        with self.assertRaises(ValueError):
            unlock.issue(self.home)

    def test_750_stopped_server_removes_its_key_so_unlock_refuses(self):
        unlock.release_key(self.home, X25519PrivateKey.generate())   # someone else's key: the file stays
        self.assertTrue((self.home / unlock.PUB).exists())
        self.server.shutdown()
        self.server.server_close()
        self.assertFalse((self.home / unlock.PUB).exists())
        with self.assertRaises(FileNotFoundError):
            unlock.issue(self.home)

    def test_750_second_server_warns_and_busy_port_keeps_the_live_key(self):
        live = (self.home / unlock.PUB).read_text(encoding="ascii")
        with self.assertRaises(OSError):
            Server(self.home, self.server.server_port)              # bind fails before any key file is touched
        self.assertEqual((self.home / unlock.PUB).read_text(encoding="ascii"), live)
        with self.assertLogs("jarves.workflow.unlock", "WARNING") as logs:
            other = Server(self.home, 0)
        self.assertIn("another dashboard server", logs.output[0])
        self.assertNotEqual((self.home / unlock.PUB).read_text(encoding="ascii"), live)
        other.server_close()
        self.assertFalse((self.home / unlock.PUB).exists())          # it removes only its own key

    def test_750_open_shows_a_code_only_for_a_listening_server_and_it_is_accepted(self):
        def run_open(port):
            err = io.StringIO()
            with patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": ""}), patch.object(cli, "owner_only_guard", return_value="terminal"), \
                    patch("jarves.workflow.opener.open_dashboard", return_value=0), redirect_stdout(io.StringIO()), redirect_stderr(err):
                cli.main(["--home", str(self.home), "--profile-home", str(self.home), "open", "--no-open", "--port", str(port)])
            return err.getvalue()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        self.assertNotIn("Unlock code", run_open(free))                 # nothing listens there: no stale-key code
        self.assertFalse((self.home / unlock.FILE).exists())
        shown = run_open(self.server.server_port).split(": ")[-1].strip()
        self.login()
        self.assertEqual(self.request("/api/unlock", {"code": shown})[0], 200)

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
