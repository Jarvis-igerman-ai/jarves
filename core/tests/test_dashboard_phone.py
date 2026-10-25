"""Owner decisions 20 Oct on what a paired LAN phone may do (A), the 24 h memory-undo purge (B) and the
confirm-every-time backup pruning of the privacy purge (C). Real HTTP, no network beyond loopback."""
import json

import pytest

from jarves import dashboard, dashboard_auth
from jarves.config import Config
from jarves.scheduler import _ctx_sched
from jarves.dashboard_auth import device_body_ok

from test_dashboard_lan import LAN, jget, new_code, pair, req
from owner_harness import owner_approves  # noqa: F401  (fixture)

PC = {"admin": True}


@pytest.fixture
def lan(home):
    from conftest import free_port
    d = dashboard.Dashboard(home, Config(), free_port(), lan=True, lan_host=LAN).start()
    yield d
    d.stop()


@pytest.fixture
def phone(lan):
    _, cookie, _ = pair(lan, new_code(lan), "Galaxy")
    _, s = jget(lan, "/api/session", cookie=cookie)
    w = {"X-Jarves": "1", "X-CSRF-Token": s["csrf"]}

    def call(method, path, body=None):
        r, data = req(lan, method, path, cookie=cookie, headers=w, body=body if body is not None else {})
        return r.status, (json.loads(data) if data[:1] == b"{" else data)
    return call


def pc(lan, method, path, body=None):
    r, data = req(lan, method, path, admin=True, host=f"127.0.0.1:{lan.port}", headers={"X-Jarves": "1"},
                  body=body if body is not None else {})
    return r.status, (json.loads(data) if data[:1] == b"{" else data)


def test_phone_manages_reminder_jobs(lan, phone, home, owner_approves):
    st, job = phone("POST", "/api/jobs", {"when": "בעוד 20 דקות", "task": "להוציא כביסה", "action": "reminder"})
    assert st == 200 and job["status"] == "draft"
    jid = job["id"]
    assert phone("POST", f"/api/jobs/{jid}", {"op": "approve"})[1]["status"] == "active"      # reminder: ok
    assert phone("POST", f"/api/jobs/{jid}", {"op": "pause"})[1]["status"] == "paused"
    assert phone("POST", f"/api/jobs/{jid}", {"op": "resume"})[1]["status"] == "active"
    assert phone("POST", f"/api/jobs/{jid}", {"op": "run_now"})[0] == 403                     # not on the list
    assert phone("POST", f"/api/jobs/{jid}", {"op": "edit", "task": "x"})[0] == 403
    assert phone("POST", f"/api/jobs/{jid}", {"op": "delete"})[0] == 200
    assert phone("POST", "/api/schedule/parse", {"when": "בעוד 20 דקות"})[0] == 200


def test_phone_cannot_approve_a_tool_job(lan, phone, home, owner_approves):
    s = _ctx_sched(lan.ctx)
    tid = s.preview("בעוד 20 דקות", "", action="tool", tool="file_write", by="voice")["id"]
    assert phone("POST", f"/api/jobs/{tid}", {"op": "approve"})[0] == 403
    assert phone("POST", f"/api/jobs/{tid}", {"op": "resume"})[0] == 403
    assert s.job(tid)["status"] == "draft"                      # nothing happened
    # a phone cannot create a tool job either: the route only makes research/reminder jobs
    st, job = phone("POST", "/api/jobs", {"when": "בעוד 20 דקות", "task": "x", "action": "tool", "tool": "file_write"})
    assert st == 200 and job["action"] == "research"
    # the computer (loopback owner) is unchanged: it can approve the tool job (its click is the approval)
    st, v = pc(lan, "POST", f"/api/jobs/{tid}", {"op": "approve"})
    assert st == 200 and v["status"] == "active"
    # a phone may still pause/delete it
    assert phone("POST", f"/api/jobs/{tid}", {"op": "pause"})[0] == 200
    assert phone("POST", f"/api/jobs/{tid}", {"op": "delete"})[0] == 200


def test_phone_flips_only_low_risk_switches(lan, phone):
    from jarves.features import CATALOG
    low = [f for f in CATALOG.values() if f.risk == "low" and f.id != "dashboard_lan"]
    assert low, "expected at least one low-risk feature"
    assert phone("POST", f"/api/features/{low[0].id}", {"enabled": not low[0].default_enabled})[0] == 200
    for fid in [f.id for f in CATALOG.values() if f.risk in ("medium", "high")] + ["dashboard_lan", "no_such"]:
        assert phone("POST", f"/api/features/{fid}", {"enabled": True})[0] == 403, fid
        assert phone("POST", f"/api/features/{fid}", {"reset": True})[0] == 403, fid
    for fid in ("calendar_write", "camera", "gmail_send", "memory_llm_pass", "workspace_agent_edit"):
        assert CATALOG[fid].risk != "low"
    # the computer is unchanged: medium/high switches work from loopback
    assert pc(lan, "POST", "/api/features/workspace_agent_edit", {"enabled": False})[0] == 200


def test_phone_calendar_create_update_only(lan, phone):
    st, r = phone("POST", "/api/calendar/preview", {"op": "create", "title": "x", "day": "tomorrow", "time": "10:00"})
    assert st == 403 and "calendar writing is off" in r["error"]       # reached the handler: the switch is off
    st, r = phone("POST", "/api/calendar/preview", {"op": "update", "title": "x"})
    assert st == 403 and "calendar writing is off" in r["error"]
    for op in ("delete", "remove", "cancel"):
        st, r = phone("POST", "/api/calendar/preview", {"op": op, "event_id": "e1"})
        assert st == 403 and "only the computer" in r["error"], op     # refused by the allowlist
    st, r = phone("POST", "/api/calendar/commit", {"preview_id": "nope"})
    assert st == 409 and "preview" in r["error"]                       # reached the handler: unknown preview
    assert phone("POST", "/api/calendar/delete", {"event_id": "e1"})[0] == 403
    assert phone("DELETE", "/api/calendar/e1")[0] == 403
    # the switch itself is high-risk: not flippable from a phone
    assert phone("POST", "/api/features/calendar_write", {"enabled": True})[0] == 403


def test_other_phone_writes_stay_denied(lan, phone):
    for path in ("/api/jobs/zz", "/api/runtime/stop", "/api/runtime/start", "/api/backup", "/api/updates/install", "/api/reports/1"):
        assert phone("POST", path, {"op": "approve"})[0] == 403, path
    assert device_body_ok(None, "GET", "/api/jobs", None)


def test_loopback_owner_unchanged(lan):
    st, job = pc(lan, "POST", "/api/jobs", {"when": "בעוד 20 דקות", "task": "x", "action": "reminder"})
    assert st == 200
    assert pc(lan, "POST", f"/api/jobs/{job['id']}", {"op": "run_now"})[0] in (200, 400)
    assert pc(lan, "POST", "/api/features/camera", {"enabled": False})[0] == 200
    assert dashboard_auth.Access(lan.ctx.home, "t").allowed({"kind": "local", "id": "local"}, "POST", "/api/anything")


# ---------------------------------------------------------------------- (B) memory undo journal: 24 h
class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_memory_undo_journal_is_purged_after_24h(home):
    from jarves.memory import MemoryStore
    d = dashboard.Dashboard(home, Config())
    clock = d.ctx.clock = Clock()
    d.start()
    try:
        mem = MemoryStore(home / "memory.db")
        a, b = mem.add("old fact"), mem.add("new fact")
        mem.close()
        w = {"X-Jarves": "1"}
        send = lambda m, p: req(d, m, p, headers=w, body={})[0].status   # noqa: E731
        from test_dashboard import req
        assert send("DELETE", f"/api/memory/{a}") == 200
        clock.t += 23 * 3600
        assert send("DELETE", f"/api/memory/{b}") == 200           # a newer entry, 1 h from the first one's expiry
        clock.t += 3600 + 60                                        # the first entry is now 24 h 1 min old
        assert dashboard.purge_memory_undo(d.ctx) == 1              # housekeeping drops only the expired one
        raw = json.loads(d.ctx.store.meta("memory_undo"))
        assert len(raw) == 1 and raw[0]["rows"][0]["text"] == "new fact"
        assert send("POST", "/api/memory/undo") == 200              # still works within 24 h
        assert send("POST", "/api/memory/undo") == 409              # the expired one is gone, not restorable
        assert "old fact" not in {i["text"] for i in MemoryStore(home / "memory.db").list(50)}
        # expiry also applies without a purge run: read side ignores stale entries
        assert send("DELETE", f"/api/memory/{b}") in (200, 404)
        clock.t += 24 * 3600 + 1
        assert send("POST", "/api/memory/undo") == 409
        # every dashboard start purges what is left
        clock.t += 10
        d.ctx.store._set_meta("memory_undo", json.dumps([{"op": "delete", "rows": [], "at": clock.t - 90000}]))
        d.ctx.store.commit()
        d._housekeeping()                                           # what start() runs
        assert json.loads(d.ctx.store.meta("memory_undo")) == []
    finally:
        d.stop()
