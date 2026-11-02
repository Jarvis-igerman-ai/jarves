"""One-step dashboard open (#789 item 4): open the private login in the default browser, print nothing secret."""
from __future__ import annotations

import socket
import sys
import webbrowser
from pathlib import Path
from typing import Callable

HOST = "127.0.0.1"


def listening(port: int, host: str = HOST, timeout: float = 1.0) -> bool:
    """True when something accepts TCP connections on the local dashboard port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def open_dashboard(home: Path, port: int, *, show_link: bool, token: Callable[[Path], str],
                   probe: Callable[[int], bool] | None = None,
                   browser: Callable[[str], bool] | None = None) -> int:
    """Open the dashboard. The token link is printed only on explicit request (--no-open).

    Exit codes: 0 opened/printed, 1 no browser could be started, 2 no dashboard server listens.
    """
    probe = probe or listening              # resolved at call time so tests can patch the module
    browser = browser or webbrowser.open
    up = probe(port)
    if show_link:
        # Explicit request (scripts, another device): print the private link. Never share it publicly.
        print(f"http://{HOST}:{port}/#token={token(home)}")
        if not up:
            print(f"Note: no dashboard server answers on port {port} yet. Start it with: jarvis workflow supervise",
                  file=sys.stderr)
        return 0
    if not up:
        # Never open a page that can only spin: say what to do instead.
        print(f"The dashboard server is not running on port {port}. Start it with: jarvis workflow supervise "
              f"(or check with factory-check), then run jarvis workflow open again.")
        return 2
    link = f"http://{HOST}:{port}/#token={token(home)}"
    try:
        opened = bool(browser(link))
    except Exception:  # noqa: BLE001 - any browser launcher failure is reported the same way
        opened = False
    if not opened:
        print("Could not start a browser. To get the private link, run: jarvis workflow open --no-open")
        return 1
    print(f"Dashboard opening in your browser (port {port}). The private link is not printed; "
          f"use --no-open to print it.")
    return 0
