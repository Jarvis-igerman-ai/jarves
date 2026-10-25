"""V5 #186: start a conversation on the (emulated) phone, continue it on the Windows desktop, and back.

Real WebChat + TextCore + dashboard/phone HTTP server (see test_web_chat.Rig), fake model (no paid call). The phone talks
through the real /api/m/chat/send endpoints with a paired token; the desktop is the owner chat (what the dashboard Chat
page and `jarvis chat` use). Acceptance of the issue: the same history opens on the other device, a test covers it, and no
second memory is created (one stored conversation, one model history)."""
from __future__ import annotations

import sqlite3

import pytest

from jarves import chat_engine
from jarves.chat_service import ChatService

from test_web_chat import (HEBREW, Rig, collect, dev_req, owner_poll, owner_req, pair)  # noqa: F401
from test_web_chat import rig  # noqa: F401  (fixture)


def phone_say(r, tok, text, mid):
    st, out = dev_req(r.dash, "POST", "/api/m/chat/send", token=tok, body={"text": text, "id": mid})
    assert st == 200, out
    got, cursor, sig = [], 0, ""
    while not any(e["type"] == "reply" and e.get("turn") == out["turn"] for e in got):
        st, ev = dev_req(r.dash, "GET", f"/api/m/chat/events?after={cursor}&sig={sig}&wait=20", token=tok)
        assert st == 200
        got += ev["events"]
        cursor, sig = ev["cursor"], ev["sig"]
    return next(e for e in got if e["type"] == "reply" and e.get("turn") == out["turn"])


def desktop_say(r, text):
    st, out = r.chat.submit(text, client="owner")
    assert st == 200, out
    ev, _, _ = collect(owner_poll(r), "reply", out["turn"])
    return next(e for e in ev if e["type"] == "reply" and e.get("turn") == out["turn"])


def desktop_resume_last(r):
    r.chat.submit("/resume")
    ev, _, _ = collect(owner_poll(r), "notice")
    listing = [e for e in ev if e["type"] == "notice"][-1]
    st, out = r.chat.submit("/resume 1")
    assert st == 200, out
    collect(owner_poll(r), "notice", out["turn"])
    return listing["sessions"]


def stored_sessions(home):
    con = sqlite3.connect(f"file:{(home / 'store.db').as_posix()}?mode=ro", uri=True)
    try:
        return [(sid, n) for sid, n in con.execute(
            "SELECT s.id, (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) FROM sessions s ORDER BY s.started")]
    finally:
        con.close()


def test_phone_to_desktop_and_back_is_one_conversation(rig):
    r = rig(mobile_on=True)
    tok, _dev = pair(r.dash)
    # 1. start on the phone (Hebrew, as the owner would)
    first = phone_say(r, tok, HEBREW, "phone-msg-0001")
    phone_say(r, tok, "and a second one", "phone-msg-0002")
    # 2. the desktop lists it and continues it: same history is loaded into the model and shown on the page
    sessions = desktop_resume_last(r)
    assert sessions[0]["first"] == HEBREW and sessions[0]["turns"] == 2
    assert r.agent.history == [("user", HEBREW), ("assistant", first["text"]),
                               ("user", "and a second one"), ("assistant", r.agent.history[3][1])]
    shown = [m["text"] for m in r.chat.state()["messages"] if m["role"] in ("user", "assistant")]
    assert shown[0] == HEBREW and shown[1] == first["text"] and len(shown) == 4
    desktop_say(r, "third, typed at the pc")
    # 3. back to the phone: the same model memory answers (it sees the earlier turns), no reset happened
    assert r.agent.resets <= 1 or r.agent.history[0] == ("user", HEBREW)
    phone_say(r, tok, "fourth, from the phone again", "phone-msg-0003")
    assert [t for role, t in r.agent.history if role == "user"] == [
        HEBREW, "and a second one", "third, typed at the pc", "fourth, from the phone again"]
    # 4. NO second memory: one stored conversation holding all 8 messages, and the phone sees the PC turn too
    assert stored_sessions(r.home) == [(stored_sessions(r.home)[0][0], 8)]
    st, state = dev_req(r.dash, "GET", "/api/m/chat/state?lang=en", token=tok)
    texts = [m["text"] for m in state["messages"] if m["role"] == "user"]
    assert "third, typed at the pc" in texts and "fourth, from the phone again" in texts


def test_a_restarted_desktop_opens_the_phone_conversation_with_the_same_history(home, rig):
    r1 = rig(mobile_on=True)
    tok, _ = pair(r1.dash)
    phone_say(r1, tok, "remember the number 42", "phone-msg-0101")
    r1.stop()                                         # the PC restarts: everything in RAM is gone
    r1.stop = lambda: None                            # (already stopped: the fixture must not stop it twice)
    r2 = rig()
    assert r2.agent.history == []                     # a fresh model memory ...
    sessions = desktop_resume_last(r2)                # ... that the desktop fills from the stored phone conversation
    assert sessions[0]["first"] == "remember the number 42"
    assert r2.agent.history[0] == ("user", "remember the number 42") and len(r2.agent.history) == 2
    desktop_say(r2, "what number?")
    assert len(stored_sessions(home)) == 1            # still the one conversation


def test_the_terminal_chat_service_resumes_a_phone_conversation(rig):
    """`jarvis chat` (/resume) uses ChatService: it must list and load a conversation that the phone started."""
    import asyncio
    r = rig(mobile_on=True)
    tok, _ = pair(r.dash)
    phone_say(r, tok, "from the phone", "phone-msg-0201")
    svc = ChatService(r.core)
    listed = svc.recent_sessions()
    assert listed and listed[0]["first"] == "from the phone" and listed[0]["turns"] == 1
    rows = svc.session_turns(listed[0]["id"])
    assert [t for _r, t in rows][0] == "from the phone" and rows[1][0] == "assistant"
    sent = []

    class Client:
        pass

    async def send(client, t, payload):
        sent.append((t, payload))
        return True
    svc.send = send
    asyncio.run_coroutine_threadsafe(svc._resume(Client(), listed[0]["id"]), r.core.loop).result(10)
    assert sent[0][1]["ok"] is True and r.agent.history[0] == ("user", "from the phone")


def test_a_private_phone_conversation_is_refused_honestly_not_faked(home, rig):
    from jarves import logs
    r = rig(mobile_on=True)
    tok, _ = pair(r.dash)
    logs.set_privacy(True)                            # privacy mode: the words are never stored
    try:
        phone_say(r, tok, "private words", "phone-msg-0301")
    finally:
        logs.set_privacy(False)
    r.chat.submit("/resume")
    ev, _, _ = collect(owner_poll(r), "notice")
    r.chat.submit("/resume 1")
    ev, _, _ = collect(owner_poll(r), "notice", "t2")
    note = [e for e in ev if e["type"] == "notice" and e.get("turn") == "t2"][-1]
    assert "private" in note["en"].lower()


def test_the_older_phone_chat_endpoint_also_lands_in_the_same_history(rig):
    """/api/m/chat (the bridge path, journalled for /api/m/sync) writes the same stored conversation the desktop resumes."""
    r = rig(mobile_on=True)
    tok, _ = pair(r.dash)
    st, out = dev_req(r.dash, "POST", "/api/m/chat", token=tok, body={"text": "via the bridge", "id": "bridge-msg-0001"})
    assert st == 200 and out["reply"]
    sessions = desktop_resume_last(r)
    assert sessions[0]["first"] == "via the bridge"
    assert r.agent.history[0] == ("user", "via the bridge")
    desktop_say(r, "continue at the pc")
    assert len(stored_sessions(r.home)) == 1
