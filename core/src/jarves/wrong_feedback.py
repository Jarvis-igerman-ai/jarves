"""JAR-230: 'Jarvis, that was wrong' feedback (beta, OFF by default). Slice 1: the core and `jarvis feedback`.

`jarvis feedback wrong [--note T]` saves the LAST EXCHANGE (what the owner said, what JARVES answered, tool names) as
one REDACTED case in `<home>/feedback_wrong.jsonl` and counts a `user_correction` in the JAR-229 failure log (the
weekly report) when that switch is on. Nothing is sent anywhere. The voice tool and dashboard page are follow-ups.
Redaction: credentials/paths (logs.redact), e-mails/phones (logs.redact_pii); a sensitive-looking turn is kept as its
length only; texts are capped; with log privacy on, say, reply and note keep their lengths only.
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

from . import features as feat, logs
from .curator import _SENSITIVE
from .drive_mode import _file_lock         # the existing cross-process lock (flock / msvcrt), fails closed

FEATURE = "wrong_feedback"
MAX_CASES, MAX_CHARS, LOOKBACK_TURNS = 200, 600, 30
_LOCK = threading.Lock()
_ID = re.compile(r"FB-[0-9a-f]{8}")
_SAFE_TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,39}")
_HEBREW = re.compile(r"[֐-׿]")
_WRONG = (r"that('s| was| is)? (wrong|incorrect|not right)|wrong answer|you('re| are| were) wrong|"
          r"לא נכון|טעית|תשובה (שגויה|לא נכונה)|זה (היה )?שגוי")
# A report STARTS with the phrase (after the name and "no," / "זה היה") and ends there or at punctuation.
_REPORT = re.compile(rf"^\W*(?:(?:hey\s+)?(?:jarvis|ג['׳]?ארוויס)\W*)?(?:(?:no|לא)\W+)?(?:זה\s+(?:היה\s+)?)?"
                     rf"(?:{_WRONG})\s*(?:$|[.,!;:\-–—])", re.IGNORECASE)
ABOUT = ("'Jarvis, that was wrong' (jarvis feedback wrong) saves the last exchange, redacted, as a local feedback "
         "case the factory can turn into a regression test; nothing leaves the PC")

feat.register(FEATURE, "משוב: זה היה לא נכון", "Feedback: that was wrong", False, stage="beta", about=ABOUT,
              targets={"windows": {"status": "implemented_unverified",
                                   "blocker": "unit tests only (test_wrong_feedback.py); not run on the owner's PC"},
                       "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "unit tests only; not run on a Pi",
                                          "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3"}},
              permissions=["local file only; nothing leaves the PC"], settings="jarvis feedback wrong")


def path(home) -> Path:
    return Path(home) / "feedback_wrong.jsonl"


def redact(text: object, limit: int = MAX_CHARS) -> str:
    s = str(text or "").strip()
    if _SENSITIVE.search(s) and not re.fullmatch(r"<\d+ chars>", s):
        return f"<sensitive, {len(s)} chars>"
    s = logs.redact_pii(logs.redact(s))
    return s if len(s) <= limit else s[:limit - 1] + "…"


def is_report(text) -> bool:
    t = str(text or "").strip()
    return not t.endswith("?") and bool(_REPORT.match(t))


def last_exchange(home) -> dict | None:
    """The newest user turn and the answer to it, from ONE session, skipping every trailing report and its reply
    (review R1). `ts` is when the answer was given (review R2). Raw texts: make_case redacts. None when no history."""
    db = Path(home) / "store.db"
    if not db.exists():
        return None
    try:
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as con:   # read-only
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute("SELECT session_id, ts, role, text FROM turns "
                                                 "ORDER BY ts DESC, id DESC LIMIT ?", (LOOKBACK_TURNS,))]
            if not rows:
                return None
            rows = [r for r in rows if r["session_id"] == rows[0]["session_id"]]
            while (i := next((k for k, r in enumerate(rows) if r["role"] == "user"), None)) is not None \
                    and is_report(rows[i]["text"]):
                rows = rows[i + 1:]
            reply: list[dict] = []
            for r in rows:
                if r["role"] == "assistant":
                    reply.append(r)
                elif r["role"] == "user" and reply:
                    tools = [t[0] for t in con.execute("SELECT name FROM tool_calls WHERE session_id = ? AND ts >= ? "
                                                       "AND ts <= ? ORDER BY ts, id",
                                                       (r["session_id"], r["ts"], reply[0]["ts"]))]
                    return {"say": r["text"] or "", "reply": " ".join(x["text"] or "" for x in reversed(reply)),
                            "ts": reply[0]["ts"], "tools": tools}
            return None
    except sqlite3.Error as e:
        logs.warn("wrong_feedback", "FEEDBACK_HISTORY_UNREADABLE", "the conversation history could not be read",
                  error=type(e).__name__)
        return None


def make_case(ex: dict, *, via: str, note: str = "", now: datetime | None = None, privacy: bool = True) -> dict:
    texts = (ex.get("say"), ex.get("reply"), note)
    say, reply, note = (f"<{len(str(t or '').strip())} chars>" if privacy else redact(t) for t in texts)
    tools = list(dict.fromkeys(t if _SAFE_TOOL.fullmatch(str(t)) else "other" for t in ex.get("tools") or []))[:5]
    try:
        at = datetime.fromtimestamp(float(ex.get("ts"))).isoformat(timespec="minutes")
    except (TypeError, ValueError, OverflowError, OSError):
        at = ""
    return {"id": "FB-" + secrets.token_hex(4), "saved": (now or datetime.now()).isoformat(timespec="seconds"),
            "via": via, "exchange_at": at, "lang": "he" if _HEBREW.search(f"{texts[0]}{texts[1]}") else "en",
            "say": say, "reply": reply, "tools": tools, "note": note[:300]}


def _read(p: Path) -> list[dict]:
    out = []
    for line in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            out.append(r)                   # a hand-edited or corrupt row is skipped, never a crash
    return out


def _write(p: Path, rows: list[dict]) -> None:
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, p)                      # atomic: a crash never leaves a truncated file


def cases(home) -> list[dict]:
    p = path(home)
    with _LOCK, _file_lock(p):
        return _read(p)


def delete(home, cid: str) -> bool:
    p = path(home)
    with _LOCK, _file_lock(p):
        rows = _read(p)
        keep = [r for r in rows if r["id"] != cid]
        if len(keep) < len(rows):
            _write(p, keep)
    return len(keep) < len(rows)


def report_wrong(home, *, via: str, note: str = "", failure_log_on: bool = False, privacy: bool = True) -> dict:
    ex = last_exchange(home)
    if ex is None:
        return {"ok": False, "code": "FEEDBACK_NO_EXCHANGE"}
    case, p = make_case(ex, via=via, note=note, privacy=privacy), path(home)
    with _LOCK, _file_lock(p):              # one writer at a time, across threads and processes
        _write(p, (_read(p) + [case])[-MAX_CASES:])
    from . import failure_log
    failure_log.record(home, "user_correction", on=failure_log_on)
    logs.info("wrong_feedback", "FEEDBACK_SAVED", "a 'that was wrong' case was saved", item=case["id"], via=via)
    return {"ok": True, "code": "FEEDBACK_SAVED", "case": case}


def add_cli(sub) -> None:
    p = sub.add_parser("feedback", help="'that was wrong' feedback: wrong [--note T] saves the last exchange "
                                        "(redacted, local) | list | delete ID")
    p.add_argument("action", nargs="?", default="list", choices=["wrong", "list", "delete"])
    p.add_argument("target", nargs="?", default="", help="delete: the case id (FB-...)")
    p.add_argument("--note", default="", help="wrong: what was wrong (optional, redacted)")
    p.add_argument("--json", action="store_true")


def _line(c: dict) -> str:
    at = str(c.get("exchange_at") or "?").replace("T", " ")
    return f"{c['id']}  exchange from {at}  via {c.get('via', '')}  {str(c.get('say', ''))[:60]}"


def cmd_feedback(a, cfg, home) -> int:
    fs = feat.Features(cfg, home)
    if a.action == "delete":                # works also while off, so a case can always be removed
        ok = bool(_ID.fullmatch(a.target or "")) and delete(home, a.target)
        print("Deleted. / נמחק." if ok else "No such feedback case (FB-...). / אין משוב כזה.")
        return 0 if ok else 1
    if not fs.active(FEATURE):
        print("Feedback is off (beta): jarvis features beta on, then jarvis features on wrong_feedback. / המשוב כבוי.")
        return 2
    if a.action == "list":
        rows = list(reversed(cases(home)))
        print(json.dumps(rows, ensure_ascii=False, indent=1) if a.json else
              "\n".join(map(_line, rows)) or "No feedback saved. / לא נשמר משוב.")
        return 0
    privacy = bool(getattr(getattr(cfg, "log", None), "privacy", True))
    r = report_wrong(home, via="cli", note=a.note, failure_log_on=bool(fs.active("failure_log")), privacy=privacy)
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else
          f"Saved: {_line(r['case'])}. Nothing was sent. / נשמר, שום דבר לא נשלח." if r["ok"] else
          "No last exchange found; is the conversation history on? / לא נמצאה שיחה אחרונה.")
    return 0 if r["ok"] else 1


COMMANDS = {"feedback": cmd_feedback}
