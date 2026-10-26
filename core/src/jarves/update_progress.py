"""#168 update progress: a small status file (updates/progress.json) written by the update steps (download, verify,
backup, stop, installer start; setup itself is untouched) and read by `jarvis update install/status` and the
dashboard Updates page. It survives the Core / dashboard restart because it is a file. "done" is NEVER written:
read() derives it only when the Core of the NEW version reached CORE_READY (updates/pending.json state "ok"), so a
success is never claimed before the health check. Stdlib only; no network, no prompt text."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

FILE = "progress.json"
STALE_S = 30 * 60                       # a "running" file older than this is reported as stalled, not as running
# stage -> (step, percent, English, Hebrew); the percent is the position in the chain, not a byte count
STAGES = {
    "downloading": (1, 10, "Downloading the installer", "מוריד את המתקין"),
    "verifying": (2, 30, "Verifying the signature, SHA256 and size", "בודק חתימה, SHA256 וגודל"),
    "backing_up": (3, 40, "Backing up your data", "מגבה את הנתונים שלך"),
    "stopping": (4, 50, "Stopping JARVES", "עוצר את ג'ארביס"),
    "installing": (5, 65, "Installing the new version; JARVES restarts by itself (about 1-3 minutes)", "מתקין את הגרסה החדשה; ג'ארביס יופעל מחדש לבד (כדקה עד שלוש)"),
    "health_check": (6, 85, "Health check: waiting for the new version to start", "בדיקת תקינות: ממתין שהגרסה החדשה תעלה"),
    "done": (7, 100, "Done: the new version is running and healthy", "הסתיים: הגרסה החדשה רצה ותקינה"),
}
STEPS = len(STAGES)
HINTS = {
    "UPDATE_NOT_APPLIED": ("Setup did not install it; the previous version is running. Nothing was lost. Try again later.",
                           "ההתקנה לא הושלמה; הגרסה הקודמת רצה. שום דבר לא אבד. נסו שוב מאוחר יותר."),
    "rolled_back": ("The new version did not start; the previous program was put back (data untouched).",
                    "הגרסה החדשה לא עלתה; התוכנה הקודמת הוחזרה (הנתונים לא נגעו)."),
    "rollback_failed": ("The new version did not start and the automatic undo failed. Run: jarvis update --rollback",
                        "הגרסה החדשה לא עלתה והחזרה האוטומטית נכשלה. הריצו: jarvis update --rollback"),
    "health_failed": ("The new version did not start. A full undo waits for your OK: jarvis update --rollback",
                      "הגרסה החדשה לא עלתה. חזרה מלאה ממתינה לאישור שלך: jarvis update --rollback"),
    "STALLED": ("No progress for a long time. Check jarvis update status; if JARVES does not start: "
                "jarvis update --rollback",
                "אין התקדמות זמן רב. בדקו jarvis update status; אם ג'ארביס לא עולה: jarvis update --rollback"),
}
DEFAULT_HINT = ("Nothing was changed; the current version keeps working. You can try again.",
                "שום דבר לא שונה; הגרסה הנוכחית ממשיכה לעבוד. אפשר לנסות שוב.")


def path(home) -> Path:
    return Path(home) / "updates" / FILE


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write(home, stage: str, *, state: str = "running", fresh: bool = False, **extra) -> None:
    """Record the stage. Never raises: progress must not break an update. `fresh` starts a new run."""
    try:
        p = path(home)
        d = {} if fresh else _read(p)
        if stage not in STAGES or stage == "done":
            return
        d.update(stage=stage, state=state, ts=time.time(), **{k: v for k, v in extra.items() if v is not None})
        if state != "failed":
            d.pop("code", None)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def fail(home, code: str) -> None:
    write(home, _read(path(home)).get("stage") or "downloading", state="failed", code=code)


def _mb(n) -> str:
    return f"{float(n) / 1048576:.1f} MB"


def read(home, current: str | None = None, now: float | None = None) -> dict:
    """{active, state: running|done|failed, stage, step, steps, percent, text_en, text_he, hint_en, hint_he, code,
    from, to, total}. `current` = the version running now (to tell that the new Core is up)."""
    home, now = Path(home), time.time() if now is None else now
    p = _read(path(home))
    if not p or p.get("stage") not in STAGES:
        return {"active": False, "state": "idle", "steps": STEPS}
    udir = home / "updates"
    pend, health = _read(udir / "pending.json"), _read(udir / "health.json")
    stage, state, code, to = p["stage"], p.get("state") or "running", p.get("code"), p.get("to")
    if state == "running" and stage in ("installing", "health_check") and to and pend.get("to") == to:
        if pend.get("state") == "ok" and (current is None or current == to):
            stage = "done"                                   # only the new Core's own CORE_READY mark gets here
        elif pend.get("state") == "not_applied":
            state, code = "failed", "UPDATE_NOT_APPLIED"
        elif health.get("to") == to and health.get("status") in ("rolled_back", "rollback_failed", "health_failed"):
            state, code = "failed", str(health["status"])
        elif current == to or int(pend.get("attempts") or 0) >= 1:   # the new Core started (note_start counted it)
            stage = "health_check"                           # the new program is up but has not said "ready" yet
    if state == "running" and stage != "done" and now - float(p.get("ts") or 0) > STALE_S:
        state, code = "failed", "STALLED"
    if stage == "done":
        state = "done"
    step, pct, en, he = STAGES[stage]
    if stage == "downloading" and p.get("total"):
        en, he = f"{en} ({_mb(p['total'])})", f"{he} ({_mb(p['total'])})"
    out = {"active": True, "state": state, "stage": stage, "step": step, "steps": STEPS, "percent": pct,
           "text_en": en, "text_he": he, "code": code, "from": p.get("from"), "to": to, "total": p.get("total"),
           "ts": p.get("ts")}
    if state == "failed":
        hen, hhe = HINTS.get(str(code), DEFAULT_HINT)
        out.update(hint_en=hen, hint_he=hhe,
                   text_en=f"Failed at: {en}" + (f" ({code})" if code else ""),
                   text_he=f"נכשל בשלב: {he}" + (f" ({code})" if code else ""))
    return out


def line(s: dict, hebrew: bool = False) -> str:
    """One CLI line: '[3/8  40%] Backing up your data'."""
    t = s.get("text_he" if hebrew else "text_en") or ""
    h = s.get("hint_he" if hebrew else "hint_en")
    return f"[{s.get('step')}/{s.get('steps')} {s.get('percent'):>3}%] {t}" + (f"\n    {h}" if h else "")


def watch(home, say: Callable[[str], None], *, current: Callable[[], str | None] = lambda: None,
          timeout_s: float = 420.0, every_s: float = 2.0, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.time, hebrew: bool = False) -> str:
    """Print each new stage until done / failed / timeout. Returns the end state ('done', 'failed', 'timeout')."""
    end, last = clock() + timeout_s, None
    while True:
        s = read(home, current())
        key = (s.get("stage"), s.get("state"))
        if s.get("active") and key != last:
            say(line(s, hebrew))
            last = key
        if s.get("state") in ("done", "failed"):
            return s["state"]
        if clock() >= end:
            return "timeout"
        sleep(every_s)


def api_progress(ctx, m, body):
    from . import updates
    return 200, read(ctx.home, updates.build_info()["version"])


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/updates/progress", api_progress)
