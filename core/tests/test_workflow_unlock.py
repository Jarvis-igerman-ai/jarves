"""#750: the private link alone is read-only; owner answers need the terminal code (unlock)."""
import io
import json
import os
import shutil
import sqlite3
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
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "--port", str(self.server.server_port), "unlock"]), 0)
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

    def test_750_files_in_the_home_are_no_offline_oracle_for_the_code(self):
        """#750: a reader of every home file (token included) must not be able to test codes offline.
        The check needs the dashboard's in-memory key; a copy of the files plus any other key confirms nothing."""
        shown = unlock.issue(self.home)
        record = json.loads((self.home / unlock.FILE).read_text(encoding="ascii"))
        self.assertEqual(set(record), {"eph", "tag", "expires"})       # no salt/hash pair to brute-force
        snapshot = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, snapshot, ignore_errors=True)
        for f in self.home.iterdir():
            try:
                if f.is_file():
                    (snapshot / f.name).write_bytes(f.read_bytes())
            except PermissionError:  # Windows byte-range locks (e.g. an open database); the unlock files are asserted below
                pass
        self.assertTrue({unlock.FILE, unlock.PUB_FILE, "access.token"} <= {p.name for p in snapshot.iterdir()})
        self.assertFalse(unlock.consume(snapshot, shown))                    # no key: nothing to compare with
        self.assertFalse(unlock.consume(snapshot, shown, key=unlock.ServerKey()))  # attacker's own key
        self.assertTrue(unlock.consume(self.home, shown, key=self.server.unlock_key))

    def test_750_issue_needs_a_published_dashboard_key(self):
        (self.home / unlock.PUB_FILE).unlink()
        with self.assertRaises(unlock.UnlockUnavailable):
            unlock.issue(self.home)
        (self.home / unlock.PUB_FILE).write_text('{"pub": "00"}', encoding="ascii")
        with self.assertRaises(unlock.UnlockUnavailable):
            unlock.issue(self.home)
        (self.home / unlock.PUB_FILE).write_text("[]", encoding="ascii")
        with self.assertRaises(unlock.UnlockUnavailable):
            unlock.issue(self.home)
        self.assertFalse((self.home / unlock.FILE).exists())
        argv = ["--home", str(self.home), "--profile-home", str(self.home), "--port", str(self.server.server_port), "unlock"]
        with patch.object(cli, "owner_only_guard", lambda action: None), \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(argv), cli.UNLOCK_UNAVAILABLE_EXIT)
        self.assertIn("restart the dashboard", err.getvalue())
        self.assertNotIn("Unlock code", out.getvalue())
        with patch.object(cli, "owner_only_guard", lambda action: None), \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            cli.main(["--home", str(self.home), "--profile-home", str(self.home), "--port", str(self.server.server_port), "open", "--no-open"])
        self.assertIn("No unlock code yet", err.getvalue())
        self.assertFalse((self.home / unlock.FILE).exists())

    def test_750_a_failed_second_start_keeps_the_live_key(self):
        before = (self.home / unlock.PUB_FILE).read_bytes()
        with self.assertRaises(OSError):
            Server(self.home, self.server.server_port)               # port taken: never publishes
        self.assertEqual((self.home / unlock.PUB_FILE).read_bytes(), before)

    def test_750_a_failed_key_publish_closes_the_socket(self):
        real_close, closed = Server.server_close, []
        def close(srv):
            closed.append(srv.server_port)
            real_close(srv)
        with patch.object(unlock.ServerKey, "publish", side_effect=OSError("disk full")), \
                patch.object(Server, "server_close", close):
            with self.assertRaises(OSError):
                Server(self.home, 0)
        self.assertEqual(len(closed), 1)

    def cli_owner(self, *args):
        argv = ["--home", str(self.home), "--profile-home", str(self.home), *args]
        with patch.object(cli, "owner_only_guard", lambda action: None), \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_750_open_and_unlock_give_no_code_when_no_dashboard_listens(self):
        """#750: a stopped dashboard withdraws its key; a key left by a crash on a port nobody
        answers gives no (dead) code, from `open` (fresh start, not yet listening) or from `unlock`."""
        port = self.server.server_port
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.assertFalse((self.home / unlock.PUB_FILE).exists())          # clean stop withdraws its own key
        unlock.ServerKey().publish(self.home, port)                       # what a crashed dashboard leaves behind
        rc, out, err = self.cli_owner("--port", str(port), "open", "--no-open")
        self.assertIn("No unlock code yet", err)
        self.assertIn(f"no dashboard is running on port {port}", err)
        self.assertNotIn("Unlock code for", out + err)
        self.assertIn("once it runs, get a code with: jarvis workflow unlock", err)   # not just "start it"
        rc, out, err = self.cli_owner("--port", str(port), "unlock")
        self.assertEqual(rc, cli.UNLOCK_UNAVAILABLE_EXIT)
        self.assertIn(f"no dashboard is running on port {port}", err)
        self.assertFalse((self.home / unlock.FILE).exists())
        self.server = Server(self.home, 0)                                 # new port: no rebind/TIME_WAIT race; tearDown closes it
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.assertEqual(self.cli_owner("--port", str(self.server.server_port), "unlock")[0], 0)

    def test_750_code_only_for_the_dashboard_that_published_the_key(self):
        live = self.server.server_port
        record = json.loads((self.home / unlock.PUB_FILE).read_text(encoding="ascii"))
        self.assertEqual(record["port"], live)
        other_home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other_home, ignore_errors=True)
        other = Server(other_home, 0)                                      # some other listener on another port
        try:
            rc, _, err = self.cli_owner("--port", str(other.server_port), "unlock")
        finally:
            other.server_close()
        self.assertEqual(rc, cli.UNLOCK_UNAVAILABLE_EXIT)
        self.assertIn(f"belongs to the dashboard on port {live}", err)
        self.assertFalse((self.home / unlock.FILE).exists())

    def test_750_unlock_without_port_uses_the_published_dashboard_port(self):
        """#750: no --port means the port the published key records (not 8188), still live-checked."""
        live = self.server.server_port
        self.assertNotEqual(live, cli.DEFAULT_PORT)
        rc, out, err = self.cli_owner("unlock")
        self.assertEqual(rc, 0, err)
        code = out.rsplit(":", 1)[1].strip()
        self.assertTrue(unlock.consume(self.home, code, key=self.server.unlock_key))
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        unlock.ServerKey().publish(self.home, live)                       # stale key whose port nobody answers
        rc, _, err = self.cli_owner("unlock")
        self.assertEqual(rc, cli.UNLOCK_UNAVAILABLE_EXIT)
        self.assertIn(f"no dashboard is running on port {live}", err)
        self.assertFalse((self.home / unlock.FILE).exists())
        self.server = Server(self.home, 0)                                 # tearDown closes it
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def test_750_close_withdraws_only_its_own_key(self):
        second = Server(self.home, 0)                                      # replaces the published key
        mine = (self.home / unlock.PUB_FILE).read_bytes()
        self.server.server_close()                                         # the first one's key is no longer published
        self.assertEqual((self.home / unlock.PUB_FILE).read_bytes(), mine)
        second.server_close()
        self.assertFalse((self.home / unlock.PUB_FILE).exists())

    def test_750_a_boolean_port_in_the_published_key_is_no_port(self):
        """JSON true/false is not a port (bool is an int in Python): no --port must not mean port 1."""
        pub = json.loads((self.home / unlock.PUB_FILE).read_text(encoding="ascii"))["pub"]
        for bad in (True, False):
            (self.home / unlock.PUB_FILE).write_text(json.dumps({"pub": pub, "port": bad}), encoding="ascii")
            with self.assertRaises(unlock.UnlockUnavailable):
                unlock.published_port(self.home)
            with self.assertRaisesRegex(unlock.UnlockUnavailable, "records no port") as ctx:
                unlock.issue(self.home, port=1)
            self.assertNotIn("port None", str(ctx.exception))
        rc, out, err = self.cli_owner("unlock")
        self.assertEqual(rc, cli.UNLOCK_UNAVAILABLE_EXIT)
        self.assertIn("records no port", err)
        self.assertNotIn("port 1", err)

    def test_750_low_order_or_malformed_eph_is_a_counted_wrong_try(self):
        """A record whose eph is all-zero (low order) or the wrong length never unlocks; the server counts the try."""
        for eph in ("00" * 32, "00" * 31):
            (self.home / unlock.FILE).write_text(json.dumps({"eph": eph, "tag": "00" * 32, "expires": 4e9}), encoding="ascii")
            self.assertFalse(unlock.consume(self.home, "0000", key=self.server.unlock_key, now=1000.0))
            self.assertTrue((self.home / unlock.FILE).exists())          # not consumed by the bad record
        session = self.login()
        status, _, body = self.request("/api/unlock", {"code": "0000"})
        self.assertEqual((status, body.get("locked")), (403, True))
        self.assertEqual((self.server.unlock_wrong.total, self.server.unlock_wrong.by_session), (1, {session: 1}))

    def test_750_server_close_twice_closes_once(self):
        closes = []
        with patch.object(type(self.store), "close", lambda store: closes.append(store)), \
                patch.object(unlock.ServerKey, "withdraw", lambda key, home: closes.append(key)):
            self.server.shutdown()
            self.thread.join(5)
            self.server.server_close()
            self.server.server_close()                                     # tearDown closes it again, too
        self.assertEqual(closes, [self.server.unlock_key, self.store])
        self.store.close()                                                 # the real close the patch replaced

    def test_750_unlock_exit_code_differs_from_usage_errors_and_is_in_help(self):
        self.assertNotIn(cli.UNLOCK_UNAVAILABLE_EXIT, (0, 2, 3))
        with redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.assertIn(f"{cli.UNLOCK_UNAVAILABLE_EXIT} no running dashboard key", out.getvalue())

    def test_750_cryptography_is_a_declared_core_dependency(self):
        """unlock imports cryptography at module top; it must stay a declared core dependency (>= 40 for raw keys)."""
        import tomllib
        deps = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        spec = [d for d in deps if d.split(">")[0].split("=")[0].strip().lower() == "cryptography"]
        self.assertEqual(len(spec), 1, deps)
        self.assertGreaterEqual(int(spec[0].split(">=")[1].split(",")[0].split(".")[0]), 40)

    def test_750_close_after_stopping_was_set_still_closes(self):
        """`stopping` is also the stream-stop event: a path that sets it first must not turn close into a no-op."""
        self.server.shutdown()
        self.thread.join(5)
        self.server.stopping.set()
        self.server.server_close()
        self.assertFalse((self.home / unlock.PUB_FILE).exists())          # key withdrawn
        with self.assertRaises(sqlite3.ProgrammingError):
            self.store.create_task({"id": "u", "title": "U", "lane": "bugs"})  # store closed

    def test_750_code_record_is_replaced_atomically(self):
        """unlock.json is replaced whole (temp file + os.replace): a failed write keeps the previous record."""
        unlock.issue(self.home)
        before = (self.home / unlock.FILE).read_bytes()
        with patch.object(unlock.os, "replace", side_effect=PermissionError("held")), \
                patch.object(unlock.time, "sleep", lambda s: None), self.assertRaises(PermissionError):
            unlock.issue(self.home)
        self.assertEqual((self.home / unlock.FILE).read_bytes(), before)
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.endswith(".tmp")], [])

    def test_750_key_publish_retries_a_briefly_locked_target_then_gives_up(self):
        real, calls = os.replace, []
        def flaky(src, dst):
            calls.append(dst)
            if len(calls) < 3:
                raise PermissionError("reader holds the file")
            real(src, dst)
        key = unlock.ServerKey()
        with patch.object(unlock.os, "replace", flaky), patch.object(unlock.time, "sleep", lambda s: None):
            key.publish(self.home, 1234)
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads((self.home / unlock.PUB_FILE).read_text(encoding="ascii"))["pub"], key.pub.hex())
        with patch.object(unlock.os, "replace", side_effect=PermissionError("held")), \
                patch.object(unlock.time, "sleep", lambda s: None), self.assertRaises(PermissionError):
            key.publish(self.home, 1234)
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.endswith(".tmp")], [])

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
        argv = ["--home", str(self.home), "--profile-home", str(self.home), "--port", str(self.server.server_port), "unlock"]
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
