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
        wab.SearchArgs.model_validate({"since": "today"})
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
    res = asyncio.run(reg.call("whatsapp_search", {"since": "today"}, origin="model"))
    assert not res.ok and res.code == "TOOL_BAD_ARGS" and "query" in res.message and "Accepted fields" in res.message
    good = asyncio.run(reg.call("whatsapp_search", {"q": "hi", "since": "2026-10-25T10:00:00"}, origin="model"))
    assert good.ok
