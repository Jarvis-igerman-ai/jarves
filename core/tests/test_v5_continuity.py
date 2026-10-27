"""V5 continuity and proactive signals: idempotent commands, one shared conversation, one-device routing, honest
offline behaviour, event-driven signal gate (quiet hours, snooze/stop, limits, cost cap). Fake clock, fake devices,
simulated disconnect/reconnect/restart; no network beyond loopback, no browser."""
from __future__ import annotations

import http.client
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarves import calm, continuity, dashboard, mobile
from jarves.config import Config
from jarves.continuity import Continuity


@pytest.fixture(autouse=True)
def _full_text():
    """These tests read the journal back, so they run with privacy off; test_journal_keeps_no_words_when_privacy_is_on
    covers the default."""
    from jarves import logs
    before = logs.privacy()
    logs.set_privacy(False)
    yield
    logs.set_privacy(before)


def test_journal_keeps_no_words_when_privacy_is_on(tmp_path):
    from jarves import logs
    logs.set_privacy(True)
    c = Continuity(tmp_path)
    c.add_turn("d1", "user", "secret words")
    assert [t["text"] for t in c.turns()] == [""]
from jarves.mobile import Bridge

DAY = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc).timestamp()      # noon
NIGHT = datetime(2026, 10, 22, 23, 30, tzinfo=timezone.utc).timestamp()


class Clock:
    def __init__(self, t=DAY):
        self.t = t

    def __call__(self):
        return self.t


def quiet(ts):
    return calm.in_quiet(datetime.fromtimestamp(ts, timezone.utc), "22:00", "07:00")


@pytest.fixture
def clk():
    return Clock()


@pytest.fixture
def cont(home, clk):
    return Continuity(home, clock=clk, quiet=quiet)


# ---------------------------------------------------------------- idempotent commands
def test_a_resent_command_runs_once_and_survives_a_pc_restart(home, clk):
    runs = []

    def go():
        runs.append(1)
        return 200, {"ok": True}
    a = Continuity(home, clock=clk)
    assert a.execute("p1", "cmd-00000001", "approval", go) == (200, {"ok": True}, False)
    assert a.execute("p1", "cmd-00000001", "approval", go) == (200, {"ok": True}, True)
    b = Continuity(home, clock=clk)                                   # the PC restarted: a fresh object, same file
    assert b.execute("p1", "cmd-00000001", "approval", go)[2] is True
    assert len(runs) == 1
    assert b.execute("p2", "cmd-00000001", "approval", go)[2] is False  # same id from ANOTHER device is another command
    assert len(runs) == 2


def test_no_id_runs_every_time_and_server_errors_are_not_remembered(cont):
    n = []
    cont.execute("p1", None, "x", lambda: (n.append(1) or 200, {}))
    cont.execute("p1", None, "x", lambda: (n.append(1) or 200, {}))
    assert len(n) == 2
    cont.execute("p1", "cmd-00000002", "x", lambda: (500, {"error": "boom"}))
    assert cont.execute("p1", "cmd-00000002", "x", lambda: (200, {"ok": 1}))[0] == 200     # a failure may be retried


def test_a_command_queued_too_long_is_refused_not_replayed_late(cont):
    ran = []
    st, body, _ = cont.execute("p1", "cmd-00000003", "goal", lambda: ran.append(1) or (200, {}),
                               age_s=continuity.REPLAY_WINDOW_S + 1)
    assert st == 410 and body["code"] == "STALE" and not ran
    st, _, _ = cont.execute("p1", "cmd-00000003", "goal", lambda: ran.append(1) or (200, {}), age_s=5)
    assert st == 200 and len(ran) == 1                                  # the user sent it again on purpose


def test_the_ledger_forgets_after_its_ttl(cont, clk):
    runs = []
    cont.execute("p1", "cmd-00000004", "x", lambda: runs.append(1) or (200, {}))
    clk.t += continuity.LEDGER_TTL_S + 1
    cont.execute("p1", "cmd-00000004", "x", lambda: runs.append(1) or (200, {}))
    assert len(runs) == 2


# ---------------------------------------------------------------- one conversation
def test_the_shared_conversation_is_ordered_deduped_and_catchable(cont):
    a = cont.add_turn("desktop", "user", "hello")
    b = cont.add_turn("desktop", "assistant", "hi")
    c = cont.add_turn("phone1", "user", "from phone", "msg-00000001")
    assert cont.add_turn("phone1", "user", "from phone", "msg-00000001") == c          # the same message id: once
    assert (a, b) < (c, c + 1) and cont.head() == c
    assert [t["text"] for t in cont.turns(b)] == ["from phone"]                          # catch up since seq b
    assert [t["device"] for t in cont.turns(0)] == ["desktop", "desktop", "phone1"]


# ---------------------------------------------------------------- routing: one device, no duplicates
def test_a_reminder_goes_to_the_active_device_only(cont, clk):
    cont.beat("desktop", "desktop", used=True)
    clk.t += 10
    cont.beat("phone1", "phone", used=True)                              # the owner just picked up the phone
    assert cont.route_now("timer:1", "timer", "pasta", desktop_present=False) == "phone1"
    assert cont.fetch_notices("desktop") == []                           # the desktop never repeats it
    got = cont.fetch_notices("phone1")
    assert [n["text"] for n in got] == ["pasta"]
    assert cont.route_now("timer:1", "timer", "pasta", desktop_present=False) == "duplicate"   # the same timer: never again


def test_the_desktop_wins_while_the_owner_is_at_the_pc(cont):
    cont.beat("phone1", "phone", used=True)
    assert cont.route_now("timer:2", "timer", "tea", desktop_present=True) == "desktop"
    assert cont.fetch_notices("phone1") == []
    assert cont.route_now("timer:2", "timer", "tea", desktop_present=True) == "desktop"       # a desktop ring that is retried
    assert cont.notice_states()[0]["state"] == "acked"


def test_nobody_reachable_keeps_the_usual_desktop_behaviour(cont):
    assert cont.route_now("timer:3", "timer", "x", desktop_present=False) is None


def test_a_lost_response_is_resent_to_the_same_device_only_until_acknowledged(cont, clk):
    cont.beat("phone1", "phone", used=True)
    cont.beat("phone2", "phone", used=False)
    cont.post_notice("signal:a:1", "signal", "note")
    first = cont.fetch_notices("phone1")
    assert len(first) == 1
    assert cont.fetch_notices("phone2") == []                            # another phone never gets it
    assert cont.fetch_notices("phone1")[0]["id"] == first[0]["id"]       # reconnect: the lost answer is repeated (same id)
    assert cont.ack_notice("phone1", first[0]["id"])
    assert cont.fetch_notices("phone1") == []                            # acknowledged: gone for good
    assert not cont.ack_notice("phone2", first[0]["id"])


def test_a_notice_for_a_vanished_device_is_rerouted_after_the_timeout(cont, clk):
    cont.beat("phone1", "phone", used=True)
    cont.post_notice("signal:b:1", "signal", "note")
    assert len(cont.fetch_notices("phone1")) == 1
    clk.t += continuity.SENT_TIMEOUT_S + 1                               # phone1 never acknowledged and went silent
    cont.beat("desktop", "desktop", used=True)
    assert [n["text"] for n in cont.fetch_notices("desktop")] == ["note"]


def test_dedupe_window_expires(cont, clk):
    assert cont.post_notice("k", "signal", "t") is not None
    assert cont.post_notice("k", "signal", "t") is None
    clk.t += continuity.DEDUPE_S + 1
    assert cont.post_notice("k", "signal", "t") is not None


# ---------------------------------------------------------------- the signal gate
def on(cont, **kw):
    return cont.configure_signals(on=True, **kw)


def test_signals_are_off_by_default_and_nothing_is_created(cont):
    assert cont.emit("goal_stalled", "1", "x") == "off"
    assert cont.notice_states() == []


def test_quiet_hours_block_and_morning_allows(cont, clk):
    on(cont)
    clk.t = NIGHT
    assert cont.emit("calendar", "e1:30", "meeting") == "quiet"
    clk.t = NIGHT + 8 * 3600                                             # 07:30
    assert cont.emit("calendar", "e1:30", "meeting") == "queued"


def test_snooze_stop_and_switching_on_again(cont, clk):
    on(cont)
    cont.snooze(60)
    assert cont.emit("goal_stalled", "1", "x") == "snoozed"
    clk.t += 3601
    assert cont.emit("goal_stalled", "1", "x") == "queued"
    cont.stop_signals()
    assert cont.emit("goal_stalled", "2", "y") == "stopped"
    assert [n["state"] for n in cont.notice_states()] == ["acked"]       # stop also clears what was waiting
    on(cont)                                                             # only the owner, on the computer, restarts it
    assert cont.emit("goal_stalled", "2", "y") == "queued"


def test_frequency_limits_hourly_and_daily_and_duplicates(cont, clk):
    on(cont, hourly=2, daily=3)
    assert [cont.emit("g", str(i), "t") for i in range(3)] == ["queued", "queued", "frequency"]
    assert cont.emit("g", "0", "t") == "frequency"                       # limits are checked before dedupe
    clk.t += 3601
    assert cont.emit("g", "0", "t") == "duplicate"                       # the same situation is never queued twice
    assert cont.emit("g", "9", "t") == "queued"
    assert cont.emit("g", "10", "t") == "frequency"                      # daily 3 reached (2 + 1 within the day... hour reset)
    assert cont.signals_state()["sent_day"] == 3


def test_spending_cap_blocks_signals_that_would_cost_requests(cont):
    on(cont)
    assert cont.emit("smart", "1", "t", cost=1) == "cap"                 # default cap is 0: no model request at all
    cont.configure_signals(cost_cap=2, hourly=10, daily=10)
    assert cont.emit("smart", "1", "t", cost=1) == "queued"
    assert cont.emit("smart", "2", "t", cost=1) == "queued"
    assert cont.emit("smart", "3", "t", cost=1) == "cap"


def test_event_adapters_and_the_proactive_scan_are_llm_free(cont, home, clk):
    on(cont, hourly=10, daily=10)
    assert cont.on_goal_event({"id": 7, "title": "Run", "updated": 5}, "stalled") == "queued"
    assert cont.on_calendar_event("ev9", "Dentist", 30) == "queued"
    assert cont.on_calendar_event("ev9", "Dentist", 30) == "duplicate"

    class P:
        def refresh(self):
            return [{"id": 1, "kind": "goal_overdue", "text": "Late goal", "state": "new"},
                    {"id": 2, "kind": "goal_stalled", "text": "seen", "state": "offered"}]
    assert cont.scan_proactive(P()) == 1
    assert cont.scan_proactive(P()) == 0


def test_a_goal_with_a_secret_like_text_is_not_stored_in_the_conversation_by_the_core_hook():
    # the Core hook (app._journal_turn) skips SENSITIVE_TEXT; the pattern exists and matches a card-like number
    from jarves.app import SENSITIVE_TEXT
    assert SENSITIVE_TEXT.search("my code is 123456")


# ---------------------------------------------------------------- over HTTP: reconnect, restart, offline
HOST = "192.168.50.7"


class Goals:
    def __init__(self):
        self.calls = []
        self.g = {"id": 1, "title": "Run 5k", "status": "active", "progress": 10, "steps": [{"id": 3, "text": "week 1", "done": False}]}

    def list(self, status=""):
        return [self.g]

    def set_status(self, gid, status, by="user"):
        if gid != 1:
            raise KeyError(gid)
        self.calls.append(("status", gid, status, by))
        self.g["status"] = status
        return self.g

    def set_step(self, sid, done, by="user"):
        self.calls.append(("step", sid, done, by))
        self.g["steps"][0]["done"] = done
        return self.g


class Fake:
    def __init__(self):
        self.chats, self.goals = [], Goals()
        self.here = False

    def bridge(self):
        def chat(text, dev):
            self.chats.append(text)
            return {"reply": "echo: " + text, "ok": True}
        return Bridge(chat=chat, voice=lambda w, d: {}, status=lambda: {"core": "running", "components": {}},
                      goals=lambda: self.goals, desktop_present=lambda: self.here)


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def srv(home, fake):
    from conftest import free_port
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST, mobile_bridge=fake.bridge()).start()
    yield d
    d.stop()


def call(d, method, path, token=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    h = {"Host": f"{HOST}:{d.port}"}
    if method != "GET":
        h["X-Jarves"] = "1"
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    out = json.loads(r.read() or b"{}")
    c.close()
    return r.status, out


def owner(d, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Host": f"127.0.0.1:{d.port}", "Cookie": f"{d.access.cookie}={d.url.split('t=')[1]}", "X-Jarves": "1"})
    r = c.getresponse()
    out = json.loads(r.read())
    c.close()
    return r.status, out


def pair(d, scopes=None):
    st, code = owner(d, "POST", "/api/mobile/code", {})
    st, got = call(d, "POST", "/api/m/pair", body={"code": code["code"], "name": "Galaxy"})
    assert st == 200
    if scopes is not None:
        assert owner(d, "POST", f"/api/mobile/devices/{got['device']}/scopes", {"scopes": scopes})[0] == 200
    return got["token"], got["device"]


def test_chat_resend_after_a_pc_restart_is_answered_from_the_ledger(srv, fake):
    tok, dev = pair(srv)
    assert call(srv, "POST", "/api/m/chat", tok, {"text": "hi", "id": "msg-aaaaaaaa"})[1]["reply"] == "echo: hi"
    srv.mobile._replies.clear()                                        # the in-memory copy is gone (PC restart)
    st, out = call(srv, "POST", "/api/m/chat", tok, {"text": "hi", "id": "msg-aaaaaaaa"})
    assert st == 200 and out["replay"] is True and out["reply"] == "echo: hi"
    assert fake.chats == ["hi"]


def test_a_late_replayed_chat_is_refused_stale(srv, fake):
    tok, _ = pair(srv)
    st, out = call(srv, "POST", "/api/m/chat", tok, {"text": "late", "id": "msg-bbbbbbbb", "age_s": 9999})
    assert st == 410 and out["code"] == "STALE" and fake.chats == []


def test_an_approval_answer_is_applied_once_on_resend(srv, monkeypatch):
    tok, _ = pair(srv)
    seen = []
    monkeypatch.setattr(mobile, "answer_approval", lambda mob, aid, ok, dev: (seen.append((aid, ok)) or (200, {"ok": True})))
    aid = "0123456789abcdef"
    for _ in range(3):                                                 # three taps / reconnect resends
        st, out = call(srv, "POST", f"/api/m/approvals/{aid}", tok, {"approved": True, "id": "appr-1-yes"})
        assert st == 200
    assert seen == [(aid, True)] and out["replay"] is True
    assert call(srv, "POST", f"/api/m/approvals/{aid}", tok, {"approved": True, "id": "bad id!"})[0] == 400


def test_goal_actions_need_the_tasks_scope_and_run_once(srv, fake):
    tok, _ = pair(srv)
    body = {"op": "status", "goal": 1, "status": "paused", "id": "goal-00000001"}
    assert call(srv, "POST", "/api/m/goals/op", tok, body)[0] == 403         # least privilege: not granted yet
    tok, dev = pair(srv, scopes=["chat", "status", "tasks"])
    assert call(srv, "POST", "/api/m/goals/op", tok, body)[0] == 200
    again = call(srv, "POST", "/api/m/goals/op", tok, body)
    assert again[0] == 200 and again[1]["replay"] is True
    assert fake.goals.calls == [("status", 1, "paused", "phone")]
    assert call(srv, "POST", "/api/m/goals/op", tok, {"op": "delete", "goal": 1, "id": "goal-00000002"})[0] == 400  # no delete from a phone
    assert call(srv, "POST", "/api/m/goals/op", tok, {"op": "status", "goal": 1, "status": "paused"})[0] == 400     # id is required
    assert call(srv, "POST", "/api/m/goals/op", tok, {"op": "status", "goal": 9, "status": "paused", "id": "goal-00000003"})[0] == 404


def test_sync_shows_the_desktop_turn_the_note_and_ack_clears_it(srv, fake):
    tok, dev = pair(srv, scopes=["chat", "status", "tasks"])
    c = srv.mobile.cont
    c.add_turn("desktop", "user", "typed at the pc")
    c.add_turn("desktop", "assistant", "answer at the pc")
    c.post_notice("signal:x:1", "signal", "goal stalled")
    st, s = call(srv, "GET", "/api/m/sync?since=0", tok)
    assert st == 200 and [t["text"] for t in s["turns"]] == ["typed at the pc", "answer at the pc"]
    assert [n["text"] for n in s["notices"]] == ["goal stalled"] and s["goals"][0]["title"] == "Run 5k"
    nid = s["notices"][0]["id"]
    assert call(srv, "GET", f"/api/m/sync?since={s['head']}", tok)[1]["notices"][0]["id"] == nid     # reconnect: same note, same id
    assert call(srv, "POST", f"/api/m/notices/{nid}/ack", tok, {})[1]["ok"] is True
    assert call(srv, "GET", f"/api/m/sync?since={s['head']}", tok)[1]["notices"] == []


def test_a_phone_chat_turn_joins_the_shared_conversation_once(srv, fake):
    tok, dev = pair(srv)
    for _ in range(2):
        call(srv, "POST", "/api/m/chat", tok, {"text": "hello", "id": "msg-cccccccc"})
    assert [(t["role"], t["text"]) for t in srv.mobile.cont.turns(0)] == [("user", "hello"), ("assistant", "echo: hello")]


def test_snooze_and_stop_from_the_phone_are_idempotent_and_scoped(srv):
    tok, dev = pair(srv, scopes=["status", "tasks"])
    srv.mobile.cont.configure_signals(on=True)
    st, out = call(srv, "POST", "/api/m/signals", tok, {"op": "snooze", "minutes": 30, "id": "sig-00000001"})
    assert st == 200 and out["snoozed_until"] > 0
    assert call(srv, "POST", "/api/m/signals", tok, {"op": "on", "id": "sig-00000002"})[0] == 400      # switching on: the PC only
    assert call(srv, "POST", "/api/m/signals", tok, {"op": "stop", "id": "sig-00000003"})[0] == 200
    assert srv.mobile.cont.signals_state()["stopped"] is True


# ---------------------------------------------------------------- the dashboard / PWA
def test_desktop_dashboard_endpoints_configure_signals(srv):
    assert owner(srv, "GET", "/api/continuity")[1]["signals"]["on"] is False
    assert owner(srv, "POST", "/api/continuity/op", {"op": "config", "on": True, "hourly": 1})[1]["hourly"] == 1
    assert owner(srv, "POST", "/api/continuity/op", {"op": "snooze", "minutes": 5})[0] == 200
    assert owner(srv, "POST", "/api/continuity/op", {"op": "nope"})[0] == 400


MOBILE_DIR = Path(mobile.__file__).parent / "dashboard_static" / "mobile"


def test_the_pwa_has_an_honest_offline_banner_outbox_and_bilingual_texts():
    js = (MOBILE_DIR / "app.js").read_text(encoding="utf-8")
    html = (MOBILE_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="banner"' in html and 'role="alert"' in html
    assert "chatView" in js and "localStorage.setItem(\"jarves_m_outbox" not in js    # the shared chat view sends; nothing queues silently
    en = re.search(r"en: \{(.*?)\n    he: \{", js, re.S).group(1)
    he = re.search(r"\n    he: \{(.*?)\n  \};", js, re.S).group(1)
    keys = lambda blob: set(re.findall(r"(\w+): \"", blob))
    assert keys(en) == keys(he)
    for k in ("banner", "snooze", "stop_notes"):
        assert k in keys(en)


# ---------------------------------------------------------------- the Core wiring of a due timer
async def test_a_due_timer_rings_on_the_phone_or_the_desktop_never_both(core):
    ops = core.ops
    rang = []

    async def ring(t, text):
        rang.append(t["id"])
        return True
    core.features.set("mobile_access", True)
    ops.cont.beat("phone1", "phone", used=True)
    assert await ops._ring_once(ring, {"id": 41}, "pasta") is True
    assert rang == []                                                   # the phone is active: it got a notice instead
    assert [n["text"] for n in ops.cont.fetch_notices("phone1")] == ["pasta"]
    assert await ops._ring_once(ring, {"id": 41}, "pasta") is True      # a retry never rings or notifies again
    assert rang == [] and ops.cont.fetch_notices("phone1")[0]["text"] == "pasta"   # same single, unacknowledged note
    assert len(ops.cont.notice_states()) == 1


def test_a_non_numeric_age_never_crashes_the_ledger(cont):
    ran = []
    st, _, _ = cont.execute("p1", "cmd-00000009", "goal", lambda: ran.append(1) or (200, {}), age_s="soon")
    assert st == 200 and ran


def test_sync_hides_the_turns_when_the_chat_page_is_not_granted(srv, fake):
    """F14 #206: /api/m/sync checked the chat scope but not the chat page grant (every other chat route checks both)."""
    tok, dev = pair(srv, scopes=["chat", "status"])
    srv.mobile.cont.add_turn("desktop", "user", "typed at the pc")
    assert [t["text"] for t in call(srv, "GET", "/api/m/sync?since=0", tok)[1]["turns"]] == ["typed at the pc"]
    assert srv.mobile.set_pages(dev, [p for p in mobile.PAGES if p != "chat"], "test")
    assert call(srv, "GET", "/api/m/sync?since=0", tok)[1]["turns"] == []
