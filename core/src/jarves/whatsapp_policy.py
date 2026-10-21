"""WhatsApp bridge send policy (SW-V4 F, Issue #17): the rules of bridge/whatsapp/PROTOCOL.md section 4 in Python.

Used by the fake bridge (core/tools/fake_whatsapp_bridge.py, so every automatic test meets the same rules as the
real Go bridge) and by the Core, which repeats the checks only to answer early and kindly. The bridge is the one that
enforces them: the shared table bridge/whatsapp/testdata/policy_table.json is run against this module
(tests/test_whatsapp_policy.py) and against the Go policy package (`go test ./internal/policy`).

Also here: the approval id (`wa1.<payload>.<sig>`, HMAC-SHA256 with the bridge's approval.key, minted by the Core
approvals service only after the owner approved the popup) and the text cleaning applied to every incoming message
(bidi / zero-width / control characters are removed and REPORTED, never silently kept).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field

MODES = ("off", "read", "send")
MAX_TEXT = 1500                    # Unicode code points; the popup shows every one of them (no ellipsis)
APPROVAL_MAX_AHEAD_S = 300
QUIET_FROM, QUIET_TO = 23, 7       # 23:00-06:59 local: refused unless the approval was typed (night = true)
LIMITS = {"per_15s": (1, 15.0), "per_hour": (10, 3600.0), "per_day": (30, 86400.0)}
RECIPIENTS_PER_HOUR = 3
WATCH_DAYS = 14                    # read mode with zero warnings before send mode may be switched on
USER_SUFFIXES = ("@s.whatsapp.net", "@lid")
GROUP_SUFFIX = "@g.us"
STATUS = {                         # code -> HTTP status (PROTOCOL.md)
    "WA_KILLED": 423, "WA_MODE_OFF": 409, "WA_MODE_READ": 409, "WA_EMPTY": 400, "WA_TOO_LONG": 400,
    "WA_GROUP_REFUSED": 403, "WA_BAD_RECIPIENT": 403, "WA_NOT_ALLOWED": 403, "WA_NO_HISTORY": 403,
    "WA_APPROVAL_MISSING": 403, "WA_APPROVAL_INVALID": 403, "WA_APPROVAL_EXPIRED": 403, "WA_APPROVAL_MISMATCH": 403,
    "WA_APPROVAL_REUSED": 403, "WA_QUIET_HOURS": 403, "WA_RATE_LIMITED": 429, "WA_NOT_CONNECTED": 503,
}
MESSAGES = {                       # fixed English sentences (never message text, names or numbers)
    "WA_KILLED": "the WhatsApp bridge is killed: nothing is sent",
    "WA_MODE_OFF": "WhatsApp access is off",
    "WA_MODE_READ": "WhatsApp is in read-only mode: sending is switched off",
    "WA_EMPTY": "the message is empty",
    "WA_TOO_LONG": "the message is longer than 1500 characters",
    "WA_GROUP_REFUSED": "groups are never sent to",
    "WA_BAD_RECIPIENT": "only one person can be sent to (no broadcasts, status or channels)",
    "WA_NOT_ALLOWED": "this contact is not on the send allow-list",
    "WA_NO_HISTORY": "there is no earlier chat with this contact",
    "WA_APPROVAL_MISSING": "no approval id: the owner did not approve this send",
    "WA_APPROVAL_INVALID": "the approval id is not valid",
    "WA_APPROVAL_EXPIRED": "the approval expired",
    "WA_APPROVAL_MISMATCH": "the approval was for another recipient or text",
    "WA_APPROVAL_REUSED": "the approval was already used",
    "WA_QUIET_HOURS": "no sends between 23:00 and 07:00 without a typed approval",
    "WA_RATE_LIMITED": "too many messages: the rate limit was reached",
    "WA_NOT_CONNECTED": "the bridge is not connected to WhatsApp",
}


# ---------------------------------------------------------------------- ids
def is_group(jid: str) -> bool:
    return str(jid).endswith(GROUP_SUFFIX)


def is_user(jid: str) -> bool:
    j = str(jid)
    return j.endswith(USER_SUFFIXES) and j.split("@", 1)[0].isdigit()


def number_to_jid(number: str, country: str = "972") -> str | None:
    """'+972 54-111-2222' / '054-111-2222' -> '972541112222@s.whatsapp.net'; a JID is returned as is."""
    s = str(number or "").strip()
    if "@" in s:
        return s if (is_user(s) or is_group(s)) else None
    from .whatsapp import normalize_phone              # one phone-number rule for the handoff and the bridge
    d = normalize_phone(s, country)
    return f"{d}@s.whatsapp.net" if d else None


def jid_number(jid: str) -> str:
    """'+972541112222' for a phone JID; '' for a LID or a group (no number is known)."""
    j = str(jid)
    return "+" + j.split("@", 1)[0] if j.endswith("@s.whatsapp.net") else ""


def chat_hash(jid: str) -> str:
    """What logs and the audit carry instead of a chat id (no number, no name)."""
    return hashlib.sha256(str(jid).encode("utf-8")).hexdigest()[:16]


def text_hash(to: str, text: str) -> str:
    return hashlib.sha256(f"{to}\n{text}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------- approval ids
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_approval(key: bytes, to: str, text: str, *, exp: float, night: bool = False, nonce: str | None = None) -> str:
    payload = {"v": 1, "n": nonce or secrets.token_hex(16), "to": to, "h": text_hash(to, text), "exp": int(exp),
               "night": bool(night)}
    body = "wa1." + _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return body + "." + _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())


@dataclass
class Approval:
    nonce: str
    to: str
    h: str
    exp: float
    night: bool


def parse_approval(key: bytes, token: str) -> tuple[Approval | None, str | None]:
    """(approval, None) when the signature is right; (None, code) otherwise. Expiry/binding are checked by check_send."""
    t = str(token or "")
    if not t:
        return None, "WA_APPROVAL_MISSING"
    parts = t.split(".")
    if len(parts) != 3 or parts[0] != "wa1" or len(t) > 2048:
        return None, "WA_APPROVAL_INVALID"
    body = parts[0] + "." + parts[1]
    try:
        sig = _unb64(parts[2])
    except (ValueError, TypeError):
        return None, "WA_APPROVAL_INVALID"
    if not hmac.compare_digest(sig, hmac.new(key, body.encode("ascii", "replace"), hashlib.sha256).digest()):
        return None, "WA_APPROVAL_INVALID"
    try:
        p = json.loads(_unb64(parts[1]).decode("utf-8"))
        a = Approval(str(p["n"]), str(p["to"]), str(p["h"]), float(p["exp"]), bool(p.get("night", False)))
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None, "WA_APPROVAL_INVALID"
    if p.get("v") != 1 or not re.fullmatch(r"[0-9a-f]{32}", a.nonce):
        return None, "WA_APPROVAL_INVALID"
    return a, None


# ---------------------------------------------------------------------- the send policy
@dataclass
class SendState:
    """What check_send looks at. `sent` = [(unix ts, to)] of messages REALLY sent (refusals do not count)."""
    mode: str = "off"
    killed: bool = False
    send_allow: set[str] = field(default_factory=set)
    history: set[str] = field(default_factory=set)
    sent: list[tuple[float, str]] = field(default_factory=list)
    used_nonces: dict[str, float] = field(default_factory=dict)     # nonce -> exp (forgotten after it passes)


@dataclass
class Refusal:
    code: str
    limit: str = ""
    retry_after_s: float = 0.0

    @property
    def status(self) -> int:
        return STATUS.get(self.code, 403)

    def body(self) -> dict:
        e = {"code": self.code, "message": MESSAGES.get(self.code, self.code)}
        if self.limit:
            e["limit"], e["retry_after_s"] = self.limit, round(self.retry_after_s, 1)
        return {"error": e}


def quiet(local_hour: int) -> bool:
    return local_hour >= QUIET_FROM or local_hour < QUIET_TO


def check_send(st: SendState, to: str, text: str, approval_id: str, key: bytes, *, now: float,
               local_hour: int, connected: bool = True) -> tuple[Refusal | None, Approval | None]:
    """PROTOCOL.md section 4, in its order. (None, approval) = may send now (then call consume)."""
    if st.killed:
        return Refusal("WA_KILLED"), None
    if st.mode != "send":
        return Refusal("WA_MODE_OFF" if st.mode == "off" else "WA_MODE_READ"), None
    if not str(text or "").strip():
        return Refusal("WA_EMPTY"), None
    if len(text) > MAX_TEXT:
        return Refusal("WA_TOO_LONG"), None
    if is_group(to):
        return Refusal("WA_GROUP_REFUSED"), None
    if not is_user(to):
        return Refusal("WA_BAD_RECIPIENT"), None
    if to not in st.send_allow:
        return Refusal("WA_NOT_ALLOWED"), None
    if to not in st.history:
        return Refusal("WA_NO_HISTORY"), None
    a, bad = parse_approval(key, approval_id)
    if bad:
        return Refusal(bad), None
    if a.exp > now + APPROVAL_MAX_AHEAD_S:
        return Refusal("WA_APPROVAL_INVALID"), None
    if a.exp <= now:
        return Refusal("WA_APPROVAL_EXPIRED"), None
    if a.to != to or not hmac.compare_digest(a.h, text_hash(to, text)):
        return Refusal("WA_APPROVAL_MISMATCH"), None
    st.used_nonces = {n: e for n, e in st.used_nonces.items() if e > now}
    if a.nonce in st.used_nonces:
        return Refusal("WA_APPROVAL_REUSED"), None
    if quiet(local_hour) and not a.night:
        return Refusal("WA_QUIET_HOURS"), None
    r = rate_refusal(st, to, now)
    if r is not None:
        return r, None
    if not connected:
        return Refusal("WA_NOT_CONNECTED"), None
    return None, a


def rate_refusal(st: SendState, to: str, now: float) -> Refusal | None:
    for name, (n, window) in LIMITS.items():
        recent = sorted(t for t, _ in st.sent if now - t < window)
        if len(recent) >= n:
            return Refusal("WA_RATE_LIMITED", name, recent[-n] + window - now)
    hour = [(t, r) for t, r in st.sent if now - t < 3600.0]
    others = {r for _, r in hour if r != to}
    if to not in {r for _, r in hour} and len(others) >= RECIPIENTS_PER_HOUR:
        return Refusal("WA_RATE_LIMITED", "recipients_per_hour", min(t for t, _ in hour) + 3600.0 - now)
    return None


def consume(st: SendState, a: Approval, to: str, now: float) -> None:
    """The message was sent: the approval is spent and the send counts towards the limits."""
    st.used_nonces[a.nonce] = a.exp
    st.sent.append((now, to))
    st.sent = [(t, r) for t, r in st.sent if now - t < 86400.0]


def rate_view(st: SendState, now: float) -> dict:
    hour = [(t, r) for t, r in st.sent if now - t < 3600.0]
    return {"last_15s": sum(1 for t, _ in st.sent if now - t < 15.0), "hour": len(hour),
            "day": sum(1 for t, _ in st.sent if now - t < 86400.0), "recipients_hour": len({r for _, r in hour}),
            "limits": {"per_15s": 1, "per_hour": 10, "per_day": 30, "recipients_per_hour": RECIPIENTS_PER_HOUR,
                       "quiet_hours": "23:00-07:00"}}


def local_hour(ts: float | None = None) -> int:
    return time.localtime(ts if ts is not None else time.time()).tm_hour


# ---------------------------------------------------------------------- incoming text
_HIDDEN = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­᠎\U000e0000-\U000e007f]")


def clean_text(text: str) -> tuple[str, list[str]]:
    """Remove bidi overrides, zero-width and other invisible characters (they can hide an instruction or reverse what
    is shown) and C0/C1 controls except newline/tab. Returns (clean text, ['U+202E', ...] that were removed)."""
    removed: list[str] = []

    def keep(ch: str) -> bool:
        if _HIDDEN.match(ch) or (unicodedata.category(ch) == "Cc" and ch not in "\n\t"):
            removed.append(f"U+{ord(ch):04X}")
            return False
        return True
    out = "".join(ch for ch in unicodedata.normalize("NFC", str(text or "")) if keep(ch))
    return out, sorted(set(removed))
