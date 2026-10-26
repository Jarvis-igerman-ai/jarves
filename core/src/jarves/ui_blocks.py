"""Smart chat UI blocks, schema v1 (S1 of docs/plans/SMART_CHAT_AND_TASK_TRIAGE_DESIGN_2026-10-26.md, #444).

Owner-facing output only: a block travels on the `ui_block` channel next to `display` (#363/#366) and is never part of
ToolResult.to_model(). The server validates and sanitizes it before anything is sent; the model never picks ids that
mean something (item ids are i1, i2, ... bound to the block id). All strings are plain text: no markup, no links.
S1 implements `text` and `checklist` only; any other kind is dropped (UI_BLOCK_INVALID, counts only in the log).
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import logs
from .skills import Skill, Tool, ToolResult

SCHEMA_VERSION = 1
KINDS = ("text", "checklist")                 # S1; cards / table / choices / form arrive in later slices
MAX_BLOCKS_PER_MESSAGE = 4
MAX_BYTES = 16 * 1024
# offered to the model only when the owner's words ask for a list (keeps the always-on prompt small, like lazy_tools)
LAZY = r"check-?list|tick|to-?do list|list of|רשימ|סמן|סימון|תיבות"
# control characters and bidi overrides (U+202A-202E, U+2066-2069); zero-width joiners stay for Hebrew
_BAD = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f\\u202a-\\u202e\\u2066-\\u2069]")


def clean(s: str) -> str:
    """Plain text only: control and bidi-override characters removed, trimmed."""
    return _BAD.sub("", str(s)).strip()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Item(_Strict):
    label: str = Field(..., min_length=1, max_length=120)
    detail: str | None = Field(None, max_length=200)
    checked: bool = False


class Action(_Strict):
    label: str = Field(..., min_length=1, max_length=40)


class Block(_Strict):
    v: Literal[1] = 1
    kind: Literal["text", "checklist"]
    title: str = Field("", max_length=80)
    dir: Literal["auto", "rtl", "ltr"] = "auto"
    text: str | None = Field(None, max_length=1000)
    items: list[Item] = Field(default_factory=list, max_length=30)
    actions: list[Action] = Field(default_factory=list, max_length=4)


def validate_block(raw: Any) -> dict:
    """One raw block -> the sanitized block with server-assigned ids. Raises ValueError (a code, never the content)."""
    if not isinstance(raw, dict):
        raise ValueError("UI_BLOCK_INVALID")
    if raw.get("v", SCHEMA_VERSION) != SCHEMA_VERSION or raw.get("kind") not in KINDS:
        raise ValueError("UI_BLOCK_UNKNOWN")
    try:
        b = Block.model_validate(raw)
    except ValidationError:
        raise ValueError("UI_BLOCK_INVALID") from None
    if b.kind == "text" and (not clean(b.text or "") or b.items or b.actions):
        raise ValueError("UI_BLOCK_INVALID")
    if b.kind == "checklist" and (not b.items or b.text is not None):
        raise ValueError("UI_BLOCK_INVALID")
    out: dict = {"v": 1, "id": "blk_" + secrets.token_hex(4), "kind": b.kind, "title": clean(b.title), "dir": b.dir}
    if b.kind == "text":
        out["text"] = clean(b.text or "")
    else:
        out["items"] = [{"id": f"i{n}", "label": clean(i.label), "detail": clean(i.detail or ""), "checked": i.checked}
                        for n, i in enumerate(b.items, 1)]
        out["actions"] = [{"id": f"a{n}", "label": clean(a.label)} for n, a in enumerate(b.actions, 1)]
    if len(json.dumps(out, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("UI_BLOCK_TOO_BIG")
    return out


def validate_blocks(raws: Any) -> list[dict]:
    """A message's blocks: invalid ones are dropped (counted in the log, never their content); at most 4 are kept."""
    if not isinstance(raws, list):
        raws = [raws]
    good, dropped = [], 0
    for r in raws:
        if len(good) >= MAX_BLOCKS_PER_MESSAGE:
            dropped += 1
            continue
        try:
            good.append(validate_block(r))
        except ValueError:
            dropped += 1
    if dropped:
        logs.warn("ui_blocks", "UI_BLOCK_INVALID", "ui blocks dropped", rejected=dropped, shown=len(good))
    for b in good:
        logs.info("ui_blocks", "UI_BLOCK_SHOWN", "ui block shown", kind=b["kind"], n=len(b.get("items", [])))
    return good


def numbered(block: dict) -> str:
    """Plain numbered list for the terminal (and any client without a renderer)."""
    head = [block["title"]] if block.get("title") else []
    if block.get("kind") == "text":
        return "\n".join(head + [str(block.get("text", ""))])
    return "\n".join(head + [f"{n}) {'[x] ' if i.get('checked') else ''}{i.get('label', '')}"
                             + (f" - {i['detail']}" if i.get("detail") else "")
                             for n, i in enumerate(block.get("items", []), 1)])


class UiShowArgs(BaseModel):
    blocks: list[dict] = Field(..., min_length=1, max_length=MAX_BLOCKS_PER_MESSAGE,
                               description="v=1 blocks: {kind:'checklist',title?,items:[{label,detail?,checked?}]} or {kind:'text',text}")


class UiBlocksSkill(Skill):
    name = "ui_blocks"
    description = "Show the owner a small checklist or text block in the chat"

    def tools(self) -> list[Tool]:
        async def ui_show(a: UiShowArgs) -> ToolResult:
            good = validate_blocks(a.blocks)
            if not good:
                return ToolResult(False, "UI_BLOCK_INVALID: no block was valid; answer in plain text instead.",
                                  code="UI_BLOCK_INVALID")
            return ToolResult(True, "Shown to the owner. Wait for the owner's reply.", {"shown_to_owner": True},
                              ui_blocks=good)
        return [Tool("ui_show", "Show a checklist in chat: 'show a checklist', 'תציג רשימה לסימון'.", ui_show, UiShowArgs,
                     permission="read", lazy=LAZY)]
