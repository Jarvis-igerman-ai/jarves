"""Browser Studio B3 (#398): human takeover and return in the SAME session. Fake page driver, fake clock, no network.
Covers: agent locked out while HUMAN, no stale ownership, input validation, what the human types never leaks or reaches the agent."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from test_dashboard import dash, req  # noqa: F401  (fixture + helper)

from jarves import browser_liveview as lv
from jarves import browser_pilot as bp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import all_pages_check as apc  # noqa: E402

LOCAL = "http://localhost:8123/login"
SECRET = "hunter2-Correct-Horse-9"


class Drv:
    def __init__(self, url=LOCAL):
        self.url, self.closed, self.inputs, self.page_text, self.on_input = url, False, [], "Login page", None
        self.text_calls = 0

    @property
    def current_url(self):
        return self.url

    def pin_hosts(self, m):
        pass

    def open(self, url):
        self.url = url

    def text(self):
        self.text_calls += 1
        return self.page_text

    def screenshot(self):
        return b"\x89PNG-fake"

    def human_input(self, kind, data):
        self.inputs.append((kind, dict(data)))
        if self.on_input:
            self.on_input(kind, data)

    def close(self):
        self.closed = True


def mk(tmp_path, url=LOCAL, on=True, takeover=True):
    t = [1000.0]
    d = Drv(url)
    s = bp.PilotSession(tmp_path, d, enabled=lambda: on, clock=lambda: t[0], resolver=lambda h: ["93.184.216.34"])
    v = lv.LiveView(s, enabled=lambda: on, clock=lambda: t[0], takeover_enabled=lambda: takeover)
    return v, s, d, t


def code(fn, *a, **k):
    with pytest.raises((lv.LiveError, bp.PilotError)) as e:
        fn(*a, **k)
    return e.value.code


def test_takeover_has_its_own_switch_off_by_default(tmp_path):
    from jarves import features as feat
    f = feat.CATALOG[lv.TAKEOVER]
    assert f.default_enabled is False and feat.stage_of(lv.TAKEOVER) == "beta" and "browser_liveview" in f.needs
    v, s, d, _ = mk(tmp_path, takeover=False)
    g = v.start()["grant"]
    assert code(v.takeover, g) == "LIVEVIEW_TAKEOVER_OFF" and code(v.human, g, "type", {"text": "abc"}) == "LIVEVIEW_TAKEOVER_OFF"
    assert s.state == bp.AGENT and d.inputs == []
    assert v.give_back(g) == {"returned": False, "state": bp.AGENT}


def test_agent_is_locked_out_while_the_owner_drives_and_can_return(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    assert v.takeover(g) == {"state": bp.HUMAN}
    for fn in (s.read, s.screenshot):
        assert code(fn) == "BROWSER_PILOT_NOT_AGENT"
    assert code(s.open, LOCAL) == "BROWSER_PILOT_NOT_AGENT"
    assert code(s.act, "click") == "BROWSER_PILOT_NOT_AGENT"
    assert v.frame(g)["seq"] >= 1                          # the owner can still watch
    assert v.give_back(g) == {"returned": True, "state": bp.AGENT, "reread": True}
    assert s.read()["untrusted_text"] == "Login page"


def test_return_means_a_fresh_read_and_a_note_not_cached_text(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    s.read()
    n = d.text_calls
    v.takeover(g)
    d.page_text = "Welcome back, account 4711"
    v.give_back(g)
    r = s.read()
    assert d.text_calls == n + 1 and "account 4711" in r["untrusted_text"]
    assert "owner used this page" in r["note"] and "never shown to you" in r["note"]


def test_takeover_waits_for_a_running_agent_call_so_ownership_is_never_stale(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    started, release, order = threading.Event(), threading.Event(), []

    def slow_text():
        started.set()
        release.wait(5)
        return "slow page"
    d.text = slow_text
    out = {}
    t1 = threading.Thread(target=lambda: (out.setdefault("read", s.read()), order.append("agent-done")))
    t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=lambda: (v.takeover(g), order.append("human-took-over")))
    t2.start()
    time.sleep(0.2)
    assert order == []                                         # the takeover is waiting for the agent call
    release.set()
    t1.join(5)
    t2.join(5)
    assert order == ["agent-done", "human-took-over"] and out["read"]["untrusted_text"] == "slow page"
    assert code(s.read) == "BROWSER_PILOT_NOT_AGENT"


def test_takeover_needs_a_valid_grant_a_running_session_and_the_switch(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    assert code(v.takeover, "wrong") == "LIVEVIEW_TOKEN"
    assert code(v.human, "wrong", "type", {"text": "abc"}) == "LIVEVIEW_TOKEN" and d.inputs == []
    v.takeover(g)
    assert code(v.takeover, g) == "LIVEVIEW_TAKEOVER"          # already yours
    v2, s2, d2, _ = mk(tmp_path / "off", on=False)
    assert code(v2.start) == "LIVEVIEW_OFF"


@pytest.mark.parametrize("kind,data", [("click", {"x": -1, "y": 5}), ("click", {"x": 5}), ("click", {"x": "5", "y": 5}),
                                       ("click", {"x": True, "y": 5}), ("click", {"x": 99999, "y": 5}), ("scroll", {"dy": 99999}),
                                       ("key", {"key": "Delete"}), ("key", {"key": "Control+W"}), ("key", {}),
                                       ("type", {"text": ""}), ("type", {"text": "x" * 501}), ("type", {"text": "a\x00b"}),
                                       ("type", {"text": 5}), ("evaluate", {"js": "1"}), ("goto", {"url": "http://x"}), ("type", None)])
def test_invalid_owner_input_is_refused_and_never_reaches_the_page(tmp_path, kind, data):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    with pytest.raises(lv.LiveError) as e:
        v.human(g, kind, data)
    assert e.value.code == "BROWSER_PILOT_INPUT" and d.inputs == []


def test_input_only_while_the_owner_drives(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    assert code(v.human, g, "type", {"text": "abc"}) == "BROWSER_PILOT_NOT_HUMAN" and d.inputs == []
    v.takeover(g)
    assert v.human(g, "type", {"text": "abc"}) == {"ok": True}
    v.give_back(g)
    assert code(v.human, g, "type", {"text": "abc"}) == "BROWSER_PILOT_NOT_HUMAN" and len(d.inputs) == 1


def test_a_driver_without_human_input_refuses(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    del Drv.human_input
    try:
        assert code(v.human, g, "click", {"x": 1, "y": 1}) == "BROWSER_PILOT_INPUT"
    finally:
        Drv.human_input = lambda self, kind, data: (self.inputs.append((kind, dict(data))), self.on_input and self.on_input(kind, data))


def test_what_the_owner_types_never_leaks(tmp_path, caplog):
    v, s, d, t = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    with caplog.at_level("DEBUG"):
        r = v.human(g, "type", {"text": SECRET})
        v.human(g, "key", {"key": "Enter"})
        v.human(g, "click", {"x": 10, "y": 20})
    assert r == {"ok": True} and ("type", {"text": SECRET}) in d.inputs                  # the PAGE got it
    assert SECRET not in caplog.text
    assert all(SECRET not in json.dumps(x) for x in s.receipts)                          # receipts: kind only
    assert [x["kind"] for x in s.receipts if x["kind"].startswith("human:")] == ["human:type", "human:key", "human:click"]
    audit = (tmp_path / bp.AUDIT_FILE).read_text(encoding="utf-8")
    assert SECRET not in audit and "human:type" in audit
    # the page echoes it back (a preview, a "you typed ..." line): the agent still does not get it
    d.page_text = f"Login page. You typed {SECRET} in the box. Welcome."
    v.give_back(g)
    out = s.read()
    assert SECRET not in json.dumps(out) and "hidden" in out["untrusted_text"]
    s.stop()
    assert s._typed == []


def test_driver_errors_use_fixed_words(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)

    def boom(kind, data):
        raise RuntimeError("selector failed for value " + SECRET)
    d.on_input = boom
    with pytest.raises(lv.LiveError) as e:
        v.human(g, "type", {"text": SECRET})
    assert e.value.code == "BROWSER_PILOT_INPUT" and SECRET not in e.value.message and SECRET not in str(e.value)


def test_a_click_that_leaves_the_test_site_stops_the_session(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    d.on_input = lambda kind, data: setattr(d, "url", "https://bank.example/")
    assert code(v.human, g, "click", {"x": 1, "y": 1}) == "BROWSER_PILOT_VIEW_TEST_SITE"
    assert s.state == bp.STOPPED and d.closed and code(v.frame, g) == "LIVEVIEW_TOKEN"


def test_no_takeover_input_on_a_non_local_page(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    d.url = "https://example.com/"
    assert code(v.human, g, "type", {"text": "abc"}) == "BROWSER_PILOT_VIEW_TEST_SITE" and d.inputs == []


def test_the_time_cap_ends_the_takeover(tmp_path):
    v, s, d, t = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    t[0] += bp.MAX_SECONDS + 1
    assert code(v.human, g, "type", {"text": "abc"}) in ("BROWSER_PILOT_CAP", "LIVEVIEW_EXPIRED")
    assert d.inputs == []


def test_stop_works_from_the_human_state(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    assert v.stop() == {"stopped": True} and s.state == bp.STOPPED and d.closed


def test_the_agent_has_no_path_to_human_input():
    src = Path(bp.__file__).read_text(encoding="utf-8")
    assert src.count("human_input(") >= 1
    assert "def act(self, kind: str)" in src
    # the agent-facing methods never call driver.human_input
    for name in ("_open", "_read", "_screenshot", "act", "view_frame"):
        body = src.split(f"def {name}(")[1].split("\n    def ")[0]
        assert "human_input" not in body


def test_route_errors_and_local_only(dash):  # noqa: F811
    lv.set_active(None)
    for path in ("takeover", "return", "input"):
        r, body = req(dash, "POST", f"/api/browser/live/{path}", headers={"X-Jarves": "1"}, body={})
        assert r.status == 404 and json.loads(body)["code"] == "LIVEVIEW_NO_SESSION"
    seen = []
    lv.dashboard_routes(lambda m, rx, fn: seen.append(fn))

    class Ctx:
        principal = {"kind": "device", "id": "phone"}
    for fn in seen[3:]:
        status, body = fn(Ctx(), None, {"grant": "x", "kind": "type", "data": {"text": "abc"}})
        assert status == 403 and body["code"] == "LIVEVIEW_LOCAL_ONLY"


# ---------------------------------------------------------------- end to end in the real dashboard page
@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            yield b
        finally:
            b.close()


PNG1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
        b"\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


def test_takeover_in_the_real_dashboard(browser, dash, tmp_path):  # noqa: F811
    v, s, d, _ = mk(tmp_path)
    d.screenshot = lambda: PNG1
    t = [0.0]
    view = lv.LiveView(s, enabled=lambda: True, clock=lambda: t[0] + time.monotonic(), takeover_enabled=lambda: True)
    lv.set_active(view)
    try:
        ctx = browser.new_context(viewport={"width": 1200, "height": 900})
        p = ctx.new_page()
        errs: list[str] = []
        p.on("pageerror", lambda e: errs.append(str(e)))

        def with_live(route):
            r = route.fetch()
            j = r.json()
            j["live_view"] = True
            route.fulfill(response=r, body=json.dumps(j), headers={**r.headers, "content-type": "application/json"})
        p.route(re.compile(r"/api/browser(\?.*)?$"), with_live)
        p.add_init_script("try{localStorage.setItem('jarves-lang','en')}catch(e){}")
        p.goto(dash.url + "#/browser")
        p.wait_for_selector("#live-view button", timeout=30000)
        assert p.locator("#live-view button:has-text('Take over')").is_disabled()
        p.click("#live-view button:has-text('Live view')")
        p.wait_for_function("() => { const i = document.querySelector('#live-view img'); return i && i.naturalWidth > 0; }", timeout=30000)
        p.click("#live-view button:has-text('Take over')")
        p.wait_for_function("() => document.querySelector('#live-view').innerText.includes('You are in control')", timeout=30000)
        assert s.state == bp.HUMAN
        p.fill("#live-view input[type=text]", SECRET)
        p.click("#live-view button:has-text('Type into the page')")
        p.wait_for_function("() => !document.querySelector('#live-view input[type=text]').value", timeout=30000)
        p.click("#live-view button[data-key=Enter]")
        p.click("#live-view img")
        deadline = time.time() + 15
        while len(d.inputs) < 3 and time.time() < deadline:
            time.sleep(0.1)
        assert [k for k, _ in d.inputs] == ["type", "key", "click"] and d.inputs[0][1] == {"text": SECRET}
        assert SECRET not in p.content() and SECRET not in p.inner_text("#live-view")
        p.click("#live-view button:has-text('Give back')")
        p.wait_for_function("() => document.querySelector('#live-view').innerText.includes('Given back')", timeout=30000)
        assert s.state == bp.AGENT
        assert errs == []
        ctx.close()
    finally:
        lv.set_active(None)
