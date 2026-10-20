"""SWV4 P01 minor findings: B3, B4, S10, O-03, O-04, O-06."""
from __future__ import annotations

import logging

import pytest

from jarves import browser as br
from jarves import logs
from jarves import timers as T
from jarves.briefing import weather_place
from jarves.skills import SkillRegistry
from jarves.skills.google import GoogleAuthError, calendar_guard, register_google
from jarves.skills.music import FakePlayer, MusicSkill
from jarves.userdata import UserDB


@pytest.fixture
def levels(monkeypatch):
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(logs, "log", lambda comp, code, msg, level=logging.INFO, **f: seen.append((code, level)))
    return seen


# ---- B3
def test_calendar_delete_refusal_is_info_not_error(levels):
    with pytest.raises(GoogleAuthError) as e:
        calendar_guard("DELETE", "https://www.googleapis.com/calendar/v3/calendars/primary/events/x")
    assert e.value.code == "CALENDAR_DELETE_FORBIDDEN"
    assert ("CALENDAR_DELETE_FORBIDDEN", logging.INFO) in levels
    assert all(lv < logging.WARNING for c, lv in levels if c == "CALENDAR_DELETE_FORBIDDEN")


def test_selfcheck_does_not_count_the_guard_refusal_as_error():
    from jarves.selfcheck import OWN_RULE
    assert "CALENDAR_DELETE_FORBIDDEN" in OWN_RULE


# ---- B4
def test_weather_place_both_languages():
    assert weather_place("Tel Aviv", "en") == "in Tel Aviv"
    assert weather_place("Tel Aviv", "he") == "בתל אביב"
    assert weather_place("Jerusalem", "he") == "בירושלים"
    assert weather_place("Haifa", "he") == "בחיפה"
    assert weather_place("Beer Sheva", "he") == "בבאר שבע"
    assert weather_place("Springfield", "he") == "ב-Springfield"          # unknown Latin name: separator, no glue
    assert weather_place("חיפה", "he") == "בחיפה"
    for c in ("Tel Aviv", "Springfield"):
        assert "בT" not in weather_place(c, "he") and "בS" not in weather_place(c, "he")


# ---- S10
async def test_pause_right_after_a_slow_track_change_is_confirmed_when_the_player_settles():
    class Slow(FakePlayer):
        presses = 0

        async def pause(self):
            self.presses += 1
            if self.presses >= 3:                   # the player only takes the third press (~4.5 s after next)
                await super().pause()
    p = Slow()
    await p.play_track(p.catalog[0])
    skill = MusicSkill(p, verify_tries=16)
    tools = {t.name: t for t in skill.tools()}
    res = await tools["music_next"].handler(type("A", (), {})())      # a fast next, then pause right away
    assert res.ok
    res = await tools["music_pause"].handler(type("A", (), {})())
    assert res.ok and p.presses == 3 and p.np.paused


# ---- O-03
async def test_google_connect_help_is_ok_when_not_connected(tmp_path):
    reg = SkillRegistry()
    register_google(reg, tmp_path)
    res = await reg.call("google_connect_help", {})
    assert res.ok and res.code == "GOOGLE_NOT_CONNECTED" and res.data["connected"] is False


# ---- O-04
@pytest.mark.parametrize("result,expected", [("blocked", logging.INFO), ("declined", logging.INFO),
                                             ("ok", logging.INFO), ("failed", logging.WARNING)])
def test_browser_decisions_log_at_info(levels, result, expected):
    svc = object.__new__(br.BrowserService)
    svc.activity, svc.health, svc._page = [], None, None
    svc._note("open", "example.com", result)
    assert levels[-1] == ("BROWSER_OPEN", expected)


# ---- O-06
def test_due_timer_without_audio_route_shows_ringing(tmp_path):
    db = UserDB(tmp_path / "m.db")
    now = [1000.0]
    tm = T.Timers(db, clock=lambda: now[0])
    t = tm.start_timer(60, "pasta")
    assert tm.view(tm.get(t["id"]))["state"] == "running"
    now[0] += 61
    v = tm.view(tm.get(t["id"]))
    assert v["state"] == "ringing" and v["seconds_left"] == 0 and "מצלצל" in v["left_he"]
    db.close()


def test_cli_calendar_create_exits_1_when_google_is_not_connected(tmp_path, monkeypatch, capsys):
    """V4-CLI-1: `jarvis calendar create` printed {"ok": true, "code": "GOOGLE_NOT_CONNECTED"} and exited 0 although
    nothing was created."""
    from jarves import cli
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    rc = cli.main(["calendar", "create", "--title", "QA", "--day", "tomorrow", "--start", "10:00"])
    out = capsys.readouterr().out
    assert rc == 1 and "GOOGLE_NOT_CONNECTED" in out
