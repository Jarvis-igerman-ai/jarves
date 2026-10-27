"""Phone view of Goal plans (V5 gap G4, issue #184; #340): scope, read-only steps and status, pause / cancel / resume /
retry (only for an untainted plan, never blocked or unknown steps), approve / skip / delete refused (computer only), idempotent commands, no arguments / results / evidence on the
phone, no text in the audit log, one state with the dashboard page, static files."""
import http.client
import json
import re
from pathlib import Path

import pytest

from jarves import dashboard, mobile
from jarves.config import Config
from jarves.goal_plans import PlanStore
from jarves.userdata import open_db

HOST = "192.168.50.7"
MOBILE_DIR = Path(mobile.__file__).parent / "dashboard_static" / "mobile"
SECRET_ARG = "TOPSECRET-BODY-9137"
SECRET_OUT = "LEAKED-RESULT-4471"


def call(d, method, path, token=None, body=None, owner=False):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    h = {"Host": f"127.0.0.1:{d.port}" if owner else f"{HOST}:{d.port}"}
    if owner:
        h["Cookie"] = f"{d.access.cookie}={d.url.split('t=')[1]}"
    if method != "GET":
        h["X-Jarves"] = "1"
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:
        return r.status, {"text": raw.decode("utf-8", "replace")}


@pytest.fixture
def srv(home):
    from conftest import free_port
    from jarves.mobile import Bridge
    b = Bridge(chat=lambda t, d: {"reply": "x", "ok": True}, voice=lambda w, d: {}, status=lambda: {"core": "running", "components": {}})
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=b).start()
    yield d
    d.stop()


def pair(d, scopes=("chat", "status", "tasks")):
    st, code = call(d, "POST", "/api/mobile/code", body={}, owner=True)
    st, got = call(d, "POST", "/api/m/pair", body={"code": code["code"], "name": "Galaxy"})
    assert st == 200
    assert call(d, "POST", f"/api/mobile/devices/{got['device']}/scopes", body={"scopes": list(scopes)}, owner=True)[0] == 200
    return got["token"], got["device"]


@pytest.fixture
def store(home):
    s = PlanStore(open_db(home))
    p = s.propose("Tidy the notes folder", [{"tool": "list_notes", "args": {"q": "x"}, "why": "see what is there"},
                                             {"tool": "send_message", "args": {"to": "a", "body": SECRET_ARG}, "why": "tell Dana"}])
    s.approve(p["id"])
    s.db.x("UPDATE goal_plan_steps SET status='done', evidence=?, error='' WHERE plan_id=? AND idx=0", (SECRET_OUT, p["id"]))
    s.db.x("UPDATE goal_plans SET result=? WHERE id=?", (SECRET_OUT, p["id"]))
    s.db.x("UPDATE goal_plan_steps SET consequential=1 WHERE plan_id=? AND idx=1", (p["id"],))
    return s, p["id"]


_n = [0]


def cid():
    _n[0] += 1
    return f"plan-test-{_n[0]:06d}"


def op(d, tok, **body):
    return call(d, "POST", "/api/m/plans/op", tok, {"id": cid(), **body})


def test_needs_a_token_and_the_tasks_scope(srv, store):
    s, pid = store
    assert call(srv, "GET", "/api/m/plans")[0] == 401
    assert call(srv, "POST", "/api/m/plans/op", None, {"id": cid(), "op": "pause", "plan": pid})[0] == 401
    tok, _ = pair(srv, scopes=("chat", "status", "approvals", "voice"))
    assert call(srv, "GET", "/api/m/plans", tok)[0] == 403
    assert op(srv, tok, op="cancel", plan=pid)[0] == 403
    assert s.get(pid)["state"] == "ready"
    tok, _ = pair(srv)
    assert call(srv, "GET", "/api/m/plans", tok)[0] == 200
    assert call(srv, "GET", "/api/goal-plans", tok)[0] == 403                         # the dashboard route stays computer-only


def test_view_shows_steps_and_status_but_no_arguments_or_results(srv, store):
    s, pid = store
    tok, _ = pair(srv)
    st, v = call(srv, "GET", "/api/m/plans", tok)
    assert st == 200 and v["enabled"] is True and "approve" in v["pc_only"]
    (p,) = v["plans"]
    assert p["title"] == "Tidy the notes folder" and p["state"] == "ready" and p["progress"] == 50
    assert [x["tool"] for x in p["steps"]] == ["list_notes", "send_message"]
    assert [x["status"] for x in p["steps"]] == ["done", "pending"] and p["steps"][1]["consequential"] is True
    assert p["steps"][1]["why"] == "tell Dana" and p["can"] == ["pause", "cancel"] and p["needs_pc"] is False
    blob = json.dumps(v)
    assert SECRET_ARG not in blob and SECRET_OUT not in blob and "args" not in blob and "evidence" not in blob


def test_pause_and_cancel_work_and_match_the_pc(srv, store):
    s, pid = store
    tok, _ = pair(srv)
    assert op(srv, tok, op="pause", plan=pid)[1]["state"] == "paused"
    _, pc = call(srv, "GET", "/api/goal-plans", owner=True)                           # the PC page: same store, same state
    assert pc["plans"][0]["state"] == "paused"
    _, v = call(srv, "GET", "/api/m/plans", tok)
    assert v["plans"][0]["can"] == ["resume", "cancel"] and v["plans"][0]["needs_pc"] is False
    assert op(srv, tok, op="pause", plan=pid)[0] == 409                               # not running any more
    assert op(srv, tok, op="resume", plan=pid)[1]["state"] == "ready"                  # #340
    assert op(srv, tok, op="resume", plan=pid)[0] == 403                              # ready: nothing to resume
    s.pause(pid)
    assert op(srv, tok, op="cancel", plan=pid)[1]["state"] == "cancelled"
    assert [x["status"] for x in s.get(pid)["steps"]] == ["done", "skipped"]
    st, out = op(srv, tok, op="cancel", plan=pid)
    assert st == 409 and out["code"] == "PLAN_STATE"
    assert op(srv, tok, op="cancel", plan=9999)[0] == 404
    assert op(srv, tok, op="pause", plan="x")[0] == 400


def test_approve_skip_and_delete_stay_on_the_computer(srv, store):
    s, pid = store
    tok, _ = pair(srv)
    s.pause(pid)
    for name in ("approve", "skip", "delete"):
        st, out = op(srv, tok, op=name, plan=pid, step=1)
        assert st == 403 and out["code"] == "PLAN_PC_ONLY", name
    assert s.get(pid)["state"] == "paused" and s.get(pid) is not None               # nothing started, nothing deleted
    assert op(srv, tok, op="explode", plan=pid)[0] == 400
    assert call(srv, "POST", "/api/m/plans/op", tok, {"op": "cancel", "plan": pid})[0] == 400      # command id required


def _fail(s, pid, attempts=1):
    s.db.x("UPDATE goal_plan_steps SET status='failed', attempts=?, error='boom' WHERE plan_id=? AND idx=1", (attempts, pid))
    s.db.x("UPDATE goal_plans SET state='failed' WHERE id=?", (pid,))


def test_retry_a_failed_plan_from_the_phone(srv, store):
    s, pid = store
    tok, _ = pair(srv)
    assert op(srv, tok, op="retry", plan=pid)[0] == 403                               # not failed: nothing to retry
    _fail(s, pid)
    _, v = call(srv, "GET", "/api/m/plans", tok)
    assert v["plans"][0]["can"] == ["retry", "cancel"] and v["plans"][0]["needs_pc"] is False
    st, out = op(srv, tok, op="retry", plan=pid)
    assert st == 200 and out["state"] == "ready"
    assert [x["status"] for x in s.get(pid)["steps"]] == ["done", "pending"]
    assert op(srv, tok, op="retry", plan=pid)[0] == 403                               # idempotent: no longer failed


def test_resume_and_retry_are_refused_for_tainted_blocked_unknown_or_exhausted_plans(srv, store):
    s, pid = store
    tok, _ = pair(srv)
    s.pause(pid)
    s.db.x("UPDATE goal_plans SET tainted=1 WHERE id=?", (pid,))                      # untrusted text was read
    assert op(srv, tok, op="resume", plan=pid)[0] == 403 and s.get(pid)["state"] == "paused" and s.get(pid)["tainted"] == 1
    s.db.x("UPDATE goal_plans SET tainted=0 WHERE id=?", (pid,))
    s.db.x("UPDATE goal_plan_steps SET status='unknown' WHERE plan_id=? AND idx=1", (pid,))   # may already have run
    assert op(srv, tok, op="resume", plan=pid)[0] == 403 and s.get(pid)["state"] == "paused"
    s.db.x("UPDATE goal_plans SET state='failed' WHERE id=?", (pid,))
    s.db.x("UPDATE goal_plan_steps SET error='x' WHERE plan_id=? AND idx=0", (pid,))
    assert op(srv, tok, op="retry", plan=pid)[0] == 403                               # unknown step: PC only
    _fail(s, pid)
    s.db.x("UPDATE goal_plan_steps SET status='failed', attempts=3 WHERE plan_id=? AND idx=1", (pid,))
    assert op(srv, tok, op="retry", plan=pid)[0] == 403                               # attempt limit
    s.db.x("UPDATE goal_plan_steps SET attempts=1 WHERE plan_id=? AND idx=1", (pid,))
    s.db.x("UPDATE goal_plans SET tainted=1 WHERE id=?", (pid,))
    assert op(srv, tok, op="retry", plan=pid)[0] == 403 and s.get(pid)["state"] == "failed"
    assert op(srv, tok, op="resume", plan=9999)[0] == 404


def test_resume_needs_the_tasks_scope_and_is_audited_without_text(srv, store, home):
    s, pid = store
    s.pause(pid)
    weak, _ = pair(srv, scopes=("chat", "status", "approvals"))
    assert op(srv, weak, op="resume", plan=pid)[0] == 403 and s.get(pid)["state"] == "paused"
    tok, _ = pair(srv)
    assert op(srv, tok, op="resume", plan=pid)[0] == 200
    audit = (home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    assert "Tidy the notes" not in audit and "tell Dana" not in audit and '"resume"' in audit


def test_a_resent_command_runs_once(srv, store):
    s, pid = store
    tok, _ = pair(srv)
    body = {"id": "plan-resend-0001", "op": "pause", "plan": pid}
    assert call(srv, "POST", "/api/m/plans/op", tok, body)[1]["state"] == "paused"
    s.resume(pid)                                                                     # the owner resumed on the PC meanwhile
    again = call(srv, "POST", "/api/m/plans/op", tok, body)                           # the phone retried a lost answer
    assert again[0] == 200 and again[1]["replay"] is True
    assert s.get(pid)["state"] == "ready"                                             # the old command did not pause it again


def test_audit_holds_ids_only(srv, store, home):
    s, pid = store
    tok, _ = pair(srv)
    op(srv, tok, op="pause", plan=pid)
    audit = (home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    assert "Tidy the notes" not in audit and "tell Dana" not in audit and '"plan"' in audit


def test_a_core_bridge_store_is_used_when_given(home):
    from conftest import free_port
    from jarves.mobile import Bridge
    own = PlanStore(open_db(home))
    own.propose("From the core", [{"tool": "t", "args": {}, "why": "w"}])
    b = Bridge(chat=lambda t, d: {}, voice=lambda w, d: {}, status=lambda: {"core": "running", "components": {}},
               plans=lambda: own, plans_on=lambda: False)
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=b).start()
    try:
        tok, _ = pair(d)
        v = call(d, "GET", "/api/m/plans", tok)[1]
        assert v["enabled"] is False and v["plans"][0]["title"] == "From the core" and v["plans"][0]["needs_pc"] is True
    finally:
        d.stop()


# ---------------------------------------------------------------- static files
def test_plans_js_is_served_csp_safe_and_translated():
    js = (MOBILE_DIR / "plans.js").read_text(encoding="utf-8")
    assert "plans.js" in mobile.STATIC_FILES and "innerHTML" not in js and "eval(" not in js and "Authorization" not in js

    def body(lang):
        m = re.search(rf"\n    {lang}: \{{(.*?)(?=\n    (?:he|en): \{{|\n  \}};)", js, re.S)
        return set(re.findall(r"\b([a-z_]+):\s*\"", m.group(1)))
    en, he = body("en"), body("he")
    assert en == he and len(en) > 25
    assert set(re.findall(r'\bt\("([a-z_]+)"\)', js)) <= en
    states = {"s_" + x for x in ("draft", "ready", "running", "paused", "blocked", "done", "failed", "cancelled")}
    steps = {"t_" + x for x in ("pending", "running", "done", "failed", "blocked", "unknown", "skipped")}
    assert states | steps <= en                                                       # every state the Core can report has words
    assert re.search(r"[֐-׿]", js)
    html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
    assert "/m/plans.js" in html and 'id="plansRoot"' in html and 'id="tabbtn-plans"' in html


def test_a_phone_never_lifts_the_taint_guard_and_waits_for_a_running_step(srv, store):
    s, pid = store
    tok, _ = pair(srv)
    s.pause(pid)
    s.db.x("UPDATE goal_plan_steps SET status='running' WHERE plan_id=? AND idx=1", (pid,))   # a step still in flight
    assert op(srv, tok, op="resume", plan=pid)[0] == 403
    s.db.x("UPDATE goal_plan_steps SET status='pending' WHERE plan_id=? AND idx=1", (pid,))
    s.db.x("UPDATE goal_plans SET tainted=1 WHERE id=?", (pid,))
    s.resume(pid, by="phone")                                                          # even if it slipped past the check
    assert s.get(pid)["tainted"] == 1
    s.pause(pid)
    s.resume(pid)                                                                      # the computer's resume still lifts it
    assert s.get(pid)["tainted"] == 0


def test_a_failed_step_error_shows_only_the_code_on_the_phone():
    """F12 #206: 'CODE: tool message' must not carry the tool's own text to the phone."""
    from jarves import mobile_plans
    assert mobile_plans._phone_error("TOOL_FAILED: could not open the private file dana-secret") == "TOOL_FAILED"
    assert mobile_plans._phone_error("the step timed out") == "STEP_FAILED"
    assert mobile_plans._phone_error("the owner did not approve this step") == "STEP_FAILED"
    assert mobile_plans._phone_error(None) == ""


def test_607_brief_escapes_bidi_and_zero_width_characters_and_keeps_hebrew():
    from jarves import mobile_plans as mp
    rlo, zwsp = chr(0x202E), chr(0x200B)
    p = {"id": 1, "title": "pay" + rlo + "now", "state": "draft", "progress": 0, "note": "n" + zwsp,
         "steps": [{"idx": 0, "tool": "fake" + zwsp + "_act", "why": "because" + rlo + "ok", "status": "pending"}]}
    b = mp.brief(p)
    blob = repr(b)
    assert rlo not in blob and zwsp not in blob
    bs = chr(92)
    assert bs + "u202e" in b["title"] and bs + "u200b" in b["steps"][0]["tool"] and bs + "u202e" in b["steps"][0]["why"]
    he = {"id": 2, "title": "שלום עולם", "state": "draft", "steps": []}
    assert mp.brief(he)["title"] == "שלום עולם"
