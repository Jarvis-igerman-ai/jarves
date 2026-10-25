"""#332: the model sends 'today', ISO datetimes, since/until aliases, find_chat rows, numbers as strings, limits
above the max. None of that may end in TOOL_BAD_ARGS; a real failure must read as a failure, not as 'no messages'."""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest
from pydantic import ValidationError

from jarves import whatsapp_archive as arch
from jarves import whatsapp_bridge as wab
from jarves.skills import FAILURE_RULE, ToolResult, _bad_args_text

ROW = {"id": "972501230001@s.whatsapp.net", "name": "Dana", "number": "972501230001"}


def test_search_accepts_today_iso_datetimes_and_aliases():
    a = wab.SearchArgs.model_validate({"query": "hello", "since": "today", "until": "2026-10-25T23:59:59+03:00"})
    assert a.since == "today" and a.until == "2026-10-25"
    b = wab.SearchArgs.model_validate({"q": "hello", "from": "2026-10-25T00:00:00Z", "to": "yesterday", "chat": ROW})
    assert (b.query, b.since, b.until, b.who) == ("hello", "2026-10-25", "yesterday", "Dana")
    c = wab.SearchArgs.model_validate({"query": "hello", "date": "today", "jid": ROW["id"]})
    assert c.since == "today" and c.who == ROW["id"]


def test_parse_when_words():
    now = datetime(2026, 10, 25, 15, 0).timestamp()
    midnight = datetime(2026, 10, 25).timestamp()
    assert arch.parse_when("today", now) == midnight
    assert arch.parse_when("today", now, end=True) == datetime(2026, 10, 26).timestamp()
    assert arch.parse_when("yesterday", now) == datetime(2026, 10, 24).timestamp()


def test_limits_clamp_and_strings_become_numbers():
    assert wab.ChatArgs.model_validate({"who": "Dana", "limit": "50"}).limit == 20
    assert wab.ChatArgs.model_validate({"chat": ROW, "count": 3}).who == "Dana"
    assert wab.FindChatArgs.model_validate({"query": "dana", "limit": "99"}).limit == 10
    s = wab.SummArgs.model_validate({"who": [ROW, "Bob"], "since_hours": "9999"})
    assert s.who == ["Dana", "Bob"] and s.since_hours == 336
    assert wab.SummArgs.model_validate({"who": ROW, "hours": "today"}).since_hours == 24


def test_find_chat_result_shapes():
    assert wab.ChatArgs.model_validate({"who": {"chats": [ROW]}}).who == "Dana"
    assert wab.ComposeArgs.model_validate({"to": ROW, "message": "hi"}).who == "Dana"
    assert wab.SendArgs.model_validate({"draft": "abc"}).draft_id == "abc"


def test_bad_args_message_names_fields_and_shape():
    with pytest.raises(ValidationError) as ei:
        wab.SearchArgs.model_validate({"query": "hi", "since": ["a", "b"]})
    text = _bad_args_text("whatsapp_search", wab.SearchArgs, ei.value)
    assert "query" in text and "Accepted fields" in text and "since" in text and "TOOL_BAD_ARGS" in text


def test_failed_result_tells_the_model_it_failed():
    out = json.loads(ToolResult(False, "bad", code="TOOL_BAD_ARGS").to_model())
    assert out["tool_failed"] is True and out["failure_rule"] == FAILURE_RULE and "no messages" in FAILURE_RULE
    ok = json.loads(ToolResult(True, "fine").to_model())
    assert "tool_failed" not in ok


def test_registry_returns_actionable_error():
    import asyncio
    from jarves.skills import Skill, SkillRegistry, Tool

    async def run(a):
        return ToolResult(True, "x")

    class S(Skill):
        name = "s"
        description = "d"

        def tools(self):
            return [Tool("whatsapp_search", "d", run, wab.SearchArgs, permission="read")]
    reg = SkillRegistry()
    reg.register(S())
    res = asyncio.run(reg.call("whatsapp_search", {"query": "hi", "since": ["a", "b"]}, origin="model"))
    assert not res.ok and res.code == "TOOL_BAD_ARGS" and "since" in res.message and "Accepted fields" in res.message
    good = asyncio.run(reg.call("whatsapp_search", {"q": "hi", "since": "2026-10-25T10:00:00"}, origin="model"))
    assert good.ok


async def test_search_today_includes_today_and_excludes_yesterday(tmp_path):
    from datetime import datetime
    from test_whatsapp_order import Stub, msg, run, skill
    now = datetime(2026, 9, 22, 12, 0).timestamp()
    ms = [msg(1, datetime(2026, 9, 21, 22, 0).timestamp(), "yesterday late"),
          msg(2, datetime(2026, 9, 22, 8, 0).timestamp(), "today morning"),
          msg(3, datetime(2026, 9, 22, 11, 0).timestamp(), "today noon")]
    # a person plus a day range returns that chat's messages in range
    r = await run(skill(tmp_path, Stub(ms), now), "whatsapp_search", query="messages", who="דנה כהן", since="today")
    assert r.ok and [m["text"] for m in r.data["untrusted_messages"]] == ["today noon", "today morning"]
    # search hits are filtered by the range too, and yesterday works with until
    r = await run(skill(tmp_path, Stub([], hits=ms), now), "whatsapp_search", query="today", since="today")
    assert sorted(m["text"] for m in r.data["untrusted_messages"]) == ["today morning", "today noon"]
    r = await run(skill(tmp_path, Stub([], hits=ms), now), "whatsapp_search", query="late", since="yesterday",
                  until="yesterday")
    assert [m["text"] for m in r.data["untrusted_messages"]] == ["yesterday late"]


# ---- #335: the owner's whatsapp_search failed on `query` (logged bad_fields=["query"]) ----
@pytest.mark.parametrize("raw", [
    {"who": "Dana", "since": "today"},                       # no query at all: "her messages today"
    {"query": "", "who": "Dana", "since": "today"},
    {"query": None, "who": "Dana"},
    {"query": "a", "who": "Dana"},                           # one character
    {"query": ROW, "who": "Dana"},                           # a find_chat row instead of words
    {"query": 5, "who": "Dana", "limit": 10, "chat_id": ROW["id"], "extra_unknown": 1},
])
def test_search_validates_without_real_words(raw):
    a = wab.SearchArgs.model_validate(raw)
    assert isinstance(a.query, str)


async def test_search_tool_empty_query_with_chat_lists_that_chat_and_without_chat_names_the_fix(tmp_path):
    from datetime import datetime
    from test_whatsapp_order import Stub, msg, run, skill
    now = datetime(2026, 9, 22, 12, 0).timestamp()
    ms = [msg(2, datetime(2026, 9, 22, 8, 0).timestamp(), "today morning")]
    r = await run(skill(tmp_path, Stub(ms), now), "whatsapp_search", who="דנה כהן", since="today")
    assert r.ok and [m["text"] for m in r.data["untrusted_messages"]] == ["today morning"]
    r = await run(skill(tmp_path, Stub(ms), now), "whatsapp_search", since="today")
    assert not r.ok and r.code == "WA_NEED_QUERY"


def test_untrusted_turn_refusal_is_a_plain_safety_rule_not_a_failure():
    r = ToolResult(False, "x", {"safety_rule": True, "user_next_step_en": "Say 'show them' to open it."},
                   code="TOOL_UNTRUSTED_TURN")
    out = json.loads(r.to_model())
    assert "tool_failed" not in out and out["user_next_step_en"].startswith("Say 'show them'")
