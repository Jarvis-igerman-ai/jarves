"""#363 / #329: messages the owner asked to read are PRINTED in the chat from the tool result (display text), newest
first for search / chronological for a chat, capped at 50 with 'N more: say more', Hebrew-safe; the display is never
given to the model and never lifts the untrusted-turn rule (no action tool afterwards). Offline: stub client."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from jarves import console_rtl, skills
from jarves import whatsapp_bridge as wab
from jarves.skills import TOOL_SINK, SkillRegistry, ToolResult
from test_whatsapp_order import NOON, Stub, at, msg, run, skill  # noqa: F401
from whatsapp_fixtures import DANA, home  # noqa: F401


def three():
    return [msg(1, at(22, 9, 1), "שלום, מה שלומך?"), msg(2, at(22, 9, 2), "my reply", from_me=True),
            msg(3, at(22, 9, 3), "קנית חלב?")]


async def test_chat_display_is_chronological_with_time_sender_and_text(home):
    r = await run(skill(home, Stub(three())), "whatsapp_chat", who="דנה כהן", limit=5)
    lines = r.display.split("\n")
    assert len(lines) == 3 and r.data["shown_to_owner"] is True
    assert lines[0].endswith("דנה כהן: שלום, מה שלומך?") and "09:01" in lines[0]
    assert lines[1].endswith("me: my reply") or lines[1].endswith("אני: my reply")
    assert lines[2].endswith("קנית חלב?")


async def test_search_display_is_newest_first(home):
    r = await run(skill(home, Stub(three())), "whatsapp_search", who="דנה כהן", since="2026-09-22")
    texts = [ln.split(": ", 1)[1] for ln in r.display.split("\n")]
    assert texts == ["קנית חלב?", "my reply", "שלום, מה שלומך?"]


async def test_display_cap_50_then_more_and_skip(home):
    ms = [msg(i, at(22, 8) + i * 60) for i in range(1, 61)]
    r = await run(skill(home, Stub(ms)), "whatsapp_search", who="דנה כהן", since="2026-09-22")
    lines = r.display.split("\n")
    assert len(lines) == 51 and "10 more" in lines[-1] and lines[0].endswith("msg 60")
    r2 = await run(skill(home, Stub(ms)), "whatsapp_search", who="דנה כהן", since="2026-09-22", skip=50)
    assert len(r2.display.split("\n")) == 10 and "more" not in r2.display


async def test_display_never_reaches_the_model(home):
    r = await run(skill(home, Stub(three())), "whatsapp_chat", who="דנה כהן", limit=5)
    out = json.loads(r.to_model())
    assert "display" not in out and "display" not in r.data


async def test_empty_result_has_no_display(home):
    r = await run(skill(home, Stub([])), "whatsapp_search", who="דנה כהן", since="2026-09-22")
    assert r.display == ""


async def test_registry_sends_display_to_the_sink_and_action_tools_stay_refused(home):
    got, calls = [], []

    class Sink:
        def __call__(self, name, ok, code):
            calls.append((name, ok, code))

        def display(self, text):
            got.append(text)
    sk = skill(home, Stub(three()))
    reg = SkillRegistry(allowed_actions=wab.ALLOWED_ACTIONS)
    reg.register(sk)
    TOOL_SINK.set(Sink())
    res = await reg.call("whatsapp_chat", {"who": "דנה כהן", "limit": 5})
    assert res.ok and len(got) == 1 and "קנית חלב?" in got[0]
    shown = await reg.call("whatsapp_show", {})              # same turn, model origin: still refused, not an error
    assert not shown.ok and shown.code == "TOOL_UNTRUSTED_TURN"
    assert len(got) == 1                                     # the display text opened nothing and printed nothing more


def test_ansi_prefix_is_not_reversed_with_hebrew():
    line = "\x1b[1;36mjarvis> \x1b[0mשלום עולם"
    out = console_rtl.render(line, 0, "visual")
    assert out.startswith("\x1b[1;36m") and out.endswith("\x1b[0m")
    assert "m[" not in out and "\x1b" not in out[len("\x1b[1;36m"):-len("\x1b[0m")]


def test_description_and_hint_tell_not_to_open_the_dashboard():
    d = {t.name: t.description for t in wab.WhatsAppBridgeSkill(__import__("pathlib").Path("."), client=Stub([])).tools()}
    assert "not after reading" in d["whatsapp_show"]
    assert "shown_to_owner" in wab.BACKEND_RULE
