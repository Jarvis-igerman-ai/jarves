"""Local web dashboard over real HTTP (WP-B, #17 item 9): token/cookie auth, loopback + Host checks, no secrets or
e-mail addresses served, every page's API, issue status writes, workspace stub and the plugin point."""
import http.client
import json
import shutil
from pathlib import Path

import pytest

from jarves import dashboard, diagnose
from jarves.config import Config
from jarves.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "session_2026-10-19.jsonl"


@pytest.fixture
def dash(home):
    st = Store(home / "store.db", tier="priority", backend_model="gpt-6-luna")
    diagnose.run(st, Config(), st.ingest_log(FIXTURE))
    st.close()
    (home / "health.json").write_text(json.dumps({"ts": 0, "core": "stopped", "components": {
        "ai": {"status": "ok", "detail": "ChatGPT plan (someone@example.com), web search on", "action": ""},
        "music": {"status": "degraded", "detail": "pear not reachable", "action": "open Pear"}}}), encoding="utf-8")
    (home / "workspace").mkdir()
    (home / "workspace" / "SOUL.md").write_text("# SOUL\nאני JARVES.", encoding="utf-8")
    d = dashboard.Dashboard(home, Config()).start()
    yield d
    d.stop()


def req(d, method, path, cookie=True, host=None, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{d.port}", **(headers or {})}
    if cookie:
        h["Cookie"] = f"{dashboard.COOKIE}={d.url.split('t=')[1]}"
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def get(d, path):
    r, data = req(d, "GET", path)
    assert r.status == 200, (path, data[:200])
    return json.loads(data)


def test_binds_loopback_and_needs_the_token(dash):
    assert dash.httpd.server_address[0] == "127.0.0.1"
    assert dash.url.startswith(f"http://127.0.0.1:{dash.port}/?t=") and len(dash.url.split("t=")[1]) >= 30
    assert req(dash, "GET", "/", cookie=False)[0].status == 403
    assert req(dash, "GET", "/api/sessions", cookie=False)[0].status == 403
    assert req(dash, "GET", "/?t=wrong", cookie=False)[0].status == 403
    r, _ = req(dash, "GET", "/?t=" + dash.url.split("t=")[1], cookie=False)
    assert r.status == 302 and r.getheader("Location") == "/"
    ck = r.getheader("Set-Cookie")
    assert "HttpOnly" in ck and "SameSite=Strict" in ck


def test_rejects_foreign_host_and_cross_site_writes(dash):
    assert req(dash, "GET", "/api/sessions", host="evil.example:80")[0].status == 403      # DNS rebinding
    assert req(dash, "POST", "/api/diagnose", body={})[0].status == 403                    # no X-Jarves header
    r, _ = req(dash, "GET", "/static/../dashboard.py")
    assert r.status == 404


def test_page_and_static_files_with_strict_headers(dash):
    r, html = req(dash, "GET", "/")
    assert r.status == 200 and b"/static/app.js" in html
    csp = r.getheader("Content-Security-Policy")
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.getheader("X-Frame-Options") == "DENY" and r.getheader("Cache-Control") == "no-store"
    for f in ("app.js", "style.css"):
        r, body = req(dash, "GET", "/static/" + f)
        assert r.status == 200 and len(body) > 1000
    assert b".innerHTML" not in req(dash, "GET", "/static/app.js")[1]    # all text via textContent


def test_conversations_and_transcript(dash):
    sessions = get(dash, "/api/sessions")
    assert len(sessions) == 1
    s = sessions[0]
    assert s["user_turns"] == 10 and s["tools"] == 6 and s["issues"] >= 5
    assert s["first_user"] == "מה שלומך ג'ארויס" and s["cost"]["total"] == pytest.approx(0.1716, abs=0.002)
    t = get(dash, "/api/session/" + s["id"])
    assert len(t["turns"]) == 26 and t["tools"][0]["name"] == "music_play"
    assert any(i["fingerprint"] == "backend_stall" for i in t["issues"])
    assert req(dash, "GET", "/api/session/nope")[0].status == 404
    assert get(dash, "/api/live") == {"session": None}            # the fixture conversation ended
    ov = get(dash, "/api/overview")
    assert ov["issues"]["open"] >= 5 and ov["sessions"] == 1


def test_costs_are_labelled_estimates(dash):
    c = get(dash, "/api/costs?days=3650")
    assert c["estimate"] is True and c["days"][0]["sessions"] == 1
    assert c["pricing"]["voice_per_minute"]["gpt-live-1"] == 0.05
    assert c["pricing"]["est_input_tokens"] == 3000            # token COUNTS are not masked as credentials
    assert any("bill is the truth" in n for n in c["notes"])


def test_issues_can_be_resolved_and_reopened(dash):
    open_ = get(dash, "/api/issues")
    stall = next(i for i in open_ if i["fingerprint"] == "backend_stall")
    r, _ = req(dash, "POST", f"/api/issues/{stall['id']}", headers={"X-Jarves": "1"}, body={"status": "resolved"})
    assert r.status == 200
    assert all(i["id"] != stall["id"] for i in get(dash, "/api/issues"))
    assert any(i["id"] == stall["id"] for i in get(dash, "/api/issues?status=resolved"))
    r, _ = req(dash, "POST", f"/api/issues/{stall['id']}", headers={"X-Jarves": "1"}, body={"status": "bogus"})
    assert r.status == 400
    r, data = req(dash, "POST", "/api/diagnose", headers={"X-Jarves": "1"}, body={})
    assert r.status == 200 and json.loads(data)["open"] >= 1


def test_skills_memory_settings_show_no_secrets(dash, home):
    sk = get(dash, "/api/skills")
    assert sk["components"]["music"]["status"] == "degraded"
    assert "example.com" not in json.dumps(sk) and "<email>" in sk["components"]["ai"]["detail"]
    music = next(s for s in sk["skills"] if s["skill"] == "music")        # from SKILL_REGISTERED in the log
    tools = {t["name"]: t for t in music["tools"]}
    assert tools["music_play"]["n"] == 1 and tools["music_play"]["avg_s"] == pytest.approx(8.46, abs=0.01)
    assert music["health"] == "degraded" and music["action"] == "open Pear"
    mem = get(dash, "/api/memory")
    assert mem["items"] == [] and mem["editable"] is True
    st = get(dash, "/api/settings")
    assert st["read_only"] is False and st["config"]["pricing"]["web_search_per_call"] == 0.01
    assert any(e["key"] == "voice.backend_deadline_s" and e["default"] == 25 for e in st["editable"])
    assert "sk-" not in json.dumps(st)


def test_workspace_files_and_plugin_route(dash):
    ws = get(dash, "/api/workspace")
    names = {f["name"] for f in ws["files"]}
    assert {"SOUL.md", "AGENTS.md", "USER.md", "MEMORY.md"} <= names and ws["writable"] is True
    assert get(dash, "/api/workspace/SOUL.md")["text"].endswith("אני JARVES.")
    assert req(dash, "GET", "/api/workspace/..%2Fconfig.toml")[0].status == 404
    r, _ = req(dash, "PUT", "/api/workspace/TOOLS.md", headers={"X-Jarves": "1"}, body={"text": "x"})
    assert r.status == 400                             # generated by the Core: never edited by hand
    seen = []
    dashboard.register("POST", r"/api/memory/(\d+)", lambda ctx, m, body: (seen.append((m.group(1), body)) or
                                                                           (200, {"ok": True})))
    try:
        r, _ = req(dash, "POST", "/api/memory/7", headers={"X-Jarves": "1"}, body={"text": "new"})
        assert r.status == 200 and seen == [("7", {"text": "new"})]
        assert get(dash, "/api/memory")["editable"] is True
    finally:
        dashboard._ROUTES[:] = [r for r in dashboard._ROUTES
                                if not (r[0] == "POST" and r[1].pattern == r"^/api/memory/(\d+)$")]


def test_live_session_is_visible_while_it_runs(home):
    st = Store(home / "store.db")
    import time
    now = time.time()
    st.ingest({"ts": now - 20, "code": "LIVE_STARTED", "session": "live1",
               "msg": "GPT-Live session started (gpt-live-1 -> gpt-6-luna)"})
    st.ingest({"ts": now - 10, "code": "LIVE_USER_SAID", "session": "live1", "text": "שלום"})
    st.commit()
    d = dashboard.Dashboard(home, Config()).start()
    try:
        live = get(d, "/api/live")
        assert live["session"]["live"] is True and live["turns"][0]["text"] == "שלום"
        assert get(d, "/api/overview")["live"]["id"] == live["session"]["id"]
        st.ingest({"ts": now - 5, "code": "LIVE_JARVES_SAID", "session": "live1", "text": "היי!", "wait_s": 0.8})
        st.commit()
        assert len(get(d, "/api/live")["turns"]) == 2              # reads what the Core writes, while it runs
    finally:
        d.stop()
        st.close()
