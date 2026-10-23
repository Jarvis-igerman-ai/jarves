"""Issue #17: finding a WhatsApp chat by PART of its display name. resolve() is tiered (number, full name, first word,
any word with Hebrew prefixes, substring); several hits are never auto-picked; whatsapp_find_chat searches NAMES only;
the instructions forbid claiming a search that is not running. Offline, against the fake bridge."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jarves import whatsapp_bridge as wab
from jarves.whatsapp_bridge import WhatsAppBridgeSkill
from whatsapp_fixtures import bridge, clock, home  # noqa: F401

LEV = "972501230001@s.whatsapp.net"
LEV2 = "972501230002@s.whatsapp.net"
NAME = "לב אשתי המושלמת לב"


def chats(*names):
    return [{"id": f"97250000000{i}@s.whatsapp.net", "name": n, "number": f"97250000000{i}"}
            for i, n in enumerate(names)]


@pytest.mark.parametrize("q", ["לב", "אשתי", "המושלמת", "לאשתי", "מהמושלמת", "אשתי המושלמת", "שלמת", "מושלמת לב"])
def test_example_name_is_found_by_parts(q):
    cs = chats(NAME, "Bob Smith", "דנה כהן")
    hits = wab.resolve(cs, q)
    assert [c["name"] for c in hits] == [NAME], q


@pytest.mark.parametrize("name", ["❤️ " + NAME, "💕 לֵב אִשְׁתִּי הַמּוּשְׁלֶמֶת לֵב ❤", NAME.replace(" ", "‏ ")])
def test_emoji_niqqud_and_bidi_marks_do_not_hide_the_name(name):
    for q in ("לב", "אשתי", "לאשתי", "המושלמת"):
        assert len(wab.resolve(chats(name, "Bob Smith"), q)) == 1, (name, q)


def test_latin_casefold_and_substring():
    cs = chats("Bob Smith", "Alice Cooper")
    assert [c["name"] for c in wab.resolve(cs, "SMITH")] == ["Bob Smith"]       # any word
    assert [c["name"] for c in wab.resolve(cs, "ooper")] == ["Alice Cooper"]     # substring
    assert wab.resolve(cs, "x") == []


def test_tiers_keep_existing_behaviour():
    cs = chats("דנה כהן", "דנה לוי", "Bob Smith", "בוב")
    assert [c["name"] for c in wab.resolve(cs, "דנה כהן")] == ["דנה כהן"]            # full name
    assert len(wab.resolve(cs, "דנה")) == 2                                         # first name: ambiguous
    assert [c["name"] for c in wab.resolve(cs, "לדנה כהן")] == ["דנה כהן"]           # prefix on the query
    assert [c["name"] for c in wab.resolve(cs, "בוב")] == ["בוב"]                    # exact beats the other tiers
    assert [c["name"] for c in wab.resolve(cs, "Bob")] == ["Bob Smith"]
    assert wab.resolve(cs, "+972500000001")[0]["id"] == cs[1]["id"]              # a number


def test_non_fuzzy_keeps_the_old_exact_rules_for_sending():
    cs = chats(NAME)
    assert wab.resolve(cs, "אשתי", fuzzy=False) == []
    assert len(wab.resolve(cs, "לב", fuzzy=False)) == 1                             # the first word, as before


@pytest.fixture
def skill(home, bridge):
    taint = []
    s = WhatsAppBridgeSkill(home, on_untrusted=lambda: taint.append(1))
    s.taint = taint
    return s


def tool(skill, name):
    return next(t for t in skill.tools() if t.name == name)


async def call(skill, name, **args):
    t = tool(skill, name)
    return await t.handler(t.args(**args))


async def test_find_chat_returns_untrusted_matches_and_taints(skill, bridge):
    bridge.add_chat(LEV, NAME)
    bridge.deliver(LEV, "בוקר טוב", ts=1_790_000_000.0 - 50)
    r = await call(skill, "whatsapp_find_chat", text="אשתי")
    assert r.ok and r.code == "WA_FIND_CHAT" and skill.taint
    row = r.data["untrusted_chats"][0]
    assert row["name"] == NAME and row["number_end"] == "0001" and row["unread"] == 1 and row["last_activity"]
    assert r.data["searched"] == "chat_names" and r.data["not_searched"] == "message_text"
    assert "untrusted" in r.data["rule"] and "NAMES" in r.data["rule"]
    assert "בוקר טוב" not in json.dumps(r.data, ensure_ascii=False)                 # names only, no message text


async def test_find_chat_several_matches_are_all_offered(skill, bridge):
    bridge.add_chat(LEV, NAME)
    bridge.add_chat(LEV2, "לב ויצמן")
    bridge.deliver(LEV, "x", ts=1_790_000_000.0 - 45)
    bridge.deliver(LEV2, "hi", ts=1_790_000_000.0 - 40)
    r = await call(skill, "whatsapp_find_chat", text="לב")
    assert {c["name"] for c in r.data["untrusted_chats"]} == {NAME, "לב ויצמן"} and r.data["total_matches"] == 2
    assert len((await call(skill, "whatsapp_find_chat", text="לב", limit=1)).data["untrusted_chats"]) == 1


async def test_find_chat_zero_hits_says_it_searched_names_and_what_next(skill):
    r = await call(skill, "whatsapp_find_chat", text="zzzqqq")
    assert r.ok and r.data["untrusted_chats"] == [] and "whatsapp_search" in r.data["next_step"]
    assert "בשמות" in r.message


async def test_chat_lookup_by_part_of_the_name_and_never_auto_picks(skill, bridge):
    bridge.add_chat(LEV, NAME)
    bridge.deliver(LEV, "שלום", ts=1_790_000_000.0 - 50)
    r = await call(skill, "whatsapp_chat", who="אשתי")
    assert r.ok and r.data["untrusted_chat_name"] == NAME
    r = await call(skill, "whatsapp_summarize_asked", who=["לאשתי"], since_hours=24)
    assert r.ok and r.data["untrusted_conversations"][0]["name"] == NAME
    bridge.add_chat(LEV2, "לב ויצמן")
    bridge.deliver(LEV, "x", ts=1_790_000_000.0 - 45)
    bridge.deliver(LEV2, "hi", ts=1_790_000_000.0 - 40)
    r = await call(skill, "whatsapp_chat", who="לב")
    assert not r.ok and r.code == "WA_AMBIGUOUS"
    assert {c["name"] for c in r.data["untrusted_candidates"]} == {NAME, "לב ויצמן"}
    assert all(set(c) == {"name", "number_end"} for c in r.data["untrusted_candidates"])


async def test_zero_hits_message_and_next_step(skill):
    r = await call(skill, "whatsapp_chat", who="zzzqqq")
    assert not r.ok and r.code == "WA_UNKNOWN_CONTACT" and "בשמות" in r.message
    assert r.data["searched"] == "chat_names" and "whatsapp_find_chat" in r.data["next_step"]


async def test_compose_stays_exact(skill, bridge):
    bridge.add_chat(LEV, NAME)
    r = await call(skill, "whatsapp_compose", who="אשתי", text="היי")
    assert not r.ok and r.code != "WA_DRAFT_READY"                                    # never by a middle word


async def test_search_is_still_message_text(skill, bridge):
    bridge.add_chat(LEV, NAME)
    bridge.deliver(LEV, "מילת סוד", ts=1_790_000_000.0 - 50)
    assert (await call(skill, "whatsapp_search", query="מילת סוד")).data["untrusted_messages"]
    r = await call(skill, "whatsapp_search", query="אשתי")                            # a NAME is not message text
    assert r.data["untrusted_messages"] == []
    desc = tool(skill, "whatsapp_search").description
    assert "TEXT" in desc and "whatsapp_find_chat" in desc
    assert "NAMES" in tool(skill, "whatsapp_find_chat").description


def test_find_chat_is_a_read_tool_everywhere(home):
    s = WhatsAppBridgeSkill(home)
    t = tool(s, "whatsapp_find_chat")
    assert t.permission == "read" and t.untrusted_output and "whatsapp_find_chat" in wab.READ_TOOLS
    assert set(t.args.model_json_schema()["properties"]) == {"text", "regex", "limit"}


def test_instructions_forbid_claiming_a_search():
    assert "still searching" in wab.BACKEND_RULE and "still searching" in wab.FRONT_RULE
    assert "synchronous" in wab.BACKEND_RULE and "whatsapp_find_chat" in wab.BACKEND_RULE
    assert "chat names or message text" in wab.BACKEND_RULE
    agent = SimpleNamespace(instructions=lambda: "base")
    wab.augment_instructions(agent, SimpleNamespace(active=lambda fid: True))
    assert "Never say you are" in agent.instructions()
