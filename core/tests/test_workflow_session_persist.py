"""#789 item 2: a dashboard login survives a server restart; only keyed digests are stored."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarves.workflow import server as srv
from jarves.workflow.server import Server


class SessionPersistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.server = None

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def start(self):
        self.server = Server(self.home, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.thread.join(5)
            self.server.server_close()
            self.server = None

    def request(self, path, data=None, cookie=""):
        h = {"Cookie": cookie, "Origin": self.server.origin}
        if data is not None:
            h.update({"Content-Type": "application/json", "X-Workflow-Request": "1"})
        req = urllib.request.Request(self.server.origin + path,
                                     data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            response.read()
            return response.code, dict(response.headers)

    def login(self):
        status, headers = self.request("/api/session", {"token": self.server.token})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";", 1)[0]

    def restart_same_port(self):
        """A new server process on a new port gets a new cookie name; rebuild the cookie for it."""
        self.stop()
        self.start()

    def test_session_survives_restart_and_file_has_no_secret(self):
        self.start()
        cookie = self.login()
        session = cookie.split("=", 1)[1]
        token = self.server.token
        stored = (self.home / "sessions.json").read_text(encoding="utf-8")
        self.assertNotIn(session, stored)
        self.assertNotIn(token, stored)
        self.assertIn(srv.session_key(token, session), stored)
        self.restart_same_port()
        name = self.server.cookie
        self.assertEqual(self.request("/api/state", cookie=f"{name}={session}")[0], 200)
        self.assertEqual(self.request("/api/state", cookie=f"{name}={'0' * 64}")[0], 401)

    def test_rotated_access_token_invalidates_stored_sessions(self):
        self.start()
        session = self.login().split("=", 1)[1]
        self.stop()
        (self.home / "access.token").unlink()
        self.start()
        self.assertEqual(self.request("/api/state", cookie=f"{self.server.cookie}={session}")[0], 401)

    def test_expired_damaged_or_future_rows_are_ignored(self):
        now = time.time()
        good, old, far = "a" * 64, "b" * 64, "c" * 64
        (self.home / "sessions.json").write_text(json.dumps({"sessions": {
            good: now + 60, old: now - 1, far: now + 10 * srv.SESSION_TTL_S, "nothex": now + 60}}), encoding="utf-8")
        self.assertEqual(set(srv.load_sessions(self.home, now)), {good})
        (self.home / "sessions.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(srv.load_sessions(self.home, now), {})

    def test_full_store_after_restart_does_not_lock_owner_out(self):
        now = time.time()
        rows = {f"{i:064x}": now + 100 + i for i in range(srv.MAX_SESSIONS)}
        (self.home / "sessions.json").write_text(json.dumps({"sessions": rows}), encoding="utf-8")
        self.start()
        self.login()  # the oldest stored session gives way instead of a 429
        kept = srv.load_sessions(self.home)
        self.assertEqual(len(kept), srv.MAX_SESSIONS)
        self.assertNotIn(f"{0:064x}", kept)

    def test_console_stale_banner_and_timeout_static_contract(self):
        app = (srv.STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("controller.abort(), 8000", app)
        self.assertIn("L.lastGood(", app[app.index("function connectionState"):app.index("async function refresh()")])
        self.assertIn("הנתונים הטובים האחרונים התקבלו לפני", app)
        self.assertIn("error instanceof TypeError ? L.unreachable", app)  # no raw English "Failed to fetch"


if __name__ == "__main__":
    unittest.main()
