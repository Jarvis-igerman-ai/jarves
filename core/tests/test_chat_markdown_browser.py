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
    messages: [{ role: 'user', text: 'hi', ts: 1760000000 }, { role: 'assistant', text: answer, ts: Math.floor(Date.now() / 1000) }, { role: 'user', text: 'old', turn: '' }] }),
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


def test_messages_show_their_time_and_none_without_one(page):
    user, assistant, old = (page.locator(".jc-msg").nth(i) for i in range(3))
    assert user.get_attribute("data-time") and user.get_attribute("title")        # an older day shows the date too
    assert assistant.get_attribute("data-time") and ":" in assistant.get_attribute("data-time")
    assert old.get_attribute("data-time") is None                                   # a resumed message has no time
    assert page.evaluate("getComputedStyle(document.querySelectorAll('.jc-msg')[1], '::after').content").strip('"') == \
        assistant.get_attribute("data-time")


# ---- #444 S1: UI blocks (checklist / text) render as text nodes, keyboard accessible, RTL-safe
BLOCKS = [
    {"role": "ui_block", "turn": "t1", "block": {"v": 1, "id": "blk_1", "kind": "checklist", "title": "<img src=x onerror=\"window.__xss=1\">", "dir": "auto",
     "items": [{"id": "i1", "label": "<script>window.__xss=1</script>", "detail": "<b>d</b>", "checked": False},
               {"id": "i2", "label": "לקנות חלב", "detail": "", "checked": True}], "actions": []}},
    {"role": "ui_block", "turn": "t1", "block": {"v": 1, "id": "blk_2", "kind": "text", "title": "", "dir": "auto", "text": "<i onclick=\"window.__xss=1\">hi</i>"}},
    {"role": "ui_block", "turn": "t1", "block": {"v": 9, "id": "blk_3", "kind": "cards"}},
]


def test_ui_blocks_render_as_text_and_work_with_keyboard(browser):
    p = browser.new_page(viewport={"width": 320, "height": 700})
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.set_content(PAGE)
    p.add_style_tag(path=str(STATIC / "chatview.css"))
    p.add_script_tag(path=str(STATIC / "chatview.js"))
    p.evaluate(BOOT.replace("messages: [", "messages: " + json.dumps(BLOCKS) + ".concat([") .replace("turn: '' }] }),", "turn: '' }]) }),") % json.dumps("x"))
    p.wait_for_selector(".jc-block")
    assert p.evaluate("window.__xss") is None and p.locator(".jc-block img, .jc-block script, .jc-block b, .jc-block i").count() == 0
    assert p.locator(".jc-block").count() == 2                                  # the unknown kind is dropped
    assert "window.__xss=1" in p.inner_text(".jc-block[data-kind=checklist]") and "<b>d</b>" in p.inner_text(".jc-block")
    boxes = p.locator(".jc-block input[type=checkbox]")
    assert boxes.count() == 2 and boxes.nth(1).is_checked() and not boxes.nth(0).is_checked()
    assert p.locator(".jc-block fieldset > legend").count() == 1
    assert p.locator(".jc-block label[dir=auto]").count() == 2
    assert p.inner_text(".jc-block [role=status]").strip() == "1 of 2 marked"
    boxes.nth(0).focus(); p.keyboard.press("Space")
    assert boxes.nth(0).is_checked() and p.inner_text(".jc-block [role=status]").strip() == "2 of 2 marked"
    assert p.locator(".jc-block label").first.bounding_box()["height"] >= 44
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert errs == []
    p.close()


# ---- #445 S2: the send button posts the block id and the ticked item ids only, then locks the list
ACTION_BLOCK = [{"role": "ui_block", "turn": "t1", "block": {"v": 1, "id": "blk_a", "kind": "checklist", "title": "Tasks", "dir": "auto",
                 "items": [{"id": "i1", "label": "buy milk", "detail": "", "checked": False},
                           {"id": "i2", "label": "<b>x</b>", "detail": "", "checked": False},
                           {"id": "i3", "label": "call Dana", "detail": "", "checked": False}],
                 "actions": [], "on": "anydo_create_tasks"}}]


def _action_page(browser, ui_action_js):
    p = browser.new_page(viewport={"width": 360, "height": 700})
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.set_content(PAGE)
    p.add_style_tag(path=str(STATIC / "chatview.css"))
    p.add_script_tag(path=str(STATIC / "chatview.js"))
    boot = (BOOT.replace("messages: [", "messages: " + json.dumps(ACTION_BLOCK) + ".concat([")
            .replace("turn: '' }] }),", "turn: '' }]) }),")
            .replace("silent: async () => ({}),", "silent: async () => ({}), uiAction: " + ui_action_js + ","))
    p.evaluate(boot % json.dumps("x"))
    p.wait_for_selector(".jc-block button")
    return p, errs


def test_checklist_send_posts_ids_only_and_locks(browser):
    p, errs = _action_page(browser, "async (b) => { window.__act = (window.__act || []).concat([b]); return { ok: true }; }")
    boxes = p.locator(".jc-block input[type=checkbox]")
    p.locator(".jc-block button").click()                                      # nothing ticked: no request
    assert p.evaluate("window.__act") is None and "Nothing is ticked" in p.inner_text(".jc-block [role=alert]")
    boxes.nth(2).focus(); p.keyboard.press("Space")
    boxes.nth(0).check()
    p.locator(".jc-block button").click()
    p.wait_for_function("window.__act && window.__act.length === 1")
    sent = p.evaluate("window.__act[0]")
    assert sent["block"] == "blk_a" and sent["selected"] == ["i1", "i3"] and set(sent) == {"block", "selected", "lang"}
    assert p.locator(".jc-block button").is_disabled() and boxes.nth(0).is_disabled()      # no second click
    assert "approval" in p.inner_text(".jc-block [role=alert]")
    assert p.locator(".jc-block button").bounding_box()["height"] >= 40
    assert errs == []
    p.close()


def test_checklist_send_refusal_reopens_and_names_the_reason(browser):
    p, errs = _action_page(browser, "async () => { const e = new Error('x'); e.code = 'UI_ACTION_STALE'; throw e; }")
    p.locator(".jc-block input[type=checkbox]").first.check()
    p.locator(".jc-block button").click()
    p.wait_for_function("document.querySelector('.jc-block [role=alert]').textContent.includes('already sent')")
    assert errs == []
    p.close()
