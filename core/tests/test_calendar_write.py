"""Google Calendar WRITE (SW-V2 D05, WP-M4), fully offline: a fake Calendar v3 (httpx.MockTransport). Create and
update need the user's approval of an exact read-back; recurring events never change as a whole series silently;
there is NO delete path (model tools, registry, REST client, dashboard)."""
import asyncio
import copy
import http.client
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from jarves import dashboard, features as feat
from jarves.config import Config
from jarves.google_auth import ALL_SCOPES, CALENDAR_SCOPES, CALENDAR_WRITE, GoogleAuth, GoogleAuthError
from jarves.guardrails import check_tool
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult
from jarves.skills.google import (CalendarWriter, CalendarWriteSkill, GoogleApi, calendar_guard, register_google)

TZ = timezone(timedelta(hours=3))
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)          # Monday
CAL = "https://www.googleapis.com/calendar/v3"
CLIENT = {"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test"}}
EVENTS = {
    "me@example.com": [
        {"id": "single1", "status": "confirmed", "summary": "רופא שיניים",
         "start": {"dateTime": "2026-10-21T09:00:00+03:00"}, "end": {"dateTime": "2026-10-21T09:30:00+03:00"}},
        {"id": "yoga_20261022", "recurringEventId": "yoga", "status": "confirmed", "summary": "יוגה",
         "start": {"dateTime": "2026-10-22T18:00:00+03:00"}, "end": {"dateTime": "2026-10-22T19:00:00+03:00"}},
    ]}
MASTERS = {"yoga": {"id": "yoga", "summary": "יוגה", "recurrence": ["RRULE:FREQ=WEEKLY"],
                    "start": {"dateTime": "2026-09-03T18:00:00+03:00", "timeZone": "Asia/Jerusalem"},
                    "end": {"dateTime": "2026-09-03T19:00:00+03:00", "timeZone": "Asia/Jerusalem"}}}


class FakeCalendar:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.writes: list[tuple[str, str, dict]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path = req.url.path
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3599})
        if path == "/calendar/v3/users/me/calendarList":
            return httpx.Response(200, json={"items": [
                {"id": "me@example.com", "summary": "me@example.com", "primary": True, "accessRole": "owner"},
                {"id": "family@group.calendar.google.com", "summary": "Family", "summaryOverride": "משפחה",
                 "accessRole": "writer"},
                {"id": "holidays", "summary": "Holidays", "accessRole": "reader"}]})
        m = re.fullmatch(r"/calendar/v3/calendars/([^/]+)/events(?:/([^/]+))?", path)
        if m is None:
            return httpx.Response(404, json={"error": {"code": 404}})
        cid, eid = m.group(1), m.group(2)
        if req.method == "GET" and eid is None:
            return httpx.Response(200, json={"items": copy.deepcopy(EVENTS.get(cid, []))})
        if req.method == "GET":
            base = MASTERS.get(eid) or next(e for e in EVENTS[cid] if e["id"] == eid)
            return httpx.Response(200, json={"etag": '"revision1"', **copy.deepcopy(base)})
        body = json.loads(req.content)
        if req.headers.get("If-Match") and getattr(self, "changed", False):
            return httpx.Response(412, json={"error": {"code": 412, "status": "conditionNotMet"}})
        self.writes.append((req.method, path, body))
        if req.method == "POST":
            return httpx.Response(200, json={"id": "new1", "status": "confirmed",
                                            "htmlLink": "https://www.google.com/calendar/event?eid=real-google-id", **body})
        if req.method == "PATCH":
            base = MASTERS.get(eid) or next(e for e in EVENTS[cid] if e["id"] == eid)
            return httpx.Response(200, json={**copy.deepcopy(base), **body})
        return httpx.Response(405)

    def transport(self):
        return httpx.MockTransport(self.handler)


def seed(home: Path, scopes) -> None:
    (home / "google").mkdir(parents=True, exist_ok=True)
    (home / "google" / "client.json").write_text(json.dumps(CLIENT), encoding="utf-8")
    GoogleAuth(home)._save({"access_token": "ya29.at-0", "refresh_token": "1//rt", "scopes": list(scopes),
                            "expires_at": time.time() + 3600, "connected_at": time.time()})


class Confirm:
    def __init__(self, answer: bool):
        self.answer, self.prompts = answer, []

    async def __call__(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return self.answer


@pytest.fixture
def fake():
    return FakeCalendar()


@pytest.fixture
def setup(home, fake):
    seed(home, CALENDAR_SCOPES + (CALENDAR_WRITE,))
    api = GoogleApi(GoogleAuth(home, transport=fake.transport()), fake.transport())
    reg = SkillRegistry()
    reg.register(CalendarWriteSkill(CalendarWriter(api, now=lambda: NOW, tz=TZ)))
    return reg


# ---------------------------------------------------------------------- create
async def test_create_reads_back_everything_and_writes_only_after_approval(setup, fake):
    setup.confirm = Confirm(False)
    args = {"title": "ארוחה עם אמא", "day": "חמישי", "start": "19:30", "duration_min": 90, "calendar": "משפחה",
            "recurrence": "weekly", "count": 4, "reminder_minutes": [30]}
    r = await setup.call("calendar_create_event", args)
    assert r.code == "TOOL_DECLINED" and fake.writes == []
    p = setup.confirm.prompts[0]
    for part in ("משפחה", "ארוחה עם אמא", "יום חמישי 22.10.2026", "19:30-21:00", "Asia/Jerusalem", "כל שבוע",
                 "4 פעמים", "30 דקות לפני"):
        assert part in p, (part, p)
    setup.confirm = Confirm(True)
    r = await setup.call("calendar_create_event", args)
    assert r.ok, r
    assert len(fake.writes) == 1
    method, path, body = fake.writes[0]
    assert method == "POST" and "family%40group.calendar.google.com" in path or "family@group" in path
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=4"]
    assert body["start"] == {"dateTime": "2026-10-22T19:30:00+03:00", "timeZone": "Asia/Jerusalem"}
    assert body["reminders"] == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]}
    assert r.data["stored"]["id"] == "new1" and "יצרתי" in r.message          # read back from what Google stored


async def test_create_all_day_and_reader_calendar_refused(setup, fake):
    setup.confirm = Confirm(True)
    r = await setup.call("calendar_create_event", {"title": "חופש", "day": "2026-11-03"})
    assert r.ok and fake.writes[0][2]["start"] == {"date": "2026-11-03"}
    r = await setup.call("calendar_create_event", {"title": "x", "day": "מחר", "start": "10:00", "calendar": "Holidays"})
    assert not r.ok and r.code == "CALENDAR_UNKNOWN" and len(fake.writes) == 1


async def test_create_guests_confirmed_before_invitations_and_returns_google_link(setup, fake):
    args = {"title": "Dinner", "day": "tomorrow", "guests": ["friend@example.com", "FRIEND@example.com"]}
    async def approve(prompt):
        assert fake.writes == []
        assert "friend@example.com" in prompt
        return True
    setup.confirm = approve
    result = await setup.call("calendar_create_event", args)
    assert result.ok
    assert fake.writes[-1][2]["attendees"] == [{"email": "friend@example.com"}]
    assert fake.calls[-1].url.params["sendUpdates"] == "all"
    assert result.data["stored"]["guests"] == ["friend@example.com"]
    assert result.data["stored"]["htmlLink"] == "https://www.google.com/calendar/event?eid=real-google-id"


@pytest.mark.parametrize("guests", [["not-an-email"], ["Name <friend@example.com>"],
                                    ["friend@example.com\r\nBcc:other@example.com"], ["a@example.com,b@example.com"],
                                    ["a\x00@example.com"]])
async def test_invalid_guests_rejected_without_confirmation_or_requests(setup, fake, guests):
    setup.confirm = Confirm(True)
    result = await setup.call("calendar_create_event", {"title": "Dinner", "day": "tomorrow", "guests": guests})
    assert result.code == "TOOL_BAD_ARGS" and not fake.calls and not setup.confirm.prompts


async def test_share_preserves_guests_and_requires_exact_confirmation(setup, fake, monkeypatch):
    existing = {"email": "existing@example.com", "responseStatus": "accepted", "optional": True}
    monkeypatch.setitem(EVENTS["me@example.com"][0], "attendees", [existing])
    monkeypatch.setitem(EVENTS["me@example.com"][0], "htmlLink", "https://calendar.google.com/calendar/event?eid=stored")
    handles = await _handles(setup)
    handle = handles["רופא שיניים"]["handle"]
    assert handles["רופא שיניים"]["htmlLink"] == "https://calendar.google.com/calendar/event?eid=stored"
    args = {"handle": handle, "guests": ["new@example.com"]}
    setup.confirm = Confirm(False)
    assert (await setup.call("calendar_share_event", args)).code == "TOOL_DECLINED"
    assert not fake.writes
    for value in ("new@example.com", "existing@example.com", "רופא שיניים", "21.10.2026", "09:00"):
        assert value in setup.confirm.prompts[0]
    setup.confirm = Confirm(True)
    result = await setup.call("calendar_share_event", args)
    assert result.ok
    assert fake.writes[-1][2] == {"attendees": [existing, {"email": "new@example.com"}]}
    assert fake.calls[-1].headers["If-Match"] == '"revision1"'
    assert fake.calls[-1].url.params["sendUpdates"] == "all"
    assert result.data["stored"]["guests"] == ["existing@example.com", "new@example.com"]
    assert result.data["stored"]["htmlLink"] == "https://calendar.google.com/calendar/event?eid=stored"


async def test_share_recurring_event_needs_explicit_scope_and_full_guest_snapshot(setup, fake, monkeypatch):
    handles = await _handles(setup)
    setup.confirm = Confirm(True)
    args = {"handle": handles["יוגה"]["handle"], "guests": ["new@example.com"]}
    assert (await setup.call("calendar_share_event", args)).code == "CALENDAR_SCOPE_NEEDED"
    assert not fake.writes
    monkeypatch.setitem(MASTERS["yoga"], "attendeesOmitted", True)
    assert (await setup.call("calendar_share_event", {**args, "scope": "series"})).code == "CALENDAR_INCOMPLETE_EVENT"
    assert not fake.writes


async def test_share_does_not_overwrite_guest_changes_during_confirmation(setup, fake):
    handles = await _handles(setup)
    async def approve(_):
        assert not fake.writes
        fake.changed = True           # another Calendar client changes guests while the user reviews the plan
        return True
    setup.confirm = approve
    result = await setup.call("calendar_share_event", {"handle": handles["רופא שיניים"]["handle"],
                                                      "guests": ["new@example.com"]})
    assert not result.ok and result.code == "GOOGLE_API_ERROR" and not fake.writes
    patches = [call for call in fake.calls if call.method == "PATCH"]
    assert len(patches) == 1 and patches[0].headers["If-Match"] == '"revision1"'


@pytest.mark.parametrize("link,valid", [("https://www.google.com/calendar/event?eid=abc", True),
                                        ("https://calendar.google.com/calendar/event?eid=abc", True),
                                        (None, False), ("http://calendar.google.com/calendar/event?eid=x", False),
                                        ("https://calendar.google.com.evil.test/calendar/event?eid=x", False),
                                        ("https://calendar.google.com@evil.test/calendar/event?eid=x", False),
                                        ("https://calendar.google.com:444/calendar/event?eid=x", False),
                                        ("https://www.google.com/redirect?url=https://evil.test", False),
                                        ("https://calendar.google.com/calendar/event?eid=x\n", False)])
async def test_found_event_link_is_real_or_absent_without_synthesis(setup, monkeypatch, link, valid):
    monkeypatch.setitem(EVENTS["me@example.com"][0], "htmlLink", link)
    handles = await _handles(setup)
    assert handles["רופא שיניים"]["htmlLink"] == (link if valid else None)


@pytest.mark.parametrize("path,body", [("/calendars/primary/acl", {"role": "owner"}),
                                     ("/calendars/primary/events/e1", {"guestsCanModify": True}),
                                     ("/users/me/calendarList/primary", {"selected": True})])
def test_calendar_guard_blocks_acl_and_unapproved_event_metadata(path, body):
    with pytest.raises(GoogleAuthError, match="unsupported calendar write"):
        calendar_guard("PATCH", CAL + path, body)


async def test_model_writes_refused_right_after_reading_calendar_text(setup, fake):
    setup.confirm = Confirm(True)
    assert (await setup.call("calendar_find_events", {"day": "week"})).ok
    r = await setup.call("calendar_create_event", {"title": "x", "day": "מחר", "start": "10:00"})
    assert r.code == "TOOL_UNTRUSTED_TURN" and fake.writes == []


# ---------------------------------------------------------------------- update and recurrence scope
async def _handles(reg) -> dict:
    r = await reg.call("calendar_find_events", {"day": "week"})
    reg._untrusted_at = None                          # the user speaks again (next turn)
    return {e["title"]: e for e in r.data["untrusted_events"]}


async def test_recurring_update_needs_an_explicit_scope(setup, fake):
    setup.confirm = Confirm(True)
    h = await _handles(setup)
    assert h["יוגה"]["recurring"] and not h["רופא שיניים"]["recurring"]
    r = await setup.call("calendar_update_event", {"handle": h["יוגה"]["handle"], "start": "19:00"})
    assert not r.ok and r.code == "CALENDAR_SCOPE_NEEDED" and fake.writes == []
    assert "כל הסדרה" in r.message


async def test_update_this_occurrence_patches_only_the_instance(setup, fake):
    setup.confirm = Confirm(True)
    h = await _handles(setup)
    r = await setup.call("calendar_update_event", {"handle": h["יוגה"]["handle"], "scope": "this", "start": "19:00"})
    assert r.ok, r
    method, path, body = fake.writes[0]
    assert method == "PATCH" and path.endswith("/events/yoga_20261022")
    assert body["start"]["dateTime"] == "2026-10-22T19:00:00+03:00" and body["end"]["dateTime"].startswith("2026-10-22T20:00")
    assert "רק הפעם הזאת" in setup.confirm.prompts[0]


async def test_series_update_keeps_the_series_start_and_says_all_occurrences(setup, fake):
    setup.confirm = Confirm(True)
    h = await _handles(setup)
    r = await setup.call("calendar_update_event", {"handle": h["יוגה"]["handle"], "scope": "series", "start": "17:30"})
    assert r.ok, r
    method, path, body = fake.writes[0]
    assert path.endswith("/events/yoga") and body["start"]["dateTime"] == "2026-09-03T17:30:00+03:00"
    assert "כל הסדרה" in setup.confirm.prompts[0]
    r = await setup.call("calendar_update_event", {"handle": h["יוגה"]["handle"], "scope": "series", "day": "שישי"})
    assert r.code == "CALENDAR_SERIES_DATE" and len(fake.writes) == 1


async def test_update_declined_and_unknown_handle(setup, fake):
    setup.confirm = Confirm(False)
    h = await _handles(setup)
    r = await setup.call("calendar_update_event", {"handle": h["רופא שיניים"]["handle"], "title": "רופא"})
    assert r.code == "TOOL_DECLINED" and fake.writes == []
    assert "אירוע יחיד" in setup.confirm.prompts[0] and "רופא שיניים" in setup.confirm.prompts[0]
    setup.confirm = Confirm(True)
    assert (await setup.call("calendar_update_event", {"handle": "e99", "title": "x"})).code == "CALENDAR_UNKNOWN_EVENT"


# ---------------------------------------------------------------------- no delete, anywhere
def test_no_delete_tool_exists(setup):
    names = [t.name for t in setup.tools()]
    assert names == ["calendar_find_events", "calendar_create_event", "calendar_update_event", "calendar_share_event"]
    assert not any(re.search(r"delet|remov|cancel|clear|move", n) for n in names)


@pytest.mark.parametrize("method,url,body", [
    ("DELETE", f"{CAL}/calendars/primary/events/e1", None),
    ("delete", f"{CAL}/calendars/primary/events/e1?sendUpdates=all", None),
    ("POST", f"{CAL}/calendars/primary/events/e1/move", None),
    ("POST", f"{CAL}/calendars/primary/clear", None),
    ("DELETE", f"{CAL}/users/me/calendarList/primary", None),
    ("PATCH", f"{CAL}/calendars/primary/events/e1", {"status": "cancelled"}),
    ("PUT", f"{CAL}/calendars/primary/events/e1", {"status": "CANCELLED", "summary": "x"}),
])
async def test_rest_client_refuses_every_delete_path_before_the_network(home, fake, method, url, body):
    seed(home, CALENDAR_SCOPES + (CALENDAR_WRITE,))
    api = GoogleApi(GoogleAuth(home, transport=fake.transport()), fake.transport())
    with pytest.raises(GoogleAuthError) as e:
        await api.request(method, url, (CALENDAR_WRITE,), json_body=body)
    assert e.value.code == "CALENDAR_DELETE_FORBIDDEN" and fake.calls == []
    calendar_guard("PATCH", f"{CAL}/calendars/primary/events/e1", {"summary": "ok"})        # normal update passes


def test_generated_skill_with_a_calendar_delete_tool_cannot_register():
    class Generated(Skill):
        name = "my_calendar_helper"

        def tools(self):
            async def go(_):
                return ToolResult(True, "deleted")
            return [Tool("calendar_delete_event", "delete an event", go, permission="destructive")]
    reg = SkillRegistry()
    with pytest.raises(ValueError, match="D05"):
        reg.register(Generated())
    assert "my_calendar_helper" not in reg.skills
    for bad in ("remove_event", "events_clear", "cancel_meeting_event", "calendar_purge", "move_event"):
        assert check_tool("calendar", bad) is not None
    for ok in ("calendar_create_event", "calendar_update_event", "music_stop", "task_cancel", "forget"):
        assert check_tool("calendar" if ok.startswith("calendar") else "x", ok) is None


def test_write_scope_only_on_request_and_switch_default_off(home):
    assert CALENDAR_WRITE not in ALL_SCOPES                        # a normal login never asks for writes
    f = feat.Features(Config(), home)
    st = f.status("calendar_write")
    assert st["enabled"] is False and st["risk"] == "high"
    assert f.owner("calendar_write", "calendar_create_event") == "calendar_write"


def test_register_google_adds_write_tools_only_with_the_scope(home, fake):
    seed(home, CALENDAR_SCOPES)
    reg = SkillRegistry()
    register_google(reg, home, transport=fake.transport())
    assert "calendar_write" not in reg.skills
    seed(home, CALENDAR_SCOPES + (CALENDAR_WRITE,))
    reg = SkillRegistry()
    register_google(reg, home, transport=fake.transport())
    assert "calendar_write" in reg.skills
    f = feat.Features(Config(), home, skills=lambda: set(reg.skills))
    reg.gate = f.gate
    assert reg.get("calendar_create_event") is None                # switch off: hidden and refused
    assert asyncio.run(reg.call("calendar_create_event", {"title": "x", "day": "today"})).code == "FEATURE_OFF"


# ---------------------------------------------------------------------- dashboard: preview -> commit, no delete
@pytest.fixture
def dash(home, fake):
    seed(home, CALENDAR_SCOPES + (CALENDAR_WRITE,))
    feat.Features(Config(), home).set("calendar_write", True)
    d = dashboard.Dashboard(home, Config())
    d.ctx.google_transport = fake.transport()
    d.ctx.calendar_kw = {"now": lambda: NOW, "tz": TZ}
    d.start()
    yield d
    d.stop()


def req(d, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Host": f"127.0.0.1:{d.port}", "Cookie": f"{dashboard.COOKIE}={d.url.split('t=')[1]}",
                       "X-Jarves": "1", "Content-Type": "application/json"})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data or b"{}")


def test_dashboard_calendar_preview_commit_and_no_delete_route(dash, fake):
    st, d = req(dash, "GET", "/api/calendar")
    assert st == 200 and d["write_enabled"] and "never deletes" in d["deletion"]
    assert {c["name"] for c in d["calendars"]} >= {"משפחה"} and len(d["events"]) == 2
    st, p = req(dash, "POST", "/api/calendar/preview", {"op": "create", "title": "טסט", "day": "מחר", "start": "08:00"})
    assert st == 200 and "טסט" in p["readback"] and fake.writes == []
    st, r = req(dash, "POST", "/api/calendar/commit", {"preview_id": p["preview_id"]})
    assert st == 200 and len(fake.writes) == 1 and "יצרתי" in r["message"]
    assert req(dash, "POST", "/api/calendar/commit", {"preview_id": p["preview_id"]})[0] == 409       # single use
    assert req(dash, "POST", "/api/calendar/preview", {"op": "delete", "handle": "e1"})[0] == 400
    for m in ("DELETE", "POST"):
        assert req(dash, m, "/api/calendar/events/e1")[0] == 404
    assert not any(m == "DELETE" and "calendar" in rx.pattern for m, rx, _ in dashboard._ROUTES)
    assert len(fake.writes) == 1 and not any(c.method == "DELETE" for c in fake.calls)


def test_dashboard_refuses_writes_while_the_switch_is_off(dash, home, fake):
    feat.Features(Config(), home).set("calendar_write", False)
    dash.ctx._features = None
    st, _ = req(dash, "POST", "/api/calendar/preview", {"op": "create", "title": "x", "day": "מחר"})
    assert st == 403 and fake.writes == []


def test_ui_has_no_delete_control():
    js = (Path(dashboard.STATIC) / "m4.js").read_text(encoding="utf-8")
    cal = js[js.index("async function pageCalendar"): js.index("async function pageDevices")]
    assert not re.search(r'method:\s*"DELETE"|op:\s*"delete"|"(?:Delete|Remove)[^"]*"|"[^"]*מחק[^"]*"', cal)
    assert "never deletes" in cal                       # the page says so instead
