"""Built-in offline Skills: clock, memory, status."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from pydantic import BaseModel, Field

from ..memory import MemoryStore
from . import Skill, Tool, ToolResult

HE_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


class ClockSkill(Skill):
    name = "clock"
    description = "Local date and time"

    def tools(self) -> list[Tool]:
        async def now(_):
            t = datetime.now().astimezone()
            return ToolResult(True, f"השעה {t:%H:%M}, יום {HE_DAYS[t.weekday()]} {t:%d.%m.%Y}",
                              {"iso": t.isoformat(timespec="minutes")})
        return [Tool("get_time", "Current local date and time.", now)]


class RememberArgs(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, description="The fact the user explicitly asked to remember")


class ForgetArgs(BaseModel):
    id: int | None = Field(None, description="Memory id to delete")
    match: str | None = Field(None, description="Delete the memories containing this text")


class RecallArgs(BaseModel):
    query: str | None = Field(None, description="Optional text to search for")


class MemorySkill(Skill):
    name = "memory"
    description = "User-controlled long-term memory"

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def tools(self) -> list[Tool]:
        async def remember(a: RememberArgs):
            mid = self.store.add(a.text)
            return ToolResult(True, "זכרתי.", {"id": mid})

        async def recall(a: RecallArgs):
            rows = self.store.search(a.query) if a.query else self.store.list(20)
            if not rows:
                return ToolResult(True, "אין לי זיכרונות שמורים.", {"items": []})
            return ToolResult(True, "; ".join(r["text"] for r in rows),
                              {"items": [{"id": r["id"], "text": r["text"]} for r in rows]})

        async def forget(a: ForgetArgs):
            if a.id is not None:
                ok = self.store.delete(a.id)
                return ToolResult(ok, "מחקתי." if ok else "לא מצאתי את הזיכרון הזה.", code="ok" if ok else "NOT_FOUND")
            if a.match:
                rows = self.store.search(a.match, 50)
                for r in rows:
                    self.store.delete(r["id"])
                return ToolResult(bool(rows), f"מחקתי {len(rows)} זיכרונות." if rows else "לא מצאתי זיכרון כזה.",
                                  {"deleted": len(rows)}, "ok" if rows else "NOT_FOUND")
            return ToolResult(False, "מה למחוק?", code="TOOL_BAD_ARGS")

        async def forget_all(_):
            n = self.store.clear()
            return ToolResult(True, f"מחקתי את כל הזיכרונות ({n}).", {"deleted": n})

        return [
            Tool("remember", "Store a fact ONLY when the user explicitly asks you to remember it.", remember,
                 RememberArgs, side_effect=True),
            Tool("recall", "List or search what the user asked you to remember.", recall, RecallArgs),
            Tool("forget", "Delete one memory by id or by matching text.", forget, ForgetArgs, side_effect=True),
            Tool("forget_all", "Delete ALL stored memories.", forget_all, side_effect=True,
                 confirm="למחוק את כל הזיכרונות? / Delete all memories?"),
        ]


class StatusSkill(Skill):
    name = "status"
    description = "JARVES self-diagnostics"

    def __init__(self, snapshot: Callable[[], dict]) -> None:
        self.snapshot = snapshot

    def tools(self) -> list[Tool]:
        async def status(_):
            s = self.snapshot()
            bad = {k: v for k, v in s["components"].items() if v["status"] in ("unavailable", "degraded")}
            if not bad:
                return ToolResult(True, "כל המערכות תקינות.", {"core": s["core"]})
            parts = [f"{k}: {v['detail'] or v['status']}" for k, v in bad.items()]
            return ToolResult(True, "בעיות: " + "; ".join(parts), {"core": s["core"], "problems": bad})
        return [Tool("get_status", "Report JARVES component health (what works, what is broken, what to try).",
                     status)]
