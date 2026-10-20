"""V3 P1 basics (Issue #17): the one-time tray notice, the Sunday-Thursday workweek, timers/alarms spoken by the Core
with no face, shopping/to-do lists and topic-based `jarvis help <topic>`. Offline, silent, fake clock/audio."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import open_face
from test_runtime import FakeTray, make_core, until

from jarves import cli, guides
from jarves import lists as L
from jarves import runtime as rt_mod
from jarves import scheduler as S
from jarves import timers as T
from jarves.skills import SkillRegistry
from jarves.userdata import UserDB

JLM = ZoneInfo("Asia/Jerusalem")


@pytest.fixture
def db(tmp_path):
    d = UserDB(tmp_path / "memory.db")
    yield d
    d.close()


# ---------------------------------------------------------------------- (1) one-time tray notice
async def test_tray_notice_once_on_first_window_close(home, cfg):
    core = make_core(home, cfg, runtime_mode="background", tray_factory=FakeTray)
    await core.start()
    try:
        tray = core.runtime.tray
        assert not core.runtime.prefs["tray_notice_shown"]
        for n in (1, 2):
            face = await open_face(core)
            await face.send("desktop.close")
            await face.wait("desktop.closed")
            await face.ws.close()
            await asyncio.sleep(0.15)
            assert len(tray.notes) == 1, n                       # second close: no second notice
        assert "במגש" in tray.notes[0] and "tray" in tray.notes[0]
        assert rt_mod.read_state(home)["prefs"]["tray_notice_shown"] is True    # survives a restart
    finally:
        await core.stop()


async def test_no_tray_means_no_false_notice(home, cfg):
    core = make_core(home, cfg)                                  # foreground: no tray icon
    await core.start()
    try:
        face = await open_face(core)
        await face.send("desktop.close")
        await face.wait("desktop.closed")
        await face.ws.close()
        await asyncio.sleep(0.15)
        assert core.runtime.tray is None and not core.runtime.prefs["tray_notice_shown"]
    finally:
        await core.stop()


# ---------------------------------------------------------------------- (2) workweek
def test_workweek_default_sun_thu_and_setting_changes_new_jobs(db):
    s = S.Scheduler(db, clock=lambda: datetime(2026, 10, 19, 9, 0, tzinfo=JLM).timestamp())
    assert db.setting("scheduler.workweek") == "0-4"
    j = s.preview("כל ימי חול בשבע", "להגיד את מזג האוויר", action="reminder")
    assert j["spec"].endswith("0-4")
    db.set_setting("scheduler.workweek", "1-5")
    j = s.preview("כל ימי חול בשבע", "להגיד את מזג האוויר", action="reminder")
    assert j["spec"].endswith("1-5")


def test_workweek_setting_rejects_junk_from_the_dashboard(home):
    from jarves import dashboard
    from jarves.config import Config
    ctx = dashboard.Ctx(home, Config())
    try:
        assert S.api_sched_settings(ctx, None, {"workweek": "bogus"})[0] == 400
        st, d = S.api_sched_settings(ctx, None, {"workweek": "1-5"})
        assert st == 200 and d["settings"]["scheduler.workweek"] == "1-5"
    finally:
        ctx.close()


# ---------------------------------------------------------------------- (3) timers / alarms
class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


async def test_timer_rings_once_and_survives_restart(db):
    clk = Clock(datetime(2026, 10, 20, 9, 0, tzinfo=JLM).timestamp())
    tm = T.Timers(db, clock=clk, tz=lambda: JLM)
    t = tm.start_timer(600, "פסטה")
    rung = []

    async def deliver(row, text):
        rung.append(text)
        return True
    assert await tm.poll(deliver) == 0
    clk.t += 601
    again = T.Timers(db, clock=clk, tz=lambda: JLM)              # a "restart": new object, same database
    assert await again.poll(deliver) == 1 and await again.poll(deliver) == 0
    assert rung == ["הטיימר של פסטה הסתיים"] and not again.pending()


async def test_late_timer_is_missed_and_unspoken_ring_is_retried(db):
    clk = Clock(1_000_000.0)
    tm = T.Timers(db, clock=clk, tz=lambda: JLM)
    old = tm.start_timer(5)
    clk.t += 5 + T.LATE_MAX_S + 1                                # the Core was off for a long time
    got = []

    async def deliver(row, text):
        got.append(text)
        return True
    assert await tm.poll(deliver) == 0 and not got and tm.get(old["id"])["status"] == "missed"
    fresh = tm.start_timer(5)
    clk.t += 6
    busy = [False, True]

    async def flaky(row, text):
        return busy.pop(0)
    assert await tm.poll(flaky) == 0 and tm.get(fresh["id"])["status"] == "pending"    # audio busy: try again
    assert await tm.poll(flaky) == 1 and tm.get(fresh["id"])["status"] == "fired"


def test_alarm_next_occurrence_and_validation(db):
    clk = Clock(datetime(2026, 10, 20, 9, 0, tzinfo=JLM).timestamp())
    tm = T.Timers(db, clock=clk, tz=lambda: JLM)
    a = tm.set_alarm("07:30")                                    # already past today: tomorrow
    assert datetime.fromtimestamp(a["due"], JLM).strftime("%Y-%m-%d %H:%M") == "2026-10-21 07:30"
    b = tm.set_alarm("23:00", "ערב")
    assert datetime.fromtimestamp(b["due"], JLM).strftime("%d %H:%M") == "20 23:00"
    for bad in ("25:00", "7", "ab:cd"):
        with pytest.raises(ValueError):
            tm.set_alarm(bad)
    with pytest.raises(ValueError):
        tm.start_timer(0)
    assert sorted(t["id"] for t in tm.cancel(label="alarm")) == [a["id"], b["id"]] and not tm.pending()


async def test_timer_skill_tools(db):
    tm = T.Timers(db, tz=lambda: JLM)
    reg = SkillRegistry()
    reg.register(T.make_skill(tm))
    reg.allowed_actions |= T.ALLOWED_ACTIONS
    r = await reg.call("timer_start", {"minutes": 2, "label": "תה"}, origin="model")
    assert r.ok and r.data["seconds_left"] in (119, 120)
    r = await reg.call("timers_list", {}, origin="model")
    assert len(r.data["timers"]) == 1
    assert (await reg.call("alarm_set", {"at": "99:99"}, origin="model")).code == "ALARM_BAD"
    assert (await reg.call("timer_cancel", {"label": "תה"}, origin="model")).ok
    assert (await reg.call("timer_cancel", {"label": "תה"}, origin="model")).code == "TIMER_NOT_FOUND"


async def test_core_speaks_timer_with_the_face_closed(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    try:
        assert core.link.face_client_id() is None                # no face connected at all
        r = await core.registry.call("timer_start", {"seconds": 1, "label": "ביצים"}, origin="model")
        assert r.ok
        await until(lambda: any("ביצים" in s for s in core.tts.spoken), timeout=6)
    finally:
        await core.stop()


# ---------------------------------------------------------------------- (4) lists
def test_lists_add_read_tick_remove_clear(db):
    ls = L.Lists(db)
    r = ls.add("קניות", ["חלב", "לחם", "חלב"])
    assert r["list"] == "shopping" and r["added"] == ["חלב", "לחם"]
    assert ls.add("shopping", ["חלב"])["already_there"] == ["חלב"]
    ls.add("משימות", ["להתקשר לאינסטלטור"])
    assert [x["name"] for x in ls.names()] == ["shopping", "todo"]
    assert ls.check("קניות", "חלב") == ["חלב"]
    assert [i["text"] for i in ls.items("shopping")] == ["לחם"]
    assert len(ls.items("shopping", include_done=True)) == 2
    assert ls.clear("shopping", only_done=True) == 1
    assert ls.remove("shopping", "לחם") == ["לחם"] and not ls.items("shopping")
    with pytest.raises(ValueError):
        ls.add("shopping", ["  "])


async def test_lists_skill_and_clear_needs_approval(db):
    ls = L.Lists(db)
    reg = SkillRegistry()
    reg.register(L.make_skill(ls))
    reg.allowed_actions |= L.ALLOWED_ACTIONS
    r = await reg.call("list_add", {"list_name": "קניות", "items": ["ביצים", "עגבניות"]}, origin="model")
    assert r.ok
    r = await reg.call("list_show", {"list_name": "קניות"}, origin="model")
    assert "ביצים" in r.message and len(r.data["items"]) == 2
    assert (await reg.call("list_check", {"list_name": "קניות", "item": "ביצים"}, origin="model")).ok
    assert (await reg.call("list_check", {"list_name": "קניות", "item": "פיל"}, origin="model")).code == "LIST_ITEM_NOT_FOUND"
    asked = []

    async def deny(prompt):
        asked.append(prompt)
        return False
    reg.confirm = deny
    await reg.call("list_clear", {"list_name": "קניות", "only_done": False}, origin="model")
    assert asked and len(ls.items("shopping")) == 1             # not cleared without approval


# ---------------------------------------------------------------------- (5) jarvis help <topic>
def test_help_topic_cli(capsys):
    assert cli.main(["help"]) == 0
    out = capsys.readouterr().out
    assert "timers" in out and "lists" in out and "google_calendar" in out
    assert cli.main(["help", "timers"]) == 0
    out = capsys.readouterr().out
    assert "טיימרים" in out and "Timers and alarms" in out and "Steps" in out
    assert cli.main(["help", "קניות"]) == 0
    assert "Shopping and to-do lists" in capsys.readouterr().out
    assert cli.main(["help", "no", "such", "zzqqx"]) == 1
    assert "unknown topic" in capsys.readouterr().err
    assert guides.find("ימי חול").id == "scheduler"
