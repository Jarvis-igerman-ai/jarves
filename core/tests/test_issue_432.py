"""#432: a recap after the tool ran in the previous user turn is no false action claim; a deleted note is not re-created."""
from __future__ import annotations

import asyncio
import time

import pytest

from jarves import notes as N
from jarves.userdata import UserDB
from jarves.skills import ToolResult
from test_swv3_p01 import _cleanup, make_live


async def _recap(user_text: str, verb_text: str, age: float):
    lv, sent = make_live()
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "תעדכן את האירוע ליום חמישי"})
    await lv.handle_event({"type": "session.output_transcript.delta", "delta": "בסדר."})
    lv._flush_transcripts()
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": user_text})
    now = time.monotonic()
    lv._prev_turn_t0, lv._turn_t0 = now - 12, now - 1
    lv._action_times = [now - age]
    await lv.handle_event({"type": "session.output_transcript.delta", "delta": verb_text})
    lv._flush_transcripts()
    await asyncio.sleep(0.5)
    return lv, sent


async def test_recap_of_previous_turn_action_is_not_a_false_claim():
    lv, sent = await _recap("אוקיי תודה", "עדכנתי את האירוע.", 8.0)
    assert "false_action_claims" not in lv._stats
    assert not [s for s in sent.items if s[0] == "commentary"]
    await _cleanup(lv)


async def test_claim_without_any_recent_action_is_still_flagged():
    lv, _ = await _recap("אוקיי תודה", "עדכנתי את האירוע.", 60.0)
    assert lv._stats["false_action_claims"] == 1
    await _cleanup(lv)


async def test_new_action_request_without_a_tool_is_still_flagged():
    lv, _ = await _recap("תמחק את האירוע", "מחקתי את האירוע.", 8.0)
    assert lv._stats["false_action_claims"] == 1
    await _cleanup(lv)


async def test_unasked_memory_claim_is_still_flagged_after_a_previous_action():
    lv, _ = await _recap("לאחותי קוראים נועה", "רשמתי שלאחותך קוראים נועה.", 8.0)
    assert lv._stats["false_action_claims"] == 1
    await _cleanup(lv)


def test_note_is_not_recreated_right_after_its_delete(tmp_path):
    db = UserDB(tmp_path / "memory.db")
    t = [1000.0]
    notes = N.Notes(db, clock=lambda: t[0])
    n = notes.add("קניות", "חלב")
    skill = N.make_skill(notes)
    tools = {x.name: x for x in skill.tools()}
    assert notes.delete(n["id"], by="voice")
    res = asyncio.run(tools["note_add"].handler(N.AddArgs(title="קניות", text="חלב")))
    assert not res.ok and res.code == "NOTE_JUST_DELETED" and not notes.search("קניות")
    again = asyncio.run(tools["note_add"].handler(N.AddArgs(title="קניות", text="חלב")))   # a deliberate repeat goes through
    assert again.ok and notes.search("קניות")
    other = asyncio.run(tools["note_add"].handler(N.AddArgs(title="רעיון", text="x")))
    assert other.ok
    db.close()
