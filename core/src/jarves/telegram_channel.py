"""Telegram outbound channel (V3 P1, Issue #17): JARVES sends a message to the owner's own Telegram chat.

- The OWNER creates the bot (BotFather) and gives JARVES the token: `jarvis telegram start` (guided, resumable) or the
  dashboard (POST /api/telegram/token). The token lives only in <state_dir>/secrets.env (TELEGRAM_BOT_TOKEN, user
  private); it is never logged, never printed and never put in an exception message.
- Recipients are an allow-list written by the wizard: the chats that messaged the bot while the owner was pairing
  (private chats only). JARVES cannot message anyone else.
- Sending is two steps, like Gmail: `telegram_draft` stores the text (nothing leaves the PC), `telegram_send` is a
  destructive tool whose impact is None, so the screen/voice confirmation is asked from EVERY origin with the exact
  text and recipient read back. A declined or unconfirmed send never calls Telegram.
- Official Bot API only (https://core.telegram.org/bots/api: getMe, getUpdates, sendMessage; checked 2026-10-20).
  Tests point JARVES_TELEGRAM_API_BASE at a local fake server (no mocks).
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from . import features as feat
from . import logs
from .channels_common import read_json, read_secret, targets, write_private_json, yes
from .config import save_secret
from .skills import Skill, Tool, ToolResult

API_BASE_ENV = "JARVES_TELEGRAM_API_BASE"
DEFAULT_API = "https://api.telegram.org"
TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
START_COMMAND = "jarvis telegram start"
MAX_TEXT = 3500                      # Telegram's limit is 4096 characters; leave room for the signature line
DRAFT_TTL_S = 1800
_TOKEN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{20,80}$")


class TelegramError(Exception):
    """Never contains the token or the request URL."""

    def __init__(self, code: str, message: str, he: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.he = code, message, he or message


def api_base() -> str:
    return (os.environ.get(API_BASE_ENV) or DEFAULT_API).rstrip("/")


def token_present() -> bool:
    return bool(os.environ.get(TOKEN_ENV, "").strip())


def pair_file(home: Path) -> Path:
    return Path(home) / "telegram" / "pair.json"


def pairing(home: Path) -> dict:
    d = read_json(pair_file(home))
    d["chats"] = [c for c in d.get("chats", []) if isinstance(c, dict) and isinstance(c.get("chat_id"), int)]
    return d


class TelegramApi:
    def __init__(self, token: str | None = None, *, base: str | None = None, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = (token if token is not None else os.environ.get(TOKEN_ENV, "")).strip()
        self.base, self.timeout, self.transport = (base or api_base()), timeout, transport

    async def call(self, method: str, **params) -> object:
        if not self._token:
            raise TelegramError("TELEGRAM_NO_TOKEN", "no Telegram bot token saved",
                                f"אין טוקן לבוט טלגרם. מריצים {START_COMMAND}.")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as c:
                r = await c.post(f"{self.base}/bot{self._token}/{method}", json=params)
        except httpx.HTTPError:                      # the message of an httpx error contains the URL (= the token)
            logs.warn("telegram", "TELEGRAM_UNREACHABLE", "Telegram could not be reached", method=method)
            raise TelegramError("TELEGRAM_UNREACHABLE", "Telegram could not be reached",
                                "לא הצלחתי להגיע לטלגרם. בדקו את החיבור לאינטרנט.") from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 200 and isinstance(data, dict) and data.get("ok"):
            return data.get("result")
        desc = str(data.get("description", "")) if isinstance(data, dict) else ""
        if r.status_code in (401, 404):
            raise TelegramError("TELEGRAM_BAD_TOKEN", "Telegram rejected the bot token",
                                "טלגרם לא מקבל את הטוקן. מעתיקים אותו מחדש מ-BotFather.")
        if r.status_code == 403:
            raise TelegramError("TELEGRAM_BLOCKED", "the chat blocked the bot or never started it",
                                "הצ'אט חסם את הבוט או שלא לחצו Start. פותחים את הבוט בטלגרם ושולחים לו הודעה.")
        if r.status_code == 429:
            raise TelegramError("TELEGRAM_RATE_LIMIT", "Telegram asks to slow down", "טלגרם מבקש להאט. ננסה עוד רגע.")
        logs.warn("telegram", "TELEGRAM_API_ERROR", f"Telegram answered {r.status_code}", method=method,
                  status=r.status_code)
        raise TelegramError("TELEGRAM_API_ERROR", f"Telegram answered {r.status_code}: {desc[:120]}",
                            "טלגרם החזיר שגיאה. נסו שוב מאוחר יותר.")

    async def get_me(self) -> dict:
        r = await self.call("getMe")
        return r if isinstance(r, dict) else {}

    async def send_message(self, chat_id: int, text: str) -> dict:
        r = await self.call("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)
        return r if isinstance(r, dict) else {}

    async def updates(self) -> list[dict]:
        r = await self.call("getUpdates", timeout=0, allowed_updates=["message"])
        return [u for u in r if isinstance(u, dict)] if isinstance(r, list) else []


def private_chats(updates: list[dict]) -> list[dict]:
    """Distinct PRIVATE chats that wrote to the bot (groups and channels are never paired), newest first."""
    seen, out = set(), []
    for u in reversed(updates):
        m = u.get("message") or {}
        chat = m.get("chat") or {}
        cid = chat.get("id")
        if chat.get("type") == "private" and isinstance(cid, int) and cid not in seen:
            seen.add(cid)
            frm = m.get("from") or {}
            out.append({"chat_id": cid, "name": str(frm.get("first_name") or chat.get("first_name") or cid)[:60]})
    return out


# ---------------------------------------------------------------------- the Skill
class DraftArgs(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT, description="the message text the user asked to send")
    to: str = Field("", max_length=60, description="a paired chat's name; empty = the owner's own chat")


class SendArgs(BaseModel):
    draft_id: str = Field(..., min_length=1, max_length=40, description="the id from telegram_draft")


class TelegramSkill(Skill):
    name = "telegram"
    description = "send a message to the owner's own Telegram chat (draft, read back, send only after approval)"

    def __init__(self, home: Path, transport: httpx.AsyncBaseTransport | None = None,
                 clock=time.monotonic) -> None:
        self.home, self.transport, self.clock = Path(home), transport, clock
        self._drafts: dict[str, dict] = {}

    def _api(self) -> TelegramApi:
        return TelegramApi(transport=self.transport)

    def _recipient(self, to: str) -> dict | None:
        chats = pairing(self.home)["chats"]
        if not chats:
            return None
        want = to.strip().casefold()
        if not want:
            return chats[0]
        return next((c for c in chats if c.get("name", "").casefold() == want), None)

    @staticmethod
    def readback(d: dict) -> str:
        return f"לשלוח בטלגרם ל{d['name']}: \"{d['text']}\"? / Send on Telegram to {d['name']}: \"{d['text']}\"?"

    async def health(self) -> tuple[str, str]:
        if not token_present():
            return "disabled", f"no bot token ({START_COMMAND})"
        if not pairing(self.home)["chats"]:
            return "degraded", f"no paired chat ({START_COMMAND})"
        return "ok", f"{len(pairing(self.home)['chats'])} paired chat(s)"

    def tools(self) -> list[Tool]:
        async def draft(a: DraftArgs) -> ToolResult:
            rec = self._recipient(a.to)
            if rec is None:
                code = "TELEGRAM_NOT_PAIRED" if not pairing(self.home)["chats"] else "TELEGRAM_UNKNOWN_CHAT"
                return ToolResult(False, f"אין צ'אט טלגרם מחובר. מריצים {START_COMMAND}." if code == "TELEGRAM_NOT_PAIRED"
                                  else "אני שולח רק לצ'אטים שחוברו בהגדרה.", code=code)
            now = self.clock()
            self._drafts = {k: v for k, v in self._drafts.items() if now - v["t"] < DRAFT_TTL_S}
            did = uuid.uuid4().hex[:10]
            d = {"chat_id": rec["chat_id"], "name": rec.get("name", ""), "text": a.text.strip(), "t": now}
            self._drafts[did] = d
            return ToolResult(True, "כתבתי טיוטה לטלגרם. " + self.readback(d),
                              {"draft_id": did, "to": d["name"], "text": d["text"],
                               "rule": "Read this draft back (recipient and text) and ask whether to send it. Only "
                                       "after a clear yes call telegram_send; the screen asks for approval too."},
                              code="TELEGRAM_DRAFT_READY")

        async def send_impact(a: SendArgs) -> tuple[int | None, str]:
            d = self._drafts.get(a.draft_id)
            return None, (self.readback(d) if d else "לשלוח את ההודעה בטלגרם?")      # None = ask from every origin

        async def send(a: SendArgs) -> ToolResult:
            d = self._drafts.get(a.draft_id)
            if d is None or self.clock() - d["t"] >= DRAFT_TTL_S:
                self._drafts.pop(a.draft_id, None)
                return ToolResult(False, "אני שולח רק טיוטה שכתבתי עכשיו ושהקראתי לך.", code="TELEGRAM_UNKNOWN_DRAFT")
            try:
                await self._api().send_message(d["chat_id"], d["text"])
            except TelegramError as e:
                return ToolResult(False, e.he, code=e.code)
            self._drafts.pop(a.draft_id, None)
            logs.info("telegram", "TELEGRAM_SENT", "Telegram message sent after the user's approval")
            return ToolResult(True, f"שלחתי לך בטלגרם.", {"to": d["name"]}, code="TELEGRAM_SENT")

        async def status(_a) -> ToolResult:
            st, detail = await self.health()
            return ToolResult(st == "ok", detail, {"status": st}, code="TELEGRAM_STATUS")

        from .skills import NoArgs
        return [
            Tool("telegram_status", "Is the Telegram channel set up (bot token saved, chat paired)?", status, NoArgs,
                 permission="read"),
            Tool("telegram_draft", "Write a message to the owner's Telegram as a DRAFT (nothing is sent). Use for "
                 "'תשלח לי הודעה בטלגרם'. Then read it back and ask before sending.", draft, DraftArgs,
                 permission="action"),
            Tool("telegram_send", "Send a draft made by telegram_draft, only after reading it back and the user said "
                 "yes. The user also approves on the screen.", send, SendArgs, permission="destructive",
                 timeout_s=20, impact=send_impact),
        ]


ALLOWED_ACTIONS = frozenset({"telegram_draft"})


def register_telegram(registry, home: Path, health=None, transport=None) -> TelegramSkill:
    skill = TelegramSkill(home, transport)
    registry.register(skill)
    registry.allowed_actions |= ALLOWED_ACTIONS
    if health is not None:
        if not token_present():
            health.set("telegram", "disabled", "Telegram not set up", START_COMMAND)
        else:
            health.set("telegram", "ok" if pairing(home)["chats"] else "degraded",
                       "set up" if pairing(home)["chats"] else "bot token saved, no chat paired", START_COMMAND)
    return skill


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    if not token_present():
        return False, f"no Telegram bot token; tell the user: \"תריץ במחשב {START_COMMAND}, ואני אדריך אותך\""
    if c.home is None or not pairing(c.home)["chats"]:
        return False, f"no Telegram chat paired; run {START_COMMAND}"
    return True, ""


feat.register("telegram", "הודעות בטלגרם", "Telegram messages", False, probe=_probe, needs=["telegram_bot"],
              risk="high", skills=["telegram"],
              about="send a message to your own Telegram chat, only after you approve the exact text (you create the "
                    "bot; the token stays on this computer)",
              targets=targets("UNIT test_channels_telegram.py (local fake Bot API server)",
                              "never sent through a real bot: needs the owner's BotFather token"),
              permissions=["action", "destructive (always confirmed)"], settings=START_COMMAND)


# ---------------------------------------------------------------------- the wizard (`jarvis telegram start`)
STEPS_HE = ("1. בטלגרם: מחפשים את @BotFather, שולחים לו /newbot, בוחרים שם וכתובת (חייבת להסתיים ב-bot).\n"
            "2. BotFather ישלח טוקן (מספר:אותיות). מעתיקים אותו.")
STEPS_EN = ("1. In Telegram: find @BotFather, send /newbot, pick a name and a username (must end in 'bot').\n"
            "2. BotFather replies with a token (digits:letters). Copy it.")


class Wizard:
    def __init__(self, home: Path, io, *, transport=None, polls: int = 6, poll_sleep: float = 2.0, sleep=asyncio.sleep,
                 add_chat: bool = False) -> None:
        self.home, self.io, self.transport = Path(home), io, transport
        self.polls, self.poll_sleep, self.sleep, self.add_chat = polls, poll_sleep, sleep, add_chat

    async def run(self) -> int:
        out = self.io.out
        out("JARVES + Telegram\n" + STEPS_HE + "\n" + STEPS_EN + "\n")
        api = TelegramApi(transport=self.transport) if token_present() else None
        bot = None
        if api is not None:
            try:
                bot = await api.get_me()
                out(f"הטוקן השמור תקין: @{bot.get('username', '?')}. / The saved token works: @{bot.get('username', '?')}.")
            except TelegramError as e:
                out(e.he + "\n" + e.message)
                api = None
        if api is None:
            tok = (read_secret(self.io, "Bot token (hidden, Enter = cancel): ") or "").strip()
            if not tok:
                out("בוטל. / Cancelled. Nothing was saved.")
                return 1
            if not _TOKEN.match(tok):
                out("זה לא נראה כמו טוקן של טלגרם (מספר:אותיות). / That does not look like a Telegram bot token.")
                return 1
            api = TelegramApi(tok, transport=self.transport)
            try:
                bot = await api.get_me()
            except TelegramError as e:
                out(e.he + "\n" + e.message)
                return 1
            save_secret(TOKEN_ENV, tok, self.home)
            logs.info("telegram", "TELEGRAM_TOKEN_SAVED", "Telegram bot token saved")
            out(f"נשמר (רק במחשב הזה): @{bot.get('username', '?')}. / Saved on this computer only.")
        have = pairing(self.home)
        if have["chats"] and not self.add_chat:
            out(f"כבר מחובר לצ'אט של {have['chats'][0].get('name')}. / Already paired. "
                f"(add another chat: {START_COMMAND} --add-chat)")
            return 0
        out(f"עכשיו פותחים בטלגרם את @{bot.get('username', '')} ושולחים לו הודעה כלשהי (למשל hi). "
            f"/ Now open @{bot.get('username', '')} in Telegram and send it any message (e.g. hi).")
        self.io.read("Enter when sent: ")
        chosen = None
        for i in range(self.polls):
            try:
                found = private_chats(await api.updates())
            except TelegramError as e:
                out(e.he + "\n" + e.message)
                return 1
            known = {c["chat_id"] for c in have["chats"]}
            found = [c for c in found if c["chat_id"] not in known]
            if found:
                chosen = found[0]
                break
            if i + 1 < self.polls:
                await self.sleep(self.poll_sleep)
        if chosen is None:
            out("לא קיבלתי הודעה. שולחים הודעה לבוט ומריצים שוב. / No message arrived: send one to the bot and run again.")
            return 1
        ok = yes(self.io.read(f"Is this you: {chosen['name']}? [y/N] "))
        if not ok:
            out("לא חיברתי את הצ'אט. / Chat not paired.")
            return 1
        data = {"bot_username": bot.get("username", ""), "chats": have["chats"] + [chosen], "paired_at": time.time()}
        write_private_json(pair_file(self.home), data)
        logs.info("telegram", "TELEGRAM_PAIRED", "Telegram chat paired")
        if yes(self.io.read("Send a test message now? [y/N] ")):
            try:
                await api.send_message(chosen["chat_id"], "JARVES: Telegram is connected. / טלגרם מחובר.")
                out("נשלחה הודעת בדיקה. / Test message sent.")
            except TelegramError as e:
                out(e.he + "\n" + e.message)
                return 1
        out("אחרי שמפעילים את המתג:  jarvis features on telegram  (כבוי כברירת מחדל). / Then switch it on: "
            "jarvis features on telegram (off by default).")
        out("אפשר לומר: \"תשלח לי בטלגרם ...\". הוא תמיד יקריא ויבקש אישור לפני שליחה.")
        return 0


def status(home: Path) -> dict:
    p = pairing(home)
    return {"token_saved": token_present(), "bot": p.get("bot_username", ""), "chats": [c.get("name") for c in p["chats"]]}


def add_cli(sub) -> None:
    t = sub.add_parser("telegram", help="Telegram outbound channel: `jarvis telegram start` guides you (you create "
                                        "the bot, JARVES sends only after you approve)")
    t.add_argument("action", choices=["start", "status", "test", "logout"])
    t.add_argument("--add-chat", action="store_true", help="start: pair one more private chat")


def cmd_telegram(a, cfg, home) -> int:
    import json

    from .setup_wizard import ConsoleIO
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    if a.action == "start":
        return asyncio.run(Wizard(home, ConsoleIO(), add_chat=bool(a.add_chat)).run())
    if a.action == "status":
        st = status(home)
        print(json.dumps(st, ensure_ascii=False))
        return 0 if st["token_saved"] and st["chats"] else 1
    if a.action == "logout":
        pair_file(home).unlink(missing_ok=True)
        from .config import secrets_file
        p = secrets_file(home)
        if p.exists():
            keep = [l for l in p.read_text(encoding="utf-8-sig").splitlines() if l.partition("=")[0].strip() != TOKEN_ENV]
            p.write_text("\n".join(keep) + "\n", encoding="utf-8")
        os.environ.pop(TOKEN_ENV, None)
        print("Telegram disconnected (token and paired chats removed from this computer). "
              "To revoke the bot itself: BotFather > /revoke.")
        return 0

    async def go() -> int:                              # test: a read-only check (getMe), nothing is sent
        try:
            bot = await TelegramApi().get_me()
        except TelegramError as e:
            print(f"{e.code}: {e.message}")
            return 1
        print(f"ok: @{bot.get('username', '?')}")
        return 0
    return asyncio.run(go())


# ---------------------------------------------------------------------- dashboard
def dashboard_routes(register) -> None:
    def get(ctx, _m, _b):
        return 200, status(ctx.home)

    def set_token(ctx, _m, body):
        tok = str((body or {}).get("token", "")).strip()
        if not _TOKEN.match(tok):
            return 400, {"error": "that does not look like a Telegram bot token (digits:letters)"}
        try:
            bot = asyncio.run(TelegramApi(tok).get_me())
        except TelegramError as e:
            return 400, {"error": e.message, "code": e.code}
        save_secret(TOKEN_ENV, tok, ctx.home)
        logs.info("telegram", "TELEGRAM_TOKEN_SAVED", "Telegram bot token saved from the dashboard")
        return 200, {**status(ctx.home), "bot": bot.get("username", ""), "next": START_COMMAND + " (pair your chat)"}
    register("GET", r"/api/telegram", get)
    register("POST", r"/api/telegram/token", set_token)
