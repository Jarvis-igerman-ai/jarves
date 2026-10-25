"""V5 cross-pillar scenarios (#187): an emulated phone (a really paired device over loopback HTTP, the real phone API)
and the desktop (the owner's dashboard session and the in-process stores) share ONE state. Fake Skills, an injected
clock, no real device, no network beyond loopback, no paid calls.

1. a memory correction made on the desktop is what the phone's Brain turn uses, and the chat keeps one history
2. a goal plan fails, the phone is told once, the owner retries it, the goal ends done on both clients
3. a proactive signal is delivered once however often the event repeats (dedupe), snooze holds later ones back
4. quiet hours hold the signals back, the phone gets nothing, and it is told again after the quiet window
"""
from __future__ import annotations

import http.client
import json
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from jarves import calm, dashboard
from jarves.config import Config
from jarves.goals import Goals
from jarves.mobile import Bridge
from jarves.preferences import Preferences, prompt_block
from jarves.skills import Skill, Tool, ToolResult
from jarves.userdata import open_db
from test_v5_goal_plans import Env, steps

HOST = "192.168.50.7"
NOON = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc).timestamp()
NIGHT = datetime(2026, 10, 22, 23, 30, tzinfo=timezone.utc).timestamp()
MORNING = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc).timestamp()


class Clock:
    def __init__(self, t=NOON):
        self.t = t

    def __call__(self):
        return self.t


def quiet(ts):
    return calm.in_quiet(datetime.fromtimestamp(ts, timezone.utc), "22:00", "07:00")


class World:
    """One PC (stores on disk), a Brain that reads the SAME learned preferences for every client, one paired phone."""

    def __init__(self, home, clock):
        from conftest import free_port
        self.clock = clock
        self.db = open_db(home)
        self.prefs = Preferences(self.db, clock=clock)
        self.goals = Goals(self.db, clock=clock)
        self.brain_saw: list[tuple[str, str]] = []                 # (client, instructions block) per Brain call

        def chat(text, dev):
            self.brain_saw.append((dev, prompt_block(self.prefs)))
            return {"reply": "ok: " + text, "ok": True}
        bridge = Bridge(chat=chat, voice=lambda w, d: {}, status=lambda: {"core": "running", "components": {}},
                        goals=lambda: self.goals, desktop_present=lambda: False)
        self.d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=bridge)
        self.d.mobile.clock = clock                                # before the first use of the continuity store
        self.d.mobile.quiet = quiet
        self.d.start()
        self.cont = self.d.mobile.cont
        st, code = self.owner("POST", "/api/mobile/code", {})
        assert st == 200
        st, got = self.phone("POST", "/api/m/pair", None, {"code": code["code"], "name": "Pixel"})
        assert st == 200
        self.token, self.device = got["token"], got["device"]
        assert self.owner("POST", f"/api/mobile/devices/{self.device}/scopes",
                          {"scopes": ["chat", "status", "tasks"]})[0] == 200

    def _call(self, host, headers, method, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.d.port, timeout=10)
        h = {"Host": f"{host}:{self.d.port}", **headers}
        if method != "GET":
            h["X-Jarves"] = "1"
            h["Content-Type"] = "application/json"
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        out = json.loads(r.read() or b"{}")
        c.close()
        return r.status, out

    def owner(self, method, path, body=None):
        return self._call("127.0.0.1", {"Cookie": f"{self.d.access.cookie}={self.d.url.split('t=')[1]}"}, method, path, body)

    def phone(self, method, path, token="self", body=None):
        tok = self.token if token == "self" else token
        return self._call(HOST, {"Authorization": "Bearer " + tok} if tok else {}, method, path, body)

    def sync(self):
        return self.phone("GET", "/api/m/sync?since=0")[1]

    def close(self):
        self.d.stop()
        self.db.close()


@pytest.fixture(autouse=True)
def _full_text():
    """The shared-history check reads the journal back, so it runs with privacy off (the default keeps no words)."""
    from jarves import logs
    before = logs.privacy()
    logs.set_privacy(False)
    yield
    logs.set_privacy(before)


@pytest.fixture
def clk():
    return Clock()


@pytest.fixture
def world(home, clk):
    w = World(home, clk)
    yield w
    w.close()


# ------------------------------------------------------------------ 1. memory correction on both clients
def test_a_memory_correction_reaches_the_phone_turn_and_the_desktop_view_alike(world):
    p = world.prefs.add_confirmed("Answer in short sentences", "style")
    world.phone("POST", "/api/m/chat", body={"text": "hi", "id": "msg-00000001"})
    assert "short sentences" in world.brain_saw[-1][1]
    st, _ = world.owner("POST", "/api/prefs/op", {"op": "edit", "id": p["item"]["id"], "text": "Answer in long, warm paragraphs"})
    assert st == 200
    world.phone("POST", "/api/m/chat", body={"text": "and now?", "id": "msg-00000002"})
    desktop_view = world.owner("GET", "/api/prefs")[1]
    assert "long, warm paragraphs" in world.brain_saw[-1][1] and "short sentences" not in world.brain_saw[-1][1]
    assert desktop_view["prompt_text"] == world.brain_saw[-1][1]           # what the desktop sends = what the phone used
    assert world.owner("POST", "/api/prefs/op", {"op": "remove", "id": p["item"]["id"]})[0] == 200
    world.phone("POST", "/api/m/chat", body={"text": "again", "id": "msg-00000003"})
    assert world.brain_saw[-1][1] == "" and world.owner("GET", "/api/prefs")[1]["prompt_text"] == ""
    n = len(world.brain_saw)                                               # a resend does not run the Brain twice
    assert world.phone("POST", "/api/m/chat", body={"text": "again", "id": "msg-00000003"})[1]["replay"] is True
    assert len(world.brain_saw) == n
    assert [t["text"] for t in world.cont.turns(0) if t["role"] == "user"] == ["hi", "and now?", "again"]


def test_a_proposed_memory_is_not_used_until_the_owner_says_yes(world):
    item = world.prefs.suggest("Call me Dana", "she said so", "name")
    world.phone("POST", "/api/m/chat", body={"text": "x", "id": "msg-00000004"})
    assert world.brain_saw[-1][1] == ""
    assert world.owner("POST", "/api/prefs/op", {"op": "confirm", "id": item["item"]["id"]})[0] == 200
    world.phone("POST", "/api/m/chat", body={"text": "y", "id": "msg-00000005"})
    assert "Call me Dana" in world.brain_saw[-1][1]


# ------------------------------------------------------------------ 2. goal: fail, tell the phone once, retry, done
async def test_a_failed_goal_plan_is_retried_and_both_clients_see_the_goal_done(world, home, clk):
    goal = world.goals.add("Tidy the notes", steps=["archive old notes"])
    env = Env(home, clock=clk)
    state = {"fail": True}

    class Flaky(Skill):
        name = "flaky"
        description = "fails once"

        def tools(self):
            class A(BaseModel):
                text: str = ""

            async def archive(a: A):
                env.ran.append(("archive", a.text))
                if state["fail"]:
                    return ToolResult(False, "disk busy", code="X_BUSY")
                return ToolResult(True, "archived")
            return [Tool("flaky_archive", "a", archive, A, permission="read")]
    env.reg.register(Flaky())
    plan = env.store.propose("tidy", steps(("flaky_archive", {"text": "old"}), ("fake_read", {"text": "after"})),
                             goal_id=goal["id"])
    env.store.approve(plan["id"])
    world.cont.configure_signals(on=True)
    await env.runner.tick()
    assert env.store.get(plan["id"])["state"] == "failed" and ("read", "after") not in env.ran
    assert world.cont.on_goal_event(world.goals.get(goal["id"]), "stalled") == "queued"   # ONE calm note, no action
    s = world.sync()
    assert [n["kind"] for n in s["notices"]] == ["signal"] and s["goals"][0]["status"] == "active"
    world.phone("POST", f"/api/m/notices/{s['notices'][0]['id']}/ack", body={})
    assert world.sync()["notices"] == []
    state["fail"] = False                                                      # the owner fixes it and retries on the PC
    env.store.retry(plan["id"])
    await env.runner.tick()
    p = env.store.get(plan["id"])
    assert p["state"] == "done" and ("read", "after") in env.ran and p["steps"][0]["attempts"] == 2
    st, _ = world.phone("POST", "/api/m/goals/op",
                        body={"op": "status", "goal": goal["id"], "status": "done", "id": "goal-00000001"})
    assert st == 200 and world.goals.get(goal["id"])["status"] == "done"       # the desktop store sees the phone's change
    assert world.sync()["goals"] == []                                         # and the phone's active list is empty
    env.db.close()


# ------------------------------------------------------------------ 3. proactive dedupe, snooze
def test_the_same_event_is_one_note_for_the_phone(world):
    world.cont.configure_signals(on=True, hourly=5, daily=10)
    g = {"id": 7, "title": "Run 5k", "updated": 100}
    assert world.cont.on_goal_event(g, "stalled") == "queued"
    assert world.cont.on_goal_event(g, "stalled") == "duplicate"              # the scheduler fires again: still one note
    assert world.cont.on_calendar_event("ev1", "Dentist", 15) == "queued"
    assert world.cont.on_calendar_event("ev1", "Dentist", 15) == "duplicate"
    notes = world.sync()["notices"]
    assert sorted(n["text"] for n in notes) == ["Dentist in 15 min", "Run 5k: stalled"]
    assert [n["id"] for n in world.sync()["notices"]] == [n["id"] for n in notes]   # reconnect: same ids, nothing new


def test_snooze_from_the_phone_holds_later_events_back(world):
    world.cont.configure_signals(on=True)
    assert world.phone("POST", "/api/m/signals", body={"op": "snooze", "minutes": 60, "id": "sig-00000001"})[0] == 200
    assert world.cont.on_calendar_event("ev2", "Gym", 10) == "snoozed"
    assert world.sync()["notices"] == []


# ------------------------------------------------------------------ 4. quiet hours
def test_quiet_hours_hold_the_signal_back_until_morning(world, clk):
    world.cont.configure_signals(on=True)
    clk.t = NIGHT
    assert world.cont.on_calendar_event("ev3", "Standup", 5) == "quiet"
    s = world.sync()
    assert s["notices"] == [] and s["signals"]["quiet_now"] is True
    clk.t = MORNING
    assert world.cont.on_calendar_event("ev3", "Standup", 5) == "queued"      # the owner can still be told after the night
    assert [n["text"] for n in world.sync()["notices"]] == ["Standup in 5 min"]
