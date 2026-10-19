"""WP-D: workspace files, self-settings Skill, memory provenance and the background curator (all offline)."""
import os
import sqlite3
import time

import pytest

from conftest import FakeSTT, FakeTTS, open_face
from jarves import workspace as wsp
from jarves.app import Core
from jarves.curator import Curator, extract_rules
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import MemorySkill
from jarves.skills.music import FakePlayer
from jarves.skills.settings import LIVE_VOICES, Settings, SettingsSkill
from jarves.voice.audio import SimAudio


# ---------------------------------------------------------------- memory provenance
def test_old_memory_db_is_migrated(tmp_path):
    db = sqlite3.connect(tmp_path / "m.db")
    db.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created TEXT NOT NULL,"
               " updated TEXT NOT NULL)")
    db.execute("INSERT INTO memories (text, created, updated) VALUES ('old fact', 'x', 'x')")
    db.commit()
    db.close()
    m = MemoryStore(tmp_path / "m.db")
    r = m.list()[0]
    assert r["text"] == "old fact" and r["source"] == "user" and r["confidence"] == 1.0
    m.close()


def test_upsert_dedupes_and_prompt_order(tmp_path):
    m = MemoryStore(tmp_path / "m.db")
    a, created = m.upsert("המשתמש אוהב קפה שחור", session="s1", confidence=0.7)
    assert created
    b, created = m.upsert("המשתמש אוהב קפה שחור!", session="s2", confidence=0.8)   # same fact, other wording
    assert b == a and not created and m.get(a)["confidence"] == 0.8
    m.add("החתול נקרא מיצי")                                       # user-stated: first in the prompt
    m.upsert("low confidence guess", confidence=0.3)
    texts = [r["text"] for r in m.for_prompt(10, min_confidence=0.6)]
    assert texts == ["החתול נקרא מיצי", "המשתמש אוהב קפה שחור"]
    m.upsert("המשתמש אוהב קפה שחור", source="user", confidence=1.0)   # the user confirms an auto fact
    assert m.get(a)["source"] == "user"
    m.upsert("other auto fact", session="s9")
    assert m.delete_session("s9") == 1 and m.count() == 3
    m.close()


# ---------------------------------------------------------------- workspace
def test_workspace_defaults_sections_and_hot_reload(tmp_path):
    ws = wsp.Workspace(tmp_path / "workspace")
    names = {f["name"] for f in ws.files()}
    assert names == set(wsp.SPECS) and (tmp_path / "workspace" / "memory").is_dir()
    front = ws.section("front")
    assert "ג'ארוויס" in front and "SOUL.md" not in front          # comment-only defaults inject nothing
    assert "AGENTS.md" in ws.section("backend") and "AGENTS.md" not in front
    p = tmp_path / "workspace" / "SOUL.md"
    p.write_text("# Personality\n- תדבר לאט ובשקט.\n", encoding="utf-8")   # edited in a text editor
    os.utime(p, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    assert "תדבר לאט" in ws.section("front")                      # picked up without a restart
    p.write_text("# P\n- " + "א" * 5000 + "\n", encoding="utf-8")
    os.utime(p, ns=(time.time_ns() + 2 * 10**9, time.time_ns() + 2 * 10**9))
    assert len(ws.section("front")) <= wsp.TOTAL_CHARS["front"] + 300 and "(truncated)" in ws.section("front")


def test_workspace_write_backup_undo_and_limits(tmp_path):
    ws = wsp.Workspace(tmp_path / "ws")
    seen = []
    ws.listeners.append(seen.append)
    orig = ws.read("USER")
    r = ws.append("USER", "קוראים לי איגל", section="פרטים")
    assert r["changed"] and "+- קוראים לי איגל" in r["diff"] and seen == ["USER.md"]
    assert "## פרטים\n- קוראים לי איגל" in ws.read("user.md")
    ws.append("USER", "גר בחיפה", section="פרטים")
    assert ws.read("USER").index("איגל") < ws.read("USER").index("חיפה")
    assert len(ws.history("USER")) == 2
    ws.undo("USER")
    assert "חיפה" not in ws.read("USER") and "איגל" in ws.read("USER")
    first = ws.history("USER")[-1]["version"]
    ws.restore("USER", first)
    assert ws.read("USER") == orig
    with pytest.raises(wsp.WorkspaceError):
        ws.write("AGENTS", "x", by="agent")                     # user-only
    with pytest.raises(wsp.WorkspaceError):
        ws.write("TOOLS", "x")                                  # generated
    with pytest.raises(wsp.WorkspaceError):
        ws.write("../config.toml", "x")                         # never an arbitrary path
    with pytest.raises(wsp.WorkspaceError):
        ws.write("USER", "my api_key = sk-abcdefghijklmnopqrstuvwx")
    with pytest.raises(wsp.WorkspaceError):
        ws.write("MEMORY", "x" * (wsp.MAX_FILE_CHARS + 1))
    assert ws.write("AGENTS", ws.read("AGENTS") + "- extra rule\n", by="user")["changed"]
    assert "- extra rule" in ws.preview("AGENTS", "") or ws.preview("AGENTS", "").startswith("---")


def test_instruction_hooks(tmp_path):
    ws = wsp.Workspace(tmp_path / "ws")
    m = MemoryStore(tmp_path / "m.db")
    m.add("שם המשתמש: איגל")

    class Agent:
        def instructions(self):
            return "BASE"
    a = Agent()
    wsp.augment_instructions(a, ws)
    assert a.instructions().startswith("BASE\n\n") and "AGENTS.md" in a.instructions()

    class Live:
        def session_config(self):
            return {"instructions": "FRONT"}
    live = Live()
    wsp.attach_live(live, ws, m)
    ins = live.session_config()["instructions"]
    assert ins.startswith("FRONT") and "IDENTITY.md" in ins and "איגל" in ins
    m.close()


# ---------------------------------------------------------------- settings Skill
async def _registry(tmp_path, changes):
    m = MemoryStore(tmp_path / "m.db")
    ws = wsp.Workspace(tmp_path / "ws")
    r = SkillRegistry()
    r.register(MemorySkill(m))
    r.register(SettingsSkill(ws, Settings(m, {"voice.voice": "vesper"}), r, lambda v, now: changes.append((v, now))))
    return r, ws, m


async def test_settings_skill_edits_with_confirmation(tmp_path):
    changes = []
    r, ws, m = await _registry(tmp_path, changes)
    res = await r.call("workspace_note", {"file": "USER", "text": "מעדיף תשובות קצרות"})
    assert res.ok and "מעדיף תשובות קצרות" in ws.read("USER")            # pre-authorized, no prompt
    res = await r.call("workspace_edit", {"file": "SOUL", "text": "- תהיה ציני מאוד"})
    assert res.code == "TOOL_DECLINED" and "ציני" not in ws.read("SOUL")   # personality needs approval
    prompts = []

    async def yes(p):
        prompts.append(p)
        return True
    r.confirm = yes
    res = await r.call("workspace_edit", {"file": "SOUL", "text": "- תהיה ציני מאוד"})
    assert res.ok and "ציני" in ws.read("SOUL") and "תהיה ציני מאוד" in prompts[-1]
    assert "ציני" in ws.section("front")
    assert (await r.call("workspace_undo", {"file": "SOUL"})).ok and "ציני" not in ws.read("SOUL")
    assert (await r.call("workspace_edit", {"file": "AGENTS", "text": "x"})).code == "TOOL_BAD_ARGS"
    assert (await r.call("workspace_edit", {"file": "USER", "text": "password: hunter22"})).code == "WORKSPACE_REFUSED"
    assert (await r.call("workspace_read", {"file": "TOOLS"})).ok
    m.close()


async def test_setup_document_is_loaded_readable_in_pages_and_read_only(tmp_path):
    r, ws, m = await _registry(tmp_path, [])
    try:
        assert "[SETUP.md" in ws.section("backend")
        parts, offset = [], 0
        while offset is not None:
            result = await r.call("workspace_read", {"file": "SETUP", "offset": offset})
            assert result.ok and not result.data["agent_can_edit"] and len(result.message) <= 4000
            parts.append(result.message)
            offset = result.data["next_offset"]
        assert "".join(parts) == ws.read("SETUP")
        assert "## Updates, backups" in parts[-1]  # guidance after page one is reachable
        assert (await r.call("workspace_read", {"file": "SETUP", "offset": -1})).code == "TOOL_BAD_ARGS"
        assert (await r.call("workspace_edit", {"file": "SETUP", "text": "pretend installed"})).code == "TOOL_BAD_ARGS"
        with pytest.raises(wsp.WorkspaceError):
            ws.write("SETUP", "pretend installed", by="user")
        user = ws.read("USER")
        (ws.root / "SETUP.md").write_text("outdated guidance", encoding="utf-8")
        reloaded = wsp.Workspace(ws.root)
        assert reloaded.read("SETUP").startswith("# JARVES setup") and reloaded.read("USER") == user
    finally:
        m.close()


async def test_core_generates_complete_feature_docs_and_refreshes_switch_state(core):
    from jarves import features
    ws = core.workspace
    before = ws.read("FEATURES")
    assert all(f"- {fid} /" in before for fid in features.CATALOG)
    assert "[FEATURES.md" in ws.section("backend") and "[SETUP.md" in ws.section("backend")
    core.features.set("greeting", not core.features.enabled("greeting"))
    assert ws.read("FEATURES") != before
    assert "## Features (switches)" not in ws.read("TOOLS")
    offset, text = 0, ""
    while offset is not None:
        result = await core.registry.call("workspace_read", {"file": "FEATURES", "offset": offset})
        assert result.ok and not result.data["agent_can_edit"]
        text += result.message
        offset = result.data["next_offset"]
    assert text == ws.read("FEATURES")


async def test_long_generated_catalog_can_be_read_past_twenty_thousand_characters(tmp_path):
    r, ws, m = await _registry(tmp_path, [])
    try:
        catalog = "feature documentation\n" * 1500 + "last feature is reachable"
        ws.write_generated("FEATURES", catalog)
        offset, text = 0, ""
        while offset is not None:
            result = await r.call("workspace_read", {"file": "FEATURES", "offset": offset})
            assert result.ok
            text += result.message
            offset = result.data["next_offset"]
        assert text == catalog
    finally:
        m.close()


async def test_settings_skill_voice_and_allowlist(tmp_path):
    changes = []
    r, ws, m = await _registry(tmp_path, changes)
    listed = await r.call("list_settings")
    assert listed.ok and "voice.voice = vesper" in listed.message and len(listed.data["voices"]) == len(LIVE_VOICES)
    assert (await r.call("set_voice", {"voice": "robot"})).code == "BAD_VOICE" and not changes
    res = await r.call("set_voice", {"voice": "Meridian", "restart_now": False})
    assert res.ok and changes == [("meridian", False)] and m.get_setting("override:voice.voice") == "meridian"
    assert (await r.call("set_setting", {"key": "ai.daily_request_limit", "value": "9999"})).code == "SETTING_REFUSED"
    assert (await r.call("set_setting", {"key": "curator.llm", "value": "maybe"})).code == "SETTING_REFUSED"
    assert (await r.call("set_setting", {"key": "curator.llm", "value": "on"})).ok
    skills = await r.call("list_skills")
    assert "settings" in skills.data["skills"] and "remember" in skills.data["skills"]["memory"]
    md = wsp.tools_markdown(r, Settings(m).view())
    assert "set_voice" in md and "meridian" in md and "forget [asks first]" in md
    m.close()


# ---------------------------------------------------------------- curator
@pytest.mark.parametrize("said,fact", [
    ("קוראים לי איגל", "שם המשתמש: איגל"),
    ("ג'ארוויס, השם שלי הוא דנה כהן", "שם המשתמש: דנה כהן"),
    ("אני גר בחיפה עם המשפחה", "המשתמש גר בחיפה עם המשפחה"),
    ("הצבע האהוב עליי הוא כחול", "הצבע האהוב על המשתמש: כחול"),
    ("הכלב שלי נקרא רקס", "לכלב של המשתמש קוראים רקס"),
    ("יש לי חתולה שקוראים לה מיצי", "לחתולה של המשתמש קוראים מיצי"),
    ("אשתי קוראים לה נועה", "לאישה של המשתמש קוראים נועה"),
    ("אני ממש אוהב ג'אז אבל לא בבוקר", "המשתמש אוהב ג'אז"),
    ("אני לא אוהבת חדשות בבוקר", "המשתמש לא אוהב חדשות בבוקר"),
    ("תדבר איתי באנגלית", "המשתמש מעדיף שידברו איתו באנגלית"),
    ("My name is Igal", "The user's name is Igal"),
    ("I love jazz", "The user loves jazz"),
])
def test_curator_rules_extract(said, fact):
    assert fact in [c.text for c in extract_rules([said])]


@pytest.mark.parametrize("said", [
    "איך קוראים לי?", "אני אוהב אותך", "תזכור שהכלב שלי נקרא רקס", "הסיסמה שלי היא אבג123",
    "הקוד של הכספת 4321 ואני אוהב אותו", "מה מזג האוויר היום", "I love you", "my name is 054123456",
])
def test_curator_rules_skip(said):
    assert extract_rules([said]) == []


async def test_curator_run_dedupes_notes_and_llm_is_opt_in(tmp_path):
    m = MemoryStore(tmp_path / "m.db")
    ws = wsp.Workspace(tmp_path / "ws")
    calls = []

    async def llm(transcript):
        calls.append(transcript)
        return [{"fact": "המשתמש עובד כמהנדס", "confidence": 0.99}, {"fact": "קוד 123456", "confidence": 1},
                {"fact": "weak", "confidence": 0.2}]
    flags = {"curator.auto": True, "curator.llm": False}
    c = Curator(m, ws, llm=llm, enabled=lambda k: flags[k])
    c.observe("user", "קוראים לי איגל ואני גר בחיפה")
    c.observe("assistant", "נעים מאוד איגל! קוראים לי ג'ארוויס")       # the assistant's words are never facts
    saved = await c.run()
    assert {s["text"] for s in saved} == {"שם המשתמש: איגל", "המשתמש גר בחיפה"} and not calls
    row = m.get(saved[0]["id"])
    assert row["source"] == "auto" and row["session"] == saved[0]["session"] and row["confidence"] >= 0.8
    c.observe("user", "קוראים לי איגל")                                # heard again: no duplicate
    assert await c.run() == [] and m.count() == 2
    note = next((tmp_path / "ws" / "memory").glob("*.md")).read_text(encoding="utf-8")
    assert "שם המשתמש: איגל" in note and saved[0]["session"] in note
    flags["curator.llm"] = True
    c.observe("user", "אני עובד כמהנדס תוכנה כבר עשר שנים בחברה גדולה בחיפה")
    saved = await c.run()
    assert len(calls) == 1 and "USER: אני עובד" in calls[0] and m.usage_today("curator_llm") == 1
    texts = {s["text"] for s in saved}
    assert "המשתמש עובד כמהנדס" in texts and not any("123456" in t or t == "weak" for t in texts)
    assert max(s["confidence"] for s in saved) <= 0.95
    assert c.forget_session(saved[0]["session"]) == len(saved)
    m.close()


async def test_curator_glues_cut_live_transcript(tmp_path):
    """Real GPT-Live run 19 Oct: the user line was cut when the answer began and its tail came separately."""
    m = MemoryStore(tmp_path / "m.db")
    c = Curator(m)
    c.observe("user", "אגב... קוראים לי איגל והכלב שלי נקרא בנ", live=True)
    c.observe("assistant", "נעים", live=True)
    c.observe("user", "זו", live=True)
    c.observe("assistant", "מאוד, איגל.", live=True)
    texts = {s["text"] for s in await c.run()}
    assert texts == {"שם המשתמש: איגל", "לכלב של המשתמש קוראים בנזו"}
    m.close()


async def test_curator_idle_run_for_typed_chat(tmp_path):
    m = MemoryStore(tmp_path / "m.db")
    c = Curator(m, idle_s=0.05)
    c.observe("user", "קוראים לי רון", idle=True)
    import asyncio
    await asyncio.sleep(0.3)
    assert [r["text"] for r in m.list()] == ["שם המשתמש: רון"]
    m.close()


# ---------------------------------------------------------------- end to end: is memory working?
async def test_memory_e2e_fact_in_session1_recalled_in_session2(home, cfg, monkeypatch):
    """Session 1 (voice): the user mentions a fact in passing. Session 2 (after a Core restart): the fact is in the
    backend and voice instructions and `what do you remember` lists it - with no explicit "remember" request."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, on_end=None, **kw):
            self.on_state, self.on_transcript, self.on_end = on_state, on_transcript, on_end

        async def start(self):
            await self.on_state("listening")
            await self.on_transcript("user", "אגב, הכלב שלי נקרא בונזו והצבע האהוב עליי הוא ירוק")
            await self.on_transcript("assistant", "איזה שם יפה!")

        async def stop(self, reason="user"):
            await self.on_end(reason)
            await self.on_state("idle")

    kw = dict(audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
              use_real_audio=False, live_factory=FakeLive)
    c = Core(cfg, home, **kw)
    await c.start()
    try:
        f = await open_face(c)
        await f.send("talk.down")
        await f.wait("face.set_state", lambda p: p["state"] == "listening")
        await f.send("talk.down")                                 # end of conversation -> curator runs
        await f.wait("face.set_state", lambda p: p["state"] == "idle")
    finally:
        await c.stop()

    c2 = Core(cfg, home, **kw)
    await c2.start()
    try:
        assert "לכלב של המשתמש קוראים בונזו" in c2.agent.instructions()
        from jarves.voice.live import LiveVoice
        live = LiveVoice(c2.registry, c2.agent.instructions)
        wsp.attach_live(live, c2.workspace, c2.memory)
        conf = live.session_config()
        assert "בונזו" in conf["instructions"] and "בונזו" in conf["delegation"]["responses"]["instructions"]
        f = await open_face(c2)
        await f.send("user.text", {"text": "מה אתה זוכר"})
        said = (await f.wait("assistant.text"))["text"]
        assert "בונזו" in said and "ירוק" in said
        assert any(p.suffix == ".md" for p in (home / "workspace" / "memory").iterdir())
    finally:
        await c2.stop()
