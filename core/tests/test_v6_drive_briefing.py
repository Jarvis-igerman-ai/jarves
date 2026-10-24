"""V6 P5 (#211): pre-drive briefing: passenger privacy, ask-first, no location, independent sections."""
from __future__ import annotations

import os

import pytest

from channels_fakes import weather_server
from jarves import briefing, drive_briefing as db_mod
from jarves import features as feat
from jarves.skills import NoArgs, Skill, SkillRegistry, Tool, ToolResult
from jarves.userdata import open_db

SECRET_TITLE = "פגישה סודית עם עו\"ד כהן"


class FakeG(Skill):
    name = "calendar"

    def __init__(self, cal_ok=True):
        self.cal_ok = cal_ok

    def tools(self):
        async def nxt(_):
            if not self.cal_ok:
                return ToolResult(False, "צריך לחבר מחדש", code="GOOGLE_RECONNECT")
            return ToolResult(True, "x", {"minutes_from_now": 25, "untrusted_events": [
                {"when": "Sun 25.10 09:00", "title": SECRET_TITLE, "location": "רחוב הסוד 5", "calendar": "c"}]})

        async def mail(_):
            return ToolResult(True, "m", {"count": 3, "senders_for_speech": ["דנה", "הבנק"]})
        return [Tool("calendar_next_event", "n", nxt, NoArgs, permission="read", untrusted_output=True),
                Tool("gmail_search", "m", mail, NoArgs, permission="read", untrusted_output=True)]


@pytest.fixture(autouse=True)
def env():
    old = os.environ.pop(briefing.WEATHER_BASE_ENV, None)
    yield
    os.environ.pop(briefing.WEATHER_BASE_ENV, None)
    if old:
        os.environ[briefing.WEATHER_BASE_ENV] = old


def make(tmp_path, cal_ok=True):
    d = open_db(tmp_path)
    reg = SkillRegistry()
    reg.register(FakeG(cal_ok))
    db_mod.register_drive_briefing(reg, d)
    return reg, d


async def test_default_is_ask_first_and_reads_nothing(tmp_path):
    reg, d = make(tmp_path)
    r = await reg.call("drive_briefing_get", {}, origin="user")
    assert r.code == "DRIVE_BRIEFING_ASK" and r.data["sections"] == []
    assert SECRET_TITLE not in r.message and "דנה" not in r.message
    d.close()


async def test_alone_gives_event_weather_and_message_count_without_senders_or_place(tmp_path):
    srv = weather_server()
    try:
        os.environ[briefing.WEATHER_BASE_ENV] = srv.url
        reg, d = make(tmp_path)
        r = await reg.call("drive_briefing_get", {"passenger": "alone"}, origin="user")
        assert r.ok and r.code == "DRIVE_BRIEFING_OK" and r.data["location_used"] is False
        ids = [s["id"] for s in r.data["sections"]]
        assert ids == ["calendar", "weather", "messages"]
        assert SECRET_TITLE in r.message and "3 הודעות" in r.message
        assert "דנה" not in r.message and "הבנק" not in r.message and "הסוד" not in r.message
        q = srv.requests[0]["path"]
        assert "latitude=32.0853" in q                 # configured place only, never a device position
        d.close()
    finally:
        srv.close()


async def test_passenger_present_drops_titles_places_and_messages(tmp_path):
    srv = weather_server()
    try:
        os.environ[briefing.WEATHER_BASE_ENV] = srv.url
        reg, d = make(tmp_path)
        r = await reg.call("drive_briefing_get", {"passenger": "present"}, origin="user")
        assert r.ok and [s["id"] for s in r.data["sections"]] == ["calendar", "weather"]
        blob = repr(r.data) + r.message
        for secret in (SECRET_TITLE, "הסוד", "דנה", "הבנק", "הודעות", "untrusted_title"):
            assert secret not in blob
        assert "25 דקות" in r.message
        d.close()
    finally:
        srv.close()


async def test_setting_drives_default_and_argument_overrides_it(tmp_path):
    reg, d = make(tmp_path, )
    sk = reg.skills["drive_briefing"]
    assert sk.mode() == "ask"
    d.set_setting("drive_briefing.passenger", "present")
    assert sk.mode() == "present" and sk.mode("alone") == "alone" and sk.mode("bogus") == "ask"
    d.set_setting("drive_briefing.passenger", "garbage")
    assert sk.mode() == "ask"
    d.close()


async def test_sections_fail_independently_and_never_invent(tmp_path):
    os.environ[briefing.WEATHER_BASE_ENV] = "http://127.0.0.1:1"
    reg, d = make(tmp_path, cal_ok=False)
    r = await reg.call("drive_briefing_get", {"passenger": "alone"}, origin="user")
    assert r.ok and r.data["partial"]
    by = {s["id"]: s for s in r.data["sections"]}
    assert by["messages"]["ok"] and not by["calendar"]["ok"] and not by["weather"]["ok"]
    assert "לא זמין" in r.message
    d.close()


async def test_nothing_available_is_an_honest_failure(tmp_path):
    os.environ[briefing.WEATHER_BASE_ENV] = "http://127.0.0.1:1"
    d = open_db(tmp_path)
    reg = SkillRegistry()
    db_mod.register_drive_briefing(reg, d)         # no Google at all
    r = await reg.call("drive_briefing_get", {"passenger": "present"}, origin="user")
    assert not r.ok and r.code == "DRIVE_BRIEFING_EMPTY"
    d.close()


async def test_untrusted_so_no_side_effect_in_same_turn(tmp_path):
    reg, d = make(tmp_path)
    t = {x.name: x for x in reg.tools()}["drive_briefing_get"]
    assert t.untrusted_output and t.permission == "read"
    d.close()


def test_filter_is_pure_and_unknown_mode_passes_nothing():
    secs = [{"id": "messages", "ok": True, "text": "x"}, {"id": "weather", "ok": True, "text": "w"}]
    assert db_mod.passenger_filter(secs, "weird") == []
    assert [s["id"] for s in db_mod.passenger_filter(secs, "present")] == ["weather"]


def test_feature_is_off_by_default_and_unverified():
    f = feat.CATALOG["drive_briefing"]
    assert f.default_enabled is False
    assert f.targets["windows"]["status"] == "implemented_unverified"
