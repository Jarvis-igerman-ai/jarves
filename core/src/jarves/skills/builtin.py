"""Built-in offline Skills: clock, memory, status."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from pydantic import BaseModel, Field

from ..memory import MemoryStore
from . import Skill, Tool, ToolResult

FORGET_LIMIT = 50          # most memories one `forget` by text may delete
HE_DAYS =["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


class ClockSkill(Skill):
    name = "clock"
    description = "Local date and time"

    def tools(self) -> list[Tool]:
        async def now(_):
            t = datetime.now().astimezone()
            return ToolResult(True, f"השעה {t:%H:%M}, יום {HE_DAYS[t.weekday()]} {t:%d.%m.%Y}",
                              {"iso": t.isoformat(timespec="minutes")})
        return [Tool("get_time", "Current local date and time.", now, permission="read")]


class CalcArgs(BaseModel):
    expression: str = Field(..., min_length=1, max_length=200,
                            description="arithmetic in digits and + - * / ** % ( ), sqrt(x), e.g. '347*18' or 'sqrt(81)'")


def calculate(expression: str) -> float | int:
    """Exact arithmetic on a safe AST subset (no names except sqrt/abs/round, no attribute access, small powers)."""
    import ast
    import math
    import operator as op
    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.FloorDiv: op.floordiv,
           ast.Mod: op.mod, ast.Pow: op.pow}
    funcs = {"sqrt": math.sqrt, "abs": abs, "round": round}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 64 or abs(a) > 1e6):
                raise ValueError("power too large")
            return ops[type(n.op)](a, b)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs and not n.keywords
                and 1 <= len(n.args) <= 2):
            return funcs[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("unsupported expression")
    expr = expression.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    v = ev(ast.parse(expr, mode="eval"))
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    return round(v, 10) if isinstance(v, float) else v


class CalcSkill(Skill):
    """CV-08 (voice campaign 20 Oct): GPT-Live delegates every sum above ten, and the backend answered 4/4 maths
    questions with a WEB SEARCH (screen 'searching', ~$0.01 each). An exact local calculator is faster and free."""
    name = "calc"
    description = "Exact arithmetic"

    def tools(self) -> list[Tool]:
        async def calc(a: CalcArgs):
            try:
                v = calculate(a.expression)
            except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError) as e:
                return ToolResult(False, f"לא הצלחתי לחשב את זה ({type(e).__name__}).", code="CALC_FAILED")
            return ToolResult(True, f"{a.expression} = {v}", {"result": v})
        return [Tool("calculate", "Exact arithmetic (use it for ANY calculation instead of web search or mental "
                     "maths): + - * / ** % ( ) and sqrt().", calc, CalcArgs, permission="read")]


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
            mid, _ = self.store.upsert(a.text, source="user", confidence=1.0)   # no duplicates
            return ToolResult(True, "זכרתי.", {"id": mid})

        async def recall(a: RecallArgs):
            rows = self.store.search(a.query) if a.query else self.store.list(20)
            if not rows:
                return ToolResult(True, "אין לי זיכרונות שמורים.", {"items": []})
            return ToolResult(True, "; ".join(r["text"] for r in rows),
                              {"items": [{"id": r["id"], "text": r["text"], "source": r.get("source", "user"),
                                          "learned": r["created"][:10]} for r in rows]})

        async def forget(a: ForgetArgs):
            if a.id is not None:
                ok = self.store.delete(a.id)
                return ToolResult(ok, "מחקתי." if ok else "לא מצאתי את הזיכרון הזה.", code="ok" if ok else "NOT_FOUND")
            if a.match:
                rows = self.store.search(a.match, FORGET_LIMIT)
                for r in rows:
                    self.store.delete(r["id"])
                return ToolResult(bool(rows), f"מחקתי {len(rows)} זיכרונות." if rows else "לא מצאתי זיכרון כזה.",
                                  {"deleted": len(rows)}, "ok" if rows else "NOT_FOUND")
            return ToolResult(False, "מה למחוק?", code="TOOL_BAD_ARGS")

        async def forget_impact(a: ForgetArgs) -> tuple[int | None, str]:
            if a.id is not None:
                return 1, f"למחוק את זיכרון מספר {a.id}? / Delete memory #{a.id}?"
            if a.match:
                n = len(self.store.search(a.match, FORGET_LIMIT))
                return n, f"למחוק {n} זיכרונות התואמים ל\"{a.match}\"? / Delete {n} matching memories?"
            return 0, ""

        async def forget_all(_):
            n = self.store.clear()
            return ToolResult(True, f"מחקתי את כל הזיכרונות ({n}).", {"deleted": n})

        async def forget_all_impact(_) -> tuple[int | None, str]:
            return None, f"למחוק את כל הזיכרונות ({self.store.count()})? / Delete all memories?"

        return [
            Tool("remember", "Store a fact ONLY when the user explicitly asks you to remember it.", remember,
                 RememberArgs, permission="action"),
            Tool("recall", "List or search what the user asked you to remember.", recall, RecallArgs,
                 permission="read"),
            Tool("forget", "Delete one memory by id or by matching text (asks the user to confirm).", forget,
                 ForgetArgs, permission="destructive", impact=forget_impact),
            Tool("forget_all", "Delete ALL stored memories (asks the user to confirm).", forget_all,
                 permission="destructive", impact=forget_all_impact,
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
                     status, permission="read")]
