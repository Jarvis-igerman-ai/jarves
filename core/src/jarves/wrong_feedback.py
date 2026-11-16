"""JAR-230: 'Jarvis, that was wrong' feedback (beta, OFF by default).

Saying "Jarvis, that was wrong" (voice or typed), pressing the button on the dashboard Feedback page or running
`jarvis feedback wrong` saves the LAST EXCHANGE (what the owner said, what JARVES answered, the tool names used) as one
REDACTED local feedback case in `<home>/feedback_wrong.jsonl`. A case has the shape of a conversation-eval scenario
(lang, say, reply, tools) so the factory can turn it into a regression test or a bug ticket. Nothing is sent anywhere.

Redaction, before anything is written: credentials and profile paths (logs.redact), e-mails and phone numbers
(logs.redact_pii); a turn that looks sensitive (password, PIN, card, ID, long digit runs: curator._SENSITIVE) is
replaced by its length only; every text is capped. With log privacy on (logs.privacy(), the flag the history store
masks with) the history holds lengths only, and so does the case: the note too is kept as its length. The file is capped at MAX_CASES cases; `jarvis feedback delete ID` removes one.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from . import features as feat, logs
from .curator import _SENSITIVE
from .drive_mode import _file_lock          # the existing cross-process lock (flock / msvcrt), fails closed
from .skills import Skill, Tool, ToolResult

FEATURE = "wrong_feedback"
MAX_CASES = 200
MAX_CHARS = 600
MAX_TOOLS = 5
LOOKBACK_TURNS = 30
FEEDBACK_MAX_CHARS = 60           # a longer turn with "לא נכון" is a real question, not the feedback phrase
VIAS = ("voice", "dashboard", "cli")
_LOCK = threading.Lock()
_ID = re.compile(r"FB-[0-9a-f]{8}")
_SAFE_TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,39}")
_MASKED = re.compile(r"^<(sensitive, )?\d+ chars>$")     # privacy-mode length or a masked sensitive turn
_LENGTH_ONLY = re.compile(r"^<\d+ chars>$")
_HEBREW = re.compile(r"[֐-׿]")
# The feedback phrase itself: offers the voice tool (lazy) and is skipped when finding the exchange it is about.
WRONG = (r"that('s| was| is)? (wrong|incorrect|not right|not correct)|wrong answer|you('re| are| were) wrong|"
         r"לא נכון|טעית|תשובה (שגויה|לא נכונה)|זה שגוי|זה היה שגוי")
_WRONG = re.compile(WRONG, re.IGNORECASE)
_LEAD = re.compile(r"^(?:(?:no|nope|לא)\W+)?(?:(?:זה|זו)\s+(?:היה|הייתה|היתה)?\s*)?", re.IGNORECASE)   # "no, " / "זה היה "
_NAME = re.compile(r"^\W*(?:(?:hey|ok|okay)\s+)?(?:jarvis|jarves|ג['׳]?(?:א)?רו?ו?יס|ג'ארביס)\W*", re.IGNORECASE)
ABOUT = ("'Jarvis, that was wrong' (voice, dashboard Feedback page or jarvis feedback wrong) saves the last exchange, "
         "redacted, as a local feedback case the factory can turn into a regression test; nothing leaves the PC")

feat.register(FEATURE, "משוב: זה היה לא נכון", "Feedback: that was wrong", False, stage="beta", about=ABOUT,
              skills=["wrong_feedback"],
              targets={"windows": {"status": "implemented_unverified",
                                   "blocker": "unit tests only (test_wrong_feedback.py); not run on the owner's PC"},
                       "linux_arm64_pi": {"status": "implemented_unverified",
                                          "blocker": "unit tests only; not run on a Pi",
                                          "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3"}},
              permissions=["local file only; nothing leaves the PC"], settings="jarvis feedback wrong")


def path(home) -> Path:
    return Path(home) / "feedback_wrong.jsonl"


def _now() -> datetime:                     # the one clock; tests replace it
    return datetime.now()


# ------------------------------------------------------------------------------------------ redaction
def redact(text: object, limit: int = MAX_CHARS) -> str:
    """A turn as it may be kept: secrets, paths, e-mails and phones masked; a sensitive turn becomes its length."""
    s = str(text or "").strip()
    if not s or _LENGTH_ONLY.match(s):
        return s
    if _SENSITIVE.search(s):
        return f"<sensitive, {len(s)} chars>"
    s = logs.redact_pii(logs.redact(s))
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ------------------------------------------------------------------------------------------ the last exchange
def _open_store(home, store=None):
    """The Core passes its own history store; the CLI and dashboard open store.db READ-ONLY (sqlite mode=ro: no
    schema, migration or pragma write on a DB the Core holds open, and never created)."""
    if store is not None:
        return store, False
    db = Path(home) / "store.db"
    if not db.exists():
        return None, False
    return _ReadOnly(db), True


class _ReadOnly:
    def __init__(self, db: Path) -> None:
        self._db = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        self._db.row_factory = sqlite3.Row

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._db.execute(sql, args)]

    def close(self) -> None:
        self._db.close()


def is_feedback_turn(text) -> bool:
    """A short feedback phrase ("Jarvis, that was wrong"), not a question that merely contains "לא נכון"."""
    t = str(text or "").strip()
    rest = _NAME.sub("", t, count=1)
    if _WRONG.match(rest) or _WRONG.match(_LEAD.sub("", rest, count=1)):   # STARTS with the phrase: any length (R3)
        return True
    return bool(_WRONG.search(t)) and len(t) <= FEEDBACK_MAX_CHARS and not t.endswith("?")


def last_exchange(home, *, store=None) -> dict | None:
    """The newest user turn and JARVES's answer to it, skipping the "that was wrong" turn itself (and anything said
    after it). None when the history is empty or off. Returned texts are RAW: callers redact (make_case does)."""
    st, owned = _open_store(home, store)
    if st is None:
        return None
    try:
        rows = st.rows("SELECT session_id, ts, role, text FROM turns ORDER BY ts DESC, id DESC LIMIT ?",
                       (LOOKBACK_TURNS,))
        # Skip the feedback phrase only when it is the NEWEST user turn (only replies follow it): an older "that was
        # wrong" or an everyday "לא נכון" inside an earlier question never moves the cut (review R1).
        i = next((k for k, r in enumerate(rows) if r["role"] == "user"), None)
        if i is not None and is_feedback_turn(rows[i]["text"]):
            rows = rows[i + 1:]
        reply: list[dict] = []
        for i, r in enumerate(rows):
            if r["role"] == "assistant":
                reply.append(r)
            elif r["role"] == "user" and reply:
                tools = st.rows("SELECT name FROM tool_calls WHERE session_id = ? AND ts >= ? AND ts <= ? ORDER BY ts, id",
                                (r["session_id"], r["ts"], reply[0]["ts"]))
                return {"say": r["text"] or "", "reply": " ".join((x["text"] or "") for x in reversed(reply)).strip(),
                        "ts": reply[0]["ts"], "tools": [t["name"] for t in tools]}
        return None
    except sqlite3.Error:
        logs.warn("wrong_feedback", "FEEDBACK_HISTORY_UNREADABLE", "the conversation history could not be read")
        return None
    finally:
        if owned:
            st.close()


def make_case(ex: dict, *, via: str, note: str = "", now: datetime | None = None, privacy: bool | None = None) -> dict:
    """privacy None = logs.privacy(): on, the note (model- or owner-written, may quote the turn) keeps its length only."""
    if via not in VIAS:
        raise ValueError(f"via must be one of {VIAS}")
    note = str(note or "").strip()
    if note and (logs.privacy() if privacy is None else privacy):
        note = f"<{len(note)} chars>"
    say, reply = redact(ex.get("say")), redact(ex.get("reply"))
    tools = []
    for t in ex.get("tools") or []:
        t = t if _SAFE_TOOL.fullmatch(str(t)) else "other"
        if t not in tools:
            tools.append(t)
    return {"id": "FB-" + secrets.token_hex(4), "saved": (now or _now()).isoformat(timespec="seconds"), "via": via,
            "lang": "he" if _HEBREW.search(str(ex.get("say") or "") + str(ex.get("reply") or "")) else "en",
            "say": say, "reply": reply, "tools": tools[:MAX_TOOLS], "note": redact(note, 300),
            "text_kept": not (_MASKED.match(say) and _MASKED.match(reply))}


# ------------------------------------------------------------------------------------------ the local file
def _read(p: Path) -> list[dict]:
    out = []
    for line in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            out.append(r)                       # a hand-edited or corrupt row is skipped, never a crash
    return out


def _write(p: Path, rows: list[dict]) -> None:
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, p)                          # atomic: a crash never leaves a truncated file


def save(home, case: dict) -> dict:
    p = path(home)
    with _LOCK, _file_lock(p):                  # one writer at a time, across threads and processes
        _write(p, (_read(p) + [case])[-MAX_CASES:])
    return case


def cases(home) -> list[dict]:
    p = path(home)
    if not p.exists():
        return []
    with _LOCK, _file_lock(p):                  # never read while a writer replaces the file (Windows os.replace)
        return _read(p)


def get(home, cid: str) -> dict | None:
    return next((c for c in cases(home) if c["id"] == cid), None)


def delete(home, cid: str) -> bool:
    p = path(home)
    with _LOCK, _file_lock(p):
        rows = _read(p)
        keep = [r for r in rows if r["id"] != cid]
        if len(keep) == len(rows):
            return False
        _write(p, keep)
    return True


def report_wrong(home, *, via: str, note: str = "", store=None, failure_log_on: bool = False,
                 now: datetime | None = None, privacy: bool | None = None) -> dict:
    """The one entry point (voice, dashboard, CLI). {"ok", "code", "case"?}. Also counts a `user_correction` in the
    JAR-229 failure log when that switch is on (metadata only)."""
    ex = last_exchange(home, store=store)
    if ex is None:
        return {"ok": False, "code": "FEEDBACK_NO_EXCHANGE"}
    case = save(home, make_case(ex, via=via, note=note, now=now, privacy=privacy))
    try:
        from . import failure_log
        failure_log.record(home, "user_correction", on=failure_log_on, now=now)
    except Exception:
        logs.warn("wrong_feedback", "FEEDBACK_FAILURE_LOG_FAILED", "could not count the correction in the failure log")
    logs.info("wrong_feedback", "FEEDBACK_SAVED", "a 'that was wrong' case was saved", item=case["id"], via=via)
    return {"ok": True, "code": "FEEDBACK_SAVED", "case": case}


def say(r: dict, lang: str) -> str:
    he = lang == "he"
    if r.get("ok"):
        cid = r["case"]["id"]
        return (f"תודה, שמרתי את השיחה האחרונה כמשוב ({cid}). שום דבר לא נשלח." if he else
                f"Thanks, I saved the last exchange as feedback ({cid}). Nothing was sent.")
    return ("לא מצאתי שיחה אחרונה לשמור. אולי היסטוריית השיחות כבויה." if he else
            "I found no last exchange to save. The conversation history may be off.")


# ------------------------------------------------------------------------------------------ voice / typed tool
class WrongArgs(BaseModel):
    note: str = Field("", max_length=300, description="what was wrong, in the owner's words (optional)")


class WrongFeedbackSkill(Skill):
    name = "wrong_feedback"
    description = "Save the last exchange as 'that was wrong' feedback (local, redacted)"

    def __init__(self, home, store_fn=lambda: None, lang_fn=lambda: "he", failure_log_on=lambda: False) -> None:
        self.home, self.store_fn, self.lang_fn, self.failure_log_on = Path(home), store_fn, lang_fn, failure_log_on

    def tools(self) -> list[Tool]:
        async def run(a: WrongArgs) -> ToolResult:
            import asyncio
            r = await asyncio.to_thread(report_wrong, self.home, via="voice", note=a.note, store=self.store_fn(),
                                        failure_log_on=bool(self.failure_log_on()))
            return ToolResult(r["ok"], say(r, self.lang_fn()), {"id": r["case"]["id"]} if r["ok"] else {},
                              code="ok" if r["ok"] else r["code"])
        return [Tool("report_wrong_answer", "The owner says your LAST answer was wrong ('that was wrong', 'זה היה לא "
                     "נכון', 'טעית'): save it as local feedback. Then apologise briefly.", run, WrongArgs,
                     permission="action", lazy=WRONG)]


def attach(core):
    """Register the lazy tool on the Core (Core.start). The feature switch gates it (skills=[...] above)."""
    def store():
        sink = getattr(core, "history", None)
        return getattr(sink, "store", None)
    core.registry.register(WrongFeedbackSkill(
        core.home, store, lambda: "he" if getattr(core.cfg.voice, "language", "he") == "he" else "en",
        lambda: core.features.active("failure_log")))
    core.registry.allowed_actions.add("report_wrong_answer")


# ------------------------------------------------------------------------------------------ dashboard
def _local(ctx):
    who = getattr(ctx, "principal", None)             # missing principal = refused (fail closed, review R2)
    if not (isinstance(who, dict) and who.get("kind") == "local"):
        return 403, {"error": "feedback cases are shown only on this computer"}
    return None


def _gate(ctx):
    refused = _local(ctx)
    if refused:
        return refused
    fs = feat._ctx_features(ctx)
    if not fs.active(FEATURE):
        return 409, {"code": "FEEDBACK_OFF", "error": "feedback is off (beta): turn on beta features, then wrong_feedback"}
    return None


def api_list(ctx, m, body):
    refused = _local(ctx)
    if refused:
        return refused
    active = bool(feat._ctx_features(ctx).active(FEATURE))
    return 200, {"active": active, "cases": list(reversed(cases(ctx.home))) if active else []}


def api_wrong(ctx, m, body):
    refused = _gate(ctx)
    if refused:
        return refused
    r = report_wrong(ctx.home, via="dashboard", note=str((body or {}).get("note") or ""),
                     failure_log_on=bool(feat._ctx_features(ctx).active("failure_log")))
    return (200, r) if r["ok"] else (404, {**r, "error": r["code"]})


def api_delete(ctx, m, body):
    refused = _local(ctx)
    if refused:
        return refused
    return (200, {"ok": True}) if delete(ctx.home, m.group(1)) else (404, {"error": "no such feedback case"})


def dashboard_routes(register) -> None:
    register("GET", r"/api/feedback", api_list)
    register("POST", r"/api/feedback/wrong", api_wrong)
    register("POST", r"/api/feedback/(FB-[0-9a-f]{8})/delete", api_delete)


# ------------------------------------------------------------------------------------------ CLI
def add_cli(sub) -> None:
    p = sub.add_parser("feedback", help="'that was wrong' feedback: wrong [--note T] saves the last exchange (redacted, "
                                        "local) | list | show ID | delete ID")
    p.add_argument("action", nargs="?", default="list", choices=["wrong", "list", "show", "delete"])
    p.add_argument("target", nargs="?", default="", help="show/delete: the case id (FB-...)")
    p.add_argument("--note", default="", help="wrong: what was wrong (optional, redacted)")
    p.add_argument("--json", action="store_true")


def cmd_feedback(a, cfg, home) -> int:
    from . import clilang
    lang = "he" if clilang.current() == "he" else "en"
    fs = feat.Features(cfg, home)
    out = None
    if a.action in ("wrong", "list", "show"):         # the same switch as the dashboard (review R6); delete always works
        if not fs.active(FEATURE):
            print("Feedback is off (beta): jarvis features beta on, then jarvis features on wrong_feedback. / "
                  "המשוב כבוי (בטא).")
            return 2
    if a.action == "wrong":
        r = report_wrong(home, via="cli", note=a.note, failure_log_on=bool(fs.active("failure_log")),
                         privacy=bool(getattr(getattr(cfg, "log", None), "privacy", True)))   # the CLI skips logs.setup
        out = r if a.json else say(r, lang)
        rc = 0 if r["ok"] else 1
    elif a.action == "list":
        rows = list(reversed(cases(home)))
        out = rows if a.json else ("\n".join(f"{c['id']}  {c.get('saved', '')}  {c.get('via', '')}  "
                                             f"{str(c.get('say', ''))[:60]}" for c in rows)
                                   or "No feedback saved. / לא נשמר משוב.")
        rc = 0
    else:
        if not _ID.fullmatch(a.target or ""):
            print("Give a case id like FB-1a2b3c4d. / תנו מזהה כמו FB-1a2b3c4d.")
            return 2
        if a.action == "show":
            c = get(home, a.target)
            out, rc = (c, 0) if c else ("No such feedback case. / אין משוב כזה.", 1)
            if c and not a.json:
                out = json.dumps(c, ensure_ascii=False, indent=1)
        else:
            ok = delete(home, a.target)
            out, rc = ("Deleted. / נמחק." if ok else "No such feedback case. / אין משוב כזה."), (0 if ok else 1)
    print(json.dumps(out, ensure_ascii=False, indent=1) if a.json and not isinstance(out, str) else out)
    return rc


COMMANDS = {"feedback": cmd_feedback}
