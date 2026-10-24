"""Pre-drive briefing (V6 P5, issue #211): next calendar event, weather, a message count, built on briefing.py.

Opt-in (feature `drive_briefing`, OFF by default); nothing runs unless the owner asks ("brief me before I drive").
Passenger privacy (plan G4): the owner's setting `drive_briefing.passenger` is `ask` (default), `present` or `alone`;
a call may override it for that call only.
- ask      nothing is read: JARVES asks "is anyone in the car with you?" first and the owner answers (ask-first).
- present  only non-sensitive items: minutes to the next event (no title, no place), the weather. No mail or message
           counts, no senders, no subjects, no event titles.
- alone    next event with its title and time, weather, count of important unread mail (never senders or subjects).
No location: the weather place is the existing settings briefing.city/lat/lon, never a device position. No network
except the Open-Meteo weather call already used by the morning briefing. Calendar/mail text is third-party text:
the tool is `untrusted_output` (no side-effect tool in the same turn). Sections fail independently.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from . import features as feat
from . import logs
from .briefing import BriefingSkill, _sec
from .channels_common import targets
from .skills import Skill, Tool, ToolResult

MODES = ("ask", "present", "alone")
SECTION_TIMEOUT_S = 14.0


class DriveBriefingArgs(BaseModel):
    passenger: Literal["ask", "present", "alone"] | None = None   # None = use the setting (default ask)


def passenger_filter(sections: list[dict], mode: str) -> list[dict]:
    """Pure privacy filter over built sections. Unknown modes behave like `ask` (nothing passes)."""
    if mode == "alone":
        return [dict(s) for s in sections]
    if mode == "present":
        out = []
        for s in sections:
            if s["id"] == "weather":
                out.append(dict(s))
            elif s["id"] == "calendar" and s.get("ok") and s.get("minutes") is not None:
                out.append(_sec("calendar", True, f"האירוע הבא בעוד {s['minutes']} דקות.", minutes=s["minutes"]))
        return out
    return []


class DriveBriefingSkill(Skill):
    name = "drive_briefing"
    description = "pre-drive briefing: next event, weather, unread message count; asks first if a passenger may hear"

    def __init__(self, registry, db, *, base: BriefingSkill | None = None, transport=None, clock=None) -> None:
        self.registry, self.db = registry, db
        self.base = base or BriefingSkill(registry, db, transport=transport, clock=clock)

    def mode(self, override: str | None = None) -> str:
        m = override or self.db.setting("drive_briefing.passenger")
        return m if m in MODES else "ask"

    async def _calendar(self) -> dict:
        r = await self.base._tool("calendar_next_event", {})
        if r is None:
            return self.base._google_missing("calendar", "היומן")
        if not r.ok:
            return _sec("calendar", False, "היומן לא זמין כרגע: " + r.message, code=r.code)
        evs = r.data.get("untrusted_events") or []
        if not evs:
            return _sec("calendar", True, "אין אירוע קרוב ביומן.", minutes=None)
        e = evs[0]                                   # title and time only: the place is never read
        mins = r.data.get("minutes_from_now")
        return _sec("calendar", True, f"האירוע הבא: {e.get('title', '')}, {e.get('when', '')}.",
                    minutes=mins, untrusted_title=e.get("title", ""))

    async def _messages(self) -> dict:
        s = await self.base.email()                  # count only: senders and subjects are dropped here
        if not s["ok"]:
            return _sec("messages", False, s["text"], code=s.get("code", ""))
        n = s.get("count", 0)
        return _sec("messages", True, "אין הודעות חשובות שלא נקראו." if not n else
                    f"{n} הודעות חשובות שלא נקראו. לא מקריא את התוכן.", count=n)

    def tools(self) -> list[Tool]:
        async def get(a: DriveBriefingArgs) -> ToolResult:
            mode = self.mode(a.passenger)
            if mode == "ask":
                return ToolResult(True, "לפני שאני מקריא: יש מישהו ברכב איתך? אם אתה לבד, אמור 'אני לבד'.",
                                  {"sections": [], "mode": "ask", "rule": "Ask the owner if a passenger is present. Read "
                                   "nothing until they answer. Then call again with passenger=alone or present."},
                                  code="DRIVE_BRIEFING_ASK")

            async def one(sid: str, fn) -> dict:
                try:
                    return await asyncio.wait_for(fn(), SECTION_TIMEOUT_S)
                except Exception:
                    logs.error("drive_briefing", "DRIVE_BRIEFING_SECTION_FAILED", "section failed", section=sid, exc=True)
                    return _sec(sid, False, "החלק הזה לא זמין כרגע.", code="SECTION_FAILED")
            wanted = [("calendar", self._calendar), ("weather", self.base.weather)]
            if mode == "alone":
                wanted.append(("messages", self._messages))
            secs = await asyncio.gather(*(one(i, f) for i, f in wanted))
            shown = passenger_filter(list(secs), mode)
            if mode == "present":                    # a passenger can hear: no event title, place, mail or messages
                shown = [s for s in shown if s["ok"]]
            if not any(s["ok"] for s in shown):
                return ToolResult(False, " ".join(s["text"] for s in secs) or "אין תדריך זמין.", {"sections": [],
                                  "mode": mode}, code="DRIVE_BRIEFING_EMPTY")
            return ToolResult(True, " ".join(s["text"] for s in shown), {
                "sections": shown, "mode": mode, "location_used": False, "partial": not all(s["ok"] for s in shown) or len(shown) < len(secs),
                "rule": "Say it briefly, in order. Never add mail senders, subjects, message text or places."},
                code="DRIVE_BRIEFING_OK")

        return [Tool("drive_briefing_get", "Pre-drive briefing: next event, weather, unread message count ('brief me before I drive', 'תדריך לפני נסיעה'). Asks first if a passenger is present. Event text is third-party data.", get,
                     DriveBriefingArgs, permission="read", timeout_s=40, untrusted_output=True)]


def register_drive_briefing(registry, db, transport=None, clock=None) -> DriveBriefingSkill:
    skill = DriveBriefingSkill(registry, db, transport=transport, clock=clock)
    registry.register(skill)
    return skill


feat.register("drive_briefing", "תדריך לפני נסיעה", "Pre-drive briefing", False, skills=["drive_briefing"],
              about="next calendar event, weather and an unread message count before you drive; asks first if a "
                    "passenger can hear, never reads senders or subjects, never uses your location",
              targets=targets("UNIT test_v6_drive_briefing.py (fake registry tools, local fake weather server)",
                              "V6 beta: not run in a real car or against a real Google account"),
              permissions=["read"], settings="drive_briefing.passenger (ask | present | alone), briefing.city")
