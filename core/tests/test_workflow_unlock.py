"""#750: the private link alone is read-only; owner answers need the terminal code (unlock)."""
import hmac
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from jarves.workflow import __main__ as cli
from jarves.workflow import autopilot, unlock
from jarves.workflow import server as server_mod
from jarves.workflow.server import Server
from jarves.workflow.store import Store


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
        shutil.copytree(self.home, copy, ignore=lambda d, names: [n for n in names if n.startswith("workflow.db")])
        self.addCleanup(shutil.rmtree, copy, True)
        self.assertFalse(unlock.consume(copy, shown))
        self.assertFalse(unlock.consume(copy, shown, key=X25519PrivateKey.generate()))  # any other private key fails
        record = json.loads((copy / unlock.FILE).read_text(encoding="ascii"))
        want, expires = bytes.fromhex(record["hash"]), record["expires"]
        for readable in (bytes.fromhex((copy / unlock.PUB).read_text(encoding="ascii").split()[0]), bytes.fromhex(record["salt"])):
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
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "--port", str(self.server.server_port), "unlock"]), 1)
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

    def unlock_cli(self, port):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"JARVES_WORKFLOW_AGENT": ""}), patch.object(cli, "owner_only_guard", return_value="terminal"), \
                redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--home", str(self.home), "--profile-home", str(self.home), "--port", str(port), "unlock"])
        return rc, out.getvalue(), err.getvalue()

    def test_750_unlock_refuses_a_stale_key_after_a_crash(self):
        with socket.socket() as probe:                                  # unlock.pub is on disk, but nothing listens
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        rc, out, err = self.unlock_cli(free)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("start the dashboard first", err)
        self.assertFalse((self.home / unlock.FILE).exists())

    def test_750_unlock_reports_a_write_error_as_it_is(self):
        with patch.object(unlock, "issue", side_effect=PermissionError("unlock.json: access denied")):
            rc, _, err = self.unlock_cli(self.server.server_port)
        self.assertEqual(rc, 1)
        self.assertIn("could not create the unlock code", err)
        self.assertIn("access denied", err)
        self.assertNotIn("start the dashboard first", err)

    def test_750_key_of_a_server_on_another_port_is_refused(self):
        with socket.socket() as other:                                 # something else answers on this port
            other.bind(("127.0.0.1", 0))
            other.listen()
            rc, out, err = self.unlock_cli(other.getsockname()[1])
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("start the dashboard first", err)
        with self.assertRaisesRegex(FileNotFoundError, f"on port {self.server.server_port}, not"):
            unlock.issue(self.home, port=self.server.server_port + 1)
        self.assertFalse((self.home / unlock.FILE).exists())

    def test_750_unrelated_server_on_the_recorded_port_gets_no_code(self):
        # Review #1304 @d5fa8845 R3: a crashed dashboard left unlock.pub for port P and another program now serves P.
        stale = X25519PrivateKey.generate().public_key().public_bytes_raw().hex()
        (self.home / unlock.PUB).write_text(f"{stale} {self.server.server_port}", encoding="ascii")
        rc, out, err = self.unlock_cli(self.server.server_port)       # it answers /health, but with another key
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("start the dashboard first", err)
        self.assertFalse((self.home / unlock.FILE).exists())

    def test_750_stop_closes_socket_and_store_when_key_cleanup_fails(self):
        # Review #1304 @d5fa8845 R1: release_key's last unlink raising must not leave the socket or workflow.db open.
        other = Server(self.home / "stop", 0)
        closed, real = [], Store.close
        with patch.object(Store, "close", lambda s: (closed.append(s), real(s))), \
                patch.object(Path, "unlink", side_effect=PermissionError("sharing violation")), \
                self.assertLogs("jarves.workflow.unlock", "WARNING") as logs:
            other.server_close()
        self.assertIn("could not remove", logs.output[-1])
        self.assertEqual(closed, [other.store])
        self.assertEqual(other.socket.fileno(), -1)
        with patch.object(server_mod.unlock, "release_key", side_effect=OSError("boom")), \
                patch.object(Store, "close", lambda s: (closed.append(s), real(s))), self.assertRaises(OSError):
            Server(self.home / "stop2", 0).server_close()               # an unexpected raise still closes the store
        self.assertEqual(len(closed), 2)

    def test_750_unlock_without_port_uses_the_server_default(self):
        with patch.object(unlock, "server_answers", return_value=True) as probe, \
                patch.object(unlock, "issue", return_value="1234") as made, patch.object(cli, "owner_only_guard"), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--home", str(self.home), "--profile-home", str(self.home), "unlock"]), 0)
        probe.assert_called_once_with(self.home, 8188)
        self.assertEqual(made.call_args.kwargs["port"], 8188)

    def test_750_open_reports_a_write_error_instead_of_hiding_it(self):
        err = io.StringIO()
        with patch.object(cli, "owner_only_guard"), patch("jarves.workflow.opener.open_dashboard", return_value=0), \
                patch.object(unlock, "issue", side_effect=PermissionError("unlock.json: access denied")), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            cli.main(["--home", str(self.home), "--profile-home", str(self.home), "open", "--no-open", "--port", str(self.server.server_port)])
        self.assertIn("No unlock code: unlock.json: access denied", err.getvalue())

    def test_750_failed_key_write_leaves_no_tmp_file(self):
        with patch.object(unlock.os, "replace", side_effect=PermissionError("sharing violation")), self.assertRaises(PermissionError):
            unlock.server_key(self.home, 1)
        self.assertEqual([p.name for p in self.home.glob("*.tmp")], [])

    def test_750_integer_issue_time_still_checks(self):
        shown = unlock.issue(self.home, now=1000)
        self.assertTrue(unlock.consume(self.home, shown, key=self.server.unlock_key, now=1001))

    def test_750_failed_listen_removes_the_key_it_wrote(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        with patch.object(Server, "server_activate", side_effect=OSError("listen failed")), self.assertRaises(OSError):
            Server(Path(other.name), 0)
        self.assertFalse((Path(other.name) / unlock.PUB).exists())

    def test_750_failed_start_closes_the_store_it_opened(self):
        # Windows CI of #1273: a failed start left workflow.db open, so the home could not be removed (WinError 32).
        closed = []
        real = Store.close
        with patch.object(Store, "close", lambda s: (closed.append(s), real(s))), \
                patch.object(Server, "server_activate", side_effect=OSError("listen failed")), self.assertRaises(OSError):
            Server(self.home / "other", 0)
        self.assertEqual(len(closed), 1)

    def test_750_windows_bind_is_exclusive_so_a_busy_port_cannot_be_shared(self):
        # Review R1: SO_REUSEADDR on Windows would let a 2nd server bind a busy port and replace unlock.pub.
        self.assertEqual(Server.allow_reuse_address, os.name != "nt")
        sock = MagicMock()
        fake = Server.__new__(Server)
        fake.socket = sock
        with patch.object(server_mod.os, "name", "nt"), patch.object(server_mod.socket, "SO_EXCLUSIVEADDRUSE", 4, create=True), \
                patch("http.server.HTTPServer.server_bind"):
            Server.server_bind(fake)
        sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, 4, 1)

    def test_750_release_does_not_remove_a_key_written_during_the_check(self):
        # Review R3: another server replacing unlock.pub between release_key's compare and unlink keeps its key.
        mine = unlock.server_key(self.home / "race", 1)
        theirs = X25519PrivateKey.generate().public_key().public_bytes_raw().hex() + " 2"
        real, raced = Path.read_text, []

        def read_then_replace(path, *a, **k):
            text = real(path, *a, **k)
            if path.name.startswith(unlock.PUB) and not raced:
                raced.append(1)
                (self.home / "race" / "new.tmp").write_text(theirs, encoding="ascii")
                os.replace(self.home / "race" / "new.tmp", self.home / "race" / unlock.PUB)
            return text
        with patch.object(Path, "read_text", read_then_replace):
            unlock.release_key(self.home / "race", mine)
        self.assertEqual(raced, [1])
        self.assertEqual((self.home / "race" / unlock.PUB).read_text(encoding="ascii"), theirs)
        self.assertEqual(sorted(p.name for p in (self.home / "race").iterdir()), [unlock.PUB])

    def test_750_release_without_hard_links_moves_the_other_key_back(self):
        # Review #1304 R2: a failed os.link (no hard links, denied) must not delete the live server's key.
        live = (self.home / unlock.PUB).read_text(encoding="ascii")
        with patch.object(unlock.os, "link", side_effect=PermissionError("no hard links")), \
                self.assertLogs("jarves.workflow.unlock", "WARNING") as logs:
            unlock.release_key(self.home, X25519PrivateKey.generate())
        self.assertIn("moving it back", logs.output[0])
        self.assertEqual((self.home / unlock.PUB).read_text(encoding="ascii"), live)
        self.assertEqual([p.name for p in self.home.glob(unlock.PUB + ".*")], [])

    def test_750_release_retries_a_busy_rename_once_then_warns(self):
        # Review #1304 @36b95648 R2: a Windows sharing violation on rename must not silently leave our key behind.
        real, calls = os.rename, []

        def busy_once(src, dst):
            calls.append(src)
            if len(calls) == 1:
                raise PermissionError("sharing violation")
            return real(src, dst)
        with patch.object(unlock.os, "rename", busy_once), patch.object(unlock.time, "sleep"):
            unlock.release_key(self.home, self.server.unlock_key)
        self.assertEqual(len(calls), 2)
        self.assertFalse((self.home / unlock.PUB).exists())
        with patch.object(unlock.os, "rename", side_effect=PermissionError("sharing violation")), \
                patch.object(unlock.time, "sleep"), self.assertLogs("jarves.workflow.unlock", "WARNING") as logs:
            unlock.release_key(self.home / "none", self.server.unlock_key)
        self.assertIn("could not remove unlock.pub on stop", logs.output[0])

    def test_750_failed_cleanup_keeps_the_start_error_and_closes_the_store(self):
        # Review #1304 @36b95648 R4: release_key raising during cleanup must not hide the listen error or leak the store.
        closed, real = [], Store.close
        with patch.object(Store, "close", lambda s: (closed.append(s), real(s))), \
                patch.object(Server, "server_activate", side_effect=OSError("listen failed")), \
                patch.object(server_mod.unlock, "release_key", side_effect=PermissionError("sharing violation")), \
                self.assertLogs("jarves.workflow.server", "WARNING"), self.assertRaisesRegex(OSError, "listen failed"):
            Server(self.home / "other", 0)
        self.assertEqual(len(closed), 1)

    def test_750_issue_rereads_unlock_pub_moved_away_for_a_moment(self):
        # Review #1304 R3: another server's release_key renames unlock.pub away briefly; issue() must not fail then.
        real, calls = unlock._read_pub, []

        def gap_once(home, name=unlock.PUB):
            calls.append(name)
            if len(calls) == 1:
                raise FileNotFoundError(name)
            return real(home, name)
        with patch.object(unlock, "_read_pub", gap_once), patch.object(unlock.time, "sleep") as pause:
            shown = unlock.issue(self.home, port=self.server.server_port)
        self.assertEqual(len(calls), 2)
        pause.assert_called_once_with(unlock._RELEASE_GAP_S)
        self.assertTrue(unlock.consume(self.home, shown, key=self.server.unlock_key))

    def test_750_cryptography_is_a_locked_runtime_dependency(self):
        # Review #1304 R1: unlock imports cryptography at module level, so it must be a base (non-extra) dependency
        # and pinned in uv.lock, which packaging/build_windows.ps1 bundles into the installer.
        import tomllib
        core = Path(__file__).resolve().parents[1]
        deps = tomllib.loads((core / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        from packaging.requirements import Requirement
        self.assertIn("cryptography", {Requirement(d).name for d in deps})
        lock = tomllib.loads((core / "uv.lock").read_text(encoding="utf-8"))
        self.assertTrue(any(p["name"] == "cryptography" and p.get("version") for p in lock["package"]))

    def test_unlock_needs_a_session(self):
        unlock.issue(self.home)
        self.assertEqual(self.request("/api/unlock", {"code": "1234"})[0], 401)

    def test_cli_unlock_refuses_agents_and_non_terminals_and_open_prints_no_code(self):
        argv = ["--home", str(self.home), "--profile-home", str(self.home), "--port", str(self.server.server_port), "unlock"]
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
