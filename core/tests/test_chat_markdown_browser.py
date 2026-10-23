"""Web chat view (chatview.js) in a real headless browser: safe markdown in answers, the slash pop-up and the status row.
Fake API, no server, no network. Missing Playwright or browser = a failure, never a skip (CI installs both)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body><div id="r"></div></body></html>"""
BOOT = """
window.__sent = [];
const answer = %s;
JarvesChat.mount(document.getElementById('r'), { tx: (h, e) => e, device: false, api: {
  state: async () => ({ available: true, cursor: 1, silent: { on: false }, approvals: [], busy: '',
    commands: [{ name: '/new', en: 'start a new conversation', he: 'x' }, { name: '/help', en: 'this list', he: 'y' }, { name: '/status', en: 'status', he: 'z' }],
    messages: [{ role: 'user', text: 'hi' }, { role: 'assistant', text: answer }] }),
  events: () => new Promise(() => {}), send: async (b) => { window.__sent.push(b.text); return {}; },
  silent: async () => ({}), answer: async () => ({}) } });
"""
ANSWER = ("# Title\n\nSome **bold** and `code` and <img src=x onerror=\"window.__xss=1\"> text.\n\n- one\n- two\n\n"
          "```python\nprint(1)\n```\n\n[ok](https://example.com) [bad](javascript:alert(1))")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser):
    p = browser.new_page(viewport={"width": 900, "height": 700})
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.set_content(PAGE)
    p.add_style_tag(path=str(STATIC / "chatview.css"))
    p.add_script_tag(path=str(STATIC / "chatview.js"))
    p.evaluate(BOOT % json.dumps(ANSWER))
    p.wait_for_selector(".jc-assistant")
    yield p
    assert errs == []
    p.close()


def test_status_row_has_no_stray_null(page):
    assert page.inner_text(".jc-status").strip() == "Not silent"


def test_markdown_renders_and_html_stays_text(page):
    a = ".jc-assistant "
    assert page.inner_text(a + ".jc-h") == "Title"
    assert page.inner_text(a + "strong") == "bold"
    assert page.locator(a + "ul li").count() == 2
    assert "print(1)" in page.inner_text(a + ".jc-code-block pre")
    assert page.locator(a + "img").count() == 0                      # HTML in an answer is shown as text, never run
    assert "<img" in page.inner_text(a + "p")
    assert page.evaluate("window.__xss") is None
    links = page.eval_on_selector_all(a + "a", "els => els.map(e => e.getAttribute('href'))")
    assert links == ["https://example.com"]                            # javascript: is never a link
    assert "[bad](javascript:alert(1))" in page.inner_text(a + "p:last-of-type")


def test_slash_popup_lists_filters_and_picks(page):
    page.fill(".jc-in", "/")
    assert page.locator(".jc-opt").count() == 3
    page.fill(".jc-in", "/he")
    assert page.locator(".jc-opt").count() == 1
    page.keyboard.press("Enter")                                       # picks the command, does not send it yet
    assert page.input_value(".jc-in") == "/help "
    assert page.evaluate("window.__sent") == []
    page.fill(".jc-in", "hello")
    assert page.locator(".jc-opt").count() == 0
