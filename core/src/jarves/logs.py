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
_SENSITIVE_FIELDS = {"text", "transcript", "query", "audio", "token", "api_key", "secret", "password", "cookie"}


def redact(s: str) -> str:
    for pat, rep in _PATTERNS:
        s = pat.sub(rep, s)
    return s


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
            # private content is replaced by its length, never written
            d[k] = f"<{len(str(v))} chars>" if k in _SENSITIVE_FIELDS and v is not None else v
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
