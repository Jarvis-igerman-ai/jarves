"""'Jarvis, that was wrong' (JAR-230): one phrase, one tap or one command saves the LAST exchange (redacted) as a local
feedback item in <home>/feedback.jsonl; week() gives the weekly report its count and items. Off by default (beta).
Nothing leaves this PC: no network; the text passes logs.redact + redact_pii and is cut to MAX_CHARS."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

from . import clilang, features as feat, logs
from .capabilities import PI_ROADMAP

FEATURE = "wrong_feedback"
FILE = "feedback.jsonl"
MAX_CHARS = 400
WEEK_S = 7 * 86400
UNRECORDED = "(not recorded: privacy mode)"


def _clean(text) -> str:
    return logs.redact_pii(logs.redact(str(text)))[:MAX_CHARS] if text else UNRECORDED


def last_exchange(db: Path):
    """(user, reply): the newest JARVES reply and the user turn before it. The 'that was wrong' turn comes AFTER it."""
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        a = con.execute("SELECT id, session_id, text FROM turns WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
        u = a and con.execute("SELECT text FROM turns WHERE role='user' AND session_id=? AND id<? ORDER BY id DESC LIMIT 1",
                              (a[1], a[0])).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return (u[0] if u else None, a[2]) if a else None


def save(home, *, note: str = "", source: str = "cli", now: float | None = None) -> dict | None:
    ex = last_exchange(Path(home) / "store.db")
    if ex is None:
        return None
    item = {"ts": round(time.time() if now is None else now, 3), "source": source, "user": _clean(ex[0]),
            "jarves": _clean(ex[1]), "note": _clean(note) if note else ""}
    with open(Path(home) / FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def week(home, now: float) -> dict:
    """The last 7 days of feedback, for the weekly report."""
    p, out = Path(home) / FILE, []
    for line in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []):
        try:
            it = json.loads(line)
        except ValueError:
            continue
        if now - float(it.get("ts", 0)) < WEEK_S:
            out.append(it)
    return {"count": len(out), "items": out}


# ---------------------------------------------------------------------- voice
def make_skill(home):
    from .skills import Skill, Tool, ToolResult

    class WrongFeedbackSkill(Skill):
        name = FEATURE
        description = "save the last exchange as 'that was wrong' feedback (local only)"

        def tools(self) -> list:
            async def wrong(_) -> ToolResult:
                if save(home, source="voice"):
                    return ToolResult(True, "רשמתי שזה היה לא נכון. תודה.", {"dashboard": "#/feedback"}, code="FEEDBACK_SAVED")
                return ToolResult(False, "אין שיחה אחרונה לשמור.", code="FEEDBACK_NOTHING")
            return [Tool("report_wrong", "'Jarvis, that was wrong', 'ג'ארביס, זה היה לא נכון': save the last exchange "
                         "as local feedback.", wrong, permission="action")]
    return WrongFeedbackSkill()


def attach(core) -> None:
    core.registry.register(make_skill(core.home))
    core.registry.allowed_actions.add("report_wrong")


# ---------------------------------------------------------------------- dashboard
def api_save(ctx, m, body):
    if not feat.Features(ctx.cfg, ctx.home).active(FEATURE):
        return 409, {"error": "feature off: jarvis features on " + FEATURE}
    it = save(ctx.home, note=str((body or {}).get("note") or ""), source="dashboard")
    return (200, {"item": it}) if it else (404, {"error": "no exchange to save"})


def api_list(ctx, m, body, q=None):
    return 200, week(ctx.home, time.time())


def dashboard_routes(register) -> None:
    register("POST", r"/api/feedback/wrong", api_save)
    register("GET", r"/api/feedback", api_list)


# ---------------------------------------------------------------------- CLI: jarvis feedback wrong [--note] | list [--json]
def add_cli(sub) -> None:
    p = sub.add_parser("feedback", help="feedback: jarvis feedback wrong [--note TEXT] saves the last exchange (redacted, "
                                        "local) as 'that was wrong'; jarvis feedback list [--json] shows the last 7 days")
    p.add_argument("action", nargs="?", default="list", choices=["wrong", "list"])
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")


def cmd_feedback(a, cfg, home) -> int:
    from .cli import _cli_features
    if a.action == "wrong":
        if not _cli_features(cfg, home).active(FEATURE):
            print(clilang.fmt("המשוב כבוי: jarvis features on " + FEATURE, "Feedback is off: jarvis features on " + FEATURE),
                  file=sys.stderr)
            return 2
        it = save(home, note=a.note, source="cli")
        print(clilang.fmt("נשמר.", "Saved.") if it else clilang.fmt("אין שיחה אחרונה לשמור.", "No exchange to save."))
        return 0 if it else 1
    w = week(home, time.time())
    if a.json:
        print(json.dumps(w, ensure_ascii=False))
    else:
        for it in w["items"]:
            print(f"- {it['user']} -> {it['jarves']}" + (f" ({it['note']})" if it["note"] else ""))
        print(clilang.fmt(f"{w['count']} פריטים ב-7 הימים האחרונים", f"{w['count']} items in the last 7 days"))
    return 0


COMMANDS = {"feedback": cmd_feedback}

feat.register(FEATURE, "משוב 'זה היה לא נכון'", "'That was wrong' feedback", False, skills=[FEATURE], risk="low",
              about="saves the last exchange, redacted, as a local feedback item for the weekly report; nothing leaves this PC",
              targets={"windows": {"status": "implemented_unverified", "evidence": "UNIT test_wrong_feedback.py",
                                   "blocker": "not yet tried by the owner on a real install"},
                       "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                                          "blocker": "pure Python; never run on a Raspberry Pi"}},
              permissions=["writes feedback.jsonl in the data folder; no network"], settings="jarvis feedback wrong|list")
