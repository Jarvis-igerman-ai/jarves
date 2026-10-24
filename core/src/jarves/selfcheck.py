"""Self-check: JARVES diagnoses himself and suggests improvements (owner request 20 Oct 2026, #17).

One Self-Check run (on demand: voice "תבדוק את עצמך" / "run a self check", `jarvis diagnose --now`, the dashboard
Self-check page; scheduled: a weekly scheduler job, after an unclean stop, after a burst of errors) looks at
- health       the Core's live components (health.json when the Core runs elsewhere)
- logs         the last N days of jarves.log (+ rotations): ERROR/WARN clusters, failures repeating across restarts,
               crashes (a process without CORE_STOPPED), reply latency percentiles and slow turns, budget burn,
               wake near-misses / false wakes, tool errors, false action claims, Pear / Google / camera
               availability, an available update
- system       free disk, free memory, size of the state folder
- config       unsafe or inconsistent settings (config.toml)
- features     switched on but not available, setup never finished, add-on code missing from the install, useful
               features never used or switched off
- conversations  the existing rule-based diagnosis (diagnose.run) and, when allowed, its AI review for recent
               conversations not reviewed yet. Nothing here re-implements those rules: their open issues are listed
               by id.
Output: a ranked report (severity, evidence with log lines / ids, root-cause hypothesis, a concrete fix or setting
change, effort) stored in store.db (selfcheck_* tables), shown on the dashboard with history and the trend against
the previous run, spoken as a short summary, and optionally as a ticket draft (Markdown, never posted).

Rules first, always local and free. The AI pass is ONLY diagnose.llm_review (the already-enabled review path: the
`diagnosis_llm_pass` switch, its privacy rule, its daily cap and the shared AI budget), at most
self_check.ai_sessions conversations per run, and only while the daily AI budget has headroom; its estimated cost is
shown. No other text leaves the machine. Evidence lines are log codes, levels, components and the (redacted) log
message; conversation text is never copied into a self-check report.

Suggestions that change a setting or a feature switch become autonomy PROPOSALS (memory.db, the existing approvals
flow): accepting a suggestion creates the proposal; the change is applied only when the user approves it (dashboard
button, terminal "y", or a spoken yes through registry.confirm). Never applied automatically: the appliers used here
are medium risk, and autonomy grants are low risk only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from . import features as feat
from . import logs
from .skills import expected_tool_refusal

FEATURE = "self_check"
SEV = {"high": 3, "medium": 2, "low": 1}
SEV_HE = {"high": "גבוהה", "medium": "בינונית", "low": "נמוכה"}
EFFORT_HE = {"minutes": "כמה דקות", "hour": "עד שעה", "bigger": "עבודה גדולה יותר"}
TRIGGERS = ("voice", "cli", "dashboard", "schedule", "crash", "restart", "errors", "test")
KEEP_RUNS = 100
MAX_LOG_LINES = 300_000              # bound for one scan (6 rotated 1 MB files are ~25k lines)
MAX_EVIDENCE = 5
AUTO_TRIGGERS = ("crash", "restart", "errors")

SCHEMA = """
CREATE TABLE IF NOT EXISTS selfcheck_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL, finished REAL, trigger TEXT NOT NULL,
    days INTEGER NOT NULL, status TEXT NOT NULL, summary TEXT, summary_he TEXT, counts TEXT, metrics TEXT, ai TEXT,
    score INTEGER);
CREATE TABLE IF NOT EXISTS selfcheck_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, rank INTEGER NOT NULL, fingerprint TEXT NOT NULL,
    category TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL, title_he TEXT NOT NULL, detail TEXT,
    detail_he TEXT, evidence TEXT, cause TEXT, cause_he TEXT, fix TEXT, fix_he TEXT, effort TEXT, action TEXT,
    count INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS selfcheck_state (
    fingerprint TEXT PRIMARY KEY, status TEXT NOT NULL, until REAL, proposal_id INTEGER, decided_at REAL,
    decided_by TEXT);
CREATE INDEX IF NOT EXISTS sc_find_run ON selfcheck_findings (run_id, rank);
"""
STATES = ("open", "accepted", "dismissed", "snoozed")


@dataclass
class Finding:
    fingerprint: str
    category: str              # health | logs | voice | wake | tools | budget | availability | system | config |
    severity: str              # features | usage | conversations
    title: str
    title_he: str
    detail: str = ""
    detail_he: str = ""
    evidence: list[str] = field(default_factory=list)
    cause: str = ""
    cause_he: str = ""
    fix: str = ""
    fix_he: str = ""
    effort: str = "minutes"
    action: dict | None = None  # {"type": "setting", key, value} | {"type": "feature_on", feature} |
    count: int = 1              # {"type": "command", command} | {"type": "open", page}


# ---------------------------------------------------------------------- small helpers
def _ts(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return None


def _when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "?"


def _line(r: dict) -> str:
    """One evidence line: time, level, component, code and the redacted log message (never fields). Evidence goes
    into ticket drafts meant for sharing: only a v2 message (a developer-written template, package B) is quoted;
    an older line's free-text message is reduced to its length (it may hold what the user or a provider said)."""
    raw = str(r.get("msg") or "")
    msg = " ".join(logs.redact_pii(logs.redact(raw)).split())[:140] if r.get("v") == logs.FORMAT_VERSION \
        else f"<{len(raw)} chars>"
    return f"{_when(r['_t'])} {r.get('level', '?')} {r.get('component', '-')} {r.get('code', '-')}: {msg}"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def read_logs(home: Path, since: float, until: float | None = None) -> list[dict]:
    """JSON log records of jarves.log and its rotations with ts in [since, until], oldest first ('_t' = epoch)."""
    from .logview import log_files
    out: list[dict] = []
    for path in log_files(home):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if len(out) >= MAX_LOG_LINES:
                break
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            t = _ts(d.get("ts"))
            if t is None or t < since or (until is not None and t > until):
                continue
            d["_t"] = t
            out.append(d)
    out.sort(key=lambda d: d["_t"])
    return out


def processes(recs: list[dict]) -> list[dict]:
    """Core processes seen in the log (record field `session` = one process), in start order."""
    by: dict[str, dict] = {}
    for r in recs:
        sid = str(r.get("session") or "")
        if not sid:
            continue
        p = by.setdefault(sid, {"session": sid, "first": r["_t"], "last": r["_t"], "started": False,
                                "stopped": False, "stopping": False, "errors": 0, "last_rec": r, "boot": None,
                                "reason": ""})
        p["last"], p["last_rec"] = r["_t"], r
        code = r.get("code")
        if code == "CORE_STARTING":
            p["started"] = True
            b = r.get("boot")
            p["boot"] = b if isinstance(b, (int, float)) and not isinstance(b, bool) else None
        elif code == "CORE_STOPPED":
            p["stopped"] = True
        elif code == "CORE_STOPPING":
            p["stopping"] = True
            p["reason"] = str(r.get("reason") or "")
        if r.get("level") == "ERROR":
            p["errors"] += 1
    return sorted(by.values(), key=lambda p: p["first"])


def end_kind(p: dict, procs: list[dict]) -> str:
    """How a process that wrote CORE_STARTING ended: "clean" (CORE_STOPPED), "restart" (the computer rebooted before the
    next JARVES start: power cut, logoff or Windows shutdown without a stop, which JARVES cannot prove was its own
    fault), "stop_hung" (it began an orderly stop that never finished) or "crash" (anything else)."""
    if p["stopped"]:
        return "clean"
    nxt = next((q for q in procs if q["started"] and q["first"] > p["first"]), None)
    if nxt is not None:
        from .runtime.shutdown import same_boot
        if same_boot(p.get("boot"), nxt.get("boot")) is False:
            return "restart"
    return "stop_hung" if p["stopping"] else "crash"


def previous_process(home: Path, current_session: str, lookback_s: float = 30 * 86400,
                     clock: Callable[[], float] = time.time) -> dict | None:
    """The Core process that ran before `current_session` (None if there was none in the log)."""
    procs = [p for p in processes(read_logs(home, clock() - lookback_s)) if p["session"] != current_session
             and p["started"]]
    return procs[-1] if procs else None


def system_info(home: Path) -> dict:
    """Free disk of the state folder's drive, physical memory, size of the state folder (no network, no psutil)."""
    out: dict = {"disk_free_gb": None, "memory": None, "state_mb": None}
    try:
        out["disk_free_gb"] = round(shutil.disk_usage(home).free / 1e9, 2)
    except OSError:
        pass
    try:
        if sys.platform == "win32":
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                out["memory"] = {"total_mb": m.ullTotalPhys // 2**20, "available_mb": m.ullAvailPhys // 2**20}
        else:
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, _, v = line.partition(":")
                info[k] = int(v.split()[0]) if v.split() else 0
            out["memory"] = {"total_mb": info.get("MemTotal", 0) // 1024,
                             "available_mb": info.get("MemAvailable", 0) // 1024}
    except Exception:
        pass
    try:
        total = sum(p.stat().st_size for p in Path(home).rglob("*") if p.is_file())
        out["state_mb"] = round(total / 1e6, 1)
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------- fixed texts (English + Hebrew)
COMPONENT_HE = {"ai": "חיבור ה-AI", "audio": "השמע", "speech_out": "הדיבור", "speech_in": "זיהוי הדיבור",
                "wake_word": "מילת ההפעלה", "mic": "המיקרופון", "link": "החיבור לפנים", "music": "המוזיקה",
                "scheduler": "המשימות המתוזמנות", "camera": "המצלמה", "google": "גוגל", "backup": "הגיבוי",
                "dashboard": "לוח הבקרה", "voice": "הקול", "helper_isolation": "הבידוד של פקודות העוזר"}
STATUS_HE = {"unavailable": "לא זמין", "degraded": "פועל חלקית"}
CORE_COMPONENTS = {"ai", "audio", "speech_out", "link", "wake_word", "mic", "voice"}
# log code -> (cause, cause_he, fix, fix_he, effort)
KNOWN_CODES = {
    "CORE_TASK_CRASH": ("a request handler raised an exception (a bug)", "בקשה נכשלה בגלל שגיאה בקוד",
                        "Make a diagnostic bundle (`jarvis diag`) and attach it to the ticket draft.",
                        "צרו חבילת אבחון (jarvis diag) וצרפו אותה לטיוטת הכרטיס.", "bigger"),
    "SCHEDULER_TICK_FAILED": ("the scheduler loop hit an error reading its jobs", "לולאת המשימות נתקלה בשגיאה",
                              "Open the Jobs page; pause the job that fails, then attach `jarvis diag`.",
                              "פתחו את עמוד המשימות הקבועות, השהו את המשימה שנכשלת וצרפו jarvis diag.", "hour"),
    "WAKE_LOOP_ERROR": ("the wake-word microphone stream failed (driver, device unplugged or in use)",
                        "זרם המיקרופון של מילת ההפעלה נכשל (דרייבר, מכשיר שנותק או תפוס)",
                        "Reconnect the microphone, close other apps using it, then `jarvis status`.",
                        "חברו מחדש את המיקרופון, סגרו תוכנות אחרות שמשתמשות בו והריצו jarvis status.", "minutes"),
    "DIAGNOSE_LLM_FAILED": ("AI review requests fail (network, API key or budget)",
                            "בקשות הבדיקה בעזרת המודל נכשלות (רשת, מפתח או תקציב)",
                            "Check `jarvis key` and the Usage page; the rule-based checks still run.",
                            "בדקו את jarvis key ואת עמוד השימוש. הבדיקות לפי כללים ממשיכות לרוץ.", "minutes"),
    "CORE_STOP_STEP_FAILED": ("one part of the orderly shutdown failed or hung (the rest still closed)",
                              "חלק אחד מהכיבוי המסודר נכשל או נתקע (השאר נסגר כרגיל)",
                              "Attach `jarvis diag` to the ticket draft: the line names the part (step=...).",
                              "צרפו jarvis diag לטיוטת הכרטיס: השורה מציינת את החלק (step=...).", "hour"),
    "LIVE_STOP_FAILED": ("the voice session did not close cleanly", "שיחת הקול לא נסגרה כראוי",
                         "Usually a network drop; if it repeats attach `jarvis diag` to the ticket draft.",
                         "בדרך כלל ניתוק רשת. אם זה חוזר צרפו jarvis diag לטיוטת הכרטיס.", "hour"),
}
# codes that have their own rule (not counted again as a generic cluster)
OWN_RULE = {"AI_BUDGET", "AI_BUDGET_OVERRUN", "LIVE_FALSE_ACTION_CLAIM", "WAKE_NEAR_MISS", "VOICE_NO_SPEECH",
            "TOOL_RESULT", "UPDATE_CHECKED", "CALENDAR_DELETE_FORBIDDEN"}
AVAIL = {
    "pear": {"codes": {"MUSIC_PLAYER_OFFLINE", "MUSIC_NOT_PAIRED", "MUSIC_PAIR_FAILED", "MUSIC_TIMEOUT",
                       "MUSIC_PLAYER_ERROR"},
             "features": ("music",), "title": "Pear Desktop (the music player) was not reachable",
             "title_he": "נגן המוזיקה Pear Desktop לא היה זמין",
             "cause": "Pear Desktop is closed, not installed, or its API Server plugin is off / not paired",
             "cause_he": "Pear Desktop סגור, לא מותקן, או שתוסף ה-API Server כבוי או לא מוצמד",
             "fix": "Start Pear Desktop, keep its API Server plugin on and press Allow once (Music guide).",
             "fix_he": "הפעילו את Pear Desktop, השאירו את תוסף ה-API Server דולק ואשרו פעם אחת (מדריך המוזיקה).",
             "action": {"type": "open", "page": "#/guide/music"}},
    "google": {"codes": {"GOOGLE_AUTH_EXPIRED", "GOOGLE_NOT_CONNECTED", "GOOGLE_OFFLINE", "GOOGLE_SCOPE_MISSING",
                         "GOOGLE_API_ERROR", "GOOGLE_NO_REFRESH_TOKEN"},
               "features": ("google_calendar", "gmail_read", "gmail_send", "calendar_write", "google_contacts"),
               "title": "Google (Calendar / Gmail) was not available", "title_he": "גוגל (יומן / ג'ימייל) לא היה זמין",
               "cause": "not connected, the sign-in expired (weekly for unverified apps) or a permission is missing",
               "cause_he": "לא מחובר, ההתחברות פגה (כל שבוע באפליקציה לא מאומתת) או שחסרה הרשאה",
               "fix": "Run `jarvis google start` to connect or sign in again.",
               "fix_he": "הריצו jarvis google start כדי להתחבר או להתחבר מחדש.",
               "action": {"type": "command", "command": "jarvis google start"}},
    "camera": {"codes": {"CAMERA_OPEN_FAILED", "CAMERA_UNAVAILABLE", "CAMERA_READ_FAILED", "CAMERA_LOOP_ERROR",
                         "CAMERA_NO_LIBRARY"},
               "features": ("camera", "camera_preview", "face_recognition", "camera_when_away"),
               "title": "The camera was not available", "title_he": "המצלמה לא הייתה זמינה",
               "cause": "the webcam is unplugged, used by another app, blocked by Windows privacy, or OpenCV is missing",
               "cause_he": "המצלמה מנותקת, תפוסה בתוכנה אחרת, חסומה בהגדרות הפרטיות של Windows, או ש-OpenCV חסר",
               "fix": "Connect the webcam, close apps that use it, allow camera access in Windows settings; "
                      "`jarvis features on camera` installs OpenCV.",
               "fix_he": "חברו את המצלמה, סגרו תוכנות שמשתמשות בה ואפשרו גישה למצלמה בהגדרות Windows. "
                         "jarvis features on camera מתקין את OpenCV.",
               "action": {"type": "open", "page": "#/guide/camera"}},
}
AVAIL_FEATURES = {f for g in AVAIL.values() for f in g["features"]}
# useful features that are off (only low-risk, never the privacy-sensitive SAFE_OFF ones)
USEFUL_ON = {
    "backup": ("a daily local backup of your memory and settings", "גיבוי מקומי יומי של הזיכרון וההגדרות"),
    "update_check": ("tells you when a newer JARVES exists (installs only after you approve)",
                     "מודיע כשיש גרסה חדשה (מתקין רק אחרי אישור)"),
    "proactive_alerts": ("reminders and finished reports reach you at the right time, with quiet hours",
                         "תזכורות ודוחות שהסתיימו מגיעים בזמן הנכון, עם שעות שקט"),
    "scheduler": ("reminders and recurring reports", "תזכורות ודוחות קבועים"),
}
# features that are on but never used in the window: (tool prefixes) -> an example phrase from their guide
USEFUL_TRY = {"morning_briefing": ("briefing_",), "notes": ("note_",), "scheduler": ("schedule_", "jobs_list"),
              "music": ("music_",)}
ISSUE_KIND_HE = {"stall": "בקשה שנתקעה", "dead_air": "שתיקה ארוכה", "filler": "מילת מילוי מבלבלת",
                 "confusion": "המשתמש לא הבין", "tool": "כלי שנכשל", "backend": "תשובת שרת איטית או שנכשלה",
                 "voice": "בעיית קול", "wake": "התעוררות לשווא", "error": "שגיאה בשיחה", "warning": "אזהרה בשיחה",
                 "cost": "עלות גבוהה", "ai_review": "בדיקת המודל"}


# ---------------------------------------------------------------------- the check
class CheckCancelled(Exception):
    """The Core is stopping: the run ends between two steps (V4-SC-1), nothing more is written."""


class SelfCheck:
    """One run. Everything it reads is local: log files, store.db, memory.db, health.json, config, features."""

    def __init__(self, home: Path, cfg, *, store=None, db=None, features=None, components=None,
                 clock: Callable[[], float] = time.time, system: Callable[[Path], dict] | None = None,
                 ai_client=None, budget=None, current_session: str | None = None) -> None:
        self.home, self.cfg, self.clock = Path(home), cfg, clock
        self._store, self._own_store = store, store is None
        self._db, self._own_db = db, db is None
        self._features = features
        self._components = components
        self.system = system or system_info
        self.ai_client, self.budget = ai_client, budget
        self.current_session = current_session
        self.cancel = threading.Event()           # set by the Core at shutdown; checked between steps

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise CheckCancelled()

    # ---- resources
    @property
    def store(self):
        if self._store is None:
            from .store import Store
            self._store = Store(self.home / "store.db", tier=self.cfg.ai.voice_service_tier,
                                backend_model=self.cfg.ai.text_model)
        ensure_schema(self._store)
        return self._store

    @property
    def db(self):
        if self._db is None:
            from .userdata import open_db
            self._db = open_db(self.home)
        return self._db

    @property
    def features(self):
        if self._features is None:
            from .setup_wizard import load_addons
            load_addons()
            self._features = feat.Features(self.cfg, self.home, components=self.components)
        return self._features

    def components(self) -> dict:
        if self._components is not None:
            try:
                return self._components() or {}
            except Exception:
                return {}
        from .health import read_snapshot
        from .runtime.lock import held
        if not held(self.home):                 # a snapshot of a stopped Core is not live evidence
            return {}
        snap = read_snapshot(self.home / "health.json") or {}
        return (snap.get("components") or {}) if snap.get("age_s", 1e9) < 120 else {}

    def close(self) -> None:
        if self._own_store and self._store is not None:
            self._store.close()
        if self._own_db and self._db is not None:
            self._db.close()

    # ---- run
    def run(self, trigger: str = "cli", *, days: int | None = None, ai: bool | None = None) -> dict:
        t0 = self.clock()
        days = int(days or self.db.setting("self_check.days") or 7)
        days = max(1, min(days, 90))
        since = t0 - days * 86400
        st = self.store
        with st._lock:
            cur = st._db.execute("INSERT INTO selfcheck_runs (started, trigger, days, status) VALUES (?,?,?,?)",
                                 (t0, trigger if trigger in TRIGGERS else "cli", days, "running"))
            run_id = cur.lastrowid
            st._db.commit()
        logs.info("selfcheck", "SELFCHECK_STARTED", "self-check started", run=run_id, trigger=trigger, days=days)
        found: list[Finding] = []
        metrics: dict = {"days": days}
        ai_info: dict = {"used": False}
        status = "done"
        try:
            recs = read_logs(self.home, since, t0)
            metrics["log_records"] = len(recs)
            for step in (lambda: self._health(found), lambda: self._logs(recs, found, metrics),
                         lambda: self._system(found, metrics), lambda: self._config(found),
                         lambda: self._feature_gaps(found, recs, since)):
                self._check_cancel()
                try:
                    step()
                except CheckCancelled:
                    raise
                except Exception as e:               # one broken check never hides the others
                    logs.error("selfcheck", "SELFCHECK_STEP_FAILED", "a self-check step failed",
                               exc=True)
                    status = "partial"
            self._check_cancel()
            ai_info = self._ai_pass(ai, since, t0)
            self._check_cancel()
            try:
                self._conversations(found, since, days)
            except CheckCancelled:
                raise
            except Exception as e:
                logs.error("selfcheck", "SELFCHECK_STEP_FAILED", "conversation diagnosis failed", step="diagnose",
                           exc=True)
                status = "partial"
        except CheckCancelled:
            with st._lock:
                st._db.execute("UPDATE selfcheck_runs SET status='cancelled' WHERE id=?", (run_id,))
                st._db.commit()
            logs.info("selfcheck", "SELFCHECK_CANCELLED", "self-check cancelled: the Core is stopping", run=run_id)
            raise
        except Exception as e:
            logs.error("selfcheck", "SELFCHECK_FAILED", "self-check failed", exc=True)
            status = "failed"
        ranked = rank(found)
        score = health_score(ranked)
        with st._lock:
            for i, f in enumerate(ranked, 1):
                st._db.execute("INSERT INTO selfcheck_findings (run_id, rank, fingerprint, category, severity, title, "
                               "title_he, detail, detail_he, evidence, cause, cause_he, fix, fix_he, effort, action, "
                               "count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (run_id, i, f.fingerprint, f.category, f.severity, f.title, f.title_he, f.detail,
                                f.detail_he, json.dumps(f.evidence[:MAX_EVIDENCE], ensure_ascii=False), f.cause,
                                f.cause_he, f.fix, f.fix_he, f.effort,
                                json.dumps(f.action, ensure_ascii=False) if f.action else None, f.count))
            counts = {s: sum(f.severity == s for f in ranked) for s in SEV}
            st._db.execute("UPDATE selfcheck_runs SET finished=?, status=?, counts=?, metrics=?, ai=?, score=? "
                           "WHERE id=?", (self.clock(), status, json.dumps(counts), json.dumps(metrics, default=str),
                                          json.dumps(ai_info), score, run_id))
            st._db.commit()
        view = run_view(st, run_id, now=self.clock())
        with st._lock:
            st._db.execute("UPDATE selfcheck_runs SET summary=?, summary_he=? WHERE id=?",
                           (view["summary"], view["summary_he"], run_id))
            old = [r[0] for r in st._db.execute("SELECT id FROM selfcheck_runs ORDER BY id DESC LIMIT -1 OFFSET ?",
                                                (KEEP_RUNS,))]
            for rid in old:
                st._db.execute("DELETE FROM selfcheck_findings WHERE run_id=?", (rid,))
                st._db.execute("DELETE FROM selfcheck_runs WHERE id=?", (rid,))
            st._db.commit()
        try:
            self.db.set_setting("self_check.last_run", t0, actor="selfcheck")
        except Exception:
            pass
        logs.info("selfcheck", "SELFCHECK_DONE", "self-check finished", n=len(ranked), run=run_id,
                  trigger=trigger, high=counts["high"], medium=counts["medium"], low=counts["low"], score=score,
                  ai_reviews=ai_info.get("reviewed", 0))
        return run_view(st, run_id, now=self.clock())

    # ---- health components
    def _health(self, out: list[Finding]) -> None:
        comps = dict(self.components())
        if "helper_isolation" not in comps:        # R-01: no live Core row: check the OS limits here
            try:
                from . import isolation
                st, detail, action = isolation.health_status(isolation.row(self.cfg))
                comps["helper_isolation"] = {"status": st, "detail": detail, "action": action}
            except Exception:
                pass
        for name, c in sorted(comps.items()):
            status = (c or {}).get("status")
            if status not in ("unavailable", "degraded"):
                continue
            sev = "high" if status == "unavailable" and name in CORE_COMPONENTS else \
                "medium" if status == "unavailable" or name in CORE_COMPONENTS else "low"
            detail = logs.redact(str(c.get("detail") or ""))[:200]
            action = str(c.get("action") or "")
            he_name = COMPONENT_HE.get(name, name)
            out.append(Finding(
                f"health:{name}", "health", sev, f"{name} is {status}: {detail}"[:160],
                f"{he_name} {STATUS_HE[status]}" + (f": {detail}" if detail else ""),
                detail=f"The Core reports the component {name} as {status}.",
                detail_he=f"ה-Core מדווח ש{he_name} {STATUS_HE[status]}.",
                evidence=[f"health {name}: {status} since {_when(c.get('since'))} - {detail}"],
                cause=detail or f"{name} failed its last check", cause_he=detail or "הבדיקה האחרונה נכשלה",
                fix=action or "Run `jarvis status` and `jarvis doctor`.",
                fix_he=(f"נסו: {action}" if action else "הריצו jarvis status ו-jarvis doctor."),
                effort="minutes", action={"type": "command", "command": action} if action.startswith(("jarvis", "uv"))
                else None))

    # ---- logs
    def _logs(self, recs: list[dict], out: list[Finding], metrics: dict) -> None:
        procs = processes(recs)
        later_start = {p["session"]: any(q["first"] > p["first"] and q["started"] for q in procs) for p in procs}
        ended = [p for p in procs if p["started"] and not p["stopped"] and p["session"] != self.current_session
                 and later_start.get(p["session"])]
        kinds = {p["session"]: end_kind(p, procs) for p in ended}
        crashed = [p for p in ended if kinds[p["session"]] == "crash"]
        restarted = [p for p in ended if kinds[p["session"]] == "restart"]
        hung = [p for p in ended if kinds[p["session"]] == "stop_hung"]
        metrics["starts"] = sum(p["started"] for p in procs)
        metrics["crashes"] = len(crashed)
        metrics["system_restarts"] = len(restarted)
        metrics["stop_hung"] = len(hung)
        if crashed:
            out.append(Finding(
                "crash", "logs", "high" if len(crashed) >= 2 else "medium",
                f"JARVES stopped without a clean shutdown {len(crashed)}x",
                f"ג'ארביס נעצר בלי כיבוי מסודר {len(crashed)} פעמים",
                detail="A Core process wrote CORE_STARTING but never CORE_STOPPED before the next start, and the "
                       "computer did not restart in between.",
                detail_he="תהליך של ה-Core התחיל ולא נסגר כראוי לפני ההפעלה הבאה, והמחשב לא הופעל מחדש באמצע.",
                evidence=[f"process {p['session']}: started {_when(p['first'])}, last line {_line(p['last_rec'])}"
                          for p in crashed[-MAX_EVIDENCE:]],
                cause="a crash or a forced kill (Task Manager, taskkill /F). A closed terminal window, a logoff and "
                      "Windows shutting down now stop JARVES cleanly, so those are no longer the likely cause",
                cause_he="קריסה או סגירה בכוח (מנהל המשימות, taskkill /F). סגירת חלון הטרמינל, התנתקות וכיבוי של "
                         "Windows כבר עוצרים את ג'ארביס בצורה מסודרת, ולכן הם כבר לא הסיבה הסבירה",
                fix="Look at the last log lines before each stop (System log page); if it was not a forced close, "
                    "attach `jarvis diag` to the ticket draft.",
                fix_he="בדקו את השורות האחרונות ביומן המערכת לפני כל עצירה. אם זו לא הייתה סגירה בכוח, "
                       "צרפו jarvis diag לטיוטת הכרטיס.",
                effort="hour", action={"type": "open", "page": "#/logs"}, count=len(crashed)))
        if restarted:
            out.append(Finding(
                "system_restart", "logs", "low",
                f"The computer restarted or lost power while JARVES was open {len(restarted)}x",
                f"המחשב הופעל מחדש או איבד חשמל כשג'ארביס היה פתוח {len(restarted)} פעמים",
                detail="A Core process had no CORE_STOPPED and the computer's boot time changed before JARVES started "
                       "again. That is the computer ending JARVES (power cut, crash of Windows, or a shutdown that did "
                       "not wait), not proof of a JARVES bug.",
                detail_he="לתהליך של ה-Core לא היה CORE_STOPPED וזמן ההפעלה של המחשב השתנה לפני שג'ארביס עלה שוב. "
                          "זה המחשב שסיים את ג'ארביס (הפסקת חשמל, קריסת Windows או כיבוי שלא המתין) ולא הוכחה לתקלה בג'ארביס.",
                evidence=[f"process {p['session']}: started {_when(p['first'])}, last line {_line(p['last_rec'])}"
                          for p in restarted[-MAX_EVIDENCE:]],
                cause="power loss, a Windows crash or update restart, or an abrupt shutdown",
                cause_he="הפסקת חשמל, קריסת Windows או הפעלה מחדש של עדכון, או כיבוי פתאומי",
                fix="Nothing to fix in JARVES. If it keeps happening, check the PC's power and Windows updates.",
                fix_he="אין מה לתקן בג'ארביס. אם זה חוזר, בדקו את החשמל של המחשב ואת עדכוני Windows.",
                effort="minutes", count=len(restarted)))
        if hung:
            out.append(Finding(
                "stop_hung", "logs", "medium",
                f"JARVES began a clean stop but did not finish it {len(hung)}x",
                f"ג'ארביס התחיל כיבוי מסודר ולא סיים אותו {len(hung)} פעמים",
                detail="CORE_STOPPING was logged but CORE_STOPPED never came: one part of the shutdown hung and the "
                       "process was killed (by the installer, Windows or the user).",
                detail_he="נרשם CORE_STOPPING אבל CORE_STOPPED לא הגיע: חלק מהכיבוי נתקע והתהליך הופסק בכוח.",
                evidence=[f"process {p['session']}: stop reason {p.get('reason') or '?'}, last line "
                          f"{_line(p['last_rec'])}" for p in hung[-MAX_EVIDENCE:]],
                cause="a part of the shutdown that never finished (see CORE_STOP_STEP_FAILED lines)",
                cause_he="חלק מהכיבוי שלא הסתיים (חפשו שורות CORE_STOP_STEP_FAILED)",
                fix="Attach `jarvis diag` to the ticket draft: the last log lines name the part that hung.",
                fix_he="צרפו jarvis diag לטיוטת הכרטיס: השורות האחרונות ביומן מציינות את החלק שנתקע.",
                effort="hour", action={"type": "open", "page": "#/logs"}, count=len(hung)))
        # ERROR / WARNING clusters and failures that come back after restarts
        groups: dict[tuple[str, str], list[dict]] = {}
        avail_hits: dict[str, list[dict]] = {k: [] for k in AVAIL}
        for r in recs:
            code = str(r.get("code") or "-")
            if expected_tool_refusal(code, r.get("origin")):
                continue
            for gid, g in AVAIL.items():
                if code in g["codes"]:
                    avail_hits[gid].append(r)
            if r.get("level") not in ("ERROR", "WARNING") or code in OWN_RULE or \
                    any(code in g["codes"] for g in AVAIL.values()) or r.get("component") == "selfcheck":
                continue
            groups.setdefault((r["level"], code), []).append(r)
        metrics["errors"] = sum(len(v) for (lv, _), v in groups.items() if lv == "ERROR")
        metrics["warnings"] = sum(len(v) for (lv, _), v in groups.items() if lv == "WARNING")
        for (level, code), rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            n, sessions = len(rs), len({r.get("session") for r in rs})
            if level == "ERROR" and n < 2 or level == "WARNING" and n < 5:
                continue
            repeated = sessions >= 3
            sev = "high" if level == "ERROR" and repeated else "medium" if level == "ERROR" else \
                "medium" if repeated and n >= 10 else "low"
            comp = str(rs[-1].get("component") or "-")
            cause, cause_he, fix, fix_he, effort = KNOWN_CODES.get(code, (
                f"{comp} reports {code}; see the evidence lines", f"הרכיב {comp} מדווח {code}. ראו את שורות היומן",
                "Open the System log filtered on this code; attach `jarvis diag` to the ticket draft if it is not "
                "explained by something you did.",
                "פתחו את יומן המערכת מסונן לקוד הזה. אם אין לזה הסבר, צרפו jarvis diag לטיוטת הכרטיס.", "hour"))
            kind = "Repeated failure" if repeated else "Errors" if level == "ERROR" else "Warnings"
            kind_he = "תקלה שחוזרת" if repeated else "שגיאות" if level == "ERROR" else "אזהרות"
            ev = [_line(r) for r in (rs[:2] + rs[-3:] if n > 5 else rs)][:MAX_EVIDENCE]
            out.append(Finding(
                f"log:{level}:{code}", "logs", sev, f"{kind}: {code} {n}x" + (f" across {sessions} runs" if repeated else ""),
                f"{kind_he}: {code} {n} פעמים" + (f" ב-{sessions} הפעלות" if repeated else ""),
                detail=f"{comp} logged {level} {code} {n} time(s) in the last {metrics['days']} day(s)"
                       + (", in several separate runs: a restart does not fix it." if repeated else "."),
                detail_he=f"{comp} רשם {code} {n} פעמים ב-{metrics['days']} הימים האחרונים"
                          + (", בכמה הפעלות נפרדות: הפעלה מחדש לא פותרת את זה." if repeated else "."),
                evidence=ev, cause=cause, cause_he=cause_he, fix=fix, fix_he=fix_he, effort=effort,
                action={"type": "open", "page": "#/logs"}, count=n))
        self._latency(recs, out, metrics)
        self._budget(recs, out, metrics)
        self._wake(recs, out, metrics)
        self._tools(recs, out, metrics)
        self._claims(recs, out, metrics)
        self._availability(avail_hits, out)
        self._update(out)

    def _latency(self, recs, out, metrics) -> None:
        waits = [(r["_t"], float(r["wait_s"]), r) for r in recs if r.get("code") == "LIVE_JARVES_SAID"
                 and isinstance(r.get("wait_s"), (int, float)) and not isinstance(r.get("wait_s"), bool)]
        vals = [w for _, w, _ in waits]
        lim = float(self.cfg.history.dead_air_s)
        metrics["latency"] = {"replies": len(vals), "p50_s": percentile(vals, 50), "p90_s": percentile(vals, 90),
                              "p95_s": percentile(vals, 95), "max_s": max(vals) if vals else None,
                              "slow": sum(v > lim for v in vals), "limit_s": lim}
        lat = metrics["latency"]
        if len(vals) < 5 or lat["p90_s"] <= lim:
            return
        slow = sorted(waits, key=lambda x: -x[1])[:MAX_EVIDENCE]
        effort_now = self.cfg.ai.voice_reasoning_effort
        action = None
        fix = ("Check the slow turns on the Conversations page (backend lookups, web search). Keep the Fast tier "
               "(ai.voice_service_tier = priority) for spoken lookups.")
        fix_he = "בדקו את התורות האיטיים בעמוד השיחות (חיפושים, בקשות לשרת). השאירו את המסלול המהיר לחיפושים מדוברים."
        if effort_now in ("low", "medium", "high", "xhigh"):
            action = {"type": "setting", "key": "ai.voice_reasoning_effort", "value": "minimal"}
            fix = f"Lower ai.voice_reasoning_effort from {effort_now} to minimal (faster spoken lookups)."
            fix_he = f"להוריד את מאמץ החשיבה בחיפושים מדוברים מ-{effort_now} ל-minimal (תשובות מהירות יותר)."
        out.append(Finding(
            "latency", "voice", "high" if lat["p50_s"] > lim else "medium",
            f"Replies are slow: p90 {lat['p90_s']:.1f} s (p50 {lat['p50_s']:.1f} s, limit {lim:g} s)",
            f"התשובות איטיות: 90% מהתשובות תוך {lat['p90_s']:.1f} שניות (חציון {lat['p50_s']:.1f}, הגבול {lim:g})",
            detail=f"{lat['slow']} of {lat['replies']} replies started more than {lim:g} s after the user stopped.",
            detail_he=f"{lat['slow']} מתוך {lat['replies']} תשובות התחילו יותר מ-{lim:g} שניות אחרי שהמשתמש סיים לדבר.",
            evidence=[f"{_when(t)} reply after {w:.1f} s (cid {r.get('cid', '-')})" for t, w, r in slow],
            cause="slow backend lookups / web search, a slow network, or high reasoning effort on spoken lookups",
            cause_he="חיפושים איטיים בשרת או באינטרנט, רשת איטית, או מאמץ חשיבה גבוה בחיפושים מדוברים",
            fix=fix, fix_he=fix_he, effort="minutes", action=action, count=lat["slow"]))

    def _budget(self, recs, out, metrics) -> None:
        hits = [r for r in recs if r.get("code") in ("AI_BUDGET", "AI_BUDGET_OVERRUN")]
        days_hit = sorted({datetime.fromtimestamp(r["_t"]).strftime("%Y-%m-%d") for r in hits})
        b = {"limit_hits": len(hits), "days_at_limit": days_hit}
        budget, own, made = self.budget, None, None
        try:
            if budget is None:
                from .budget import Budget
                from .memory import MemoryStore
                own = MemoryStore(self.home / "memory.db")
                budget = made = Budget(own, self.cfg.ai)
            b["requests_today"] = {"used": budget.used("ai_requests"), "limit": budget.limit("ai_requests")}
        except Exception:
            pass
        finally:
            if made is not None:
                made.close()
            if own is not None:
                own.close()
        try:
            from .store import daily_costs
            dc = [d for d in daily_costs(self.store, self.cfg.pricing, metrics["days"]) if d["total"] > 0]
            b["cost_avg_usd"] = round(sum(d["total"] for d in dc) / metrics["days"], 3) if dc else 0.0
            b["cost_max_usd"] = round(max((d["total"] for d in dc), default=0.0), 3)
        except Exception:
            pass
        metrics["budget"] = b
        limit = int(self.cfg.ai.daily_request_limit)
        if days_hit:
            new = max(limit + 50, int(limit * 1.5)) if limit else 200
            out.append(Finding(
                "budget:limit", "budget", "medium" if len(days_hit) >= 2 else "low",
                f"The daily AI limit was reached on {len(days_hit)} day(s)",
                f"הגעתי למגבלת ה-AI היומית ב-{len(days_hit)} ימים",
                detail=f"{len(hits)} AI_BUDGET event(s): JARVES stopped answering with AI until the next day.",
                detail_he=f"{len(hits)} פעמים נעצרו תשובות ה-AI עד למחרת בגלל המגבלה היומית.",
                evidence=[_line(r) for r in hits[-MAX_EVIDENCE:]],
                cause=f"ai.daily_request_limit = {limit} is lower than your daily use",
                cause_he=f"המגבלה היומית ({limit} בקשות) נמוכה מהשימוש היומי שלכם",
                fix=f"If the cost is fine for you, raise ai.daily_request_limit to {new} (costs more; check the "
                    "Usage page first).",
                fix_he=f"אם העלות מתאימה לכם, להעלות את מגבלת הבקשות היומית ל-{new} (עולה יותר כסף, בדקו קודם את "
                       "עמוד השימוש).",
                effort="minutes", action={"type": "setting", "key": "ai.daily_request_limit", "value": new},
                count=len(hits)))
        alert = float(self.cfg.pricing.daily_alert_usd)
        avg = b.get("cost_avg_usd") or 0.0
        if alert > 0 and avg > alert:
            out.append(Finding(
                "budget:burn", "budget", "medium", f"Average cost ~${avg:.2f}/day is above your ${alert:.2f} alert",
                f"העלות הממוצעת כ-${avg:.2f} ליום, מעל סף ההתראה ${alert:.2f}",
                detail="Estimate from [pricing]; your OpenAI bill is the truth.",
                detail_he="הערכה לפי טבלת המחירים. החשבון של OpenAI הוא הקובע.",
                evidence=[f"max day ~${b.get('cost_max_usd', 0):.2f}, average ~${avg:.2f} over {metrics['days']} days"],
                cause="long voice conversations, the Fast (priority, 2x) tier, or many web searches",
                cause_he="שיחות קול ארוכות, המסלול המהיר (פי 2 במחיר) או הרבה חיפושים באינטרנט",
                fix="Open the Costs page to see which conversations cost most; shorten voice.idle_disconnect_s or use "
                    "the default tier if speed matters less.",
                fix_he="פתחו את עמוד העלויות כדי לראות אילו שיחות עלו הכי הרבה. אפשר לקצר את זמן הסגירה של שיחה שקטה "
                       "או לעבור למסלול הרגיל אם המהירות פחות חשובה.",
                effort="minutes", action={"type": "open", "page": "#/costs"}))

    def _wake(self, recs, out, metrics) -> None:
        wakes = [r for r in recs if r.get("code") == "WAKE_WORD"]
        false = [r for r in recs if r.get("code") == "VOICE_NO_SPEECH"]
        near = [r for r in recs if r.get("code") == "WAKE_NEAR_MISS"]
        th = float(self.cfg.voice.wake_threshold)
        metrics["wake"] = {"wakes": len(wakes), "false_wakes": len(false), "near_misses": len(near), "threshold": th}
        if len(false) >= 3 and (not wakes or len(false) / max(1, len(wakes)) >= 0.25):
            new = round(min(0.8, max(th + 0.05, 0.3)), 2)   # issue #17: from 0.15 a +0.05 step never stopped the false wakes
            out.append(Finding(
                "wake:false", "wake", "medium", f"False wakes: {len(false)} of {len(wakes) or len(false)} wakes had no speech",
                f"התעוררויות לשווא: ב-{len(false)} מתוך {len(wakes) or len(false)} התעוררויות אף אחד לא דיבר",
                detail="Each false wake opens a paid voice session for ~12 s and can interrupt the room.",
                detail_he="כל התעוררות לשווא פותחת שיחת קול בתשלום לכ-12 שניות ויכולה להפריע בחדר.",
                evidence=[_line(r) for r in false[-MAX_EVIDENCE:]],
                cause=f"wake threshold {th} is sensitive for this room (TV, similar words such as \"Travis\")",
                cause_he=f"סף ההתעוררות {th} רגיש מדי לחדר הזה (טלוויזיה, מילים דומות)",
                fix=f"Raise voice.wake_threshold from {th} to {new}, or calibrate with `jarvis wake test --apply`.",
                fix_he=f"להעלות את סף מילת ההפעלה מ-{th} ל-{new}, או לכייל עם jarvis wake test --apply.",
                effort="minutes", action={"type": "setting", "key": "voice.wake_threshold", "value": new},
                count=len(false)))
        elif len(near) >= 10 and not false:
            out.append(Finding(
                "wake:near", "wake", "low", f"Wake near-misses: {len(near)}x he almost woke up",
                f"כמעט-התעוררויות: {len(near)} פעמים הוא כמעט התעורר",
                detail="Scores just below the threshold: either you were not heard clearly or the room is noisy.",
                detail_he="ציונים קצת מתחת לסף: או שלא שמע אתכם ברור או שיש רעש בחדר.",
                evidence=[_line(r) for r in near[-MAX_EVIDENCE:]],
                cause="the microphone is far / quiet, or the threshold is a little high for your voice",
                cause_he="המיקרופון רחוק או חלש, או שהסף קצת גבוה לקול שלכם",
                fix="Calibrate to your voice and room: `jarvis wake test --apply`.",
                fix_he="כיילו לקול ולחדר שלכם: jarvis wake test --apply.",
                effort="minutes", action={"type": "command", "command": "jarvis wake test --apply"}, count=len(near)))

    def _tools(self, recs, out, metrics) -> None:
        stats: dict[str, dict] = {}
        for r in recs:
            if r.get("code") != "TOOL_RESULT":
                continue
            name = str(r.get("tool") or str(r.get("msg") or "").split(" -> ")[0] or "?")
            code = (str(r.get("result_code") or "?") if "result_code" in r
                    else str(r.get("msg") or "").rsplit(" -> ", 1)[-1] if " -> " in str(r.get("msg") or "") else "?")
            if expected_tool_refusal(code, r.get("origin")):
                continue
            s = stats.setdefault(name, {"calls": 0, "fails": 0, "codes": {}, "lines": []})
            s["calls"] += 1
            if r.get("ok") is False:
                s["fails"] += 1
                s["codes"][code] = s["codes"].get(code, 0) + 1
                s["lines"].append(r)
        metrics["tools"] = {n: {"calls": s["calls"], "fails": s["fails"]} for n, s in sorted(stats.items())}
        metrics["_tool_names"] = sorted(stats)
        for name, s in sorted(stats.items(), key=lambda kv: -kv[1]["fails"]):
            if s["fails"] < 3 and not (s["fails"] >= 2 and s["fails"] / s["calls"] >= 0.5):
                continue
            rate = s["fails"] / s["calls"]
            top = max(s["codes"].items(), key=lambda kv: kv[1])[0] if s["codes"] else "?"
            config_like = top in ("FEATURE_OFF", "TOOL_FORBIDDEN", "SKILL_DISABLED", "GOOGLE_NOT_CONNECTED",
                                  "GOOGLE_SCOPE_MISSING", "MUSIC_NOT_PAIRED", "MUSIC_PLAYER_OFFLINE")
            out.append(Finding(
                f"tool:{name}", "tools", "high" if rate >= 0.5 and s["fails"] >= 3 else "medium",
                f"Tool {name} failed {s['fails']} of {s['calls']} calls (mostly {top})",
                f"הכלי {name} נכשל ב-{s['fails']} מתוך {s['calls']} קריאות (בעיקר {top})",
                detail="Across all conversations of the period. The user may have been told it worked.",
                detail_he="בכל השיחות בתקופה. ייתכן שנאמר למשתמש שזה הצליח.",
                evidence=[_line(r) for r in s["lines"][-MAX_EVIDENCE:]],
                cause=("a setting or connection is missing (" + top + ")") if config_like else
                f"the tool or the service behind it returns {top}",
                cause_he=("חסרה הגדרה או חיבור (" + top + ")") if config_like else f"הכלי או השירות שמאחוריו מחזיר {top}",
                fix=("Fix the connection on the Features / Connections page." if config_like else
                     "Open a conversation where it failed (Conversations page); attach the ticket draft if it is a bug."),
                fix_he=("תקנו את החיבור בעמוד היכולות או החיבורים." if config_like else
                        "פתחו שיחה שבה זה נכשל (עמוד השיחות). אם זה באג, צרפו את טיוטת הכרטיס."),
                effort="minutes" if config_like else "hour",
                action={"type": "open", "page": "#/features" if config_like else "#/conversations"},
                count=s["fails"]))

    def _claims(self, recs, out, metrics) -> None:
        claims = [r for r in recs if r.get("code") == "LIVE_FALSE_ACTION_CLAIM"]
        fixed = sum(r.get("code") == "LIVE_CLAIM_FIXED" for r in recs)
        metrics["false_action_claims"] = {"detected": len(claims), "fixed": fixed}
        if not claims:
            return
        out.append(Finding(
            "claims", "voice", "high" if len(claims) >= 2 else "medium",
            f"JARVES claimed an action no tool performed: {len(claims)}x",
            f"ג'ארביס אמר שעשה פעולה שאף כלי לא ביצע: {len(claims)} פעמים",
            detail=f"Detected and corrected aloud in the conversation ({fixed} made true afterwards).",
            detail_he=f"זוהה ותוקן בקול באותה שיחה ({fixed} תוקנו בפועל אחר כך).",
            evidence=[_line(r) for r in claims[-MAX_EVIDENCE:]],
            cause="the voice model answered from memory instead of calling the tool (a model behaviour)",
            cause_he="מודל הקול ענה מהזיכרון במקום להפעיל את הכלי (התנהגות של המודל)",
            fix="Open those conversations; if one tool is involved each time, attach the ticket draft so its tool "
                "description can be tightened.",
            fix_he="פתחו את השיחות האלה. אם זה תמיד אותו כלי, צרפו את טיוטת הכרטיס כדי לחדד את תיאור הכלי.",
            effort="hour", action={"type": "open", "page": "#/conversations"}, count=len(claims)))

    def _availability(self, hits: dict[str, list[dict]], out: list[Finding]) -> None:
        f = self.features
        for gid, g in AVAIL.items():
            enabled = [x for x in g["features"] if x in feat.CATALOG and f.enabled(x)]
            down = []
            for x in enabled:
                ok, why = f.available(x)
                if not ok:
                    down.append((x, why))
            rs = hits[gid]
            if not rs and not down:
                continue
            if not enabled and not rs:
                continue
            sev = "medium" if down or len(rs) >= 3 else "low"
            ev = [f"feature {x}: not available - {logs.redact(why)[:120]}" for x, why in down]
            ev += [_line(r) for r in rs[-(MAX_EVIDENCE - len(ev)):]] if len(ev) < MAX_EVIDENCE else []
            out.append(Finding(
                f"avail:{gid}", "availability", sev, g["title"] + (f" ({len(rs)}x in the log)" if rs else ""),
                g["title_he"] + (f" ({len(rs)} פעמים ביומן)" if rs else ""),
                detail=("Switched on but not available now: " + ", ".join(x for x, _ in down) + ". ") if down else "",
                detail_he=("דולק אבל לא זמין כרגע: " + ", ".join(x for x, _ in down) + ".") if down else "",
                evidence=ev[:MAX_EVIDENCE], cause=g["cause"], cause_he=g["cause_he"], fix=g["fix"],
                fix_he=g["fix_he"], effort="minutes", action=g["action"], count=max(1, len(rs))))

    def _update(self, out: list[Finding]) -> None:
        try:
            from .updates import UpdateChecker
            s = UpdateChecker(self.home, cfg=self.cfg).status()      # the cached answer: no network here
        except Exception:
            return
        if not s.get("available"):
            return
        v = (s.get("latest") or {}).get("version", "?")
        out.append(Finding(
            "update", "system", "low", f"A newer JARVES is available: {v} (you have {s.get('current')})",
            f"יש גרסה חדשה של ג'ארביס: {v} (מותקנת {s.get('current')})",
            detail="Found by the daily update check; nothing is installed without your approval.",
            detail_he="נמצא בבדיקת העדכונים היומית. שום דבר לא מותקן בלי אישור שלכם.",
            evidence=[f"updates.json: latest {v}, checked {s.get('checked_at') or '?'}"],
            cause="a new release was published", cause_he="יצאה גרסה חדשה",
            fix="Run `jarvis update install` (backs up first, then opens the installer for you to confirm).",
            fix_he="הריצו jarvis update install (מגבה קודם ואז פותח את ההתקנה לאישור שלכם).",
            effort="minutes", action={"type": "command", "command": "jarvis update install"}))

    # ---- system
    def _system(self, out: list[Finding], metrics: dict) -> None:
        s = self.system(self.home) or {}
        metrics["system"] = s
        free = s.get("disk_free_gb")
        if isinstance(free, (int, float)) and free < 3:
            sev = "high" if free < 1 else "medium"
            out.append(Finding(
                "disk", "system", sev, f"Low disk space: {free:.1f} GB free", f"מעט מקום פנוי בדיסק: {free:.1f} GB",
                detail="Logs, history and backups need room; below 1 GB SQLite writes can fail.",
                detail_he="היומנים, ההיסטוריה והגיבויים צריכים מקום. מתחת ל-1 GB הכתיבה למסד הנתונים עלולה להיכשל.",
                evidence=[f"free {free:.2f} GB on the drive of {self.home.name or self.home}",
                          f"state folder {s.get('state_mb')} MB"],
                cause="the drive is nearly full", cause_he="הדיסק כמעט מלא",
                fix="Free space on the drive (Windows Storage settings); old JARVES backups can be removed on the "
                    "Backup page.",
                fix_he="פנו מקום בדיסק (הגדרות האחסון של Windows). אפשר למחוק גיבויים ישנים בעמוד הגיבוי.",
                effort="minutes", action={"type": "open", "page": "#/backup"}))
        mem = s.get("memory") or {}
        avail = mem.get("available_mb")
        if isinstance(avail, (int, float)) and avail < 500:
            out.append(Finding(
                "memory", "system", "medium", f"Low free memory: {avail} MB available",
                f"מעט זיכרון פנוי: {avail} MB",
                detail="Voice and the face window may stutter or be closed by the system.",
                detail_he="הקול וחלון הפנים עלולים לגמגם או להיסגר על ידי המערכת.",
                evidence=[f"available {avail} MB of {mem.get('total_mb')} MB"],
                cause="other programs use most of the memory", cause_he="תוכנות אחרות משתמשות ברוב הזיכרון",
                fix="Close programs you do not need; on the Pi keep only JARVES running.",
                fix_he="סגרו תוכנות שלא צריך. ב-Raspberry Pi השאירו רק את ג'ארביס.", effort="minutes"))
        size = s.get("state_mb")
        if isinstance(size, (int, float)) and size > 2000:
            keep = int(self.cfg.history.keep_days)
            out.append(Finding(
                "state_size", "system", "low", f"The JARVES data folder is large: {size:.0f} MB",
                f"תיקיית הנתונים של ג'ארביס גדולה: {size:.0f} MB",
                detail=f"Conversation history is kept {keep} days.", detail_he=f"היסטוריית השיחות נשמרת {keep} ימים.",
                evidence=[f"state folder {size} MB"], cause="long history retention and backups",
                cause_he="שמירת היסטוריה ארוכה וגיבויים",
                fix=f"Keep history {max(30, keep // 2)} days instead of {keep}.",
                fix_he=f"לשמור היסטוריה {max(30, keep // 2)} ימים במקום {keep}.",
                effort="minutes", action={"type": "setting", "key": "history.keep_days", "value": max(30, keep // 2)}))

    # ---- config sanity
    def _config(self, out: list[Finding]) -> None:
        c = self.cfg
        v, h, ai = c.voice, c.history, c.ai

        def add(fp, sev, title, title_he, cause, cause_he, fix, fix_he, action=None, evidence=None):
            out.append(Finding(f"config:{fp}", "config", sev, title, title_he, evidence=evidence or [], cause=cause,
                               cause_he=cause_he, fix=fix, fix_he=fix_he, effort="minutes", action=action,
                               detail="config.toml (applies at the next Core start).",
                               detail_he="config.toml (חל בהפעלה הבאה של ג'ארביס)."))
        if c.link.host not in ("127.0.0.1", "localhost", "::1"):
            add("link_host", "high", f"The face link listens on the network ({c.link.host})",
                f"החיבור לפנים פתוח לרשת ({c.link.host})",
                "link.host is not the loopback address: other devices can try to connect to the Core",
                "link.host אינו הכתובת המקומית: מכשירים אחרים יכולים לנסות להתחבר",
                "Set link.host = \"127.0.0.1\" in config.toml (the phone uses the paired dashboard instead).",
                "הגדירו link.host = \"127.0.0.1\" ב-config.toml (הטלפון משתמש בלוח הבקרה המוצמד).",
                evidence=[f"link.host = {c.link.host}"])
        if ai.provider == "openai" and ai.daily_request_limit == 0:
            add("no_ai", "high", "The daily AI request limit is 0: AI answers are off",
                "מגבלת בקשות ה-AI היומית היא 0: תשובות ה-AI כבויות", "ai.daily_request_limit = 0",
                "ai.daily_request_limit = 0", "Set ai.daily_request_limit to 200 (the default).",
                "להגדיר את מגבלת הבקשות היומית ל-200 (ברירת המחדל).",
                {"type": "setting", "key": "ai.daily_request_limit", "value": 200}, ["ai.daily_request_limit = 0"])
        if v.progress_after_s == 0:
            add("no_progress", "medium", "No \"still checking\" note during slow lookups",
                "אין הודעת \"עוד בודק\" בזמן חיפוש איטי", "voice.progress_after_s = 0: a slow lookup is pure silence",
                "voice.progress_after_s = 0: חיפוש איטי הוא שקט מוחלט",
                "Set voice.progress_after_s to 2.5.", "להגדיר את voice.progress_after_s ל-2.5.",
                {"type": "setting", "key": "voice.progress_after_s", "value": 2.5}, ["voice.progress_after_s = 0"])
        elif v.progress_after_s >= h.dead_air_s:
            new = round(max(1.0, h.dead_air_s / 2), 1)
            add("progress_late", "medium", f"\"Still checking\" comes after {v.progress_after_s:g} s, after the "
                f"dead-air limit {h.dead_air_s:g} s", f"הודעת \"עוד בודק\" מגיעה אחרי {v.progress_after_s:g} שניות, "
                f"אחרי גבול השתיקה ({h.dead_air_s:g})", "inconsistent: silence is flagged before JARVES says anything",
                "לא עקבי: שתיקה מסומנת כבעיה לפני שג'ארביס אומר משהו", f"Set voice.progress_after_s to {new}.",
                f"להגדיר את voice.progress_after_s ל-{new}.",
                {"type": "setting", "key": "voice.progress_after_s", "value": new},
                [f"voice.progress_after_s = {v.progress_after_s}", f"history.dead_air_s = {h.dead_air_s}"])
        if v.backend_deadline_s == 0:
            add("no_deadline", "medium", "A stuck lookup is never given up", "חיפוש תקוע אף פעם לא מבוטל",
                "voice.backend_deadline_s = 0 (the 19 Oct ~90 s stall)", "voice.backend_deadline_s = 0",
                "Set voice.backend_deadline_s to 25.", "להגדיר את voice.backend_deadline_s ל-25.",
                {"type": "setting", "key": "voice.backend_deadline_s", "value": 25.0}, ["voice.backend_deadline_s = 0"])
        elif v.backend_deadline_s >= h.stall_s:
            new = float(max(5, int(h.stall_s) - 5))
            add("deadline_late", "low", f"The lookup deadline ({v.backend_deadline_s:g} s) is after the stall limit "
                f"({h.stall_s:g} s)", f"זמן הוויתור על חיפוש ({v.backend_deadline_s:g}) ארוך מגבול התקיעה ({h.stall_s:g})",
                "every give-up-and-retry is also reported as a stalled request",
                "כל ויתור וניסיון חוזר מדווח גם כבקשה תקועה", f"Set voice.backend_deadline_s to {new:g}.",
                f"להגדיר את voice.backend_deadline_s ל-{new:g}.",
                {"type": "setting", "key": "voice.backend_deadline_s", "value": new},
                [f"voice.backend_deadline_s = {v.backend_deadline_s}", f"history.stall_s = {h.stall_s}"])
        if ai.daily_request_limit and h.llm_review_daily_max > ai.daily_request_limit // 4:
            new = max(1, ai.daily_request_limit // 10)
            add("review_share", "low", f"AI reviews may use up to {h.llm_review_daily_max} of {ai.daily_request_limit} "
                "daily requests", f"בדיקות המודל יכולות לנצל עד {h.llm_review_daily_max} מתוך {ai.daily_request_limit} "
                "הבקשות היומיות", "history.llm_review_daily_max is more than a quarter of ai.daily_request_limit",
                "המכסה של בדיקות המודל גדולה מרבע מהמגבלה היומית", f"Set history.llm_review_daily_max to {new}.",
                f"להגדיר את history.llm_review_daily_max ל-{new}.",
                {"type": "setting", "key": "history.llm_review_daily_max", "value": new})
        if c.log.level in ("DEBUG", "VERBOSE"):
            add("log_level", "low", f"Log level {c.log.level} is always on", f"רמת היומן {c.log.level} דולקת תמיד",
                "verbose logs fill the disk and rotate useful history out", "יומן מפורט ממלא את הדיסק ודוחק היסטוריה",
                "Set log.level to INFO (use `jarvis --log-level debug` only while debugging).",
                "להגדיר את log.level ל-INFO (רמת debug רק בזמן בדיקה).",
                {"type": "setting", "key": "log.level", "value": "INFO"})
        if not h.enabled:
            add("history_off", "medium", "Conversation history is off: self-diagnosis cannot see conversations",
                "היסטוריית השיחות כבויה: האבחון העצמי לא רואה שיחות", "history.enabled = false",
                "history.enabled = false", "Set history.enabled = true in config.toml (privacy mode keeps only timings).",
                "הגדירו history.enabled = true ב-config.toml (במצב פרטיות נשמרים רק זמנים).")
        if v.wake_threshold < 0.2 and v.wake_enabled:
            add("wake_low", "low", f"Very sensitive wake word (threshold {v.wake_threshold:g})",
                f"מילת הפעלה רגישה מאוד (סף {v.wake_threshold:g})",
                "near-miss words (\"hey Travis\", \"Jarvie\") wake him (QA 20 Oct: 4/20 at 0.15)",
                "מילים דומות מעירות אותו (בדיקה 20.10: 4 מתוך 20 בסף 0.15)",
                "Calibrate to your voice: `jarvis wake test --apply` (keeps it as low as your voice needs).",
                "כיילו לקול שלכם: jarvis wake test --apply (נשאר נמוך רק כמה שהקול שלכם צריך).",
                {"type": "command", "command": "jarvis wake test --apply"})

    # ---- features: gaps, setup, bundle, unused
    def _feature_gaps(self, out: list[Finding], recs: list[dict], since: float) -> None:
        from . import setup_wizard as sw
        f = self.features
        for s in f.list(wait=True):
            fid = s["id"]
            if not s["enabled"] or s["available"] or fid in AVAIL_FEATURES:
                continue
            h = sw.hint(fid, False, s["reason"])
            cmd = h.get("command")
            g = _guide(fid)
            step_he = step_en = ""
            try:
                from . import guides
                sh = guides.setup_hint(fid, f) or {}
                step_he, step_en = sh.get("next_step_he", ""), sh.get("next_step_en", "")
            except Exception:
                pass
            reason = logs.redact(str(s["reason"] or "not available"))[:200]
            out.append(Finding(
                f"feature:{fid}", "features", "medium" if s["risk"] != "high" else "low",
                f"{s['title_en']} is switched on but not available", f"{s['title_he']} דולק אבל לא זמין",
                detail=reason, detail_he=f"הסיבה: {reason}",
                evidence=[f"feature {fid}: enabled, not available - {reason}"],
                cause=reason, cause_he=reason if re.search(r"[֐-׿]", reason) else f"חסר תנאי מוקדם ({reason})",
                fix=(h.get("en") or step_en or f"Open the {fid} guide and finish its setup."),
                fix_he=(h.get("he") or step_he or "פתחו את המדריך של היכולת והשלימו את ההגדרה."),
                effort="minutes", action={"type": "command", "command": cmd} if cmd else
                ({"type": "open", "page": f"#/guide/{g.id}"} if g else {"type": "open", "page": "#/features"})))
        if sw.first_run(self.home):
            out.append(Finding(
                "setup", "features", "medium", "The setup was never finished", "ההגדרה הראשונית לא הושלמה",
                detail="Language, connection, names, wake word, voice and add-ons were not confirmed.",
                detail_he="השפה, החיבור, השמות, מילת ההפעלה, הקול והתוספים לא אושרו.",
                evidence=["no setup_state.json finished_at / setup_done marker"],
                cause="`jarvis setup` was not completed", cause_he="jarvis setup לא הושלם",
                fix="Run `jarvis setup` (about 5 minutes, every step can be skipped).",
                fix_he="הריצו jarvis setup (כ-5 דקות, אפשר לדלג על כל שלב).",
                effort="minutes", action={"type": "command", "command": "jarvis setup"}))
        for fid, checks in sw.BUNDLE.items():
            if fid not in feat.CATALOG or not f.enabled(fid):
                continue
            bad = []
            for chk in checks:
                try:
                    ok, d = chk()
                except Exception as e:
                    ok, d = False, f"{type(e).__name__}"
                if not ok:
                    bad.append(d)
            if bad:
                t = feat.CATALOG[fid]
                out.append(Finding(
                    f"bundle:{fid}", "features", "high", f"Part of {t.title_en} is missing from this install",
                    f"חלק מ{t.title_he} חסר בהתקנה הזאת", evidence=[f"{fid}: {b}"[:200] for b in bad][:MAX_EVIDENCE],
                    cause="the install is incomplete (a wheel, model or data file is missing)",
                    cause_he="ההתקנה חסרה (ספרייה, מודל או קובץ נתונים)",
                    fix=f"Run `jarvis features on {fid}` (installs supported locked prerequisites) or repair/update "
                        "JARVES.", fix_he=f"הריצו jarvis features on {fid} (מתקין רכיבים נתמכים) או תקנו/עדכנו את ג'ארביס.",
                    effort="minutes", action={"type": "command", "command": f"jarvis features on {fid}"}))
        # useful but off (never the privacy-sensitive SAFE_OFF ones)
        for fid, (en, he) in USEFUL_ON.items():
            if fid not in feat.CATALOG or fid in sw.SAFE_OFF or f.enabled(fid):
                continue
            t = feat.CATALOG[fid]
            out.append(Finding(
                f"useful:{fid}", "usage", "low", f"Worth switching on: {t.title_en} ({en})",
                f"כדאי להדליק: {t.title_he} ({he})", evidence=[f"feature {fid}: off"],
                cause="switched off", cause_he="כבוי", fix=f"Switch on {t.title_en} (Features page).",
                fix_he=f"להדליק את {t.title_he} (עמוד היכולות).", effort="minutes",
                action={"type": "feature_on", "feature": fid}))
        # on, available, but never used while JARVES is otherwise in use
        try:
            convs = self.store.rows("SELECT COUNT(*) n FROM sessions WHERE started >= ?", (since,))[0]["n"] or 0
        except Exception:
            convs = 0
        used = {str(r.get("tool") or "") for r in recs if r.get("code") == "TOOL_RESULT"}
        if convs >= 3:
            for fid, prefixes in USEFUL_TRY.items():
                if fid not in feat.CATALOG or not f.active(fid):
                    continue
                if any(u.startswith(prefixes) for u in used):
                    continue
                g = _guide(fid)
                say = g.say[0] if g is not None and g.say else ("", "")
                t = feat.CATALOG[fid]
                out.append(Finding(
                    f"unused:{fid}", "usage", "low", f"Never used in {convs} conversations: {t.title_en}",
                    f"לא השתמשתם ב{t.title_he} ב-{convs} שיחות", evidence=[f"no {', '.join(prefixes)}* tool call in "
                                                                        f"the period; {convs} conversations"],
                    cause="maybe it is not known that he can do it", cause_he="אולי לא ידוע שהוא יודע לעשות את זה",
                    fix=f"Try saying: \"{say[1] or say[0]}\"" if say[0] else f"Open the {t.title_en} guide.",
                    fix_he=f"נסו להגיד: \"{say[0]}\"" if say[0] else f"פתחו את המדריך של {t.title_he}.",
                    effort="minutes", action={"type": "open", "page": f"#/guide/{g.id}"} if g else None))

    # ---- AI pass: the existing diagnose review only
    def _ai_pass(self, ai: bool | None, since: float, t0: float) -> dict:
        from . import diagnose
        from .store import delegation_cost
        info: dict = {"used": False, "reviewed": 0, "found": 0, "cost_usd": 0.0, "skipped": ""}
        want = bool(self.db.setting("self_check.ai_pass")) if ai is None else bool(ai)
        if not want:
            info["skipped"] = "AI pass off for this run (rules only)"
            return info
        st = self.store
        cands = st.rows("SELECT id FROM sessions WHERE started >= ? AND kind IN ('live','realtime') ORDER BY started "
                        "DESC LIMIT 20", (since,))
        todo = [c["id"] for c in cands if not st.meta(f"llm_reviewed:{c['id']}")]
        if not todo:
            info["skipped"] = "no unreviewed voice conversation in the period"
            return info
        why = diagnose.review_skip_reason(st, self.cfg, todo[0], have_client=self.ai_client is not None)
        if why:
            info["skipped"] = why
            return info
        budget, own, made = self.budget, None, None
        try:
            if budget is None:
                from .budget import Budget
                from .memory import MemoryStore
                own = MemoryStore(self.home / "memory.db")
                budget = made = Budget(own, self.cfg.ai)
            limit = budget.limit("ai_requests")
            if budget.remaining("ai_requests") < max(5, limit // 10):
                info["skipped"] = f"daily AI budget nearly used ({budget.remaining('ai_requests')} of {limit} left)"
                return info
            n = max(0, int(self.db.setting("self_check.ai_sessions") or 0))
            mark = st.rows("SELECT COALESCE(MAX(id), 0) m FROM delegations")[0]["m"]
            for sid in todo[:n]:
                if diagnose.review_skip_reason(st, self.cfg, sid, have_client=self.ai_client is not None):
                    continue
                info["found"] += len(diagnose.llm_review(st, self.cfg, sid, client=self.ai_client, budget=budget))
                info["reviewed"] += 1
            rows = st.rows("SELECT * FROM delegations WHERE id > ? AND kind = 'review'", (mark,))
            info["cost_usd"] = round(sum(delegation_cost(r, self.cfg.pricing) for r in rows), 5)
            info["used"] = info["reviewed"] > 0
            if not info["used"]:
                info["skipped"] = "every candidate was already reviewed or not reviewable"
        finally:
            if made is not None:
                made.close()
            if own is not None:
                own.close()
        return info

    # ---- conversations: the existing rule-based diagnosis, listed by issue id
    def _conversations(self, out: list[Finding], since: float, days: int) -> None:
        from . import diagnose
        st = self.store
        diagnose.run(st, self.cfg, days=days)
        issues = [i for i in st.issues("open") if i["last_seen"] >= since]
        if not issues:
            return
        top = [i for i in issues if i["severity"] in ("high", "medium")][:3]
        for i in top:
            kind_he = ISSUE_KIND_HE.get(i["kind"], "בעיה בשיחה")
            out.append(Finding(
                f"issue:{i['fingerprint']}", "conversations", i["severity"], f"Conversations: {i['title']}"[:160],
                f"בשיחות: {kind_he} - {i['title']}"[:160],
                detail=f"Self-diagnosis issue #{i['id']}, seen in {i['count']} conversation(s).",
                detail_he=f"בעיה #{i['id']} מהאבחון העצמי, הופיעה ב-{i['count']} שיחות.",
                evidence=[f"issue #{i['id']} ({i['kind']}, x{i['count']}, last {_when(i['last_seen'])})"],
                cause=i.get("detail") or "", cause_he="ראו את הפרטים בעמוד הבעיות",
                fix="Open the Issues page for the evidence and mark it resolved once fixed.",
                fix_he="פתחו את עמוד הבעיות כדי לראות את ההוכחות, וסמנו כנפתר אחרי התיקון.",
                effort="hour", action={"type": "open", "page": "#/issues"}, count=int(i["count"])))
        rest = [i for i in issues if i not in top]
        if rest:
            out.append(Finding(
                "issues:other", "conversations", "low", f"{len(rest)} more open conversation issue(s)",
                f"עוד {len(rest)} בעיות פתוחות בשיחות",
                evidence=[f"issue #{i['id']} ({i['kind']}, {i['severity']}, x{i['count']})" for i in rest[:MAX_EVIDENCE]],
                cause="see the Issues page", cause_he="ראו את עמוד הבעיות",
                fix="Open the Issues page.", fix_he="פתחו את עמוד הבעיות.", effort="minutes",
                action={"type": "open", "page": "#/issues"}, count=len(rest)))


def _guide(fid: str):
    try:
        from . import guides
        return guides.for_feature(fid)
    except Exception:
        return None


# ---------------------------------------------------------------------- ranking, storage, views
CATEGORY_WEIGHT = {"health": 6, "logs": 5, "features": 4, "availability": 4, "voice": 3, "tools": 3, "budget": 3,
                   "wake": 2, "config": 2, "system": 2, "conversations": 1, "usage": 0}


def rank(found: list[Finding]) -> list[Finding]:
    """Most important first: severity, then category weight, then how often it happened. One per fingerprint."""
    seen: dict[str, Finding] = {}
    for f in found:
        if f.severity not in SEV:
            f.severity = "low"
        cur = seen.get(f.fingerprint)
        if cur is None or SEV[f.severity] > SEV[cur.severity]:
            seen[f.fingerprint] = f
    return sorted(seen.values(), key=lambda f: (-SEV[f.severity], -CATEGORY_WEIGHT.get(f.category, 0),
                                                -min(f.count, 1000), f.fingerprint))


def health_score(found) -> int:
    pen = {"high": 15, "medium": 6, "low": 1}
    return max(0, 100 - sum(pen.get(f.severity if isinstance(f, Finding) else f["severity"], 0) for f in found))


def ensure_schema(store) -> None:
    if getattr(store, "_selfcheck_schema", False):
        return
    with store._lock:
        store._db.executescript(SCHEMA)
        store._db.commit()
    store._selfcheck_schema = True


def _state(store, now: float) -> dict[str, dict]:
    out = {}
    for r in store.rows("SELECT * FROM selfcheck_state"):
        if r["status"] == "snoozed" and (r["until"] or 0) <= now:
            r["status"] = "open"                       # the snooze ran out
        out[r["fingerprint"]] = r
    return out


def _finding_row(r: dict, state: dict) -> dict:
    s = state.get(r["fingerprint"]) or {}
    d = {k: r[k] for k in ("rank", "fingerprint", "category", "severity", "title", "title_he", "detail", "detail_he",
                           "cause", "cause_he", "fix", "fix_he", "effort", "count")}
    d["evidence"] = json.loads(r["evidence"] or "[]")
    d["action"] = json.loads(r["action"]) if r["action"] else None
    d["state"] = s.get("status", "open")
    d["until"] = s.get("until")
    d["proposal_id"] = s.get("proposal_id")
    d["severity_he"], d["effort_he"] = SEV_HE.get(d["severity"], ""), EFFORT_HE.get(d["effort"] or "", "")
    d["changes_setting"] = bool(d["action"] and d["action"].get("type") in ("setting", "feature_on"))
    return d


def runs(store, limit: int = 30) -> list[dict]:
    ensure_schema(store)
    out = []
    for r in store.rows("SELECT * FROM selfcheck_runs ORDER BY id DESC LIMIT ?", (limit,)):
        out.append({**{k: r[k] for k in ("id", "started", "finished", "trigger", "days", "status", "summary",
                                         "summary_he", "score")},
                    "counts": json.loads(r["counts"] or "{}"), "ai": json.loads(r["ai"] or "{}")})
    return out


def summary(findings: list[dict], ai: dict | None = None) -> tuple[str, str]:
    """The short spoken summary ('3 issues, the main one is ...'), English and Hebrew."""
    open_ = [f for f in findings if f["state"] in ("open", "accepted")]
    cost = (ai or {}).get("cost_usd") or 0.0
    cost_en = f" The AI review cost about ${cost:.3f}." if cost else ""
    cost_he = f" הבדיקה בעזרת המודל עלתה בערך ${cost:.3f}." if cost else ""
    if not open_:
        return ("Self-check done: no issues found." + cost_en, "בדקתי את עצמי: הכל תקין, לא מצאתי בעיות." + cost_he)
    top = open_[0]
    n = len(open_)
    high = sum(f["severity"] == "high" for f in open_)
    en = (f"{n} issue{'s' if n != 1 else ''}" + (f" ({high} serious)" if high else "") +
          f", the main one is: {top['title']}. Suggested: {top['fix']}" + cost_en)
    he = ((f"מצאתי בעיה אחת" if n == 1 else f"מצאתי {n} בעיות") + (f", {high} מהן חמורות" if high > 1 else
                                                                      ", אחת חמורה" if high == 1 and n > 1 else "")
          + f". העיקרית: {top['title_he']}. מה לעשות: {top['fix_he']}" + cost_he)
    return en, he


def run_view(store, run_id: int | None = None, *, now: float | None = None) -> dict | None:
    """One run with its ranked findings (state merged), the trend against the previous run and the summary."""
    ensure_schema(store)
    now = time.time() if now is None else now
    r = (store.rows("SELECT * FROM selfcheck_runs WHERE id = ?", (run_id,)) if run_id is not None else
         store.rows("SELECT * FROM selfcheck_runs WHERE status != 'running' ORDER BY id DESC LIMIT 1"))
    if not r:
        return None
    r = r[0]
    state = _state(store, now)
    rows = store.rows("SELECT * FROM selfcheck_findings WHERE run_id = ? ORDER BY rank", (r["id"],))
    findings = [_finding_row(x, state) for x in rows]
    prev = store.rows("SELECT * FROM selfcheck_runs WHERE id < ? AND status != 'running' ORDER BY id DESC LIMIT 1",
                      (r["id"],))
    trend = None
    if prev:
        p = prev[0]
        pf = {x["fingerprint"]: x for x in store.rows("SELECT fingerprint, severity, count FROM selfcheck_findings "
                                                       "WHERE run_id = ?", (p["id"],))}
        cur = {f["fingerprint"] for f in findings}
        for f in findings:
            f["trend"] = "new" if f["fingerprint"] not in pf else (
                "worse" if f["count"] > pf[f["fingerprint"]]["count"] or SEV[f["severity"]] > SEV.get(
                    pf[f["fingerprint"]]["severity"], 0) else "same")
        trend = {"previous_run": p["id"], "previous_started": p["started"], "previous_score": p["score"],
                 "score_delta": (r["score"] or 0) - (p["score"] or 0) if r["score"] is not None else None,
                 "new": sorted(cur - set(pf)), "persisting": sorted(cur & set(pf)), "resolved": sorted(set(pf) - cur)}
    else:
        for f in findings:
            f["trend"] = "new"
    ai = json.loads(r["ai"] or "{}")
    visible = [f for f in findings if f["state"] in ("open", "accepted")]
    en, he = summary(findings, ai)
    return {"id": r["id"], "started": r["started"], "finished": r["finished"], "trigger": r["trigger"],
            "days": r["days"], "status": r["status"], "score": r["score"], "counts": json.loads(r["counts"] or "{}"),
            "metrics": json.loads(r["metrics"] or "{}"), "ai": ai, "findings": findings,
            "open": len(visible), "trend": trend, "summary": en, "summary_he": he}


# ---------------------------------------------------------------------- suggestions: accept / dismiss / snooze
def _autonomy(home: Path, cfg, db, features):
    from .autonomy import Autonomy
    return Autonomy(db, cfg=cfg, home=home, features=features)


def propose(finding: dict, *, home: Path, cfg, db, features=None, clock=time.time) -> int | None:
    """Turn a settings-changing suggestion into an autonomy proposal (the approvals flow). Never applies it.
    Returns the proposal id (an existing open proposal for the same change is reused)."""
    a = finding.get("action") or {}
    if a.get("type") == "setting" and a.get("key") == "voice.wake_threshold":
        applier, params = "wake_threshold", {"from": float(cfg.voice.wake_threshold), "to": float(a["value"])}
        fp = f"false_wake:{json.dumps(params, sort_keys=True)}"         # the same key autonomy.sync uses
    elif a.get("type") == "setting":
        from .config_edit import EDITABLE
        if a.get("key") not in EDITABLE:
            raise ValueError(f"{a.get('key')} is not a dashboard-editable setting")
        applier, params = "config_setting", {"key": a["key"], "value": a["value"]}
        fp = f"selfcheck:{finding['fingerprint']}:{json.dumps(params, sort_keys=True)}"
    elif a.get("type") == "feature_on":
        applier, params = "feature_on", {"feature": a["feature"]}
        fp = f"selfcheck:{finding['fingerprint']}:{json.dumps(params, sort_keys=True)}"
    else:
        return None
    old = db.row("SELECT id, status FROM proposals WHERE fingerprint = ?", (fp,))
    if old and old["status"] in ("proposed", "observed", "failed"):
        return old["id"]
    if old:                                            # decided before (applied / rejected / reverted): ask again
        fp = f"{fp}:{int(clock())}"
    from .autonomy import APPLIERS
    cur = db.x("INSERT INTO proposals (fingerprint, title, detail, evidence, applier, params, risk, status, created) "
               "VALUES (?,?,?,?,?,?,?,?,?)",
               (fp, finding["fix_he"][:200], f"Self-check: {finding['title']}. {finding['fix']}"[:600],
                "\n".join(finding.get("evidence") or [])[:2000], applier, json.dumps(params),
                APPLIERS[applier].risk, "proposed", clock()))
    pid = cur.lastrowid
    db.audit("selfcheck", "proposal_new", pid, {"finding": finding["fingerprint"], "applier": applier})
    logs.info("selfcheck", "SELFCHECK_PROPOSAL", "self-check suggestion proposed for approval", proposal_id=pid,
              applier=applier)
    return pid


def decide(store, fingerprint: str, op: str, *, by: str, home: Path, cfg, db=None, features=None, days: float = 7,
           clock=time.time) -> dict:
    """accept | dismiss | snooze | reopen one suggestion (by fingerprint) of the latest run."""
    ensure_schema(store)
    if op not in ("accept", "dismiss", "snooze", "reopen"):
        raise ValueError("op must be accept, dismiss, snooze or reopen")
    latest = run_view(store, now=clock())
    f = next((x for x in (latest or {}).get("findings", []) if x["fingerprint"] == fingerprint), None)
    if f is None:
        raise KeyError(fingerprint)
    now = clock()
    pid = f.get("proposal_id")
    status, until = {"accept": "accepted", "dismiss": "dismissed", "snooze": "snoozed", "reopen": "open"}[op], None
    if op == "snooze":
        until = now + max(1.0, float(days)) * 86400
    if op == "accept" and f["changes_setting"]:
        own = db is None
        if own:
            from .userdata import open_db
            db = open_db(home)
        try:
            pid = propose(f, home=home, cfg=cfg, db=db, features=features, clock=clock)
        finally:
            if own:
                db.close()
    with store._lock:
        store._db.execute("INSERT INTO selfcheck_state (fingerprint, status, until, proposal_id, decided_at, decided_by)"
                          " VALUES (?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET status=excluded.status, "
                          "until=excluded.until, proposal_id=COALESCE(excluded.proposal_id, proposal_id), "
                          "decided_at=excluded.decided_at, decided_by=excluded.decided_by",
                          (fingerprint, status, until, pid, now, by))
        store._db.commit()
    logs.info("selfcheck", "SELFCHECK_DECIDED", "suggestion decided", op=op, fingerprint=fingerprint, by=by,
              proposal_id=pid)
    return {"fingerprint": fingerprint, "state": status, "until": until, "proposal_id": pid,
            "needs_approval": bool(pid), "changes_setting": f["changes_setting"]}


# ---------------------------------------------------------------------- ticket draft (never posted)
def ticket_draft(view: dict) -> dict:
    """A GitHub-issue draft (Markdown) of one run: no conversation text, no AI-review titles, evidence redacted."""
    from . import __version__
    fs = [f for f in view["findings"] if f["state"] in ("open", "accepted")]
    top = fs[0]["title"] if fs else "no open findings"
    title = f"[self-check] {top}"[:120]
    lines = [f"Self-check run #{view['id']} ({_when(view['started'])}, trigger {view['trigger']}, last {view['days']} "
             f"days), score {view['score']}/100, JARVES {__version__} on {sys.platform}.", "",
             "| # | Severity | Finding | Suggested fix | Effort |", "|---|---|---|---|---|"]
    for f in fs:
        title_txt = f["title"] if not f["fingerprint"].startswith("issue:ai:") else "AI review finding (see Issues)"
        lines.append(f"| {f['rank']} | {f['severity']} | {title_txt.replace('|', '/')} | "
                     f"{(f['fix'] or '').replace('|', '/')} | {f['effort']} |")
    lines.append("")
    for f in fs[:8]:
        if f["fingerprint"].startswith("issue:ai:"):
            continue
        lines += [f"### {f['rank']}. {f['title']}", f"- Root cause (hypothesis): {f['cause']}", "- Evidence:"]
        lines += [f"  - `{logs.redact(e)[:200]}`" for e in f["evidence"]] or ["  - (none)"]
        lines.append("")
    m = view.get("metrics") or {}
    lat = m.get("latency") or {}
    lines += ["### Metrics", f"- replies {lat.get('replies')}, p50 {lat.get('p50_s')} s, p90 {lat.get('p90_s')} s",
              f"- starts {m.get('starts')}, crashes {m.get('crashes')}, errors {m.get('errors')}, warnings "
              f"{m.get('warnings')}", "", "Draft made locally by `jarvis diagnose --now --ticket`; review before "
              "posting. (#17)"]
    body = "\n".join(lines)
    body = logs.redact_pii(logs.redact(body))         # SW-V4 B: no secret, e-mail or phone number in a draft
    return {"title": logs.redact_pii(logs.redact(title)), "body": body}


# ---------------------------------------------------------------------- Core service: triggers + scheduled job
class ErrorCounter(logging.Handler):
    """Counts ERROR records of this process (log sink); calls on_burst from the logging thread."""

    def __init__(self, on_error: Callable[[], None]) -> None:
        super().__init__(logging.ERROR)
        self.on_error = on_error

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR and getattr(record, "component", "") != "selfcheck":
            try:
                self.on_error()
            except Exception:
                pass


class SelfCheckService:
    """Inside the Core: on-demand runs (voice tool), the weekly scheduler job, and automatic runs after an unclean
    stop / every start (self_check.on_start) or after self_check.error_burst errors, at most every
    self_check.min_interval_s. Automatic runs report through the alert engine (quiet hours: popup, never speech)."""

    def __init__(self, core, ops) -> None:
        self.core, self.ops = core, ops
        self.db = ops.db
        self._lock = asyncio.Lock()
        self._errors = 0
        self._sink: ErrorCounter | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()
        self.last: dict | None = None
        self.clock = time.time
        self._stopping = False
        self._checks: set[tuple[SelfCheck, threading.Event]] = set()   # running checks and their "thread ended" flags

    def active(self) -> bool:
        try:
            return self.core.features.active(FEATURE)
        except Exception:
            return True

    def make_check(self) -> SelfCheck:
        c = self.core
        return SelfCheck(c.home, c.cfg, db=self.db, features=c.features, components=c._health_components,
                         budget=getattr(c, "budget", None), clock=self.clock, current_session=logs._SESSION)

    async def run(self, trigger: str, *, days: int | None = None, ai: bool | None = None) -> dict:
        async with self._lock:
            if self._stopping:
                raise CheckCancelled()
            chk = self.make_check()
            ended = threading.Event()
            entry = (chk, ended)
            self._checks.add(entry)

            def work() -> dict:                       # the thread closes its own handles: nothing else touches them
                try:
                    return chk.run(trigger, days=days, ai=ai)
                finally:
                    try:
                        chk.close()
                    finally:
                        ended.set()
            try:
                self.last = await asyncio.to_thread(work)
            finally:
                if ended.is_set():
                    self._checks.discard(entry)
            self._errors = 0
            return self.last

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            ensure_job(self.ops.scheduler, self.db)
        except Exception:
            logs.error("selfcheck", "SELFCHECK_JOB_FAILED", "weekly self-check job not created", exc=True)
        self._sink = ErrorCounter(self._on_error)
        logs.add_sink(self._sink)
        self._spawn(self._startup())

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro, name="selfcheck")
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _on_error(self) -> None:                     # logging thread
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._count_error)

    def _count_error(self) -> None:
        self._errors += 1
        n = int(self.db.setting("self_check.error_burst") or 0)
        if n and self._errors >= n and not self._lock.locked():
            self._errors = 0
            self._spawn(self.auto("errors"))

    async def _startup(self) -> None:
        mode = str(self.db.setting("self_check.on_start") or "crash")
        if mode == "never":
            return
        prev = await asyncio.to_thread(previous_process, self.core.home, logs._SESSION, 30 * 86400, self.clock)
        if prev is None:
            return
        if not prev["stopped"]:
            from .runtime.shutdown import boot_time, same_boot
            if same_boot(prev.get("boot"), boot_time()) is False:
                logs.info("selfcheck", "SELFCHECK_SKIPPED", "the computer restarted since the last run; not a crash",
                          previous=prev.get("session"))
                if mode == "always":
                    await self.auto("restart")
                return
            await self.auto("crash", prev=prev)
        elif mode == "always":
            await self.auto("restart")

    def _due(self) -> bool:
        last = float(self.db.setting("self_check.last_auto") or 0)
        return self.clock() - last >= float(self.db.setting("self_check.min_interval_s") or 0)

    async def auto(self, trigger: str, prev: dict | None = None) -> dict | None:
        if not self.active() or not self._due() or self.core.stopping:
            return None
        self.db.set_setting("self_check.last_auto", self.clock(), actor="selfcheck")
        logs.info("selfcheck", "SELFCHECK_AUTO", "automatic self-check", trigger=trigger,
                  previous=(prev or {}).get("session"))
        try:
            v = await self.run(trigger)
        except CheckCancelled:
            return None
        except Exception:
            logs.error("selfcheck", "SELFCHECK_FAILED", "automatic self-check failed", exc=True)
            return None
        if v["open"] and getattr(self.ops, "alerts", None) is not None:
            high = any(f["severity"] == "high" and f["state"] == "open" for f in v["findings"])
            why = {"crash": "אחרי עצירה לא מסודרת", "restart": "אחרי הפעלה מחדש", "errors": "אחרי רצף שגיאות"}
            try:
                self.ops.alerts.raise_("selfcheck", f"בדיקה עצמית {why.get(trigger, '')}: {v['open']} ממצאים",
                                       v["summary_he"][:400], priority="high" if high else "normal", kind="selfcheck",
                                       dedupe_key=f"selfcheck:{v['id']}", ref=f"selfcheck:{v['id']}")
            except Exception:
                logs.error("selfcheck", "SELFCHECK_ALERT_FAILED", "self-check alert not raised", exc=True)
        return v

    async def stop(self) -> None:
        if self._sink is not None:
            logs.remove_sink(self._sink)
            self._sink = None
        self._stopping = True
        for chk, _ in list(self._checks):
            chk.cancel.set()                         # the running check ends between two steps
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for _, ended in list(self._checks):          # the worker thread has closed its handles before the Core closes its own
            await asyncio.to_thread(ended.wait, 30)
        self._checks.clear()


DEFAULT_SCHEDULE = "0 10 * * 0"                    # Sunday 10:00 local (owner's work week starts on Sunday)


def ensure_job(scheduler, db) -> str | None:
    """Create the weekly self-check job once (owner request 20 Oct: weekly by default). A job the user deleted is
    not recreated; its time is changed on the Jobs page or with `jarvis diagnose --schedule`."""
    jid = db.setting("self_check.job_id") or ""
    if jid:
        return jid if scheduler.job(jid) else None
    v = scheduler.preview(DEFAULT_SCHEDULE, "self check", name="בדיקה עצמית שבועית / weekly self-check",
                          action="tool", tool="self_check_run", tool_args={"trigger": "schedule"},
                          skills=["self_check"], owner="system", by="system", delivery=["report", "alert"])
    scheduler.approve(v["id"], by="system (owner request 20 Oct 2026: weekly self-check by default)")
    db.set_setting("self_check.job_id", v["id"], actor="system")
    logs.info("selfcheck", "SELFCHECK_JOB", "weekly self-check job created", job=v["id"], spec=DEFAULT_SCHEDULE)
    return v["id"]


def set_schedule(scheduler, db, when: str, *, by: str) -> dict | None:
    """Change (or with 'off' pause) the self-check job's time. Returns the job view (None when paused/removed)."""
    jid = ensure_job(scheduler, db) if not db.setting("self_check.job_id") else db.setting("self_check.job_id")
    job = scheduler.job(jid) if jid else None
    if when.strip().lower() in ("off", "never", "כבוי"):
        if job is not None:
            scheduler.pause(job["id"], by=by)
        return None
    if job is None:                                    # deleted earlier: make it again on explicit request
        db.set_setting("self_check.job_id", "", actor=by)
        jid = ensure_job(scheduler, db)
        job = scheduler.job(jid)
    v = scheduler.edit(job["id"], by=by, when=when)
    if v["status"] == "paused":
        v = scheduler.approve(job["id"], by=by)
    return v


def quiet_note(db, view: dict, scheduler) -> str:
    """Warn when the job's time is inside quiet hours (it still runs; its note is a silent popup then)."""
    from .alerts import in_quiet
    try:
        tz = scheduler.tz(view["tz"])
        nxt = view.get("next_run")
        if not nxt or not db.setting("alerts.quiet_enabled"):
            return ""
        if in_quiet(datetime.fromtimestamp(nxt, tz), db.setting("alerts.quiet_start"), db.setting("alerts.quiet_end")):
            return "inside quiet hours: the run is silent and its note waits as a popup"
    except Exception:
        return ""
    return ""


# ---------------------------------------------------------------------- voice tools
class RunArgs(BaseModel):
    days: int = Field(0, ge=0, le=90, description='0 = default 7')
    ai: bool | None = Field(None, description='also run the AI review')
    trigger: str = Field("voice")


class DecideArgs(BaseModel):
    number: int = Field(..., ge=1, le=50)
    decision: str = Field(..., description='accept | dismiss | snooze')
    days: int = Field(7, ge=1, le=90, description='snooze days')


def make_skill(service: SelfCheckService, registry=None):
    from .skills import NoArgs, Skill, Tool, ToolResult

    def lang_text(v: dict) -> str:
        return v["summary_he"] if getattr(service.core.cfg.voice, "language", "he") == "he" else v["summary"]

    class SelfCheckSkill(Skill):
        name = "self_check"
        description = "self-check: JARVES checks his own health, logs and settings and suggests improvements"

        def tools(self) -> list:
            async def run_(a: RunArgs) -> ToolResult:
                trig = a.trigger if a.trigger in TRIGGERS else "voice"
                v = await service.run(trig, days=a.days or None, ai=a.ai)
                items = [{"number": f["rank"], "severity": f["severity"], "title": f["title_he"], "fix": f["fix_he"],
                          "changes_setting": f["changes_setting"]} for f in v["findings"]
                         if f["state"] in ("open", "accepted")][:5]
                return ToolResult(v["status"] != "failed", lang_text(v),
                                  {"run": v["id"], "score": v["score"], "open": v["open"], "top": items,
                                   "ai_cost_usd": (v.get("ai") or {}).get("cost_usd", 0.0),
                                   "rule": "Say the summary in one or two sentences. Offer the top suggestion; a "
                                           "settings change is applied only after the user agrees (it asks again).",
                                   "dashboard": "#/selfcheck"},
                                  code="SELFCHECK_OK" if v["status"] == "done" else "SELFCHECK_PARTIAL")

            async def list_(_) -> ToolResult:
                st = None
                v = service.last
                if v is None:
                    from .store import Store
                    st = Store(service.core.home / "store.db")
                    try:
                        v = run_view(st)
                    finally:
                        st.close()
                if v is None:
                    return ToolResult(False, "עוד לא בדקתי את עצמי. אפשר לבקש: תבדוק את עצמך.", code="SELFCHECK_NONE")
                items = [{"number": f["rank"], "severity": f["severity"], "title": f["title_he"], "fix": f["fix_he"],
                          "state": f["state"], "changes_setting": f["changes_setting"]} for f in v["findings"]][:10]
                return ToolResult(True, lang_text(v), {"run": v["id"], "suggestions": items})

            async def decide_(a: DecideArgs) -> ToolResult:
                from .store import Store
                if a.decision not in ("accept", "dismiss", "snooze"):
                    return ToolResult(False, "decision must be accept, dismiss or snooze", code="SELFCHECK_BAD_OP")
                st = Store(service.core.home / "store.db")
                try:
                    v = run_view(st)
                    f = next((x for x in (v or {}).get("findings", []) if x["rank"] == a.number), None)
                    if f is None:
                        return ToolResult(False, "אין הצעה במספר הזה בבדיקה האחרונה.", code="SELFCHECK_NOT_FOUND")
                    r = decide(st, f["fingerprint"], a.decision, by="voice", home=service.core.home,
                               cfg=service.core.cfg, db=service.db, features=service.core.features, days=a.days)
                finally:
                    st.close()
                if a.decision != "accept" or not r["proposal_id"]:
                    return ToolResult(True, {"accept": "רשמתי שמקבלים את ההצעה.", "dismiss": "ההצעה הוסרה.",
                                             "snooze": f"דחיתי את ההצעה ל-{a.days} ימים."}[a.decision], r)
                confirm = getattr(registry, "confirm", None)
                if not (confirm and await confirm(f"לשנות: {f['fix_he']}?")):
                    return ToolResult(True, "השינוי מחכה לאישור בלוח הבקרה (עמוד בדיקה עצמית).",
                                      {**r, "applied": False}, code="SELFCHECK_NEEDS_APPROVAL")
                p = _autonomy(service.core.home, service.core.cfg, service.db, service.core.features).decide(
                    r["proposal_id"], True, by="voice")
                ok = p["status"] == "applied"
                return ToolResult(ok, ("שיניתי: " if ok else "השינוי נכשל: ") + (p["result"] or ""),
                                  {**r, "applied": ok, "proposal": p["id"]},
                                  code="SELFCHECK_APPLIED" if ok else "SELFCHECK_APPLY_FAILED")

            async def ticket_(_) -> ToolResult:
                from .store import Store
                st = Store(service.core.home / "store.db")
                try:
                    v = run_view(st)
                finally:
                    st.close()
                if v is None:
                    return ToolResult(False, "עוד לא בדקתי את עצמי.", code="SELFCHECK_NONE")
                t = ticket_draft(v)
                return ToolResult(True, "הכנתי טיוטת כרטיס בלוח הבקרה, בעמוד בדיקה עצמית. לא שלחתי אותה לשום מקום.",
                                  {"title": t["title"], "dashboard": "#/selfcheck"})

            return [Tool("self_check_run", "Run a SELF-CHECK (health, logs, settings): 'check yourself', 'תבדוק את עצמך'. Ranked problems and fixes; free unless AI review runs.",
                         run_, RunArgs, permission="read", timeout_s=120),
                    Tool("self_check_suggestions", "Numbered suggestions of the last self-check: 'what did it find', 'מה הצעות הבדיקה'.", list_, NoArgs,
                         permission="read"),
                    Tool("self_check_decide", "Accept, dismiss or snooze one suggestion by number, only after the user said so ('accept 2', 'תאשר 2'). Settings changes ask again.", decide_, DecideArgs, permission="action", timeout_s=120),
                    Tool("self_check_ticket", "Draft a ticket (Markdown, on the dashboard) from the last self-check: 'make a ticket', 'תכין פנייה'. Nothing is sent.", ticket_, NoArgs, permission="read")]

    return SelfCheckSkill()


ALLOWED_ACTIONS = {"self_check_decide"}


def attach(core, ops) -> SelfCheckService:
    svc = SelfCheckService(core, ops)
    core.registry.register(make_skill(svc, core.registry))
    core.registry.allowed_actions |= ALLOWED_ACTIONS
    svc.start()
    return svc


# ---------------------------------------------------------------------- feature registration
def _targets() -> dict:
    from .capabilities import PI_ROADMAP
    return {"windows": {"status": "implemented_unverified",
                        "evidence": "UNIT test_selfcheck.py (real store/log/scheduler/autonomy in tmp dirs, synthetic "
                                    "log files)",
                        "blocker": "not yet run on the owner's real logs and Core"},
            "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                               "blocker": "pure Python + SQLite (/proc/meminfo for memory); never run on a Raspberry "
                                          "Pi (no device in the test setup)"}}


feat.register(FEATURE, "בדיקה עצמית והצעות לשיפור", "Self-check & suggestions", True, skills=["self_check"],
              about="checks health, recent logs, settings and features on demand and weekly, then suggests ranked "
                    "fixes; settings change only after you approve",
              targets=_targets(), permissions=["read", "action (approval first)"],
              settings="dashboard Self-check page, Jobs page (weekly time), self_check.* settings")


# ---------------------------------------------------------------------- dashboard
def _ctx_check(ctx) -> SelfCheck:
    from .userdata import ctx_db
    return SelfCheck(ctx.home, ctx.cfg, store=ctx.store, db=ctx_db(ctx), features=feat._ctx_features(ctx),
                     clock=getattr(ctx, "clock", time.time))


def _settings(db) -> dict:
    return {k: db.setting(k) for k in SETTINGS}


SETTINGS = ("self_check.on_start", "self_check.error_burst", "self_check.min_interval_s", "self_check.days",
            "self_check.ai_pass", "self_check.ai_sessions")


def _job_view(ctx) -> dict | None:
    from .scheduler import Scheduler
    from .userdata import ctx_db
    db = ctx_db(ctx)
    jid = db.setting("self_check.job_id")
    if not jid:
        return None
    s = Scheduler(db)
    j = s.job(jid)
    return s.view(j) if j else None


def api_selfcheck(ctx, m, body, q=None):
    from .userdata import ctx_db
    ensure_schema(ctx.store)
    now = getattr(ctx, "clock", time.time)()
    v = run_view(ctx.store, now=now)
    return 200, {"latest": v, "history": runs(ctx.store, 30), "settings": _settings(ctx_db(ctx)),
                 "job": _job_view(ctx), "enabled": feat._ctx_features(ctx).enabled(FEATURE)}


def api_selfcheck_run_get(ctx, m, body, q=None):
    v = run_view(ctx.store, int(m.group(1)), now=getattr(ctx, "clock", time.time)())
    return (200, v) if v else (404, {"error": "no such self-check run"})


def api_selfcheck_run(ctx, m, body):
    b = body or {}
    chk = _ctx_check(ctx)
    ai = b.get("ai")
    days = b.get("days")
    v = chk.run("dashboard", days=int(days) if days else None, ai=None if ai is None else bool(ai))
    return 200, v


def api_selfcheck_decide(ctx, m, body):
    from .userdata import ctx_db
    b = body or {}
    try:
        r = decide(ctx.store, str(b.get("fingerprint") or ""), str(b.get("op") or ""), by="dashboard", home=ctx.home,
                   cfg=ctx.cfg, db=ctx_db(ctx), features=feat._ctx_features(ctx), days=float(b.get("days") or 7),
                   clock=getattr(ctx, "clock", time.time))
    except KeyError:
        return 404, {"error": "no such suggestion in the latest run"}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, r


def api_selfcheck_ticket(ctx, m, body, q=None):
    v = run_view(ctx.store, int(m.group(1)), now=getattr(ctx, "clock", time.time)())
    return (200, ticket_draft(v)) if v else (404, {"error": "no such self-check run"})


def api_selfcheck_settings(ctx, m, body):
    from .userdata import ctx_db
    db = ctx_db(ctx)
    b = body or {}
    try:
        for k, val in b.items():
            if k not in SETTINGS:
                raise ValueError(f"unknown setting {k}")
            if k == "self_check.on_start" and val not in ("crash", "always", "never"):
                raise ValueError("self_check.on_start must be crash, always or never")
            if k in ("self_check.error_burst", "self_check.days", "self_check.ai_sessions"):
                val = max(0, min(int(val), 1000))
            if k == "self_check.min_interval_s":
                val = max(0.0, float(val))
            if k == "self_check.ai_pass":
                val = bool(val)
            db.set_setting(k, val, actor="dashboard")
    except (TypeError, ValueError) as e:
        return 400, {"error": str(e)}
    return 200, _settings(db)


def dashboard_routes(register) -> None:
    register("GET", r"/api/selfcheck", api_selfcheck)
    register("GET", r"/api/selfcheck/(\d+)", api_selfcheck_run_get)
    register("GET", r"/api/selfcheck/(\d+)/ticket", api_selfcheck_ticket)
    register("POST", r"/api/selfcheck/run", api_selfcheck_run)
    register("POST", r"/api/selfcheck/decide", api_selfcheck_decide)
    register("POST", r"/api/selfcheck/settings", api_selfcheck_settings)


# ---------------------------------------------------------------------- CLI (jarvis diagnose --now / --history ...)
def cli_main(a, cfg, home: Path, *, out=print, ask=input) -> int:
    """The self-check part of `jarvis diagnose`. Returns the exit code (0 ok, 1 failed run, 2 bad use)."""
    from .store import Store
    from .userdata import open_db
    st = Store(home / "store.db", tier=cfg.ai.voice_service_tier, backend_model=cfg.ai.text_model)
    db = open_db(home)
    try:
        ensure_schema(st)
        if getattr(a, "schedule", None):
            from .scheduler import ScheduleError, Scheduler
            s = Scheduler(db)
            try:
                v = set_schedule(s, db, a.schedule, by="cli")
            except ScheduleError as e:
                out(f"schedule not changed: {e.message}")
                return 2
            if v is None:
                out("weekly self-check paused (jarvis diagnose --schedule \"every sunday at 10\" turns it on again)")
            else:
                note = quiet_note(db, v, s)
                out(f"self-check scheduled: {v['human']} (next {v['next_run_local']})" + (f"; {note}" if note else ""))
            return 0
        if getattr(a, "suggestion", None):
            try:
                r = decide(st, a.suggestion, a.op, by="cli", home=home, cfg=cfg, db=db, days=a.snooze_days)
            except KeyError:
                out(f"no suggestion {a.suggestion!r} in the latest self-check (see: jarvis diagnose --history)")
                return 2
            except ValueError as e:
                out(str(e))
                return 2
            out(f"{a.suggestion}: {r['state']}")
            if r.get("proposal_id"):
                p = db.row("SELECT * FROM proposals WHERE id=?", (r["proposal_id"],))
                out(f"proposed change #{p['id']}: {p['detail']}")
                ans = (ask("Apply this change now? [y/N] ") or "").strip().lower()
                if ans in ("y", "yes", "כן"):
                    res = _autonomy(home, cfg, db, feat.Features(cfg, home)).decide(p["id"], True, by="cli")
                    out(f"{res['status']}: {res['result']}")
                else:
                    out("not applied; approve it later on the dashboard (Self-check or Autonomy page)")
            return 0
        if getattr(a, "history", False):
            for r in runs(st, 20):
                c = r["counts"]
                out(f"#{r['id']:<4} {_when(r['started'])}  {r['trigger']:<9} score {r['score']!s:>3}  "
                    f"high {c.get('high', 0)} medium {c.get('medium', 0)} low {c.get('low', 0)}  {r['status']}")
            return 0
        chk = SelfCheck(home, cfg, store=st, db=db)
        try:
            ai = None if getattr(a, "ai", None) is None else bool(a.ai)
            v = chk.run("cli", days=getattr(a, "days", None), ai=ai)
        finally:
            chk.close()
        if getattr(a, "json", False):
            out(json.dumps(v, ensure_ascii=False, indent=1, default=str))
            return 0 if v["status"] != "failed" else 1
        out(f"SELF-CHECK #{v['id']}: score {v['score']}/100, {v['open']} open finding(s) "
            f"(last {v['days']} days, {v['metrics'].get('log_records', 0)} log records)")
        t = v.get("trend")
        if t:
            out(f"  vs #{t['previous_run']}: score {t['score_delta']:+d}, new {len(t['new'])}, "
                f"resolved {len(t['resolved'])}")
        for f in v["findings"]:
            if f["state"] not in ("open", "accepted"):
                continue
            out(f"{f['rank']:>2}. [{f['severity']:<6}] {f['title']}  ({f['category']}, effort {f['effort']})")
            for e in f["evidence"][:2]:
                out(f"      evidence: {e}")
            if f["cause"]:
                out(f"      cause: {f['cause']}")
            out(f"      fix: {f['fix']}" + ("   [accept: jarvis diagnose --suggestion "
                                            f"{f['fingerprint']} --op accept]" if f["changes_setting"] else ""))
        ai_ = v.get("ai") or {}
        out(f"AI pass: {'reviewed ' + str(ai_.get('reviewed')) + ' conversation(s), ~$' + format(ai_.get('cost_usd', 0), '.4f') if ai_.get('used') else 'not used (' + str(ai_.get('skipped') or 'off') + ')'}")
        out(f"summary: {v['summary']}")
        if getattr(a, "ticket", False):
            tk = ticket_draft(v)
            out("\n--- ticket draft (not posted) ---\n# " + tk["title"] + "\n\n" + tk["body"])
        out("details: `jarvis dashboard` -> Self-check")
        return 0 if v["status"] != "failed" else 1
    finally:
        db.close()
        st.close()


def add_cli_args(dg) -> None:
    """Extra flags of the existing `jarvis diagnose` parser."""
    dg.add_argument("--now", action="store_true", help="run a full self-check now (health, logs, settings, "
                    "features, conversations) and print ranked suggestions")
    dg.add_argument("--ai", dest="ai", action="store_true", default=None,
                    help="self-check: also run the AI review of recent conversations (budget and privacy rules apply)")
    dg.add_argument("--no-ai", dest="ai", action="store_false", help="self-check: rules only")
    dg.add_argument("--json", action="store_true", help="self-check: print the report as JSON")
    dg.add_argument("--ticket", action="store_true", help="self-check: also print a ticket draft (nothing is posted)")
    dg.add_argument("--history", action="store_true", help="list earlier self-check runs")
    dg.add_argument("--schedule", metavar="WHEN", help="weekly self-check time, e.g. \"every sunday at 10\" / "
                    "\"0 10 * * 0\"; \"off\" pauses it")
    dg.add_argument("--suggestion", metavar="FINGERPRINT", help="act on one suggestion of the latest self-check")
    dg.add_argument("--op", choices=["accept", "dismiss", "snooze", "reopen"], default="accept")
    dg.add_argument("--snooze-days", type=float, default=7)


def wants_selfcheck(a) -> bool:
    return bool(getattr(a, "now", False) or getattr(a, "history", False) or getattr(a, "schedule", None)
                or getattr(a, "suggestion", None))
