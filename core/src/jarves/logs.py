"""Bounded structured JSON-lines logging with secret redaction and correlation IDs.

Log file: <state_dir>/logs/jarves.log (rotated, JSON lines). Terminal: readable coloured lines with its own
level (ERROR/WARNING/INFO/DEBUG/VERBOSE) and per-module filters (show only / hide components).
Never logs raw audio, transcripts or secrets.
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

VERBOSE = 5                       # below DEBUG: per-message / per-event traces
logging.addLevelName(VERBOSE, "VERBOSE")
LEVELS = ("ERROR", "WARNING", "INFO", "DEBUG", "VERBOSE")


def level_no(name: str) -> int:
    name = name.upper()
    if name not in LEVELS:
        raise ValueError(f"log level must be one of {', '.join(LEVELS)}")
    return VERBOSE if name == "VERBOSE" else getattr(logging, name)


_cid: contextvars.ContextVar[str] = contextvars.ContextVar("cid", default="-")
_SESSION = uuid.uuid4().hex[:8]

_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "<jwt>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|cookie)(\"?\s*[:=]\s*\"?)[^\s\",}]+"), r"\1\2***"),
]
# Field names whose VALUE is private CONTENT (masked only in privacy mode) or a CREDENTIAL (ALWAYS masked),
# at any nesting depth (compared case-insensitively).
_CONTENT_FIELDS = {
    # user content
    "text", "transcript", "transcription", "query", "q", "search", "match", "phrase", "utterance", "prompt",
    "instructions", "input", "output", "content", "contents", "message", "messages", "body", "subject", "snippet",
    "note", "notes", "memory", "memories", "fact", "title", "artist", "lyrics", "summary", "arguments", "delta",
    "audio", "pcm", "wav", "recipient", "recipients", "cc", "bcc", "sender",
    # identity
    "email", "login_hint", "name", "full_name", "phone", "address", "location", "sub", "subject_id",
}
_CREDENTIAL_FIELDS = {
    "token", "access_token", "refresh_token", "id_token", "id_token_hint", "api_key", "apikey", "secret",
    "client_secret", "password", "passcode", "cookie", "cookies", "authorization", "auth", "code_verifier",
    "verifier", "nonce", "credentials",
}
# ...and any field name containing one of these fragments is a credential.
_SENSITIVE_FRAGMENTS = ("token", "secret", "password", "cookie", "api_key", "apikey", "credential", "auth")
_MAX_DEPTH = 8


def redact(s: str) -> str:
    for pat, rep in _PATTERNS:
        s = pat.sub(rep, s)
    return s


# Privacy mode (log.privacy): True = mask user content (what was said/heard/answered); False = full content for
# development. Credentials (keys, tokens, passwords, cookies) are masked in BOTH modes. Library default: True.
_privacy = True


def set_privacy(on: bool) -> None:
    global _privacy
    _privacy = bool(on)


def privacy() -> bool:
    _poll_privacy()
    return _privacy


# Runtime privacy switch (dashboard button, WP-K): <log_dir>/privacy.json {"privacy": bool, "at": ts}. Whoever writes
# it (the dashboard in another process, the CLI) changes privacy for NEW log lines / history rows of every process
# that logs into the same folder, within a second, without a restart. A flag older than this process's setup() is
# ignored (config.toml, also updated by the switch, is the truth at start).
PRIVACY_FILE = "privacy.json"
_flag: Path | None = None
_flag_mtime: float | None = None
_flag_checked = 0.0
_runtime: bool | None = None          # set by a runtime switch since setup() (None = the config value holds)


def runtime_privacy() -> bool | None:
    """The value of a runtime switch made since setup() (dashboard button), else None."""
    _poll_privacy()
    return _runtime


def _poll_privacy(force: bool = False) -> None:
    global _flag_mtime, _flag_checked, _runtime
    if _flag is None:
        return
    now = time.monotonic()
    if not force and now - _flag_checked < 1.0:
        return
    _flag_checked = now
    try:
        m = _flag.stat().st_mtime
    except OSError:
        return
    if m == _flag_mtime:
        return
    _flag_mtime = m
    try:
        v = json.loads(_flag.read_text(encoding="utf-8")).get("privacy")
    except (OSError, ValueError, AttributeError):
        return
    if isinstance(v, bool):
        set_privacy(v)
        _runtime = v


def watch_privacy(log_dir: Path) -> None:
    """Follow the runtime switch in <log_dir>/privacy.json from now on (called by setup())."""
    global _flag, _flag_mtime, _runtime
    _flag, _runtime = Path(log_dir) / PRIVACY_FILE, None
    try:
        _flag_mtime = _flag.stat().st_mtime         # baseline: only LATER switches apply
    except OSError:
        _flag_mtime = None


def write_privacy(log_dir: Path, on: bool) -> None:
    """Flip privacy now for this process and (via the flag file) for every process logging into log_dir."""
    global _flag_mtime, _runtime
    import os
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    tmp = log_dir / (PRIVACY_FILE + ".tmp")
    tmp.write_text(json.dumps({"privacy": bool(on), "at": time.time()}), encoding="utf-8")
    os.replace(tmp, log_dir / PRIVACY_FILE)
    set_privacy(on)
    _runtime = bool(on)
    if _flag is not None and _flag == log_dir / PRIVACY_FILE:
        try:
            _flag_mtime = _flag.stat().st_mtime       # our own write: already applied
        except OSError:
            pass


def is_credential(key: object) -> bool:
    k = str(key).lower()
    return k in _CREDENTIAL_FIELDS or any(f in k for f in _SENSITIVE_FRAGMENTS)


def is_sensitive(key: object, private: bool | None = None) -> bool:
    """Should this field's value be masked? Credentials always; content only in privacy mode."""
    if is_credential(key):
        return True
    return (privacy() if private is None else private) and str(key).lower() in _CONTENT_FIELDS


def _mask(v: object) -> str:
    return f"<{len(v) if isinstance(v, (str, bytes, list, tuple, dict)) else len(str(v))} chars>"


def scrub(value: object, depth: int = 0, private: bool | None = None) -> object:
    """Recursively replace values under sensitive keys (any depth) by their length; redact secrets in strings.
    `private` overrides the current privacy mode (e.g. True for a support bundle meant for sharing)."""
    if depth > _MAX_DEPTH:
        return "<nested>"
    if isinstance(value, dict):
        return {str(k): (_mask(v) if is_sensitive(k, private) and v is not None else scrub(v, depth + 1, private))
                for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [scrub(v, depth + 1, private) for v in value]
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


class ConsoleFormatter(logging.Formatter):
    """`19:20:01 INFO    voice   LIVE_STARTED   GPT-Live session started   score=0.9` (same redaction as the file)."""
    COLORS = {"ERROR": "31", "WARNING": "33", "INFO": "32", "DEBUG": "36", "VERBOSE": "90"}

    def __init__(self, color: bool) -> None:
        super().__init__()
        self.color = color

    def format(self, r: logging.LogRecord) -> str:
        comp = getattr(r, "component", r.name)
        extra = []
        for k, v in (getattr(r, "fields", None) or {}).items():
            v = _mask(v) if is_sensitive(k) and v is not None else scrub(v)
            extra.append(f"{k}={json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v}")
        lvl = f"{r.levelname:<7}"
        if self.color:
            lvl = f"[{self.COLORS.get(r.levelname, '0')}m{lvl}[0m"
            comp_s = f"[1m{comp:<8}[0m"
        else:
            comp_s = f"{comp:<8}"
        line = (f"{datetime.fromtimestamp(r.created).strftime('%H:%M:%S')} {lvl} {comp_s} "
                f"{getattr(r, 'code', '-'):<22} {r.getMessage()}" + ("   " + " ".join(extra) if extra else ""))
        if r.exc_info:
            line += "   exc=" + self.formatException(r.exc_info).splitlines()[-1]
        return redact(line)


class ModuleFilter(logging.Filter):
    """Terminal filter: minimum level + only/hide by component (module) name, e.g. only={'voice','wake'}."""

    def __init__(self, level: int, only=(), hide=()) -> None:
        super().__init__()
        self.level = level
        self.only = {m.strip().lower() for m in only if m.strip()}
        self.hide = {m.strip().lower() for m in hide if m.strip()}

    def filter(self, r: logging.LogRecord) -> bool:
        comp = str(getattr(r, "component", r.name)).lower()
        if r.levelno >= logging.ERROR:          # errors always show, whatever the module filter
            return r.levelno >= self.level
        return r.levelno >= self.level and (not self.only or comp in self.only) and comp not in self.hide


_logger = logging.getLogger("jarves")
_debug_until = 0.0
_base_level = logging.INFO
_console: ModuleFilter | None = None
_console_base = logging.INFO
# Extra handlers that survive setup() and always receive INFO and above (e.g. the conversation history store).
_sinks: list[logging.Handler] = []


def add_sink(handler: logging.Handler) -> None:
    """Attach a handler that gets every INFO+ record with its structured fields, whatever the file/console level."""
    handler.setLevel(min(handler.level or logging.INFO, logging.INFO))
    if handler not in _sinks:
        _sinks.append(handler)
        _logger.addHandler(handler)
    _logger.setLevel(min(_logger.level or logging.INFO, logging.INFO))


def remove_sink(handler: logging.Handler) -> None:
    if handler in _sinks:
        _sinks.remove(handler)
    _logger.removeHandler(handler)


def _logger_level(base: int) -> int:
    return min(base, logging.INFO) if _sinks else base


def setup(log_dir: Path, level: str = "INFO", max_bytes: int = 1_000_000, backups: int = 5,
          console: bool = True, console_level: str | None = None, only=(), hide=(), privacy: bool = True) -> Path:
    """File gets `level` (JSON); the terminal gets `console_level` (default = level) filtered by module."""
    global _base_level, _console, _console_base
    set_privacy(privacy)
    log_dir.mkdir(parents=True, exist_ok=True)
    watch_privacy(log_dir)
    path = log_dir / "jarves.log"
    for h in list(_logger.handlers):
        if h in _sinks:
            continue
        _logger.removeHandler(h)
        h.close()
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    _logger.addHandler(fh)
    _base_level = level_no(level)
    fh.setLevel(_base_level)
    _console = None
    if console:
        ch = logging.StreamHandler(sys.stderr)
        color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        if color and sys.platform == "win32":
            import os
            os.system("")                      # enable ANSI colours in the Windows console
        ch.setFormatter(ConsoleFormatter(color))
        _console_base = level_no(console_level or level)
        _console = ModuleFilter(_console_base, only, hide)
        ch.addFilter(_console)
        _logger.addHandler(ch)
    _logger.setLevel(_logger_level(min(_base_level, _console_base if console else _base_level)))
    _logger.propagate = False
    return path


def enable_debug(minutes: int) -> None:
    """Temporary DEBUG; reverts automatically on the next log call after expiry."""
    global _debug_until
    _debug_until = time.time() + minutes * 60
    _logger.setLevel(min(logging.DEBUG, _logger.level))
    for h in _logger.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            h.setLevel(logging.DEBUG)


def log(component: str, code: str, msg: str, level: int = logging.INFO, exc: bool = False, **fields) -> None:
    global _debug_until
    if _debug_until and time.time() > _debug_until:
        _debug_until = 0.0
        for h in _logger.handlers:
            if isinstance(h, logging.handlers.RotatingFileHandler):
                h.setLevel(_base_level)
        _logger.setLevel(_logger_level(min(_base_level, _console_base if _console else _base_level)))
    _logger.log(level, msg, exc_info=exc, extra={"component": component, "code": code, "fields": fields})


def info(component: str, code: str, msg: str, **f) -> None:
    log(component, code, msg, logging.INFO, **f)


def warn(component: str, code: str, msg: str, **f) -> None:
    log(component, code, msg, logging.WARNING, **f)


def error(component: str, code: str, msg: str, exc: bool = False, **f) -> None:
    log(component, code, msg, logging.ERROR, exc=exc, **f)


def debug(component: str, code: str, msg: str, **f) -> None:
    log(component, code, msg, logging.DEBUG, **f)


def verbose(component: str, code: str, msg: str, **f) -> None:
    if _logger.isEnabledFor(VERBOSE):
        log(component, code, msg, VERBOSE, **f)
