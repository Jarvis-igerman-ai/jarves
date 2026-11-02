"""#789 item 4: `jarvis workflow open` opens the login in one step and prints nothing secret."""
import contextlib
import io
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarves.workflow import __main__ as cli
from jarves.workflow import opener
from jarves.workflow.server import access_token


def _run(home: Path, *args, probe=lambda port: True, browser=None):
    calls = []
    browser = browser or (lambda url: calls.append(url) or True)
    out, err = io.StringIO(), io.StringIO()
    with patch.object(opener, "listening", probe), patch.object(opener.webbrowser, "open", browser), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["--home", str(home), "--profile-home", str(home), "open", *args])
    return code, out.getvalue(), err.getvalue(), calls


class WorkflowOpenTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "wf"

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_opens_browser_and_prints_no_token(self):
        code, out, err, calls = _run(self.home)
        token = access_token(self.home.resolve())
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn(f"#token={token}", calls[0])
        self.assertNotIn(token, out + err)
        self.assertNotIn("#token=", out + err)
        self.assertIn("opening in your browser", out)

    def test_no_open_prints_link_explicitly(self):
        code, out, _, calls = _run(self.home, "--no-open")
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn(f"#token={access_token(self.home.resolve())}", out)

    def test_server_down_never_opens_a_spinning_page(self):
        code, out, err, calls = _run(self.home, probe=lambda port: False)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("jarvis workflow supervise", out)
        self.assertNotIn("#token=", out + err)

    def test_no_open_with_server_down_still_prints_link_and_warns(self):
        code, out, err, _ = _run(self.home, "--no-open", probe=lambda port: False)
        self.assertEqual(code, 0)
        self.assertIn("#token=", out)
        self.assertIn("no dashboard server", err)

    def test_browser_failure_points_to_no_open_without_secret(self):
        def broken(url):
            raise OSError("no browser")
        code, out, err, _ = _run(self.home, browser=broken)
        self.assertEqual(code, 1)
        self.assertIn("--no-open", out)
        self.assertNotIn("#token=", out + err)
        code, out, _, _ = _run(self.home, browser=lambda url: False)
        self.assertEqual(code, 1)
        self.assertNotIn("#token=", out)

    def test_listening_probe_real_socket(self):
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            self.assertTrue(opener.listening(port))
        self.assertFalse(opener.listening(port, timeout=0.2))


if __name__ == "__main__":
    unittest.main()
