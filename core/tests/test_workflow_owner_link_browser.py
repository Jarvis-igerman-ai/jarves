"""#1416: the real dashboard page forwards the `owner` value from the link fragment to /api/session."""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

import pytest

from jarves.workflow import unlock
from jarves.workflow.server import Server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as temp:
        srv = Server(Path(temp), 0)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            yield srv
        finally:
            srv.shutdown()
            thread.join(5)
            srv.server_close()


def login_body(browser, srv, fragment):
    page = browser.new_page()
    try:
        with page.expect_request(lambda r: r.url.endswith("/api/session") and r.method == "POST") as seen:
            page.goto(f"{srv.origin}/#{fragment}")
        body = seen.value.post_data_json
        page.wait_for_function("location.hash === '#overview'")     # the fragment left the address bar
        return body
    finally:
        page.close()


def test_page_forwards_the_owner_value_and_binds_that_window_1416(browser, server):
    unlock.issue(server.home, owner="nonce-browser")
    body = login_body(browser, server, f"token={server.token}&owner=nonce-browser")
    assert body == {"token": server.token, "owner": "nonce-browser"}
    assert server.unlock_wrong.owner in server.sessions                # that browser window is the bound owner window


def test_page_without_an_owner_value_sends_only_the_token_1416(browser, server):
    unlock.issue(server.home, owner="nonce-browser")
    assert login_body(browser, server, f"token={server.token}") == {"token": server.token}
    assert server.unlock_wrong.owner == ""
