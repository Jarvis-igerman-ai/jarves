"""V5 web chat (owner request 21 Oct 2026): `jarvis web` and the dashboard Chat page, and the same chat for paired phones.

Real WebChat + TextCore (text-only Brain, real registry/approvals/store/memory in tmp dirs), a fake model (no network),
the real dashboard and phone HTTP server on a free port, real ApprovalService. No browser, no sleeping: every wait is
event-driven (the chat long-poll, asyncio events, thread events)."""
import asyncio
import http.client
import json
import threading
import time

import pytest
from pydantic import BaseModel

from jarves import approvals as appr
from jarves import chat_engine, cli, dashboard, mobile, web_cli
from jarves.config import Config
from jarves.skills import Skill, Tool, ToolResult
from jarves.store import Store

HOST = "192.168.50.7"
HEBREW = "שלום, מה שלומך היום? אני רוצה לתכנן ארוחת ערב"
SECRET = "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0"
EMAIL = "dana.levi@example.com"


def free_port():
    from conftest import free_port as fp
    return fp()


class EchoSecret(Skill):
    """One read tool that returns a credential and an e-mail address, one action that needs the owner's approval."""
    name = "fakes"
    description = "test tools"

    def __init__(self, typed=False):
        self.typed = typed

    def tools(self):
        async def leak(_a):
            return ToolResult(True, f"token {SECRET} for {EMAIL}: done", {"n": 1})

        async def danger(_a):
            return ToolResult(True, "sent")

        def meta(_a):
            return {"details": {"recipient": "Dana Levi"}, "typed": self.typed, "why": "you asked to send the report"}
        return [Tool("leak_check", "returns a secret", leak, permission="read"),
                Tool("send_report", "send the report", danger, permission="action",
                     confirm="Send the weekly report to Dana?", approval_meta=meta)]


class FakeAgent:
    """The model: streams its answer in pieces, calls tools on request, remembers what it was told."""

    def __init__(self, registry):
        self.registry, self.seen, self.history, self.resets = registry, [], [], 0
        self.instructions = lambda: ""
        self.release = None                       # an asyncio.Event a test can hold a turn open with

    def available(self):
        return True, "", ""

    def reset_conversation(self):
        self.resets += 1
        self.history = []

    async def seed_history(self, turns):
        self.history = list(turns)

    async def reply(self, text, on_delta=None):
        self.seen.append(text)
        self.history.append(("user", text))
        if "tool" in text:
            await self.registry.call("leak_check", {}, origin="model")
        if "report" in text:
            r = await self.registry.call("send_report", {}, origin="model")
            answer = "sent" if r.ok else "not sent: " + r.code
        elif text.startswith("hold"):
            await self.release.wait()
            answer = "released"
        else:
            answer = "בסדר, " + text[-12:] if any("֐" <= c <= "׿" for c in text) else "ok " + text[-12:]
        for i in range(0, len(answer), 4):
            if on_delta:
                on_delta(answer[i:i + 4])
        self.history.append(("assistant", answer))
        return answer


class Rig:
    def __init__(self, home, *, typed=False, mobile_on=False, popup_channel=None):
        self.home = home
        self.cfg = Config()
        self.agents: list[FakeAgent] = []

        def factory(cfg, registry, memory, home_, budget=None):
            a = FakeAgent(registry)
            self.agents.append(a)
            return a
        from jarves.userdata import open_db
        _db = open_db(home)
        try:
            _db.set_setting("alerts.quiet_enabled", False)   # the suite must not depend on the time of day
        finally:
            _db.close()
        if mobile_on:
            from jarves.features import Features
            Features(self.cfg, home).set("mobile_access", True)   # the owner's switch; default OFF
        self.h = web_cli.start_standalone(self.cfg, home, mobile_on=mobile_on, lan_host=HOST if mobile_on else None,
                                          agent_factory=factory, popup=False, port=free_port())
        self.core, self.chat, self.dash = self.h.core, self.h.chat, self.h.dashboard
        self.agent = self.agents[0]
        self.core.registry.register(EchoSecret(typed))
        self.core.registry.allowed_actions.add("send_report")
        self.channel = popup_channel
        if popup_channel is not None:
            self.core.approvals.channels.append(popup_channel)

    def stop(self):
        self.h.stop()


@pytest.fixture
def rig(home):
    from jarves import logs as _logs
    was = _logs.privacy()
    _logs.set_privacy(False)                 # the history keeps the words (the owner's choice; default is masked)
    made = []

    def make(**kw):
        r = Rig(home, **kw)
        made.append(r)
        return r
    yield make
    for r in made:
        r.stop()
    _logs.set_privacy(was)


class FakePopup:
    """An approvals channel standing for the popup / terminal: records what it saw while the question was asked."""

    def __init__(self, name="popup"):
        self.name, self.opened, self.turn_seen = name, [], []

    def available(self, a):
        self.turn_seen.append(appr.CHAT_TURN.get())
        return True

    def open(self, a):
        self.opened.append(a.id)

    def close(self, a):
        pass


def owner_req(d, method, path, body=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=60)
    h = {"Host": host or f"127.0.0.1:{d.port}", "Cookie": f"{d.access.cookie}={d.url.split('t=')[1]}", "X-Jarves": "1"}
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, json.loads(out)


def dev_req(d, method, path, *, token=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=60)
    h = {"Host": f"{HOST}:{d.port}"}
    if method != "GET":
        h["X-Jarves"] = "1"
    if token:
        h["Authorization"] = "Bearer " + token
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    try:
        return r.status, json.loads(out)
    except ValueError:
        return r.status, out


def pair(d):
    st, code = owner_req(d, "POST", "/api/mobile/code", {})
    assert st == 200, code
    st, got = dev_req(d, "POST", "/api/m/pair", body={"code": code["code"], "name": "Galaxy"})
    assert st == 200, got
    return got["token"], got["device"]


def collect(poll, until_type, turn=None, after=0, sig=""):
    """Poll the chat (long-poll, event-driven) until an event of `until_type` for `turn` arrived; return all events."""
    seen = []
    for _ in range(200):
        r = poll(after, sig)
        seen += r["events"]
        after, sig = r["cursor"], r["sig"]
        if any(e["type"] == until_type and (turn is None or e.get("turn") == turn) for e in r["events"]):
            return seen, after, sig
    raise AssertionError("event not reached: " + until_type)


def owner_poll(rig):
    return lambda after, sig: rig.chat.poll(after, 20, sig, lambda: chat_engine.owner_approvals(rig.home))


# ---------------------------------------------------------------- slash commands (pure)
def test_slash_commands_are_parsed_and_unknown_ones_named():
    assert chat_engine.parse_command("/new") == ("new", "")
    assert chat_engine.parse_command("  /resume 2 ") == ("resume", "2")
    assert chat_engine.parse_command("/UNDO") == ("undo", "")
    assert chat_engine.parse_command("/status") == ("status", "")
    assert chat_engine.parse_command("/help") == ("help", "")
    assert chat_engine.parse_command("/frobnicate x") == ("unknown", "frobnicate")
    for plain in ("hello /new", "5 / 2", "/ 5", "//path", "", "שלום /new"):
        assert chat_engine.parse_command(plain) is None, plain
    assert "/resume" in chat_engine.help_text("en") and "שיחה" in chat_engine.help_text("he")


def test_scrub_masks_credentials_and_addresses_but_keeps_hebrew():
    out = chat_engine.scrub(f"{HEBREW} {SECRET} {EMAIL} password=hunter22 \x07")
    assert HEBREW in out and SECRET not in out and EMAIL not in out and "hunter22" not in out and "\x07" not in out
    assert len(chat_engine.scrub("x" * 5000)) <= chat_engine.DETAIL_CHARS


# ---------------------------------------------------------------- streaming, tools, Hebrew round trip, history
def test_hebrew_round_trip_streams_and_is_stored(rig):
    r = rig()
    st, out = r.chat.submit(HEBREW, client="owner", mid="hebrew-0001", lang="he")
    assert st == 200 and out["turn"] == "t1"
    events, _, _ = collect(owner_poll(r), "reply", "t1")
    assert [e["text"] for e in events if e["type"] == "user"] == [HEBREW]
    reply = next(e for e in events if e["type"] == "reply")
    assert reply["ok"] and reply["text"].startswith("בסדר") and reply["text"] == r.agent.history[-1][1]
    assert r.agent.seen == [HEBREW]
    deltas = [e for e in events if e["type"] == "delta"]
    assert all(e["text"] and reply["text"].startswith(e["text"]) for e in deltas)
    # a streamed answer is ONE replaceable event: the log never holds two deltas of one turn
    assert sum(1 for e in r.chat.events.since(0) if e["type"] == "delta" and e["turn"] == "t1") == 0
    assert any(m["role"] == "assistant" and m["text"] == reply["text"] for m in r.chat.state()["messages"])
    # the exchange is in the shared history (what jarvis chat /resume and the Windows session read)
    sess = chat_engine.recent_sessions(r.home)
    assert sess and sess[0]["first"] == HEBREW and sess[0]["turns"] == 1
    st2 = Store(r.home / "store.db")
    try:
        texts = [t["text"] for t in st2.timeline(sess[0]["id"])["turns"]]
    finally:
        st2.close()
    assert texts == [HEBREW, reply["text"]]


def test_tool_call_is_one_line_with_redacted_details(rig):
    r = rig()
    r.chat.submit("please run the tool now", client="owner")
    events, _, _ = collect(owner_poll(r), "reply", "t1")
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["name"] == "leak_check" and tool["ok"] is True and tool["code"] == "ok"
    assert SECRET not in tool["detail"] and EMAIL not in tool["detail"] and "<email>" in tool["detail"]
    assert "done" in tool["detail"]
    blob = json.dumps(r.chat.state(), ensure_ascii=False)
    assert SECRET not in blob and EMAIL not in blob


def test_message_ids_are_idempotent(rig):
    r = rig()
    a = r.chat.submit("hello there", client="phone1", mid="dup-id-0001")
    collect(owner_poll(r), "reply", "t1")
    b = r.chat.submit("hello there", client="phone1", mid="dup-id-0001")
    assert a[0] == b[0] == 200 and b[1]["replay"] is True and b[1]["turn"] == a[1]["turn"]
    assert r.agent.seen == ["hello there"]                         # the model ran the message once
    st, bad = r.chat.submit("x", mid="no")
    assert st == 400 and bad["code"] == "CHAT_BAD_ID"
    assert r.chat.submit("   ")[1]["code"] == "CHAT_EMPTY"
    assert r.chat.submit("x" * (chat_engine.MAX_TEXT + 1))[1]["code"] == "CHAT_TOO_LONG"


def test_one_turn_at_a_time_and_commands_wait(rig):
    r = rig()
    r.agent.release = asyncio.Event()
    r.chat.submit("hold on", client="owner")
    st, busy = r.chat.submit("second message", client="owner")
    assert st == 409 and busy["code"] == "CHAT_BUSY"
    assert r.chat.submit("/new")[0] == 409                        # /new would reset a running answer
    st, ok = r.chat.submit("/help")
    assert st == 200 and ok["command"] == "help"                  # reading commands stay available
    r.core.loop.call_soon_threadsafe(r.agent.release.set)
    collect(owner_poll(r), "reply", "t1")
    assert r.chat.submit("now it works")[0] == 200


def test_new_resets_the_model_and_starts_a_new_stored_conversation(rig):
    r = rig()
    r.chat.submit("first topic here", client="owner")
    collect(owner_poll(r), "reply", "t1")
    r.chat.submit("/new")
    collect(owner_poll(r), "notice", None, after=0)
    assert r.agent.resets == 1 and r.chat.state()["messages"] == [m for m in r.chat.state()["messages"] if m["role"] == "notice"]
    r.chat.submit("second topic here", client="owner")
    collect(owner_poll(r), "reply", "t3", after=0)
    sess = chat_engine.recent_sessions(r.home)
    assert len(sess) == 2 and {s["first"] for s in sess} == {"first topic here", "second topic here"}


def test_resume_across_surfaces(rig):
    """A conversation written by `jarvis chat` (typed), by the Windows voice session (live) and by the web page can each
    be listed and continued from the web page; the web page's own exchange is listed for the others."""
    r = rig()
    other = Store(r.home / "store.db")
    try:
        other.record_typed("שאלה מהטרמינל", "תשובה מהטרמינל", 1_700_000_000.0, session="cli", fresh=True)
        other.ingest({"code": "LIVE_STARTED", "ts": 1_700_001_000.0, "level": "INFO", "component": "voice", "session": "x1"})
        other.ingest({"code": "LIVE_USER_SAID", "ts": 1_700_001_001.0, "level": "INFO", "component": "voice",
                      "text": "מה השעה בקול", "session": "x1"})
        other.ingest({"code": "LIVE_JARVES_SAID", "ts": 1_700_001_002.0, "level": "INFO", "component": "voice",
                      "text": "שלוש וחצי", "session": "x1"})
        other.commit()
    finally:
        other.close()
    r.chat.submit("/resume")
    ev, _, _ = collect(owner_poll(r), "notice")
    listing = next(e for e in ev if e["type"] == "notice")
    firsts = [s["first"] for s in listing["sessions"]]
    assert "שאלה מהטרמינל" in firsts and "מה השעה בקול" in firsts
    n = 1 + firsts.index("שאלה מהטרמינל")
    r.chat.submit(f"/resume {n}")
    collect(owner_poll(r), "notice", "t2")
    assert r.agent.history == [("user", "שאלה מהטרמינל"), ("assistant", "תשובה מהטרמינל")]
    texts = [m["text"] for m in r.chat.state()["messages"] if m["role"] in ("user", "assistant")]
    assert texts == ["שאלה מהטרמינל", "תשובה מהטרמינל"]
    r.chat.submit("and now from the web", client="owner")
    collect(owner_poll(r), "reply", "t3")
    assert "and now from the web" in {s["first"] for s in chat_engine.recent_sessions(r.home)}
    assert r.chat.submit("/resume 99")[0] == 200
    ev, _, _ = collect(owner_poll(r), "notice", "t4")
    assert "No such conversation" in next(e for e in ev if e.get("turn") == "t4" and e["type"] == "notice")["en"]


def test_status_help_unknown_and_undo(rig):
    r = rig()
    ws = r.core.workspace
    ws.write("USER.md", "# first version\n", by="user", note="t")
    ws.write("USER.md", "# second version\n", by="user", note="t")
    r.chat.submit("/status")
    r.chat.submit("/frob")
    r.chat.submit("/undo")
    seen, after = [], 0
    for _ in range(100):
        got, after, _ = collect(owner_poll(r), "notice", after=after)
        seen += [e for e in got if e["type"] == "notice"]
        if len(seen) >= 3:
            break
    by = {e["command"]: e for e in seen}
    assert "Core:" in by["status"]["en"] and "ליבה" in by["status"]["he"] and by["status"]["silent"]["on"] in (True, False)
    assert "Unknown command /frob" in by["unknown"]["en"]
    assert "Undid the last change" in by["undo"]["en"] and "# first version" in ws.read("USER.md")


# ---------------------------------------------------------------- silence
def test_silent_turns_never_speak_ring_or_light_the_face(rig):
    pop = FakePopup()
    r = rig(popup_channel=pop)
    spoken = []

    async def speak(text):
        spoken.append(text)
    r.core.speak = speak
    assert r.chat.state()["can_speak"] is True
    r.chat.submit("loud please", client="owner", speak=True)
    collect(owner_poll(r), "reply", "t1")
    assert spoken == [r.agent.history[-1][1]]                                # explicitly asked, not silent: spoken once
    # the silent switch forbids it
    r.chat.set_silent(True)
    assert r.chat.silent_state() == {"on": True, "setting": True, "quiet_hours": False}
    r.chat.submit("quiet please", client="owner", speak=True)
    ev, _, _ = collect(owner_poll(r), "reply", "t2")
    assert len(spoken) == 1 and next(e for e in ev if e["type"] == "reply" and e["turn"] == "t2")["spoken"] is False
    # a silent turn that asks for approval reaches channels with CHAT_TURN.silent set (face / voice channels refuse it)
    r.chat.submit("send the report", client="owner")
    asked = _wait_pending(r)
    for _ in range(100):                                          # the channels are asked just after the request exists
        if pop.turn_seen:
            break
        time.sleep(0.05)
    assert pop.turn_seen[-1] == {"client": "web", "silent": True}
    ok, _ = r.core.approvals.answer(asked[0]["id"], True, channel="popup")
    assert ok
    collect(owner_poll(r), "reply", "t3")
    # a phone never makes the computer speak, silent or not
    r.chat.set_silent(False)
    code, out = r.chat.submit("phone says hi", client="dev", device=True, speak=True)
    ev, _, _ = collect(owner_poll(r), "reply", "t4")
    assert len(spoken) == 1 and next(e for e in ev if e["type"] == "reply" and e["turn"] == "t4")["spoken"] is False
    assert chat_engine.COMMANDS and r.chat.state(device=True)["can_speak"] is False


def test_quiet_hours_make_the_chat_silent(rig, home):
    from jarves.userdata import open_db
    r = rig()
    db = open_db(home)
    try:
        db.set_setting("alerts.quiet_enabled", True)
        db.set_setting("alerts.quiet_start", "00:00")
        db.set_setting("alerts.quiet_end", "23:59")
    finally:
        db.close()
    r.chat._quiet = (0.0, False)
    s = r.chat.silent_state()
    assert s["on"] and s["quiet_hours"] and not s["setting"]


def test_popup_window_is_told_to_stay_silent(home, monkeypatch):
    """The approval window rings a bell only for a turn that is not silent (approval_popup reads req['silent'])."""
    captured = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            captured["req"] = json.loads(open(argv[argv.index("--in") + 1], encoding="utf-8").read())
            self.returncode, self.pid = 0, 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

        def communicate(self, input=None, timeout=None):
            return b"", b""
    monkeypatch.setattr(appr.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(appr, "desktop_available", lambda: (True, ""))

    async def go():
        svc = appr.ApprovalService(home, timeout_s=5)
        svc.channels.append(appr.PopupChannel(svc, enabled=True))
        done = asyncio.Event()
        svc.on_ask.append(lambda a: done.set())
        appr.CHAT_TURN.set({"client": "web", "silent": True})
        t = asyncio.create_task(svc.ask("Send it?", title="send_report"))
        await asyncio.wait_for(done.wait(), 30)
        svc.answer(svc.pending()[0]["id"], False, channel="popup")
        assert await t is False
    asyncio.run(go())
    assert captured["req"]["silent"] is True
    import inspect
    from jarves import approval_popup
    assert 'req.get("silent")' in inspect.getsource(approval_popup)


# ---------------------------------------------------------------- approvals inline, first answer wins
def _wait_pending(r, n=1):
    ev = threading.Event()
    box = []

    def check():
        rows = chat_engine.owner_approvals(r.home)
        if len(rows) >= n:
            box.append(rows)
            ev.set()
    r.core.approvals.on_ask.append(lambda a: check())
    check()
    assert ev.wait(45), "no approval request appeared"
    return box[0]


def test_approval_appears_inline_with_the_popup_description(rig):
    pop = FakePopup()
    r = rig(popup_channel=pop)
    r.chat.submit("send the report", client="owner")
    rows = _wait_pending(r)
    a = rows[0]
    from jarves import approval_popup as pop_mod
    raw = r.core.approvals.pending()[0]
    assert a["headline"] == pop_mod.headline(raw, "en") and a["sentence"] == pop_mod.sentence(raw)
    assert any(x["label"] and x["value"] for x in a["rows"]) and a["risk"] in ("low", "medium", "high")
    assert chat_engine.describe_approval(raw, "he")["headline"] == pop_mod.headline(raw, "he")
    assert pop.opened == [a["id"]]                                # the popup got the same request at once
    ok, _ = r.core.approvals.answer(a["id"], True, channel="popup")
    assert ok
    events, _, _ = collect(owner_poll(r), "reply", "t1")
    assert any(e["type"] == "approval" for e in events) and any(e["type"] == "approval_closed" and e["channel"] == "popup" for e in events)
    assert next(e for e in events if e["type"] == "reply")["text"] == "sent"


@pytest.mark.parametrize("first,second", [("popup", "dashboard"), ("dashboard", "terminal"), ("terminal", "phone")])
def test_first_answer_wins_across_popup_terminal_web_and_phone(rig, first, second):
    r = rig(popup_channel=FakePopup())
    r.chat.submit("send the report", client="owner")
    aid = _wait_pending(r)[0]["id"]

    def answer(channel):
        if channel == "dashboard":
            return owner_req(r.dash, "POST", f"/api/approvals/{aid}", {"approved": True})
        ok, why = r.core.approvals.answer(aid, True, channel=channel)
        return (200, {}) if ok else (409, {"error": why})
    st1, _ = answer(first)
    assert st1 == 200
    events, _, _ = collect(owner_poll(r), "reply", "t1")
    assert next(e for e in events if e["type"] == "reply")["text"] == "sent"
    st2, body = answer(second)
    assert st2 == 409                                              # too late: the first answer already decided
    if second == "dashboard":
        assert body["code"] == "APPROVAL_ALREADY_ANSWERED"
    hist = appr.read_history(r.home, 5)
    assert [h["channel"] for h in hist if h.get("title") == "send_report"] == [first]


def test_denying_on_the_web_page_stops_the_action(rig):
    r = rig(popup_channel=FakePopup())
    r.chat.submit("send the report", client="owner")
    aid = _wait_pending(r)[0]["id"]
    assert owner_req(r.dash, "POST", f"/api/approvals/{aid}", {"approved": False})[0] == 200
    events, _, _ = collect(owner_poll(r), "reply", "t1")
    assert next(e for e in events if e["type"] == "reply")["text"] == "not sent: TOOL_DECLINED"


def test_typed_code_approvals_are_pc_only(rig):
    r = rig(typed=True, mobile_on=True, popup_channel=FakePopup())
    tok, dev = pair(r.dash)
    owner_req(r.dash, "POST", "/api/mobile/settings", {"phone_approvals": True})
    r.chat.submit("send the report", client="owner")
    mine = _wait_pending(r)[0]
    assert mine["typed"] and len(mine["code"]) == 4                # the computer shows the code
    st, view = dev_req(r.dash, "GET", "/api/m/chat/state", token=tok)
    theirs = view["approvals"][0]
    assert theirs["typed"] and theirs["code"] == "" and theirs["id"] == mine["id"]   # the phone never receives it
    assert mine["code"] not in json.dumps(view)
    st, body = dev_req(r.dash, "POST", f"/api/m/approvals/{mine['id']}", token=tok, body={"approved": True})
    assert st == 403 and body["code"] == "TYPED_PC_ONLY"
    st, body = owner_req(r.dash, "POST", f"/api/approvals/{mine['id']}", {"approved": True, "typed": "0000x"})
    assert st == 409 and body["code"] == "APPROVAL_CODE_NEEDED"
    st, _ = owner_req(r.dash, "POST", f"/api/approvals/{mine['id']}", {"approved": True, "typed": mine["code"]})
    assert st == 200
    events, _, _ = collect(owner_poll(r), "reply", "t1")
    assert next(e for e in events if e["type"] == "reply")["text"] == "sent"


def test_phone_approval_needs_the_switch_and_loses_to_an_earlier_answer(rig):
    r = rig(mobile_on=True, popup_channel=FakePopup())
    tok, dev = pair(r.dash)
    r.chat.submit("send the report", client="owner")
    aid = _wait_pending(r)[0]["id"]
    st, body = dev_req(r.dash, "POST", f"/api/m/approvals/{aid}", token=tok, body={"approved": True})
    assert st == 403 and body["code"] == "PHONE_APPROVALS_OFF"
    owner_req(r.dash, "POST", "/api/mobile/settings", {"phone_approvals": True})
    ok, _ = r.core.approvals.answer(aid, False, channel="popup")
    assert ok
    collect(owner_poll(r), "reply", "t1")
    st, body = dev_req(r.dash, "POST", f"/api/m/approvals/{aid}", token=tok, body={"approved": True})
    assert st == 409 and body["code"] == "APPROVAL_ALREADY_ANSWERED"


# ---------------------------------------------------------------- the owner's dashboard API
def test_dashboard_chat_api_and_feature_switch(rig):
    r = rig()
    d = r.dash
    st, state = owner_req(d, "GET", "/api/chat/state?lang=he")
    assert st == 200 and state["available"] and state["commands"] and state["silent"]["on"] in (True, False)
    st, out = owner_req(d, "POST", "/api/chat/send", {"text": HEBREW, "id": "web-http-001", "lang": "he"})
    assert st == 200 and out["turn"] == "t1"
    got = []
    cursor, sig = 0, ""
    while not any(e["type"] == "reply" for e in got):
        st, ev = owner_req(d, "GET", f"/api/chat/events?after={cursor}&sig={sig}&wait=20&lang=he")
        assert st == 200
        got += ev["events"]
        cursor, sig = ev["cursor"], ev["sig"]
    assert next(e for e in got if e["type"] == "reply")["text"].startswith("בסדר")
    st, s = owner_req(d, "POST", "/api/chat/silent", {"on": True})
    assert st == 200 and s["silent"]["setting"] is True
    assert owner_req(d, "POST", "/api/chat/silent", {"on": "yes"})[0] == 400
    from jarves.features import Features
    time.sleep(1.1)                        # other processes are picked up within a second (features._refresh)
    Features(r.cfg, r.home).set("web_chat", False)
    st, body = owner_req(d, "GET", "/api/chat/state")
    assert st == 403 and body["code"] == "CHAT_FEATURE_OFF"
    assert owner_req(d, "POST", "/api/chat/send", {"text": "x"})[1]["code"] == "CHAT_FEATURE_OFF"


def test_dashboard_without_a_chat_engine_says_so(home):
    d = dashboard.Dashboard(home, Config(), free_port()).start()
    try:
        st, state = owner_req(d, "GET", "/api/chat/state")
        assert st == 200 and state["available"] is False and state["code"] == "CHAT_UNAVAILABLE"
        st, body = owner_req(d, "POST", "/api/chat/send", {"text": "hi"})
        assert st == 503 and body["code"] == "CHAT_UNAVAILABLE"
    finally:
        d.stop()


def test_lan_cookie_devices_cannot_reach_the_chat_api():
    from jarves.dashboard_auth import DEVICE_READ_DENY
    assert "/api/chat" in DEVICE_READ_DENY


# ---------------------------------------------------------------- paired phones and PCs
def test_device_chat_streams_hebrew_and_is_idempotent(rig):
    r = rig(mobile_on=True)
    tok, dev = pair(r.dash)
    st, state = dev_req(r.dash, "GET", "/api/m/chat/state?lang=he", token=tok)
    assert st == 200 and state["available"] and state["can_speak"] is False
    st, out = dev_req(r.dash, "POST", "/api/m/chat/send", token=tok, body={"text": HEBREW, "id": "phone-id-0001", "speak": True})
    assert st == 200 and out["turn"] == "t1"
    got, cursor, sig = [], 0, ""
    while not any(e["type"] == "reply" for e in got):
        st, ev = dev_req(r.dash, "GET", f"/api/m/chat/events?after={cursor}&sig={sig}&wait=20", token=tok)
        assert st == 200
        got += ev["events"]
        cursor, sig = ev["cursor"], ev["sig"]
    reply = next(e for e in got if e["type"] == "reply")
    assert reply["text"].startswith("בסדר") and reply["spoken"] is False
    st, again = dev_req(r.dash, "POST", "/api/m/chat/send", token=tok, body={"text": HEBREW, "id": "phone-id-0001"})
    assert st == 200 and again["replay"] is True and r.agent.seen == [HEBREW]
    # the audit log holds ids, paths and counts, never what was said
    audit = (r.home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    assert "chat/send" in audit and HEBREW not in audit and "שלום" not in audit and reply["text"] not in audit


def test_device_chat_is_rate_limited(rig):
    r = rig(mobile_on=True)
    tok, _ = pair(r.dash)
    r.dash.mobile.clock = lambda: 1000.0                          # a frozen minute: the 21st message is refused
    codes = [dev_req(r.dash, "POST", "/api/m/chat/send", token=tok, body={"text": "/help", "id": f"rate-id-{i:04d}"})[0]
             for i in range(mobile.CHAT_RATE_PER_MIN + 1)]
    assert codes[:mobile.CHAT_RATE_PER_MIN] == [200] * mobile.CHAT_RATE_PER_MIN and codes[-1] == 429


def test_forbidden_pages_are_refused_by_the_server_with_fixed_codes(rig):
    r = rig(mobile_on=True)
    tok, dev = pair(r.dash)
    d = r.dash
    # the pages every device has by default
    st, pages = dev_req(d, "GET", "/api/m/pages", token=tok)
    assert st == 200 and pages["allowed"] == ["home", "chat", "approvals", "health"]
    assert pages["pc_only"] == ["settings", "secrets", "files", "devices"]
    for ok_path in ("/api/m/home", "/api/m/health", "/api/m/chat/state", "/api/m/approvals", "/api/m/pages/chat"):
        assert dev_req(d, "GET", ok_path, token=tok)[0] == 200, ok_path
    # computer-only pages: refused whatever the page shows, with PAGE_PC_ONLY
    forbidden = [("GET", "/api/m/pages/settings"), ("GET", "/api/m/pages/secrets"), ("GET", "/api/m/pages/files"),
                 ("GET", "/api/m/pages/devices"), ("GET", "/api/m/settings"), ("POST", "/api/m/settings"),
                 ("GET", "/api/m/secrets"), ("GET", "/api/m/files"), ("PUT", "/api/m/workspace/USER.md"),
                 ("GET", "/api/m/workspace/USER.md"), ("GET", "/api/m/devices"), ("POST", f"/api/m/devices/{dev}/revoke"),
                 ("POST", "/api/m/devices/0123456789ab/revoke"), ("POST", "/api/m/mobile/code"), ("GET", "/api/m/memory"),
                 ("POST", "/api/m/features/dashboard_lan"), ("GET", "/api/m/backup")]
    for method, path in forbidden:
        st, body = dev_req(d, method, path, token=tok, body={} if method != "GET" else None)
        assert st == 403 and body["code"] == "PAGE_PC_ONLY", (method, path, st, body)
    # nothing was revoked or changed by those attempts
    assert [x["revoked"] for x in r.dash.mobile.devices()] == [None]
    assert dev_req(d, "GET", "/api/m/me", token=tok)[0] == 200
    audit = [e for e in r.dash.mobile.audit_tail() if e["event"] == "refused_pc_only"]
    assert len(audit) >= len(forbidden)
    # an unknown page is a plain 404, not a refusal that hints at anything
    assert dev_req(d, "GET", "/api/m/pages/nothing", token=tok)[1]["code"] == "PAGE_UNKNOWN"
    # no token, no page
    assert dev_req(d, "GET", "/api/m/pages/settings")[0] == 401


def test_owner_chooses_pages_per_device_and_the_server_enforces_it(rig):
    r = rig(mobile_on=True)
    tok, dev = pair(r.dash)
    d = r.dash
    st, _ = owner_req(d, "POST", f"/api/mobile/devices/{dev}/pages", {"pages": ["home", "approvals"]})
    assert st == 200
    assert [x["pages"] for x in d.mobile.devices()] == [["home", "approvals"]]
    for path in ("/api/m/chat/state", "/api/m/chat/events?after=0", "/api/m/health", "/api/m/status", "/api/m/pages/chat"):
        st, body = dev_req(d, "GET", path, token=tok)
        assert st == 403 and body["code"] == "PAGE_NOT_ALLOWED", (path, st, body)
    for path, payload in (("/api/m/chat/send", {"text": "hi"}), ("/api/m/chat", {"text": "hi"}), ("/api/m/voice", {})):
        st, body = dev_req(d, "POST", path, token=tok, body=payload)
        assert st == 403 and body.get("code") == "PAGE_NOT_ALLOWED", (path, st, body)
    assert r.agent.seen == []                                      # the refused message never reached the model
    assert dev_req(d, "GET", "/api/m/home", token=tok)[0] == 200 and dev_req(d, "GET", "/api/m/approvals", token=tok)[0] == 200
    # a computer-only page can never be handed to a device, by the owner's API or the CLI
    st, body = owner_req(d, "POST", f"/api/mobile/devices/{dev}/pages", {"pages": ["chat", "settings"]})
    assert st == 403 and body["code"] == "PAGE_PC_ONLY"
    assert d.mobile.set_pages(dev, ["chat", "settings", "files"], by="test") and d.mobile.devices()[0]["pages"] == ["chat"]
    assert dev_req(d, "GET", "/api/m/chat/state", token=tok)[0] == 200
    # the choice survives a new session of the same device
    assert mobile.MobileAccess(r.home).authenticate(tok)["pages"] == ["chat"]


def test_cli_lists_and_sets_device_pages(rig, capsys):
    r = rig(mobile_on=True)
    tok, dev = pair(r.dash)
    assert cli.main(["web", "--set-pages", dev, "chat,home"]) == 0
    out = capsys.readouterr().out
    assert dev in out and "home, chat" in out and "computer-only" in out
    assert cli.main(["web", "--set-pages", dev, "chat,settings"]) == 1
    assert "stay on the computer" in capsys.readouterr().err
    assert cli.main(["web", "--set-pages", dev, "chat,nothing"]) == 1
    assert cli.main(["web", "--set-pages", "ffffffffffff", "chat"]) == 1
    capsys.readouterr()
    assert cli.main(["web", "--device-pages"]) == 0
    assert "home, chat" in capsys.readouterr().out


def test_the_phone_page_files_are_served_and_safe(rig):
    r = rig(mobile_on=True)
    for name, ctype in (("chatview.js", "text/javascript; charset=utf-8"), ("chatview.css", "text/css; charset=utf-8")):
        c = http.client.HTTPConnection("127.0.0.1", r.dash.port, timeout=10)
        c.request("GET", "/m/" + name, headers={"Host": f"{HOST}:{r.dash.port}"})
        resp = c.getresponse()
        body = resp.read()
        c.close()
        assert resp.status == 200 and resp.getheader("Content-Type") == ctype and body
    from pathlib import Path
    static = Path(mobile.__file__).parent / "dashboard_static"
    for js in ("chatview.js", "chat.js"):
        src = (static / js).read_text(encoding="utf-8")
        assert "innerHTML" not in src and "eval(" not in src and "document.write" not in src and "http://" not in src and "https://" not in src
    idx = (static / "index.html").read_text(encoding="utf-8")
    assert 'href="#/chat"' in idx and "chatview.js" in idx and "chat.js" in idx


# ---------------------------------------------------------------- jarvis web
def test_jarvis_web_opens_the_chat_page_without_a_core_and_without_a_browser(home, monkeypatch):
    opened, handles, ready = [], [], threading.Event()
    real = web_cli.start_standalone

    def spy(*a, **kw):
        h = real(*a, popup=False, agent_factory=lambda cfg, reg, mem, hm, budget=None: FakeAgent(reg), **kw)
        handles.append(h)
        return h
    monkeypatch.setattr(web_cli, "start_standalone", spy)
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: (opened.append(url), ready.set()))
    code = []
    t = threading.Thread(target=lambda: code.append(cli.main(["web", "--port", str(free_port())])), daemon=True)
    t.start()
    assert ready.wait(60), "the chat page was not opened"
    url = opened[0]
    assert url.endswith("#/chat") and "127.0.0.1" in url
    st, state = owner_req(handles[0].dashboard, "GET", "/api/chat/state")
    assert st == 200 and state["available"]
    handles[0].dashboard.httpd.shutdown()                          # ends the foreground command like Ctrl+C
    t.join(60)
    assert code == [0] and not t.is_alive()


def test_jarvis_web_asks_a_running_core_to_open_the_page(home, monkeypatch, capsys):
    sent = []

    def fake_call(h, port, msg, expect, timeout=5):
        sent.append((msg, expect))
        return {"url": f"http://127.0.0.1:{port}/", "browser": True}
    monkeypatch.setattr(cli, "_ws_call", fake_call)
    assert cli.main(["web"]) == 0
    assert sent[0][0]["type"] == "ui.open_dashboard" and sent[0][0]["payload"] == {"page": "#/chat", "open": True}
    assert sent[0][1] == "dashboard.opened" and "Chat page is open" in capsys.readouterr().out
    assert cli.main(["web", "--no-open"]) == 0 and sent[1][0]["payload"]["open"] is False


def test_jarvis_web_refuses_when_its_switch_is_off(home, capsys):
    from jarves.features import Features
    Features(Config(), home).set("web_chat", False)
    assert cli.main(["web"]) == 1
    assert "jarvis features on web_chat" in capsys.readouterr().err


def test_core_link_lets_the_cli_open_the_dashboard():
    from jarves import link
    assert "ui.open_dashboard" in link.ALLOWED["cli"]
