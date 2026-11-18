"""JAR-230 'Jarvis, that was wrong' feedback, slice 1: the core only, not reachable yet (no feature switch, CLI, voice
or dashboard: those are the next slices, which gate it beta/OFF). report_wrong() saves the LAST EXCHANGE (said, answer,
tool names, time) as one REDACTED case in `<home>/feedback_wrong.jsonl` and counts a `user_correction` for the JAR-229
weekly report when that log is on. Nothing is sent. Redaction: logs.redact + redact_pii; a sensitive turn keeps its
length only; with privacy on (the default), say, reply and note keep their lengths only.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path

from . import failure_log, logs
from .curator import _SENSITIVE
from .drive_mode import _file_lock         # the existing cross-process lock (flock / msvcrt), fails closed

MAX_CASES, MAX_CHARS, LOOKBACK_TURNS = 200, 600, 30
_LOCK = threading.Lock()
_SAFE_TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,39}")
_MASKED = re.compile(r"<\d+ chars>")       # a turn the history kept as its length only (privacy mode)
_WRONG = (r"that('s| was| is)? (wrong|incorrect|not right)|wrong answer|you('re| are| were) wrong|"
          r"לא נכון|טעית|תשובה (שגויה|לא נכונה)|זה (היה )?שגוי")
# A report STARTS with the phrase (after the name and "no," / "זה היה") and ends there or at punctuation.
_REPORT = re.compile(rf"^\W*(?:(?:hey\s+)?(?:jarvis|ג['׳]?ארוויס)\W*)?(?:(?:no|לא)\W+)?(?:זה\s+(?:היה\s+)?)?"
                     rf"(?:{_WRONG})\s*(?:$|[.,!;:\-–—])", re.IGNORECASE)
VIAS = ("voice", "dashboard", "cli")


def path(home) -> Path:
    return Path(home) / "feedback_wrong.jsonl"


def redact(text: object, limit: int = MAX_CHARS) -> str:
    s = str(text or "").strip()
    if _SENSITIVE.search(s):
        return f"<sensitive, {len(s)} chars>"
    s = logs.redact_pii(logs.redact(s))
    return s if len(s) <= limit else s[:limit - 1] + "…"


def is_report(text) -> bool:
    t = str(text or "").strip()
    return not t.endswith("?") and bool(_REPORT.match(t))


def last_exchange(home) -> dict | None:
    """The newest user turn and its answer, from ONE session, skipping every trailing report and its reply (review
    R1); `ts` is when the answer was given (review R2). Raw texts: make_case redacts. None when there is no history."""
    db = Path(home) / "store.db"
    if not db.exists():
        return None
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as con:  # read-only
            rows = con.execute("SELECT session_id, ts, role, COALESCE(text, '<' || chars || ' chars>') FROM turns "
                               "ORDER BY ts DESC, id DESC LIMIT ?", (LOOKBACK_TURNS,)).fetchall()
            rows = [r for r in rows if r[0] == rows[0][0]] if rows else []
            while (i := next((k for k, r in enumerate(rows) if r[2] == "user"), -1)) >= 0 and is_report(rows[i][3]):
                rows = rows[i + 1:]
            reply = []
            for sid, ts, role, text in rows:
                if role == "assistant":
                    reply.insert(0, (ts, text))
                elif role == "user" and reply:
                    tools = con.execute("SELECT name FROM tool_calls WHERE session_id = ? AND ts BETWEEN ? AND ? "
                                        "ORDER BY ts, id", (sid, ts, reply[-1][0])).fetchall()
                    return {"say": text, "reply": " ".join(t for _, t in reply), "ts": reply[-1][0],
                            "tools": [t[0] for t in tools]}
            return None
    except sqlite3.Error as e:              # once per owner press, never per turn
        logs.warn("wrong_feedback", "FEEDBACK_HISTORY_UNREADABLE", "the conversation history could not be read",
                  error=type(e).__name__)
        return None


def make_case(ex: dict, *, via: str, note: str = "", now: datetime | None = None, privacy: bool = True) -> dict:
    if via not in VIAS:
        raise ValueError(f"via must be one of {VIAS}")
    def keep(t) -> str:
        t = str(t or "").strip()
        return t if not t or _MASKED.fullmatch(t) else f"<{len(t)} chars>" if privacy else redact(t)
    tools = list(dict.fromkeys(t if _SAFE_TOOL.fullmatch(str(t)) else "other" for t in ex.get("tools") or []))[:5]
    try:
        at = datetime.fromtimestamp(float(ex.get("ts"))).isoformat(timespec="minutes")
    except (TypeError, ValueError, OverflowError, OSError):
        at = ""
    return {"id": "FB-" + secrets.token_hex(4), "saved": (now or datetime.now()).isoformat(timespec="seconds"),
            "via": via, "exchange_at": at, "say": keep(ex.get("say")), "reply": keep(ex.get("reply")),
            "tools": tools, "note": keep(note)[:300]}


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
    with _LOCK, _file_lock(path(home)):
        return _read(path(home))


def delete(home, cid: str) -> bool:
    with _LOCK, _file_lock(p := path(home)):
        rows = _read(p)
        if found := any(r["id"] == cid for r in rows):
            _write(p, [r for r in rows if r["id"] != cid])
    return found


def report_wrong(home, *, via: str, note: str = "", failure_log_on: bool = False, privacy: bool = True) -> dict:
    ex = last_exchange(home)
    if ex is None:
        return {"ok": False, "code": "FEEDBACK_NO_EXCHANGE"}
    case, p = make_case(ex, via=via, note=note, privacy=privacy), path(home)
    with _LOCK, _file_lock(p):              # one writer at a time, across threads and processes
        _write(p, (_read(p) + [case])[-MAX_CASES:])
    failure_log.record(home, "user_correction", on=failure_log_on)
    logs.info("wrong_feedback", "FEEDBACK_SAVED", "a 'that was wrong' case was saved", item=case["id"], via=via)
    return {"ok": True, "code": "FEEDBACK_SAVED", "case": case}
