"""Conversation context trace (owner request, Issue #17): capture of what GPT-Live / the backend were given, secret
redaction, privacy mode, retention, the privacy actions, typed chat and the dashboard routes (offline, fakes)."""
import http.client
import json
import time

import pytest
from conftest import drain_tools

from jarves import context_trace as ct
from jarves import dashboard, features, logs, privacy
from jarves import workspace as wsp
from jarves.config import Config
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import ClockSkill, MemorySkill
from jarves.store import Store
from jarves.voice.live import LiveVoice, clock_note

SECRET = "sk-proj-ABCDEFGHIJKLMNOP1234567890"
EMAIL = "someone.private@example.com"


class FakeConn:
    """Only the send methods the trace taps (thinking / commentary notes, tool results)."""

    def __init__(self):
        self.sent = []
        outer = self

        class Notes:
            def __init__(self, kind):
                self.kind = kind

            async def append(self, content, delegation_id=None, event_id=None):
                outer.sent.append((self.kind, content, event_id))

        class Item:
            async def create(self, item, event_id=None):
                outer.sent.append(("item", item, event_id))

        class Resp:
            def __init__(self):
                self.item = Item()

            async def create(self, event_id=None):
                outer.sent.append(("response.create", None, event_id))

        class Session:
            def __init__(self):
                self.thinking, self.commentary = Notes("thinking"), Notes("commentary")
        self.session, self.response = Session(), Resp()


@pytest.fixture(autouse=True)
def _privacy_off():
    before = logs._privacy
    logs.set_privacy(False)
    yield
    logs.set_privacy(before)
    ct.close_all()


def _core_like(home, *, user_md="אני גר בחיפה ואוהב קפה.", facts=("הצבע האהוב עליי כחול",)):
    mem = MemoryStore(home / "memory.db")
    for f in facts:
        mem.add(f)
    ws = wsp.Workspace(home / "workspace")
    (home / "workspace" / "USER.md").write_text(user_md, encoding="utf-8")   # as if edited in a text editor
    reg = SkillRegistry()
    reg.register(ClockSkill())
    reg.register(MemorySkill(mem))
    feats = features.Features(Config(), home)
    ended = []

    async def on_end(reason):
        ended.append(reason)
    backend = lambda: "You are JARVES, the backend.\nFacts the user asked you to remember:\n" + "\n".join(
        f"- {m['text']}" for m in mem.list(20))
    lv = LiveVoice(reg, backend, name="JARVES", on_end=on_end)
    wsp.attach_live(lv, ws, mem)
    features.attach_live(lv, feats)
    assert ct.attach_live(lv, home=home, cfg=Config(), memory=mem, workspace=ws, registry=reg, feats=feats)
    return lv, mem, ws, reg, ended


def _start(lv):
    """What LiveVoice.start does: open the connection, then build + send the session configuration."""
    lv.conn = FakeConn()
    lv.running = True
    return lv.session_config()


def _link_session(home, started):
    st = Store(home / "store.db")
    st.ingest({"ts": started + 1.0, "code": "LIVE_STARTED", "session": logs._SESSION,
               "msg": "GPT-Live session started (gpt-live-1 -> gpt-6-luna)"})
    st.commit()
    return st, st.sessions()[0]["id"]


def _view(home, lv):
    ct.recorder(home).flush()
    tr_started = ct._recorders[str(home / "store.db")]._db.execute(
        "SELECT started FROM context_traces ORDER BY started DESC LIMIT 1").fetchone()[0]
    st, sid = _link_session(home, tr_started)
    return st, sid, ct.view(st, sid)


async def test_live_capture_blocks_memory_tools_notes_and_end(home):
    lv, mem, ws, reg, ended = _core_like(home)
    config = _start(lv)
    assert "Things you know about the user" in config["instructions"]      # the conversation itself is unchanged
    # during the conversation: a clock note, a background-task announcement, a tool call (remember), a ws change
    await lv.conn.session.thinking.append(content=clock_note(), delegation_id=None, event_id="jarves_clock_1")
    lv.running = False                                         # _send_note: no 1.5 s rejection wait in the test
    assert await lv._send_note("[task] The research finished. Result: מחיר המניה עלה", "t42")
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {"type": "response.created"}})
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {
        "type": "response.output_item.done", "item": {"type": "function_call", "name": "remember", "call_id": "c1",
                                                      "arguments": json.dumps({"text": "יש לי כלב בשם רקס"})}}})
    await drain_tools(lv)
    ws.write("USER.md", "אני גר בתל אביב.", by="user")
    lv.running = True
    assert await wsp.note_live(lv, ws, "USER.md")
    await lv.on_end("user")
    assert ended == ["user"]                                   # the Core's own on_end still runs

    st, sid, v = _view(home, lv)
    assert v["captured"] and v["kind"] == "live" and v["session_id"] == sid
    ids = [b["id"] for b in v["blocks"]]
    for want in ("front.persona", "front.self", "front.workspace", "front.workspace.USER.md", "front.memory",
                 "backend.rules", "backend.memory", "backend.tools"):
        assert want in ids, (want, ids)
    persona = next(b for b in v["blocks"] if b["id"] == "front.persona")
    assert persona["text"].startswith("You are ") and persona["tokens"] > 100 and len(persona["hash"]) == 12
    # the joined front blocks are exactly what was sent
    front = [b for b in v["blocks"] if b["group"] == "front"]
    assert sum(b["chars"] for b in front) <= len(config["instructions"]) and v["start_tokens"] == sum(
        b["tokens"] for b in v["blocks"])
    fact = next(m for m in v["memory"] if m["text"] == "הצבע האהוב עליי כחול")
    assert fact["source"] == "user" and fact["confidence"] == 1.0 and set(fact["targets"]) == {"front", "backend"}
    tools = {t["name"]: t for t in v["tools"]}
    assert tools["remember"]["offered"] and tools["get_time"]["offered"] and tools["web_search"]["offered"]
    assert v["meta"]["model"] == "gpt-live-1" and v["meta"]["backend_model"] == "gpt-6-luna"
    kinds = [e["kind"] for e in v["events"]]
    assert kinds[:2] == ["clock", "task"] and "tool_result" in kinds and "workspace" in kinds and kinds[-1] == "end"
    tool = next(e for e in v["events"] if e["kind"] == "tool_result")
    assert tool["data"]["tool"] == "remember" and tool["data"]["ok"] is True and "רקס" in tool["data"]["args"]
    end = v["events"][-1]["data"]
    assert [f["text"] for f in end["memory_added"]] == ["יש לי כלב בשם רקס"] and end["workspace_front_changed"]
    # the workspace note shows as a new version at the end; the diff lists it with its time
    assert any(b["id"] == ct.WS_NOTE_BLOCK and b["version"] == 1 for b in v["end_blocks"])
    assert any(d["id"] == ct.WS_NOTE_BLOCK and d["change"] == "added" and d["at"] for d in v["diff"]["blocks"])
    assert v["total_tokens"] > v["start_tokens"]
    st.close()


async def test_secrets_and_emails_never_stored(home):
    lv, *_ = _core_like(home, user_md=f"my key is {SECRET} and mail {EMAIL}", facts=(f"password: hunter2 {EMAIL}",))
    _start(lv)
    await lv.conn.session.thinking.append(content=f"[clock] token={SECRET}", event_id="x")
    ct.recorder(home).flush()
    raw = (home / "store.db").read_bytes() + b"".join(p.read_bytes() for p in home.glob("store.db-*"))
    assert SECRET.encode() not in raw and b"ABCDEFGHIJKLMNOP" not in raw
    assert EMAIL.encode() not in raw and b"hunter2" not in raw


async def test_privacy_mode_keeps_only_names_sizes_hashes(home):
    logs.set_privacy(True)
    lv, *_ = _core_like(home, user_md="סוד: אני אוהב פיצה עם אננס")
    _start(lv)
    await lv.conn.session.thinking.append(content=clock_note(), event_id="c")
    st, sid, v = _view(home, lv)
    assert v["private"] and all(b["text"] is None and b["chars"] > 0 and b["hash"] for b in v["blocks"])
    assert all(m["text"] is None and m["chars"] for m in v["memory"]) and v["memory"]
    assert v["events"][0]["text"] is None and v["events"][0]["chars"] > 0
    assert "אננס".encode() not in (home / "store.db").read_bytes()
    assert {t["name"] for t in v["tools"]} >= {"remember", "get_time"}       # names stay
    st.close()


def test_retention_prunes_old_traces(home):
    rec = ct.Recorder(home / "store.db")
    for days, tid in ((100, "old"), (1, "new")):
        rec._db.execute("INSERT INTO context_traces (id, kind, started) VALUES (?, 'live', ?)",
                        (tid, time.time() - days * 86400))
        rec._db.execute("INSERT INTO context_events (trace_id, ts, kind) VALUES (?, ?, 'clock')", (tid, time.time()))
    rec._db.commit()
    rec.close()
    rec = ct.Recorder(home / "store.db", keep_days=90)
    assert [r[0] for r in rec._db.execute("SELECT id FROM context_traces")] == ["new"]
    assert [r[0] for r in rec._db.execute("SELECT trace_id FROM context_events")] == ["new"]
    rec.close()


async def test_privacy_actions_mask_and_delete_context(home):
    lv, *_ = _core_like(home)
    _start(lv)
    await lv.conn.session.thinking.append(content=clock_note(), event_id="c")
    st, sid, v = _view(home, lv)
    st.close()
    ctx = dashboard.Ctx(home, Config())
    privacy.mask_history(ctx)
    v = ct.view(ctx.store, sid)
    assert v["private"] and all(b["text"] is None for b in v["blocks"]) and v["events"][0]["text"] is None
    assert all(m["text"] is None for m in v["memory"]) and v["blocks"][0]["hash"]
    privacy.delete_conversations(ctx)
    assert ctx.store.rows("SELECT COUNT(*) n FROM context_traces")[0]["n"] == 0
    ctx.close()


async def test_typed_chat_records_requests_and_changes(home):
    mem = MemoryStore(home / "memory.db")
    mem.add("אני אוהב חתולים")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    rules = {"text": "You are JARVES.\nFacts the user asked you to remember:\n- אני אוהב חתולים"}

    class Agent:
        cfg = Config().ai
        connection = "api_key"

        def instructions(self):
            return rules["text"]

        async def reply(self, text):
            return "ok"
    a = Agent()
    assert ct.attach_brain(a, home=home, cfg=Config(), memory=mem, registry=reg)
    assert await a.reply("שלום") == "ok"
    await a.reply("מה השעה")                                   # unchanged: no event
    rules["text"] += "\n- יש לי כלב"
    await a.reply("עוד משהו")
    ct.recorder(home).flush()
    db = ct._recorders[str(home / "store.db")]._db
    assert db.execute("SELECT COUNT(*) FROM context_traces WHERE kind = 'typed'").fetchone()[0] == 1
    ev = db.execute("SELECT kind, block FROM context_events").fetchall()
    assert ev == [("block_changed", "backend.memory")]


def test_switch_off_captures_nothing(home):
    lv, *_ = _core_like(home)
    f = features.Features(Config(), home)
    f.set(ct.FEATURE, False)
    lv2 = LiveVoice(SkillRegistry(), lambda: "b")
    ct.attach_live(lv2, home=home, cfg=Config(), feats=f)
    _start(lv2)
    assert lv2._ctx_trace is None
    c = Config()
    c.history.enabled = False
    assert not ct.attach_live(LiveVoice(SkillRegistry(), lambda: "b"), home=home, cfg=c)


def _req(d, path):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=5)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{d.port}", "Cookie": f"{dashboard.COOKIE}={d.url.split('t=')[1]}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


async def test_dashboard_routes(home):
    lv, *_ = _core_like(home, user_md=f"אני גר בחיפה. {EMAIL}")
    _start(lv)
    await lv.conn.session.thinking.append(content=clock_note(), event_id="c")
    st, sid, _ = _view(home, lv)
    st.close()
    d = dashboard.Dashboard(home, Config()).start()
    try:
        status, body = _req(d, f"/api/session/{sid}/context")
        v = json.loads(body)
        assert status == 200 and v["captured"] and v["blocks"] and EMAIL.encode() not in body
        status, body = _req(d, f"/api/session/{sid}/context/export")
        x = json.loads(body)
        assert status == 200 and x["format"] == "jarves-context-1" and x["redacted"] and x["blocks"]
        status, body = _req(d, "/api/context/usage?file=USER.md")
        u = json.loads(body)
        assert status == 200 and u["conversations"][0]["session_id"] == sid and u["conversations"][0]["used"]
        assert _req(d, "/api/context/usage?file=../x")[0] == 400
        assert _req(d, "/api/session/nope/context")[0] == 404
        status, js = _req(d, "/static/context.js")
        assert status == 200 and b"innerHTML" not in js and b"PAGES.context" in js
        assert b"/static/context.js" in _req(d, "/")[1]
    finally:
        d.stop()


def test_uncaptured_session_explains_why(home):
    st = Store(home / "store.db")
    st.ingest({"ts": time.time(), "code": "LIVE_STARTED", "session": "other", "msg": "GPT-Live session started"})
    st.commit()
    v = ct.view(st, st.sessions()[0]["id"])
    assert v["captured"] is False and "No context" in v["why"]
    st.close()


def test_split_is_lossless_and_labels_workspace_files():
    text = ("You are X.\nrules\n\nAlso delegate (a word or two...) more\n\nWorkspace files (the user can edit them; ...):"
            "\n[SOUL.md - personality and tone]\nfunny\n\n[USER.md - about the user]\nlikes tea\n\n"
            "Things you know about the user (from memory):\n- a\n\nFeatures that are OFF or not available right now: x")
    bs = ct.split_instructions("front", text, ct.FRONT_MARKERS, False)
    assert [b["id"] for b in bs] == ["front.persona", "front.self", "front.workspace", "front.workspace.SOUL.md",
                                     "front.workspace.USER.md", "front.memory", "front.features"]
    assert "".join(b["text"] for b in bs).replace("\n", "") == text.replace("\n", "")
    assert bs[3]["file"] == "SOUL.md" and ct.estimate_tokens("שלום עולם") >= 3
