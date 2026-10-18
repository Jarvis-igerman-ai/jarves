"""Bounded structured JSON-lines logging with secret redaction and correlation IDs.

Log file: <state_dir>/logs/jarves.log (rotated). Never logs raw audio, transcripts or secrets.
"""
from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_cid: contextvars.ContextVar[str] = contextvars.ContextVar("cid", default="-")
_SESSION = uuid.uuid4().hex[:8]

_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "<jwt>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|cookie)(\"?\s*[:=]\s*\"?)[^\s\",}]+"), r"\1\2***"),
]
# Field names whose VALUE is private content or a credential, at any nesting depth (compared case-insensitively).
_SENSITIVE_FIELDS = {
    # user content
    "text", "transcript", "transcription", "query", "q", "search", "match", "phrase", "utterance", "prompt",
    "instructions", "input", "output", "content", "contents", "message", "messages", "body", "subject", "snippet",
    "note", "notes", "memory", "memories", "fact", "title", "artist", "lyrics", "summary", "arguments", "delta",
    "audio", "pcm", "wav", "recipient", "recipients", "cc", "bcc", "sender",
    # identity
    "email", "login_hint", "name", "full_name", "phone", "address", "location", "sub", "subject_id",
    # credentials
    "token", "access_token", "refresh_token", "id_token", "id_token_hint", "api_key", "apikey", "secret",
    "client_secret", "password", "passcode", "cookie", "cookies", "authorization", "auth", "code_verifier",
    "verifier", "nonce", "credentials",
}
# ...and any field name containing one of these fragments.
_SENSITIVE_FRAGMENTS = ("token", "secret", "password", "cookie", "api_key", "apikey", "credential", "auth")
_MAX_DEPTH = 8


def redact(s: str) -> str:
    for pat, rep in _PATTERNS:
        s = pat.sub(rep, s)
    return s


def is_sensitive(key: object) -> bool:
    k = str(key).lower()
    return k in _SENSITIVE_FIELDS or any(f in k for f in _SENSITIVE_FRAGMENTS)


def _mask(v: object) -> str:
    return f"<{len(v) if isinstance(v, (str, bytes, list, tuple, dict)) else len(str(v))} chars>"


def scrub(value: object, depth: int = 0) -> object:
    """Recursively replace values under sensitive keys (any depth) by their length; redact secrets in strings."""
    if depth > _MAX_DEPTH:
        return "<nested>"
    if isinstance(value, dict):
        return {str(k): (_mask(v) if is_sensitive(k) and v is not None else scrub(v, depth + 1))
                for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [scrub(v, depth + 1) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))


def safe_fields(payload: object, allow: tuple[str, ...] | set[str] | frozenset[str] = ()) -> dict:
    """Only allow-listed top-level keys with scalar values (bool/int/float, short non-sensitive strings).

    For logging device/link payloads: everything else is reported only by key name, never by value.
    Usage: logs.info("device", "DEVICE_EVENT", "...", **logs.safe_fields(p, allow=("id", "muted")))
    """
    if not isinstance(payload, dict):
        return {}
    out: dict = {}
    for k, v in payload.items():
        if k not in allow or is_sensitive(k):
            continue
        if v is None or isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, str) and len(v) <= 64:
            out[k] = redact(v)
        else:
            out[k] = _mask(v)
    dropped = sorted(str(k) for k in payload if k not in out)
    if dropped:
        out["omitted_keys"] = dropped[:20]
    return out


def new_cid() -> str:
    cid = uuid.uuid4().hex[:10]
    _cid.set(cid)
    return cid


def set_cid(cid: str) -> None:
    _cid.set(cid)


def get_cid() -> str:
    return _cid.get()


class JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        d = {"ts": datetime.fromtimestamp(r.created, timezone.utc).isoformat(timespec="milliseconds"),
             "level": r.levelname, "component": getattr(r, "component", r.name), "code": getattr(r, "code", "-"),
             "msg": r.getMessage(), "cid": getattr(r, "cid", None) or _cid.get(), "session": _SESSION}
        for k, v in (getattr(r, "fields", None) or {}).items():
            # private content (at any depth) is replaced by its length, never written
            d[k] = _mask(v) if is_sensitive(k) and v is not None else scrub(v)
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info).splitlines()[-1]
        return redact(json.dumps(d, ensure_ascii=False, default=str))


_logger = logging.getLogger("jarves")
_debug_until = 0.0
_base_level = logging.INFO


def setup(log_dir: Path, level: str = "INFO", max_bytes: int = 1_000_000, backups: int = 5,
          console: bool = True) -> Path:
    global _base_level
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "jarves.log"
    for h in list(_logger.handlers):
        _logger.removeHandler(h)
        h.close()
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    _logger.addHandler(fh)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(JsonFormatter())
        _logger.addHandler(ch)
    _base_level = getattr(logging, level)
    _logger.setLevel(_base_level)
    _logger.propagate = False
    return path


def enable_debug(minutes: int) -> None:
    """Temporary DEBUG; reverts automatically on the next log call after expiry."""
    global _debug_until
    _debug_until = time.time() + minutes * 60
    _logger.setLevel(logging.DEBUG)


def log(component: str, code: str, msg: str, level: int = logging.INFO, exc: bool = False, **fields) -> None:
    global _debug_until
    if _debug_until and time.time() > _debug_until:
        _debug_until = 0.0
        _logger.setLevel(_base_level)
    _logger.log(level, msg, exc_info=exc, extra={"component": component, "code": code, "fields": fields})


def info(component: str, code: str, msg: str, **f) -> None:
    log(component, code, msg, logging.INFO, **f)


def warn(component: str, code: str, msg: str, **f) -> None:
    log(component, code, msg, logging.WARNING, **f)


def error(component: str, code: str, msg: str, exc: bool = False, **f) -> None:
    log(component, code, msg, logging.ERROR, exc=exc, **f)


def debug(component: str, code: str, msg: str, **f) -> None:
    log(component, code, msg, logging.DEBUG, **f)
