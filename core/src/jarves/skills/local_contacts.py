"""Explicitly remembered contact relationships, stored with ordinary deletable local memories."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator

from ..memory import MemoryStore
from . import Skill, Tool, ToolResult

_EMAIL = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[A-Za-z]{2,}$")
KIND = "contact"


class ContactArgs(BaseModel):
    label: str = Field(..., min_length=1, max_length=80, description="User's relationship or alias, e.g. my wife")
    email: str = Field(..., min_length=3, max_length=254)
    name: str = Field("", max_length=80)

    @field_validator("label", "name")
    @classmethod
    def plain(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Use a single line")
        return value.strip()

    @field_validator("label")
    @classmethod
    def nonempty(cls, value):
        if not value:
            raise ValueError("A relationship or alias is required")
        return value

    @field_validator("email")
    @classmethod
    def address(cls, value):
        value = value.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or not _EMAIL.fullmatch(value):
            raise ValueError("Use one plain email address")
        return value


class LookupArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=120)

    @field_validator("query")
    @classmethod
    def query_text(cls, value):
        if not value.strip():
            raise ValueError("A name or relationship is required")
        return value.strip()


class LocalContactsSkill(Skill):
    name = "contacts"
    description = "Explicitly remembered local relationships and email addresses"

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    def records(self, query: str) -> list[dict]:
        out = []
        # Search only explicit contact memories, independent of the prompt's recent-fact limit.
        for row in self.memory.by_kind(KIND):
            try:
                a = ContactArgs.model_validate(json.loads(row["text"]))
            except (ValueError, TypeError):
                continue
            if query.casefold() in " ".join((a.label, a.name, a.email)).casefold():
                out.append({"memory_id": row["id"], **a.model_dump()})
        return out

    def tools(self):
        async def remember(a: ContactArgs):
            rows = self.records(a.label)
            same = [r for r in rows if r["label"].casefold() == a.label.casefold()]
            if same:
                if len(same) == 1 and same[0]["email"].casefold() == a.email.casefold() and same[0]["name"] == a.name:
                    return ToolResult(True, "Contact already remembered.", {"contact": same[0]})
                return ToolResult(False, "That relationship already has saved contact details. Ask the user which "
                                  "to keep; use the existing forget tool before replacing them.",
                                  {"candidates": same}, "CONTACT_CONFLICT")
            text = json.dumps(a.model_dump(), ensure_ascii=False, separators=(",", ":"))
            if len(text) > 500:
                return ToolResult(False, "Use a shorter contact label or name.", code="TOOL_BAD_ARGS")
            mid = self.memory.add(text, source="user", confidence=1.0, kind=KIND)
            return ToolResult(True, "Contact remembered locally.", {"contact": {"memory_id": mid, **a.model_dump()}})

        async def lookup(a: LookupArgs):
            rows = self.records(a.query.strip())
            return ToolResult(True, "Choose the intended person/address if there is more than one match." if len(rows) > 1
                              else "Contact found." if rows else "No saved contact. Ask the user, or search Google "
                              "Contacts by a known name if enabled; never guess a relationship.",
                              {"contacts": rows[:20], "ambiguous": len(rows) > 1, "truncated": len(rows) > 20})

        return [Tool("contact_remember", "Save a relationship/name and email ONLY when the user explicitly asks. "
                     "Never infer who is the user's wife from a contact name. Does not edit Google Contacts.",
                     remember, ContactArgs, permission="action"),
                Tool("contact_lookup", "Find a locally remembered relationship/name/email before addressing mail "
                     "or inviting guests. Multiple results require the user's choice. Older unstructured facts "
                     "can be checked with recall. No automatic sending.", lookup, LookupArgs, permission="read")]


# Specific remember forms must precede the general remember intent.
INTENT_RULES = [
    ("contact_remember", re.compile(r"^remember(?:\s+that)?\s+(?P<label>.+?)(?:'s)?\s+email(?:\s+address)?\s+is\s+"
                                    r"(?P<email>[^\s]+@[^\s]+)$", re.I),
     lambda m: {"label": m["label"].strip(), "email": m["email"]}),
    ("contact_remember", re.compile(r"^(?:תזכור|זכור)\s+(?:ש)?(?:האימייל|המייל)\s+של\s+(?P<label>.+?)\s+(?:הוא\s+)?"
                                    r"(?P<email>[^\s]+@[^\s]+)$"),
     lambda m: {"label": m["label"].strip(), "email": m["email"]}),
]
