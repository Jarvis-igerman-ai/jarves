"""Self-diagnosis slice A (issue #336): a READ-ONLY self-check for one area (anydo, whatsapp, google, voice, github).

`run(area, code, ...) -> DiagReport` collects REDACTED evidence (error code, the SHAPE of the raw response, connection
and permission state, version, the last log codes of the area) and matches code + area to the guide `[[problems]]`
entries (`codes = [...]`). The cause and the next step come from the guide (English + Hebrew, deterministic, no model
call). Optional wording by the online model (`self_diagnosis_ai_wording`) gets the redacted report only.

Contract (tested): nothing is written, no outside network call is made (the WhatsApp collector only reads the local bridge on 127.0.0.1), no token / address / phone number / message text ever
enters the evidence: values are never copied from a response, only key names and type names; log lines are reduced to
level + component + code.
"""
from __future__ import annotations

import json
import platform
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import logs

AREAS = ("anydo", "whatsapp", "google", "voice", "github")
MAX_EVIDENCE = 24
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_PREFIX = (("ANYDO_", "anydo"), ("WA_", "whatsapp"), ("WHATSAPP_", "whatsapp"), ("BRIDGE_", "whatsapp"),
           ("GOOGLE_", "google"), ("GMAIL_", "google"), ("CALENDAR_", "google"), ("CONTACTS_", "google"),
           ("GITHUB_", "github"), ("VOICE_", "voice"), ("LIVE_", "voice"), ("STT_", "voice"), ("WAKE_", "voice"), ("AI_", "voice"))
_TOOL_PREFIX = (("anydo", "anydo"), ("whatsapp", "whatsapp"), ("wa_", "whatsapp"), ("gmail", "google"),
                ("calendar", "google"), ("google", "google"), ("github", "github"))
_LOG_COMPONENT = {"anydo": ("anydo",), "whatsapp": ("whatsapp", "whatsapp_bridge", "wa"), "google": ("google", "gmail", "calendar"),
                  "voice": ("live", "voice", "stt", "wake"), "github": ("github",)}


def area_for(code: str = "", tool: str = "") -> str | None:
    """The area a failure belongs to: by its error code first, then by the tool name. None = not covered."""
    c = str(code or "").upper()
    for p, a in _PREFIX:
        if c.startswith(p):
            return a
    t = str(tool or "").lower()
    for p, a in _TOOL_PREFIX:
        if t.startswith(p):
            return a
    return None


@dataclass
class DiagReport:
    area: str
    code: str
    evidence: list[str] = field(default_factory=list)
    cause_en: str = ""
    cause_he: str = ""
    fix_en: str = ""
    fix_he: str = ""
    guide_id: str = ""
    confidence: str = "low"            # high = the code is in a guide problem entry; low = unknown, try a coding agent
    ai_worded: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def text(self, lang: str = "en") -> str:
        """At most two sentences: the cause, then the next step."""
        he = lang == "he"
        return f"{self.cause_he if he else self.cause_en} {self.fix_he if he else self.fix_en}".strip()


# ------------------------------------------------------------------------------------------ evidence helpers
def shape_of(v: Any, depth: int = 0) -> Any:
    """The SHAPE of a value: key names and type names, never the values (depth-limited, key count-limited)."""
    if isinstance(v, dict):
        if depth >= 2:
            return "dict"
        return {str(k)[:40]: shape_of(x, depth + 1) for k, x in list(v.items())[:20]}
    if isinstance(v, (list, tuple)):
        return [shape_of(v[0], depth + 1)] if v and depth < 2 else "list"
    return type(v).__name__


def _line(key: str, value: Any) -> str:
    return logs.redact_pii(logs.redact(f"{key}: {value}"))[:200]


def _slug(v: Any, n: int) -> str:
    """A log level or component as plain lowercase letters, digits and underscore only (never free text)."""
    return re.sub(r"[^A-Za-z0-9_]", "", str(v or "?"))[:n] or "?"


def _safe_code(v: Any) -> str:
    s = str(v or "").strip()
    return s if _CODE.match(s) else ""


def recent_log_codes(home: Path | None, area: str, limit: int = 20) -> list[str]:
    """The last log CODES of this area as `LEVEL COMPONENT CODE` (no messages, no fields: they may hold content)."""
    if home is None:
        return []
    try:
        from ..logview import log_files
        files = log_files(Path(home))
    except Exception:
        return []
    comps = _LOG_COMPONENT.get(area, ())
    out: list[str] = []
    for f in reversed(files):
        try:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 400_000))
                chunk = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        for ln in reversed(chunk.splitlines()):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            comp, code = str(d.get("component") or "").lower(), _safe_code(d.get("code"))
            if code and (comp in comps or area_for(code) == area):
                out.append(f"{_slug(d.get('level'), 8)} {_slug(comp, 24)} {code}")
                if len(out) >= limit:
                    return out
    return out


def _version() -> str:
    try:
        from .. import __version__
        return __version__
    except Exception:
        return "?"


# ------------------------------------------------------------------------------------------ area collectors
# each returns a list of "key: value" evidence lines; all read-only and local (no HTTP request, no writes)
def _ev_anydo(home: Path | None, cfg) -> list[str]:
    from .. import anydo
    if home is None:
        return [_line("connection", "no data folder")]
    st = anydo.TokenStore(Path(home)).status()
    return [_line("connected", bool(st.get("connected"))), _line("needs_reconnect", bool(st.get("needs_reconnect"))),
            _line("can_write_scope", bool(st.get("can_write"))), _line("scopes_count", len(st.get("scopes") or []))]


def _ev_google(home: Path | None, cfg) -> list[str]:
    from ..google_auth import GoogleAuth
    if home is None:
        return [_line("connection", "no data folder")]
    st = GoogleAuth(Path(home)).status()
    out = [_line(k, bool(st.get(k))) for k in ("connected", "needs_reconnect", "client_configured", "calendar",
                                                 "gmail_read", "gmail_send", "calendar_write", "contacts")]
    if _safe_code(st.get("last_error")):
        out.append(_line("last_error_code", st["last_error"]))
    return out


def _ev_whatsapp(home: Path | None, cfg) -> list[str]:
    """Bridge state from its local status file/endpoint only when it is already running; shape of the answer, no values."""
    from .. import whatsapp_bridge as wb
    if home is None:
        return [_line("bridge", "no data folder")]
    try:
        st = wb.status_or_none(Path(home))     # localhost call to the bridge on this PC (read-only GET /status)
    except Exception:
        st = None
    if st is None:
        return [_line("bridge_reachable", False)]
    out = [_line("bridge_reachable", True), _line("status_shape", json.dumps(shape_of(st), sort_keys=True))]
    out += [_line(k, v) for k, v in st.items() if isinstance(v, bool)][:8]
    return out


def _ev_github(home: Path | None, cfg) -> list[str]:
    """Connection state only (#439): token present yes/no, the scope SHAPE (how many scopes the guide asks for, read-only),
    never the token, a repo name or a response value. Rate-limit state is a live client property, not stored: not read here."""
    from .. import github_client as gc
    if home is None:
        return [_line("connection", "no data folder")]
    st = gc.TokenStore(Path(home)).status()
    return [_line("token_present", bool(st.get("connected"))), _line("read_only", bool(st.get("read_only"))),
            _line("scopes_needed_count", len(st.get("scopes_needed") or []))]


def _ev_voice(home: Path | None, cfg) -> list[str]:
    out: list[str] = []
    if cfg is not None:
        v = getattr(cfg, "voice", None)
        ai = getattr(cfg, "ai", None)
        if v is not None:
            out += [_line("voice_mode", getattr(v, "mode", "?")), _line("voice_activation", getattr(v, "activation", "?"))]
        if ai is not None:
            out.append(_line("ai_connection", getattr(ai, "connection", "?")))
    try:
        from ..config import openai_key_present
        out.append(_line("api_key_present", bool(openai_key_present())))
    except Exception:
        pass
    return out


COLLECTORS: dict[str, Callable[[Path | None, Any], list[str]]] = {
    "anydo": _ev_anydo, "google": _ev_google, "whatsapp": _ev_whatsapp, "voice": _ev_voice, "github": _ev_github}


# ------------------------------------------------------------------------------------------ matching
def match_problem(code: str) -> tuple[dict | None, str]:
    """(guide problem entry, guide id) whose `codes` list holds this error code; (None, "") when none does."""
    from .. import guides
    c = str(code or "").upper()
    for g in guides.load().values():
        for p in g.problems:
            if c and c in [str(x).upper() for x in p.get("codes", [])]:
                return p, g.id
    return None, ""


def _unknown(area: str) -> tuple[str, str, str, str]:
    return (f"I do not recognise this {area} problem.", f"לא זיהיתי את התקלה הזאת ב-{area}.",
            "Hand it to a coding agent, or run jarvis diagnose and share the result.",
            "אפשר להעביר אותה לסוכן קוד, או להריץ jarvis diagnose ולשתף את התוצאה.")


def run(area: str, code: str = "", *, home: Path | None = None, cfg=None, failure_data: dict | None = None) -> DiagReport:
    """The read-only runner. Never raises: a collector that fails becomes one evidence line."""
    area = area if area in AREAS else "voice"
    code = _safe_code(str(code or "").upper()) or "UNKNOWN"
    ev = [_line("area", area), _line("error_code", code), _line("jarves_version", _version()),
          _line("os", platform.system() + " " + platform.release())]
    if failure_data:
        ev.append(_line("response_shape", json.dumps(shape_of(failure_data), sort_keys=True)))
    try:
        ev += COLLECTORS[area](home, cfg)
    except Exception as e:                         # a broken collector never breaks the diagnosis
        ev.append(_line("collector_error", type(e).__name__))
    codes = recent_log_codes(home, area)
    if codes:
        ev.append(_line("last_log_codes", "; ".join(codes)))
    p, gid = match_problem(code)
    if p is not None:
        rep = DiagReport(area, code, [], p["en"], p["he"], p["fix_en"], p["fix_he"], gid, "high")
        rep.cause_en, rep.cause_he = _cause(p["en"], "en"), _cause(p["he"], "he")
    else:
        c_en, c_he, f_en, f_he = _unknown(area)
        rep = DiagReport(area, code, [], c_en, c_he, f_en, f_he, "", "low")
    rep.evidence = [logs.redact_pii(logs.redact(x)) for x in ev][:MAX_EVIDENCE]
    return rep


def _cause(problem_text: str, lang: str) -> str:
    """The guide entry is written as what the owner sees ("He says ..."): present it as the diagnosed symptom."""
    t = str(problem_text).strip().rstrip(".")
    return (f"הבעיה: {t}." if lang == "he" else f"Cause: {t}.")


# ------------------------------------------------------------------------------------------ optional model wording
WORDING_PROMPT = ("Reword this diagnosis for a non-technical owner in {lang}: at most 2 short sentences, the cause then "
                  "the next step. Use ONLY the facts below; do not invent causes. Facts: ")


def evidence_for_model(rep: DiagReport) -> str:
    """The ONLY thing sent to the model: the redacted report (no conversation text, no tokens, no raw responses)."""
    d = logs.redact_deep({"area": rep.area, "code": rep.code, "evidence": rep.evidence, "cause": rep.cause_en,
                          "next_step": rep.fix_en})
    return logs.redact_pii(json.dumps(d, ensure_ascii=False))[:2500]


async def word(rep: DiagReport, lang: str, ask_model: Callable[[str], Awaitable[str]] | None) -> str:
    """Model wording of a report; any failure (offline, budget, empty) returns the deterministic text."""
    plain = rep.text(lang)
    if ask_model is None:
        return plain
    try:
        out = await ask_model(WORDING_PROMPT.format(lang="Hebrew" if lang == "he" else "English") + evidence_for_model(rep))
    except Exception:
        return plain
    out = logs.redact_pii(logs.redact(str(out or "").strip()))
    if not out or len(out) > 600:
        return plain
    rep.ai_worded = True
    return out
