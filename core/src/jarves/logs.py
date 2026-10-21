"""Bounded structured JSON-lines logging with field classification, secret redaction and correlation IDs.

Log file: <state_dir>/logs/jarves.log (rotated, JSON lines). Terminal: readable coloured lines with its own
level (ERROR/WARNING/INFO/DEBUG/VERBOSE) and per-module filters (show only / hide components).

SW-V4 package B (issue #15): structured messages.
  logs.info(component, CODE, "fixed developer-written description", field=value, ...)
* `msg` is ALWAYS a string literal (tests/test_log_message_lint.py fails the build otherwise: no f-strings, no %,
  no variables). Every variable part is a keyword field.
* Every field name is classified once in FIELD_CLASS:
    safe     counts, durations, ids, enum states, codes             -> written as is (secret patterns redacted)
    secret   keys, tokens, passwords, cookies                       -> ALWAYS masked
    content  user or provider text (what was said, error text...)   -> masked in privacy mode; never in a bundle
    pii      names, numbers, e-mails, addresses                     -> masked in privacy mode; hashed in a bundle
  An unknown field name is `content`. Provider error text goes to `provider_error` (content): use error_fields(e).
* Records that did not come through logs.* (third-party libraries) carry free text: their message is `content`.
* Secrets (API keys, OAuth/JWT tokens, bot tokens, URL query tokens, DPAPI blobs...) and the Windows user name in
  profile paths are redacted from every string in BOTH modes.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
import traceback
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

VERBOSE = 5                       # below DEBUG: per-message / per-event traces
logging.addLevelName(VERBOSE, "VERBOSE")
LEVELS = ("ERROR", "WARNING", "INFO", "DEBUG", "VERBOSE")
FORMAT_VERSION = 2                # "v" of every line: 2 = msg is a developer-written template (package B)


def level_no(name: str) -> int:
    name = name.upper()
    if name not in LEVELS:
        raise ValueError(f"log level must be one of {', '.join(LEVELS)}")
    return VERBOSE if name == "VERBOSE" else getattr(logging, name)


_cid: contextvars.ContextVar[str] = contextvars.ContextVar("cid", default="-")
_SESSION = uuid.uuid4().hex[:8]

# ---------------------------------------------------------------------------------------------- secret patterns
# Order matters: specific token shapes first, then the generic key=value and URL-query rules.
_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),                                   # OpenAI / Anthropic keys
    (re.compile(r"(?i)\bbearer\s+(?!\*\*\*)[A-Za-z0-9._~+/\-]+=*"), "Bearer ***"),
    (re.compile(r"(?i)\bbasic\s+(?=[A-Za-z0-9+/]*\d)[A-Za-z0-9+/]{16,}={0,2}"), "Basic ***"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"), "<jwt>"),       # OAuth id/access, HA LLAT
    (re.compile(r"\bya29\.[A-Za-z0-9_\-.]{8,}"), "<google-token>"),                     # Google access token
    (re.compile(r"(?<![\w/])1//[A-Za-z0-9_\-]{16,}"), "<google-token>"),                # Google refresh token
    (re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{8,}"), "<google-secret>"),                    # Google client secret
    (re.compile(r"(?:(?<=/bot)|(?<=\bbot)|(?<![\w:]))\d{6,12}:[A-Za-z0-9_\-]{30,}"), "<bot-token>"),             # Telegram bot token
    (re.compile(r"\b(?:EAA|IGQV|IGAA)[A-Za-z0-9_\-]{20,}"), "<meta-token>"),           # Instagram / Meta tokens
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "<github-token>"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "<slack-token>"),
    (re.compile(r"AQAAANCMnd8BFdERjHoAwE/Cl\+sBAAAA[A-Za-z0-9+/=]*"), "<dpapi-blob>"),   # Windows DPAPI blob (base64)
    (re.compile(r"\bwa[ab]_[A-Za-z0-9_\-]{20,}"), "<whatsapp-bridge-token>"),           # SW-V4 F bridge user/admin token
    (re.compile(r"\bwa1\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"), "<whatsapp-approval>"),   # signed approval id
    (re.compile(r"(?i)([?&#;](?:t|k|key|token|access_token|refresh_token|id_token|code|secret|client_secret|sig|"
                r"signature|auth|password|pwd|session|sid|apikey|api_key|state)=)[^&#\s\"'<>]+"), r"\1***"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|cookie|authorization)(\"?\s*[:=]\s*\"?)"
                r"(?!\*\*\*|<\d+ chars>|<secret>)[^\s\",}&]+"), r"\1\2***"),
]
# Windows user-profile paths: C:\Users\<name>\ (also / and JSON-escaped \\) -> C:\Users\<user>\
_USER_PATH = re.compile(r"(?i)((?:[a-z]:)?(?:\\\\|\\|/)(?:users|documents and settings)(?:\\\\|\\|/)|(?<![\w.])/home/)"
                        r"(?!<user>|public(?:\\|/|$)|default(?:\\|/|$))[^\\/\s\"'<>:*?|]+")


_TOKEN_SHAPES = [p for p, _ in _PATTERNS[:1] + _PATTERNS[3:14]]   # token shapes only (no bearer/key=value/URL rules)


def has_secret(s: str) -> bool:
    """Does this text contain a credential-shaped string (API key, OAuth/JWT, bot/Meta/GitHub token, DPAPI blob)?
    Used by backups (such a file is left out) and by the leak checks; ***-masked values do not count."""
    return any(p.search(m.group(0)) and "***" not in m.group(0) for p in _TOKEN_SHAPES for m in p.finditer(s))


def redact_deep(v: object, depth: int = 0) -> object:
    """redact() applied to every string inside a JSON-like value (structure and numbers kept)."""
    if depth > 12:
        return v
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: redact_deep(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [redact_deep(x, depth + 1) for x in v]
    return v


def redact(s: str) -> str:
    """Secrets (both modes, every string) and the user name in profile paths."""
    for pat, rep in _PATTERNS:
        s = pat.sub(rep, s)
    return _USER_PATH.sub(r"\1<user>", s)


# Personal identifiers inside free strings (privacy mode / bundles): e-mails and phone numbers.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<![\w+.:/])(?:\+|00)?\d(?:[\s\-()]?\d){8,14}(?![\w.:])")


def redact_pii(s: str) -> str:
    s = _EMAIL.sub("<email>", s)
    return _PHONE.sub(lambda m: "<phone>" if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0), s)


# ---------------------------------------------------------------------------------------------- field registry
SAFE, SECRET, CONTENT, PII = "safe", "secret", "content", "pii"
CLASSES = (SAFE, SECRET, CONTENT, PII)


def _reg(cls: str, names: str) -> dict[str, str]:
    return {n: cls for n in names.split()}


# Every keyword field of a logs.* call is listed here (the lint test checks it). Classify by what the VALUE can hold:
# anything a user said, a provider returned or an exception printed is content; a person's identifier is pii.
FIELD_CLASS: dict[str, str] = {
    **_reg(SAFE, """
        amber automatic blocked cap_usd error_type est_usd failed item item_count part passed red remedy skipped_because
        ticket usd visibility within_budget
        action actions activation active after_s age_s ai_reviews alerts_paused answer_chars api_code applier
        asr_ms attempt attempts audio_ended audio_s audio_started autonomy available away backend backend_model
        backend_s backup bad_fields browser budget_s builtin bytes calendar camera camera_when_away caps changed
        channel chars chatgpt checkpoint classification cloud_audio code_name codes connect_ms connection count
        current days deadline_s decision delegation delegations deleted detector device done duration_s elapsed_s
        enabled engine entities entity error_code error_type errors events exc_frames exc_type excluded exit_code
        faces fallback fatal feature features features_off file files fingerprint first_audio_ms floor_db
        flushed_bytes found fps frames gain_db gate_closed_for_s gmail grant greeting guide height high bind_host http results
        id idem in_s input_closed installer job job_id keep_running key keys kind known lang late late_s latest
        level_db lib limit list_id log log_lines low medium meter method mic_active mic_state mime minutes missed missing
        mode model model_audio_in_s model_calls model_speech_s modules ms music muted n near_misses needs next_run
        omitted_keys on only_done op operation origin overruns owner_pid param path peak_level peak_rms peak_score
        percent permission phase photos_today pid plan plan_usage poll_s port previous processes purpose rc reason
        recognition removed requested response_id restart result_code retry retry_s returncode revoked risk risky
        role run run_id said_chars samples saved scopes score seconds series session sessions setting sha256
        shown side silence_rms_max silenced size skill slept_s speaker_queue_s speaking speech speech_ms
        speech_no_match stage state status step task_id target_kind threshold timed_out timeout_s timer_id
        tool tools tray tried trigger turns typed url_host usage_cached usage_in usage_out usage_reasoning
        used_today verb version via voice voice_mode voice_seconds wait_s waiting wake web_searches width window
        window_s copied_from home stats drops proposal_id spec_id step_id alert_kind task_kind
        after alert_id by callback conversation_id domain emotion event extra fields language msg_type ok outcome
        pages paused peer priority quality report searches section service source sources speech_db target
        task_name tier where crash_kind note_id code args_chars ok_count
        index_rows docs cap took_ms term_count ranged build_s fts_ok orphan_fts docs_without_fts masked_or_gone
        deleted_sessions added
        chat_hash chats logout override unread killed text_sha256 retry_after_s days_left
        turn route history_items reserved_usd spent_usd actual_usd requests deleg_s scope err_code daily_usd turn_usd
        transport plugin provider tailnet tls_source hours bind_tailnet_only
        """),
    **_reg(SECRET, """
        token access_token refresh_token id_token id_token_hint api_key apikey secret secrets client_secret
        password passcode cookie cookies authorization auth code_verifier verifier nonce credentials
        device_store bridge_store session_store dpapi
        approval_id approval_key admin_token bridge_token store_key
        """),
    **_reg(CONTENT, """
        text transcript transcription query q search match phrase utterance prompt instructions input output
        content contents message messages body subject snippet note notes memory memories fact facts title artist
        lyrics summary arguments args tool_args delta audio pcm wav detail hint why error provider_error exc_text
        result say value lib_text conversation page_mentions_query alert task timer list proposal spec host user_path
        unparsed label question answer reply url
        """),
    **_reg(PII, """
        email login_hint name full_name phone address location sub subject_id recipient recipients cc bcc sender
        to frm profile contact chat user username account mic speaker
        """),
}
_HEADER = ("ts", "level", "component", "code", "msg", "cid", "session", "v")
# ...and any field name containing one of these fragments is a credential, whatever the registry says.
_SENSITIVE_FRAGMENTS = ("token", "secret", "password", "cookie", "api_key", "apikey", "credential", "auth")
_MAX_DEPTH = 8


def is_credential(key: object) -> bool:
    k = str(key).lower()
    return FIELD_CLASS.get(k) == SECRET or any(f in k for f in _SENSITIVE_FRAGMENTS)


def field_class(key: object, default: str = CONTENT) -> str:
    """The class of a field name: secret by name fragment first, then the registry, else `default`."""
    k = str(key).lower()
    if is_credential(k):
        return SECRET
    return FIELD_CLASS.get(k, default)


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


FULL_TEXT_HOURS = 24                  # "full text on request" (owner decision OD-12, 2026-10-21): never longer
_clock = time.time                    # tests move this clock
_until = 0.0                          # end of the current full-text window (0 = none)


def full_text_until() -> float:
    """End (epoch seconds) of a running "full text on request" window, else 0.0. Applies the expiry first."""
    _poll_privacy()
    return _until if _until and not privacy() else 0.0


def _expire_full_text() -> None:
    """The window is over: privacy back on for every process (flag file), then one fixed-literal line."""
    global _until
    _until = 0.0
    if _flag is not None:
        write_privacy(_flag.parent, True)
    info("privacy", "PRIVACY_FULL_TEXT_EXPIRED", "the 24 hour full text window ended, privacy is on again")


def _poll_privacy(force: bool = False) -> None:
    global _flag_mtime, _flag_checked, _runtime, _until
    if _flag is None:
        return
    if _until and _clock() >= _until:
        _expire_full_text()
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
        d = json.loads(_flag.read_text(encoding="utf-8"))
        v = d.get("privacy")
    except (OSError, ValueError, AttributeError):
        return
    if isinstance(v, bool):
        u = d.get("until")
        _until = float(u) if v is False and isinstance(u, (int, float)) and not isinstance(u, bool) else 0.0
        set_privacy(v)
        _runtime = v
        if _until and _clock() >= _until:
            _expire_full_text()


def watch_privacy(log_dir: Path) -> None:
    """Follow the runtime switch in <log_dir>/privacy.json from now on (called by setup())."""
    global _flag, _flag_mtime, _runtime, _until
    _flag, _runtime, _until = Path(log_dir) / PRIVACY_FILE, None, 0.0
    try:
        _flag_mtime = _flag.stat().st_mtime         # baseline: only LATER switches apply
    except OSError:
        _flag_mtime = None


def write_privacy(log_dir: Path, on: bool, until: float | None = None) -> None:
    """Flip privacy now for this process and (via the flag file) for every process logging into log_dir.
    `until` (only with on=False) = the full-text window: privacy returns by itself at that time."""
    global _flag_mtime, _runtime, _until
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    tmp = log_dir / (PRIVACY_FILE + ".tmp")
    rec = {"privacy": bool(on), "at": time.time()}
    if until and not on:
        rec["until"] = float(until)
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    os.replace(tmp, log_dir / PRIVACY_FILE)
    _until = float(until) if until and not on else 0.0
    set_privacy(on)
    _runtime = bool(on)
    if _flag is not None and _flag == log_dir / PRIVACY_FILE:
        try:
            _flag_mtime = _flag.stat().st_mtime       # our own write: already applied
        except OSError:
            pass


PRIVACY_OFF_NOTE = "Privacy: off — conversation text is kept in logs"
PRIVACY_OFF_NOTE_HE = "פרטיות: כבויה — טקסט השיחות נשמר ביומנים"


def privacy_note(lang: str = "en") -> str:
    """The plain-language state shown by `jarvis status` and the dashboard ("" when privacy is on)."""
    if privacy():
        return ""
    return PRIVACY_OFF_NOTE_HE if str(lang).startswith("he") else PRIVACY_OFF_NOTE


def is_sensitive(key: object, private: bool | None = None) -> bool:
    """Should this field's value be masked? Credentials always; content and pii only in privacy mode."""
    c = field_class(key, default=SAFE)
    if c == SECRET:
        return True
    return (privacy() if private is None else private) and c in (CONTENT, PII)


def _mask(v: object) -> str:
    return f"<{len(v) if isinstance(v, (str, bytes, list, tuple, dict)) else len(str(v))} chars>"


def scrub(value: object, depth: int = 0, private: bool | None = None, parent: str = SAFE) -> object:
    """Recursively mask values under sensitive keys (any depth) by their length; redact secrets in strings.
    `private` overrides the current privacy mode (e.g. True for a support bundle meant for sharing). Nested keys
    that are not in the registry inherit the class of their parent field (`parent`)."""
    priv = privacy() if private is None else private
    if depth > _MAX_DEPTH:
        return "<nested>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            c = field_class(k, default=parent)
            if v is not None and (c == SECRET or (priv and c in (CONTENT, PII))):
                out[str(k)] = _mask(v)
            else:
                out[str(k)] = scrub(v, depth + 1, private, c)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [scrub(v, depth + 1, private, parent) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        s = redact(value)
        return redact_pii(s) if priv else s
    if value is None or isinstance(value, (bool, int, float)):
        return value
    s = redact(str(value))
    return redact_pii(s) if priv else s


def render_field(key: object, value: object, private: bool | None = None) -> object:
    """A top-level log field as it is written to the file / console / history store."""
    priv = privacy() if private is None else private
    c = field_class(key)
    if isinstance(value, str) and _MASKED.match(value):
        return value                    # already masked (re-masking an old line keeps its length)
    if c == CONTENT and _number(value) and str(key).lower() not in FIELD_CLASS:
        return value                    # an unregistered NUMBER (a counter from numeric_fields) holds no text
    if c in (CONTENT, PII) and isinstance(value, bool):
        return value                    # a yes/no flag holds no text
    if value is not None and (c == SECRET or (priv and c in (CONTENT, PII))):
        return _mask(value)
    return scrub(value, private=priv, parent=c)


def _number(v: object) -> bool:
    return isinstance(v, (int, float))          # (bool is an int)


def numeric_fields(d: object, suffixes: tuple[str, ...] = ()) -> dict:
    """Counters / timings of a stats dict as log fields: only int/float/bool values (never text), optionally only
    keys ending with one of `suffixes`. Their names need no registry entry (numbers carry no content)."""
    if not isinstance(d, dict):
        return {}
    return {str(k): v for k, v in d.items() if _number(v) and (not suffixes or str(k).endswith(suffixes))
            and field_class(k, default=SAFE) == SAFE and str(k) not in _HEADER}


def private_record(d: dict) -> dict:
    """An already-written log record re-masked for privacy mode (privacy.mask_logs, mask_history): content and
    pii fields masked, an unregistered field treated as content, an older (pre-v2) free-text msg masked."""
    out: dict = {}
    v2 = d.get("v") == FORMAT_VERSION
    for k, v in d.items():
        if k == "msg":
            out[k] = v if v2 or not isinstance(v, str) or _MASKED.match(v) else _mask(v)
        elif k in _HEADER:
            out[k] = v
        elif k == "exc":
            out[k] = v if isinstance(v, str) and _MASKED.match(v) else _mask(v)
        else:
            out[k] = render_field(k, v, private=True)
    return out


def safe_fields(payload: object, allow: tuple[str, ...] | set[str] | frozenset[str] = ()) -> dict:
    """Only allow-listed top-level keys with scalar values (bool/int/float, short non-sensitive strings).

    For logging device/link payloads: everything else is reported only by key name, never by value.
    Usage: logs.info("device", "DEVICE_EVENT", "...", **logs.safe_fields(p, allow=("id", "muted")))
    Keys that are not registered as `safe` are dropped (named in omitted_keys).
    """
    if not isinstance(payload, dict):
        return {}
    out: dict = {}
    for k, v in payload.items():
        if k not in allow or field_class(k) != SAFE:
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


def error_fields(e: BaseException | object, text: object = None, code: object = None) -> dict:
    """An exception or provider error as classified fields: its type and code are safe, its text is content
    (provider_error: masked in privacy mode, never in a bundle)."""
    out: dict = {"error_type": type(e).__name__ if isinstance(e, BaseException) else str(e)[:64]}
    t = str(e) if text is None and isinstance(e, BaseException) else text
    if t:
        out["provider_error"] = str(t)[:300]
    c = code if code is not None else getattr(e, "code", None)
    if isinstance(c, (str, int)) and str(c):
        out["error_code"] = str(c)[:64]
    return out


def new_cid() -> str:
    cid = uuid.uuid4().hex[:10]
    _cid.set(cid)
    return cid


def set_cid(cid: str) -> None:
    _cid.set(cid)


def get_cid() -> str:
    return _cid.get()


_PKG = Path(__file__).resolve().parent


def frames(tb) -> list[str]:
    """Stack frames as "module/file.py:line in func" (no local values, no absolute paths)."""
    out = []
    for fs in traceback.extract_tb(tb)[-12:]:
        p = Path(fs.filename)
        try:
            rel = p.resolve().relative_to(_PKG.parent).as_posix()
        except (ValueError, OSError):
            rel = "/".join(p.parts[-2:])
        out.append(redact(f"{rel}:{fs.lineno} in {fs.name}"))
    return out


def exc_fields(exc_info) -> dict:
    """exc_info -> {exc_type (safe), exc_frames (safe), exc_text (content)}."""
    et, ev, tb = exc_info
    if et is None:
        return {}
    return {"exc_type": getattr(et, "__name__", str(et)), "exc_frames": frames(tb),
            "exc_text": "".join(traceback.format_exception_only(et, ev)).strip()[-500:]}


def record_dict(r: logging.LogRecord, private: bool | None = None) -> dict:
    """One record as the classified dict written to jarves.log (and the history sink)."""
    templ = getattr(r, "template", False)
    msg = r.msg if templ and isinstance(r.msg, str) else r.getMessage()
    d = {"ts": datetime.fromtimestamp(r.created, timezone.utc).isoformat(timespec="milliseconds"),
         "level": r.levelname, "component": getattr(r, "component", r.name), "code": getattr(r, "code", "-"),
         "msg": redact(msg) if templ else "third-party library message",
         "cid": getattr(r, "cid", None) or _cid.get(), "session": _SESSION, "v": FORMAT_VERSION}
    fields = dict(getattr(r, "fields", None) or {})
    if not templ:                       # free text from another logger: the message is content
        fields.setdefault("lib", r.name)
        fields["lib_text"] = redact(msg)[:1000]
    if r.exc_info:
        fields.update(exc_fields(r.exc_info))
    for k, v in fields.items():
        if k in _HEADER and k != "session":     # a field never replaces the header (curator: session = its own)
            k = f"field_{k}"
        d[k] = render_field(k, v, private)
    return d


class JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        return redact(json.dumps(record_dict(r), ensure_ascii=False, default=str))


class ConsoleFormatter(logging.Formatter):
    """`19:20:01 INFO    voice   LIVE_STARTED   GPT-Live session started   score=0.9` (same rules as the file)."""
    COLORS = {"ERROR": "31", "WARNING": "33", "INFO": "32", "DEBUG": "36", "VERBOSE": "90"}

    def __init__(self, color: bool) -> None:
        super().__init__()
        self.color = color

    def format(self, r: logging.LogRecord) -> str:
        d = record_dict(r)
        extra = [f"{k}={json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v}"
                 for k, v in d.items() if k not in _HEADER and k != "exc_frames"]
        lvl = f"{r.levelname:<7}"
        comp = d["component"]
        if self.color:
            lvl = f"\x1b[{self.COLORS.get(r.levelname, '0')}m{lvl}\x1b[0m"
            comp_s = f"\x1b[1m{comp:<8}\x1b[0m"
        else:
            comp_s = f"{comp:<8}"
        line = (f"{datetime.fromtimestamp(r.created).strftime('%H:%M:%S')} {lvl} {comp_s} "
                f"{d['code']:<22} {d['msg']}" + ("   " + " ".join(extra) if extra else ""))
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


# Third-party libraries (httpx, openai, urllib3, websockets, playwright...) log through the root logger with free
# text (URLs with query tokens, server messages). Their WARNING+ records are routed into jarves.log as
# LIB_LOG with the text as `lib_text` (content: masked in privacy mode); nothing reaches stderr unformatted.
THIRD_PARTY = ("httpx", "httpcore", "openai", "urllib3", "websockets", "playwright", "asyncio", "PIL", "comtypes")


class _ThirdParty(logging.Handler):
    def emit(self, r: logging.LogRecord) -> None:
        if r.name == "jarves" or r.name.startswith("jarves."):
            return
        try:
            _logger.handle(_lib_record(r))
        except Exception:
            pass


_PROBE_TEXTS = ("did not receive a valid HTTP request", "opening handshake failed", "connection closed while reading HTTP request")


def _lib_record(r: logging.LogRecord) -> logging.LogRecord:
    if r.name.startswith("websockets") and any(t in r.getMessage() or t in repr(r.exc_info and r.exc_info[1])
                                               for t in _PROBE_TEXTS):
        # V4-LG-1: a plain TCP probe of the link port (single-instance check) is not an error: fixed text, INFO
        r = logging.LogRecord(r.name, logging.INFO, r.pathname, r.lineno,
                              "a client connected to the link port without a WebSocket handshake", None, None)
    n = logging.LogRecord("jarves", r.levelno, r.pathname, r.lineno, r.getMessage(), None, r.exc_info)
    n.created, n.component, n.code, n.template = r.created, f"lib:{r.name.split('.')[0]}"[:24], "LIB_LOG", False
    n.fields, n.name = {"lib": r.name}, r.name
    return n


_third_party = _ThirdParty(logging.WARNING)


def capture_third_party() -> None:
    root = logging.getLogger()
    if _third_party not in root.handlers:
        root.addHandler(_third_party)
    if root.level > logging.WARNING or root.level == logging.NOTSET:
        root.setLevel(logging.WARNING)
    for name in THIRD_PARTY:
        lg = logging.getLogger(name)
        if lg.level == logging.NOTSET or lg.level < logging.WARNING:
            lg.setLevel(logging.WARNING)      # httpx INFO would log every URL


# Crash output: unhandled exceptions (main thread, other threads, asyncio) are logged as CRASH records with
# frames only (exc_text is content); faulthandler (Core only) writes native-crash frames to logs/crash.txt.
_hooks_installed = False


def _crash(kind: str, exc_info) -> None:
    try:
        log("core", "CRASH", "unhandled exception", logging.ERROR, crash_kind=kind, **exc_fields(exc_info))
    except Exception:
        pass


def install_crash_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    prev = sys.excepthook

    def hook(et, ev, tb):
        _crash("main", (et, ev, tb))
        if prev is not sys.__excepthook__ and prev is not hook:
            prev(et, ev, tb)
        else:                                   # stderr: type and frames only (never the exception text)
            sys.stderr.write(f"JARVES crashed: {getattr(et, '__name__', et)} (details in jarves.log)\n")

    sys.excepthook = hook

    def thook(a):
        if a.exc_type is not SystemExit:
            _crash("thread", (a.exc_type, a.exc_value, a.exc_traceback))

    threading.excepthook = thook


def asyncio_handler(loop, context: dict) -> None:
    """loop.set_exception_handler(logs.asyncio_handler): the context message is free text (content)."""
    e = context.get("exception")
    f: dict = {"crash_kind": "asyncio", "detail": str(context.get("message") or "")[:300]}
    if isinstance(e, BaseException):
        f.update(exc_fields((type(e), e, e.__traceback__)))
    log("core", "CRASH", "unhandled exception in a background task", logging.ERROR, **f)


CRASH_MAX_BYTES = 1_000_000


def enable_faulthandler(log_dir: Path):
    """Native crashes (segfaults, hangs dumped on demand): frames only, into logs/crash.txt (Core process)."""
    import faulthandler
    try:
        p = Path(log_dir) / "crash.txt"
        if p.exists() and p.stat().st_size > CRASH_MAX_BYTES:           # retention: one older file is kept
            os.replace(p, p.with_name("crash.txt.1"))
        fh = open(p, "a", encoding="utf-8")   # noqa: SIM115 (kept open on purpose)
        faulthandler.enable(fh, all_threads=True)
        return fh
    except OSError:
        return None


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
            os.system("")                      # enable ANSI colours in the Windows console
        ch.setFormatter(ConsoleFormatter(color))
        _console_base = level_no(console_level or level)
        _console = ModuleFilter(_console_base, only, hide)
        ch.addFilter(_console)
        _logger.addHandler(ch)
    _logger.setLevel(_logger_level(min(_base_level, _console_base if console else _base_level)))
    _logger.propagate = False
    capture_third_party()
    install_crash_hooks()
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
    """`msg` is a fixed description (a literal; the lint test checks every call); variable parts are `fields`."""
    global _debug_until
    if _debug_until and time.time() > _debug_until:
        _debug_until = 0.0
        for h in _logger.handlers:
            if isinstance(h, logging.handlers.RotatingFileHandler):
                h.setLevel(_base_level)
        _logger.setLevel(_logger_level(min(_base_level, _console_base if _console else _base_level)))
    if not _logger.isEnabledFor(level):
        return
    # msg is the record's msg with no args: never %-formatted, so a "%" in it is harmless
    _logger.log(level, msg, exc_info=exc,
                extra={"component": component, "code": code, "fields": fields, "template": True})


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


# ---------------------------------------------------------------------------------------------- support bundles
_TOKEN_LIKE = re.compile(r"^[A-Za-z0-9_.:+\-/\\<>=,@ ]{0,96}$")
_MASKED = re.compile(r"^<\d+ (?:chars|bytes)>$")   # codes, states, model ids, numbers, ISO times


def pii_hash(value: object, salt: bytes) -> str:
    """A stable per-bundle pseudonym (HMAC with a random per-bundle salt: equal values correlate inside one
    bundle, and the value cannot be brute-forced from the bundle alone)."""
    s = unicodedata.normalize("NFC", " ".join(str(value).split()).casefold())
    return "pii:" + hmac.new(salt, s.encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def _bundle_value(v: object, cls: str, salt: bytes, depth: int) -> object:
    if depth > _MAX_DEPTH:
        return "<nested>"
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str) and _MASKED.match(v):
        return v                                # masked when written (privacy mode): the length stays
    if cls == SECRET:
        return "<secret>"
    if cls == CONTENT:
        return _mask(v) if not isinstance(v, (int, float)) else "<content>"
    if cls == "number":                         # unregistered top-level key with a numeric value
        return v if _number(v) else _mask(v)
    if cls == PII:
        if isinstance(v, (list, tuple)):
            return [pii_hash(x, salt) for x in v][:50]
        return pii_hash(v, salt)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, dict):
        return {str(k): _bundle_value(x, field_class(k, default=cls), salt, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_bundle_value(x, cls, salt, depth + 1) for x in v]
    s = redact_pii(redact(str(v)))
    # "safe" strings are kept only when they look like identifiers (codes, ids, states, numbers, hosts);
    # anything sentence-like or non-ASCII (Hebrew text) is reduced to its length - belt and braces.
    return s if _TOKEN_LIKE.match(s) and s.count(" ") <= 2 else _mask(s)


def bundle_record(d: dict, salt: bytes) -> dict:
    """One parsed log record for a support bundle, whatever log.privacy was when it was written: content is
    stripped (length only), pii hashed, secrets dropped, stack-trace text dropped (frames kept). The `msg` of a
    v2 record is a developer template and is kept; older records' free-text msg is reduced to its length."""
    out: dict = {}
    v2 = d.get("v") == FORMAT_VERSION
    for k, v in d.items():
        k = str(k)
        if k == "msg":
            out[k] = redact(str(v)) if v2 and isinstance(v, str) and len(v) <= 200 else _mask(v)
        elif k in ("ts", "level", "component", "code", "cid", "session", "v"):
            out[k] = _bundle_value(v, SAFE, salt, 0)
        elif k == "exc":                      # pre-v2 lines: the last exception line (free text)
            out[k] = _mask(v)
        else:
            c = field_class(k)
            if c == CONTENT and _number(v) and k.lower() not in FIELD_CLASS:
                c = "number"
            out[k] = _bundle_value(v, c, salt, 0)
    return out
