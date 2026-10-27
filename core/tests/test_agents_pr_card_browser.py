"""G6 card on the Coding agents page in a real headless browser against the real dashboard server; the three APIs are faked at the HTTP edge.
Missing Playwright or browser = a failure, never a skip (CI installs both)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from test_dashboard import dash  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

EVIL = "ci‮<img src=x onerror=\"window.__xss=1\">"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


def page_with(browser, dash, work, flag=True):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    p = ctx.new_page()
    errs: list[str] = []
    p.on("pageerror", lambda e: errs.append(str(e)))

    def agents(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"enabled": True, "pr_card": flag, "sessions": [], "projects": [{"name": "jarves"}], "digest": {"text": "none"}}))

    def approvals(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"pending": [], "approvals": []}))

    def gh(route):
        if work is None:
            route.fulfill(status=404, content_type="application/json", body="{}")
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(work))
    p.route(re.compile(r"/api/agents/approvals(\?.*)?$"), approvals)
    p.route(re.compile(r"/api/agents(\?.*)?$"), agents)
    p.route(re.compile(r"/api/github/work(\?.*)?$"), gh)
    p.add_init_script("try{localStorage.setItem('jarves-lang','en')}catch(e){}")
    p.goto(dash.url + "#/agents")
    return ctx, p, errs


def test_the_card_lists_branches_states_and_next_steps_as_text(browser, dash):  # noqa: F811
    work = {"enabled": True, "ok": True, "text": "x", "rows": [
        {"branch": "agent/fix-1", "state": "checks_failing", "next": "Pull request #2: 2 check(s) failed (" + EVIL + "). Re-run them."},
        {"branch": "agent/ok", "state": "ready", "next": "Pull request #3: all checks passed."}]}
    ctx, p, errs = page_with(browser, dash, work)
    p.wait_for_selector("#agents-prs", timeout=30000)
    txt = p.inner_text("#agents-prs")
    assert "agent/fix-1" in txt and "checks failing" in txt and "ready" in txt and "all checks passed" in txt
    assert p.evaluate("window.__xss") is None and p.locator("#agents-prs img").count() == 0       # GitHub text stays text
    assert errs == []
    ctx.close()


@pytest.mark.parametrize("work,flag", [(None, False), ({"enabled": False, "rows": [], "text": ""}, True)])
def test_the_card_is_absent_when_off_or_the_api_is_missing(browser, dash, work, flag):  # noqa: F811
    ctx, p, errs = page_with(browser, dash, work, flag)
    p.wait_for_selector("text=New session", timeout=30000)
    assert p.locator("#agents-prs").count() == 0 and errs == []
    ctx.close()


def test_the_page_never_asks_the_pr_route_when_the_switch_is_off(browser, dash):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1200, "height": 900})
    p = ctx.new_page()
    asked = []
    p.on("request", lambda r: asked.append(r.url) if "/api/github/work" in r.url else None)
    p.route(re.compile(r"/api/agents/approvals(\?.*)?$"), lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"pending": []})))
    p.route(re.compile(r"/api/agents(\?.*)?$"), lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"enabled": True, "pr_card": False, "sessions": [], "projects": [{"name": "jarves"}], "digest": {"text": "none"}})))
    p.goto(dash.url + "#/agents")
    p.wait_for_selector("text=New session", timeout=30000)
    assert asked == []
    ctx.close()
