"""The #49 message envelope, lifecycle states, explicit result codes and the untrusted-text rules.

Every message between JARVES and a coding agent (hook event, channel push, reply, approval request, command) is an
Envelope: schema_version, message_id (idempotency), correlation_id, request_id, provider / session / project ids,
kind, timestamp, expires_at, severity, hops (loop guard) and an ALLOW-LISTED payload (unknown keys are dropped,
every string is cleaned and capped). Agent text (replies, summaries, commands, previews) is UNTRUSTED: it is data
for the owner, never instructions for JARVES (like an e-mail body) - see `clean()`.
"""
from __future__ import annotations

import re
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import logs

SCHEMA_VERSION = 1
KINDS = ("hello", "status_snapshot", "status_request", "status_response", "progress", "need_input",
         "approval_requested", "approval_decision", "command", "command_ack", "command_result", "completed", "error",
         "heartbeat", "goodbye", "usage")
SEVERITIES = ("info", "notice", "warning", "critical")
PROVIDERS = ("claude", "codex")
STATES = ("DISCOVERED", "RUNNING", "IDLE", "WAITING_INPUT", "WAITING_APPROVAL", "PAUSED", "STOPPED", "DISCONNECTED",
          "UNKNOWN")
LIVE_STATES = ("RUNNING", "IDLE", "WAITING_INPUT", "WAITING_APPROVAL", "PAUSED", "DISCOVERED")
# explicit result codes (#49 section 6 + package E acceptance): each is tested and has an English + Hebrew sentence
CODES = ("NO_LIVE_CHANNEL", "SESSION_CLOSED", "WAITING_FOR_APPROVAL", "AUTH_REQUIRED", "UNSUPPORTED_ATTACH",
         "BUDGET_REACHED", "AMBIGUOUS_SESSION", "NO_SUCH_SESSION", "NOT_ALLOWED_PROJECT", "SECOND_WRITER",
         "HOP_LIMIT", "CORE_NOT_RUNNING", "PROVIDER_MISSING", "NOT_APPROVED", "EXPIRED", "QUEUED", "SENT", "ok")
MAX_HOPS = 2
DEFAULT_TTL_S = 6 * 3600
TEXT_CAP = 1200                     # one payload string
SUMMARY_CAP = 400                   # what may reach the voice model
# payload keys that may travel (anything else is dropped at the door)
PAYLOAD_KEYS = frozenset({
    "text", "summary", "question", "reply", "status", "state", "event", "tool", "command", "cwd", "files",
    "description", "input_preview", "decision", "reason", "behavior", "risk", "notification_type", "title",
    "source", "model", "permission_mode", "auth", "cost_usd", "tokens_in", "tokens_out", "tokens_total", "turns",
    "duration_ms", "waiting_for", "name", "branch", "kind", "pid", "error", "exit_code", "channel", "delivery",
    "approval_id", "tool_name", "turn_id", "thread_id", "item_id", "version", "plugin", "live", "result",
    "stop_hook_active", "test_result", "ci", "transcript", "project_root", "started_at"})

_BIDI = re.compile("[‪-‮⁦-⁩​-‏⁠-⁤﻿­]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.S)
_WS = re.compile(r"[ \t\r\f\v]+")


def clean(text: Any, cap: int = TEXT_CAP, *, keep_code: bool = False) -> str:
    """Untrusted agent text -> safe display/speech data: NFC, no direction overrides / invisible characters /
    control characters, code blocks folded, whitespace collapsed, secrets redacted (logs.redact), capped with an
    ellipsis. The result is still UNTRUSTED (never executed, never followed as an instruction)."""
    s = unicodedata.normalize("NFC", str(text if text is not None else ""))
    s = _BIDI.sub("", s)
    s = _CTRL.sub(" ", s)
    if not keep_code:
        s = _CODE_BLOCK.sub(" [code omitted] ", s)
    s = "\n".join(_WS.sub(" ", line).strip() for line in s.splitlines())
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    s = logs.redact(s)
    if len(s) > cap:
        s = s[: max(1, cap - 1)].rstrip() + "…"
    return s


def summary(text: Any, cap: int = SUMMARY_CAP) -> str:
    """A short single-paragraph form for voice / lists (no code, no long paths)."""
    s = clean(text, cap * 3)
    s = " ".join(s.split())
    return s[: cap - 1].rstrip() + "…" if len(s) > cap else s


def _clean_value(v: Any, depth: int = 0) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, (list, tuple)) and depth < 2:
        return [_clean_value(x, depth + 1) for x in list(v)[:50]]
    if isinstance(v, dict) and depth < 2:
        return {str(k)[:40]: _clean_value(x, depth + 1) for k, x in list(v.items())[:30]
                if not logs.is_credential(k)}
    return clean(v, TEXT_CAP, keep_code=True)


def clean_payload(payload: dict | None) -> dict:
    out = {}
    for k, v in (payload or {}).items():
        if k in PAYLOAD_KEYS and not logs.is_credential(k):
            out[k] = _clean_value(v)
    return out


@dataclass
class Envelope:
    kind: str
    provider: str = ""
    session_id: str = ""                 # JARVES's registry id (provider:provider_session_id)
    project: str = ""
    payload: dict = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str = ""
    request_id: str = ""
    origin: str = "jarves"               # jarves | agent | owner
    severity: str = "info"
    hops: int = 0
    timestamp: float = field(default_factory=time.time)
    expires_at: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


class EnvelopeError(ValueError):
    pass


def make(kind: str, **kw) -> Envelope:
    e = Envelope(kind=kind, **kw)
    return validate(e.to_dict())


def validate(d: dict) -> Envelope:
    """A dict from the wire -> a checked Envelope. Unknown kinds, bad versions, oversize ids and hop overflow are
    refused; the payload is allow-listed and cleaned."""
    if not isinstance(d, dict):
        raise EnvelopeError("envelope must be an object")
    if int(d.get("schema_version") or 0) != SCHEMA_VERSION:
        raise EnvelopeError("unsupported schema_version")
    kind = str(d.get("kind") or "")
    if kind not in KINDS:
        raise EnvelopeError(f"unknown kind {kind[:20]!r}")
    mid = str(d.get("message_id") or uuid.uuid4().hex)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{6,80}", mid):
        raise EnvelopeError("bad message_id")
    hops = int(d.get("hops") or 0)
    if hops < 0 or hops > MAX_HOPS:
        raise EnvelopeError("HOP_LIMIT")
    provider = str(d.get("provider") or "")
    if provider and provider not in PROVIDERS:
        raise EnvelopeError("unknown provider")
    sev = str(d.get("severity") or "info")
    now = time.time()
    ts = float(d.get("timestamp") or now)
    exp = float(d.get("expires_at") or 0) or (ts + DEFAULT_TTL_S)
    return Envelope(kind=kind, provider=provider, session_id=str(d.get("session_id") or "")[:200],
                    project=str(d.get("project") or "")[:300], payload=clean_payload(d.get("payload")),
                    message_id=mid, correlation_id=str(d.get("correlation_id") or "")[:80],
                    request_id=str(d.get("request_id") or "")[:80],
                    origin=str(d.get("origin") or "agent") if str(d.get("origin") or "agent") in
                    ("jarves", "agent", "owner") else "agent",
                    severity=sev if sev in SEVERITIES else "info", hops=hops, timestamp=ts, expires_at=exp)


REPLY_RULE = ("Answer with the jarves_reply tool (pass the correlation_id) in a few short sentences: current task, "
              "last activity, files or commits as evidence, blocker, next step. If you have no jarves_reply tool, "
              "answer in your normal reply. Then go on with your work.")


def wrap_for_agent(rows: list[dict]) -> str:
    """The owner's queued messages (outbox rows) as ONE text for a coding agent (Stop-hook reason, prompt context,
    channel content). Marked as coming from the owner through JARVES; asks never approve anything."""
    parts = []
    for r in rows:
        p = r.get("payload") or {}
        text = clean(p.get("text") or p.get("question") or "", 2000, keep_code=True)
        cid = r.get("correlation_id") or r.get("message_id")
        if r.get("kind") == "status_request":
            parts.append(f"[JARVES, for the owner, correlation_id={cid}] The owner asks: {text}\n{REPLY_RULE}")
        else:
            parts.append(f"[JARVES, for the owner, correlation_id={cid}] The owner says: {text}")
    head = ("Message(s) relayed by JARVES, the owner's local assistant, on the owner's behalf. They are not "
            "permission approvals; your normal permission rules still apply.")
    return head + "\n\n" + "\n\n".join(parts)


# ---------------------------------------------------------------------- spoken / shown texts of the codes
TEXTS: dict[str, tuple[str, str]] = {
    "NO_LIVE_CHANNEL": ("I can't reach that session live; this is its last reported status.",
                        "אין לי חיבור חי לסשן הזה; זה המצב האחרון שהוא דיווח."),
    "SESSION_CLOSED": ("That session has ended.", "הסשן הזה הסתיים."),
    "WAITING_FOR_APPROVAL": ("It is waiting for your approval.", "הוא מחכה לאישור שלך."),
    "AUTH_REQUIRED": ("It needs a subscription sign-in first; I won't switch to paid API billing.",
                      "צריך קודם להתחבר עם המנוי; אני לא עובר לחיוב API בתשלום."),
    "UNSUPPORTED_ATTACH": ("I can see that session but can't steer it; I can resume it under JARVES if you approve.",
                           "אני רואה את הסשן הזה אבל לא יכול לכוון אותו; אפשר להמשיך אותו דרך ג'רוויס אם תאשר."),
    "BUDGET_REACHED": ("Today's limit of turns I may start is reached.", "הגעתי למכסה היומית של תורות שמותר לי להתחיל."),
    "AMBIGUOUS_SESSION": ("More than one session matches; which one?", "יותר מסשן אחד מתאים; לאיזה התכוונת?"),
    "NO_SUCH_SESSION": ("I don't see such a session.", "אני לא רואה סשן כזה."),
    "NOT_ALLOWED_PROJECT": ("That project is not on the allow-list.", "הפרויקט הזה לא ברשימת המותרים."),
    "SECOND_WRITER": ("Another agent is already working there; I can open a new worktree instead.",
                      "סוכן אחר כבר עובד שם; אפשר לפתוח worktree חדש במקום."),
    "HOP_LIMIT": ("Not forwarded: that would make agents talk in a loop.", "לא הועבר: זה היה יוצר לולאה בין סוכנים."),
    "CORE_NOT_RUNNING": ("JARVES is not running; start it first.", "ג'רוויס לא פועל; צריך להפעיל אותו קודם."),
    "PROVIDER_MISSING": ("That coding agent is not installed here.", "סוכן הקוד הזה לא מותקן כאן."),
    "NOT_APPROVED": ("Not done: it was not approved.", "לא בוצע: לא אושר."),
    "EXPIRED": ("The message expired before it was delivered.", "ההודעה פגה לפני שנמסרה."),
    "QUEUED": ("Queued; it will get it at its next safe stop.", "נשמר בתור; הוא יקבל את זה בעצירה הבטוחה הבאה."),
    "SENT": ("Sent to the session.", "נשלח לסשן."),
    "ok": ("Done.", "בוצע."),
}


def say(code: str, lang: str = "en") -> str:
    en, he = TEXTS.get(code, (code, code))
    return he if str(lang or "").startswith("he") else en
