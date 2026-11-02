"""#789: read-only 'one dashboard server' check counts the real port listeners, not only the pid record."""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from jarves.workflow import webchild
from jarves.workflow.__main__ import main


def _home() -> Path:
    return Path(tempfile.mkdtemp())


ALIVE = {4242: 100.0}.get


class Census(unittest.TestCase):
    def _census(self, home, pids):
        return webchild.census(home, 8188, table=lambda port: [(p, True) for p in pids], started=ALIVE)

    def test_exactly_one_recorded_server_passes(self):
        home = _home()
        webchild.record(home, 8188, pid=4242, started=ALIVE)
        result = self._census(home, [4242, 4242])  # IPv4 and IPv6 sockets of one process count once
        self.assertEqual((result["ok"], result["state"], result["listeners"]), (True, "one", [4242]))

    def test_orphan_without_record_fails_even_when_the_record_is_fine(self):
        home = _home()
        webchild.record(home, 8188, pid=4242, started=ALIVE)
        result = self._census(home, [4242, 9999])
        self.assertEqual((result["ok"], result["state"]), (False, "several"))

    def test_unrecorded_or_foreign_listener_fails(self):
        home = _home()
        self.assertEqual(self._census(home, [9999])["state"], "foreign")
        webchild.record(home, 8188, pid=4242, started=lambda pid: 1.0)  # pid reused since: record does not match
        self.assertEqual(self._census(home, [4242])["state"], "foreign")

    def test_old_server_still_holding_connections_is_stale(self):
        """Live finding 2 Nov 2026: pid A listened while pid B (no listener) still held accepted connections on 8188."""
        home = _home()
        webchild.record(home, 8188, pid=4242, started=ALIVE)
        result = webchild.census(home, 8188, table=lambda port: [(4242, True), (23784, False), (0, False)], started=ALIVE)
        self.assertEqual((result["ok"], result["state"], result["stale"]), (False, "stale", [23784]))
        self.assertIn("23784", result["detail"])

    def test_none_and_unknown_fail(self):
        home = _home()
        self.assertEqual(self._census(home, [])["state"], "none")
        result = webchild.census(home, 8188, table=lambda port: None, started=ALIVE)
        self.assertEqual((result["ok"], result["state"]), (False, "unknown"))

    def test_cli_prints_one_line_and_exit_code(self):
        home = _home()
        webchild.record(home, 8188, pid=4242, started=ALIVE)
        with mock.patch.object(webchild, "sockets", lambda port: [(4242, True), (4242, False)]), mock.patch.object(webchild, "started_at", ALIVE):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["web-check", "--home", str(home), "--port", "8188"])
            self.assertEqual((rc, out.getvalue().strip()), (0, "PASS one dashboard server (pid 4242, port 8188)"))
        with mock.patch.object(webchild, "sockets", lambda port: [(4242, True), (77, False)]), mock.patch.object(webchild, "started_at", ALIVE):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["web-check", "--home", str(home), "--port", "8188", "--json"])
            self.assertEqual((rc, json.loads(out.getvalue())["state"]), (1, "stale"))


@unittest.skipUnless(sys.platform == "win32" or sys.platform.startswith("linux"), "port table read on Windows/Linux only")
class RealPortTable(unittest.TestCase):
    """Real sockets on a free loopback port: the reproduction of the address-sharing orphans."""

    SHARE = "SO_REUSEPORT" if sys.platform.startswith("linux") else "SO_REUSEADDR"

    def _listen(self, port=0, share=False):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if share:
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, self.SHARE), 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        self.addCleanup(sock.close)
        return sock

    def test_own_listener_is_seen(self):
        port = self._listen().getsockname()[1]
        self.assertEqual({pid for pid, listen in webchild.sockets(port) if listen}, {os.getpid()})
        home = _home()
        webchild.record(home, port)
        self.assertEqual(webchild.census(home, port)["state"], "one")

    def test_second_process_sharing_the_port_is_reported(self):
        if not hasattr(socket, self.SHARE):
            self.skipTest(f"{self.SHARE} unavailable")
        port = self._listen(share=True).getsockname()[1]
        code = (f"import socket,sys,time; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.{self.SHARE}, 1); "
                f"s.bind(('127.0.0.1', {port})); s.listen(1); import os; print(os.getpid(), flush=True); sys.stdin.read()")
        child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True)
        try:
            line = child.stdout.readline().strip()
            if not line.isdigit():
                self.skipTest("this system refused the shared bind")
            other = int(line)  # the real interpreter pid (a venv launcher handle can differ)
            home = _home()
            webchild.record(home, port)
            result = webchild.census(home, port)
            self.assertEqual(result["listeners"], sorted([os.getpid(), other]))
            self.assertEqual((result["ok"], result["state"]), (False, "several"))
        finally:
            child.stdin.close()
            child.wait(timeout=10)
            child.stdout.close()


if __name__ == "__main__":
    unittest.main()
