"""WhatsApp assisted handoff (V3 P1, owner decision "WhatsApp v1: assisted handoff"): JARVES drafts the message, opens
the chat with the draft pre-filled through a `wa.me` link, and the OWNER presses send in WhatsApp.

JARVES never sends anything itself, never logs into WhatsApp, never reads chats (no unofficial API, no automation of
the WhatsApp client - nothing that risks the owner's account). The only effect on this PC is opening one link in the
default browser/WhatsApp, and only after the user approved the read-back (`whatsapp_open` is destructive with
impact None = asked from every origin).

wa.me format (https://faq.whatsapp.com/5913398998672934, checked 2026-10-20): https://wa.me/<number>?text=<urlencoded>
with the number in full international format, digits only (no +, 00, spaces or dashes).
Phone numbers the owner told JARVES are kept locally in <state_dir>/whatsapp/contacts.json.
"""
from __future__ import annotations

import re
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from pydantic import BaseModel, Field

from . import features as feat
from . import logs
from .channels_common import read_json, targets, write_private_json
from .skills import Skill, Tool, ToolResult

DEFAULT_COUNTRY = "972"          # Israel: 054-123-4567 -> 972541234567
DRAFT_TTL_S = 1800
MAX_TEXT = 1500                  # keeps the link well under browser URL limits


def contacts_file(home: Path) -> Path:
    return Path(home) / "whatsapp" / "contacts.json"


def normalize_phone(raw: str, country: str = DEFAULT_COUNTRY) -> str | None:
    """International digits-only number, or None when it cannot be a phone number."""
    s = raw.strip()
    if re.search(r"[^\d\s+()\-.]", s):
        return None
    plus = s.startswith("+")
    d = re.sub(r"\D", "", s)
    if d.startswith("00"):
        d = d[2:]
    elif not plus and d.startswith("0"):
        d = country + d[1:]
    return d if 8 <= len(d) <= 15 and not d.startswith("0") else None


def chat_link(phone: str, text: str) -> str:
    return f"https://wa.me/{phone}?text={quote(text, safe='')}"


class DraftArgs(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT, description="the message the user wants to send")
    name: str = Field("", max_length=60, description="who it is for, as the user said it")
    phone: str = Field("", max_length=30, description="the phone number if the user said it (saved for next time)")


class OpenArgs(BaseModel):
    draft_id: str = Field(..., min_length=1, max_length=40)


class WhatsAppSkill(Skill):
    name = "whatsapp"
    description = "draft a WhatsApp message and open the chat for the owner to press send (never sends by itself)"

    def __init__(self, home: Path, opener: Callable[[str], bool] | None = None, clock=time.monotonic,
                 country: str = DEFAULT_COUNTRY) -> None:
        self.home, self.opener, self.clock, self.country = Path(home), opener or webbrowser.open, clock, country
        self._drafts: dict[str, dict] = {}

    def _book(self) -> dict:
        return {k: v for k, v in read_json(contacts_file(self.home)).items() if isinstance(v, str)}

    def lookup(self, name: str) -> str | None:
        want = name.strip().casefold()
        return next((p for n, p in self._book().items() if n.casefold() == want), None) if want else None

    @staticmethod
    def readback(d: dict) -> str:
        who = d["name"] or d["phone"]
        return (f"לפתוח את הצ'אט עם {who} בוואטסאפ עם ההודעה \"{d['text']}\"? אני לא שולח: את/ה לוחצ/ת שלח. "
                f"/ Open the WhatsApp chat with {who} with this draft? I do not send it - you press send.")

    def tools(self) -> list[Tool]:
        async def draft(a: DraftArgs) -> ToolResult:
            phone = normalize_phone(a.phone, self.country) if a.phone.strip() else self.lookup(a.name)
            if a.phone.strip() and phone is None:
                return ToolResult(False, "המספר לא נראה תקין. מה המספר המלא, עם קידומת?", code="WHATSAPP_BAD_PHONE")
            if phone is None:
                return ToolResult(False, f"אין לי מספר של {a.name or 'האדם הזה'}. מה המספר?",
                                  code="WHATSAPP_PHONE_UNKNOWN")
            if a.phone.strip() and a.name.strip():            # remember what the user told us (local file only)
                book = self._book()
                book[a.name.strip()[:60]] = phone
                write_private_json(contacts_file(self.home), book)
            now = self.clock()
            self._drafts = {k: v for k, v in self._drafts.items() if now - v["t"] < DRAFT_TTL_S}
            did = uuid.uuid4().hex[:10]
            d = {"name": a.name.strip(), "phone": phone, "text": a.text.strip(), "t": now}
            self._drafts[did] = d
            return ToolResult(True, f"כתבתי טיוטה לוואטסאפ ל{d['name'] or d['phone']}: \"{d['text']}\". "
                                    "Next: whatsapp_open (its approval asks the user once).",
                              {"draft_id": did, "to": d["name"] or phone, "text": d["text"],
                               # CV-14 (campaign 20 Oct): asking here AND in the approval made the owner say yes
                               # twice; the approval itself is the one read-back question
                               "rule": "Do NOT ask the user yourself: call whatsapp_open with this draft_id right "
                                       "away in this same response. The system then reads the draft back and asks "
                                       "the user for ONE yes/no. You cannot send it: the owner presses send in "
                                       "WhatsApp."},
                              code="WHATSAPP_DRAFT_READY")

        async def open_impact(a: OpenArgs) -> tuple[int | None, str]:
            d = self._drafts.get(a.draft_id)
            return None, (self.readback(d) if d else "לפתוח את הצ'אט בוואטסאפ?")

        async def open_(a: OpenArgs) -> ToolResult:
            d = self._drafts.get(a.draft_id)
            if d is None or self.clock() - d["t"] >= DRAFT_TTL_S:
                self._drafts.pop(a.draft_id, None)
                return ToolResult(False, "אני פותח רק טיוטה שכתבתי עכשיו ושהקראתי לך.", code="WHATSAPP_UNKNOWN_DRAFT")
            try:
                ok = bool(self.opener(chat_link(d["phone"], d["text"])))
            except Exception:
                ok = False
            if not ok:
                return ToolResult(False, "לא הצלחתי לפתוח את וואטסאפ במחשב.", code="WHATSAPP_OPEN_FAILED")
            self._drafts.pop(a.draft_id, None)
            logs.info("whatsapp", "WHATSAPP_CHAT_OPENED", "WhatsApp chat opened with a draft; the owner presses send")
            return ToolResult(True, "פתחתי את הצ'אט עם הטיוטה. לוחצים שלח בוואטסאפ.", {"to": d["name"] or d["phone"]},
                              code="WHATSAPP_CHAT_OPENED")

        return [
            Tool("whatsapp_draft", "Prepare a WhatsApp message for someone (text + name or phone). Nothing is opened "
                 "or sent. Then call whatsapp_open at once: its approval reads the draft back to the user (one yes).",
                 draft, DraftArgs, permission="action"),
            Tool("whatsapp_open", "Open the WhatsApp chat with the drafted text pre-filled. The system first reads the "
                 "draft back and asks the user's approval (do not ask yourself). The user presses send; you cannot "
                 "send WhatsApp messages.", open_, OpenArgs,
                 permission="destructive", impact=open_impact),
        ]


ALLOWED_ACTIONS = frozenset({"whatsapp_draft"})


def register_whatsapp(registry, home: Path, opener=None) -> WhatsAppSkill:
    skill = WhatsAppSkill(home, opener)
    registry.register(skill)
    registry.allowed_actions |= ALLOWED_ACTIONS
    return skill


feat.register("whatsapp_handoff", "וואטסאפ (פתיחת צ'אט עם טיוטה)", "WhatsApp (open chat with a draft)", True,
              risk="medium", skills=["whatsapp"],
              about="drafts a WhatsApp message and opens the chat for you; you press send. He never sends or reads "
                    "WhatsApp himself",
              targets=targets("UNIT test_channels_whatsapp.py (link building, approval, fake opener)",
                              "never opened with the real WhatsApp app/Web"),
              permissions=["action", "destructive (always confirmed)"], settings="")
