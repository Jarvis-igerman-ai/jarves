"""Issue #17 voice behaviour fixes (real PC evidence): jargon filler, claims before results, note delete vs create,
acknowledged-but-undelegated, polite stop wording, repeated workspace_read (offline, fakes only)."""
from __future__ import annotations

import asyncio

import pytest

from jarves import diagnose, notes as N, phrases
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult
from jarves.skills.settings import SettingsSkill
from jarves.userdata import UserDB
from jarves.voice import live as live_mod
from jarves.voice.live import action_claim, front_instructions, waiting_only
from test_voice_fixes_cv import _cleanup, _jarves, _user, make_live


# ---------------------------------------------------------------- (1) one reviewed filler list
def test_jargon_ack_is_banned_in_the_persona_and_known_to_diagnose():
    p = front_instructions()
    for w in ("roger", "copy", "wilco"):
        assert f'"{w}"' in p
    assert "radio, military or slang jargon" in p
    assert "רוגס" in phrases.JARGON_ACKS and "רוגס" in diagnose.ACK       # the ASR spelling the owner saw
    assert diagnose.ACK is phrases.ACK_WORDS                              # one list, not two copies
    assert live_mod._FILLER_STEMS is phrases.LEAD_IN_STEMS
    assert "roger" in live_mod._BARE_REPLIES


def test_jargon_only_appears_inside_the_ban_list():
    p = front_instructions()
    assert p.count('"roger"') == 1 and 'a filler like "רגע"' in p


# ---------------------------------------------------------------- (2) no claim before the result
def test_persona_claims_only_after_result_for_camera_mute_notes():
    p = front_instructions()
    assert "only after the result says it is off" in p
    assert "only after the mute result" in p
    assert "NEVER create or add a note" in p and "which note was deleted" in p


def test_claim_detector_knows_camera_off_verbs():
    assert action_claim("כיבתי את המצלמה") and action_claim("כיביתי את המצלמה") and action_claim("השתקתי")
    assert not action_claim("אני מכבה את המצלמה אחרי שתאשר")


def test_false_claim_note_asks_for_a_one_sentence_self_correction():
    n = live_mod.FALSE_CLAIM_NOTE
    assert "self-correction" in n and "ONE short sentence" in n and "really happened" in n


async def test_false_camera_claim_triggers_self_correction_note(monkeypatch):
    monkeypatch.setattr(live_mod, "ANNOUNCE_USER_QUIET_S", 0.0)
    lv, sent = make_live()
    try:
        await _user(lv, "מה אתה עושה עכשיו")
        await _jarves(lv, "כיביתי את המצלמה.")
        await asyncio.sleep(2.0)
        notes = [c for k, c, _ in sent.items if k == "commentary"]
        assert any("self-correction" in n and "כיביתי" in n for n in notes)
        assert lv._stats["false_action_claims"] == 1
    finally:
        await _cleanup(lv)


# ---------------------------------------------------------------- (3) delete deletes, never creates
@pytest.fixture
def db(tmp_path):
    d = UserDB(tmp_path / "memory.db")
    yield d
    d.close()


def _reg(notes):
    reg = SkillRegistry(allowed_actions=N.ALLOWED_ACTIONS)
    reg.register(N.make_skill(notes))
    return reg


async def test_note_delete_by_title_deletes_and_names_the_note(db):
    notes = N.Notes(db)
    notes.add("קניות", "חלב")
    notes.add("רעיון", "רובוט")
    reg = _reg(notes)
    asked = []

    async def yes(prompt, **_):
        asked.append(prompt)
        return True
    reg.confirm = yes
    r = await reg.call("note_delete", {"query": "קניות"})
    assert r.ok and "קניות" in r.message and r.data["deleted"]["title"] == "קניות"
    assert "קניות" in asked[0]
    assert [n["title"] for n in notes.search()] == ["רעיון"]               # deleted, nothing created


async def test_note_delete_never_guesses_among_several_or_without_target(db):
    notes = N.Notes(db)
    notes.add("קניות א", "x")
    notes.add("קניות ב", "y")
    reg = _reg(notes)

    async def yes(prompt, **_):
        return True
    reg.confirm = yes
    amb = await reg.call("note_delete", {"query": "קניות"})
    assert not amb.ok and amb.code == "NOTE_AMBIGUOUS" and len(amb.data["notes"]) == 2
    none = await reg.call("note_delete", {})
    assert not none.ok and len(notes.search()) == 2


def test_note_tool_descriptions_are_unambiguous():
    d = {t.name: t.description for t in _reg(None).tools()}
    assert "Never for delete" in d["note_add"] and "never create" in d["note_delete"]


# ---------------------------------------------------------------- (4) acknowledgement without delegation
def test_waiting_word_rule_is_in_the_persona():
    assert waiting_only("רגע...") and not waiting_only("מחקתי את הפתק קניות.")
    assert "in the same response" in front_instructions()


# ---------------------------------------------------------------- (5) polite stop / no unverified advice
def test_persona_has_polite_stop_and_no_session_advice_rules():
    p = front_instructions()
    assert "Stop requests" in p and "polite" in p
    assert "Never give advice on how to end the conversation" in p


# ---------------------------------------------------------------- (6) repeated reads are served once
def test_workspace_read_is_cached_for_30s():
    tool = {t.name: t for t in SettingsSkill(object(), object()).tools()}["workspace_read"]
    assert tool.cache_s == 30.0


async def test_registry_serves_an_identical_read_once_until_a_side_effect_or_new_turn():
    n = {"c": 0}

    async def h(_):
        n["c"] += 1
        return ToolResult(True, "x")

    class K(Skill):
        name = "k"

        def tools(self):
            return [Tool("rd", "read", h, permission="read", cache_s=30.0),
                    Tool("wr", "act", h, permission="action")]
    reg = SkillRegistry()
    reg.register(K())
    reg.allowed_actions.add("wr")
    a = await reg.call("rd", {})
    b = await reg.call("rd", {})
    assert a.ok and b.ok and n["c"] == 1 and "cached" in b.data
    await reg.call("wr", {})                         # a side effect drops the cache
    await reg.call("rd", {})
    assert n["c"] == 3
    reg.new_turn()                                   # a new user turn reads fresh
    await reg.call("rd", {})
    assert n["c"] == 4
