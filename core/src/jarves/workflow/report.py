"""Morning / daily report (#761): one page from the ledger plus read-only ``gh``. Hebrew-first text, English JSON fields.

Read-only toward the factory: it reads the ledger and GitHub metadata. The only thing written is the supervisor's
daily report file (<home>/reports/YYYY-MM-DD.md), one ledger event ``daily_report`` and one message to the manager
role. No notifications, no network in tests (``gh`` is injectable).
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import observability as obs

TZ = ZoneInfo("Asia/Jerusalem")
REPO = "igalgerman/jarves"
DAILY_HOUR = 8
GAP_LAYERS = ("supervisor", "autopilot", "core")
_GH_CACHE: dict = {}


def gh_merged(since: float, *, run=subprocess.run) -> tuple[bool, list[dict]]:
    """Merged PRs since ``since`` (read-only gh). Any failure returns (False, [])."""
    day = datetime.fromtimestamp(since, TZ).date().isoformat()
    cmd = ["gh", "pr", "list", "--repo", REPO, "--state", "merged", "--search", f"merged:>={day}",
           "--json", "number,title,mergedAt", "--limit", "100"]
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=15, encoding="utf-8")
        if proc.returncode != 0:
            return False, []
        rows = json.loads(proc.stdout or "[]")
        out = []
        for row in rows:
            merged = datetime.fromisoformat(str(row["mergedAt"]).replace("Z", "+00:00")).timestamp()
            if merged >= since:
                out.append({"number": int(row["number"]), "title": str(row["title"])[:200], "merged_at": merged})
        return True, sorted(out, key=lambda r: r["merged_at"])
    except Exception:  # noqa: BLE001  a missing gh, no network or bad JSON must never break the report
        return False, []


def local(ts: float | None) -> str:
    return "" if not ts else datetime.fromtimestamp(ts, TZ).strftime("%d.%m %H:%M")


def _dur(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _gap_reason(gap: dict, marks: list[dict]) -> str:
    near = [m for m in marks if gap["start"] - 60 <= m["ts"] <= gap["end"] + 60
            and m["kind"] in {"supervisor", "supervisor_status", "supervisor_error", "autopilot_idle", "autopilot_paused", "autopilot_stop"}]
    return near[-1]["summary"][:160] if near else f"no heartbeat from {gap['layer']}"


def build(store, hours: float = 24, now: float | None = None, gh=gh_merged) -> dict:
    now = store.clock() if now is None else now
    since = now - hours * 3600
    tl = obs.timeline(store, since=since, until=now, now=now)
    with store._tx() as db:
        tasks = store._all(db, "task")
        questions = [q for q in store._all(db, "question") if (q.get("status") or "pending") in {"pending", "deferred", "open"}]
        events = [dict(r) for r in db.execute("SELECT ts,kind,summary FROM workflow_events WHERE ts>=? AND ts<=? AND "
                                              "kind IN ('incident_opened','task_flow') ORDER BY id", (since, now))]
        ready = [t for t in tasks if t["state"] == "queued" and t.get("execution_allowed", True)
                 and not obs._is_incident_task(t) and store._ready(db, t)]
    from .store import task_order
    ok, prs = gh(since)
    verified = [{"id": t["id"], "title": t["title"]} for t in sorted(tasks, key=task_order)
                if t["state"] in {"verified", "done"} and t.get("updated_at", 0) >= since]
    stalled = [{"layer": g["layer"], "start": g["start"], "end": g["end"], "seconds": g["seconds"],
                "reason": _gap_reason(g, tl["marks"]), "ready_work": g["ready_work"]}
               for g in tl["gaps"] if g["layer"] in GAP_LAYERS]
    incidents = [{"ts": e["ts"], "summary": e["summary"]} for e in events if e["kind"] == "incident_opened"]
    workers = {"started": 0, "finished": 0, "blocked": 0}
    for e in events:
        if e["kind"] != "task_flow":
            continue
        try:
            flow = json.loads(e["summary"])
        except ValueError:
            continue
        if flow.get("to") == "running":
            workers["started"] += 1
        elif flow.get("to") in {"review", "verified", "done"} and flow.get("from") == "running":
            workers["finished"] += 1
        elif flow.get("to") == "blocked":
            workers["blocked"] += 1
    cards = sorted(({"id": q["id"], "title": q.get("title", ""), "minutes": q.get("minutes") or 0,
                     "asked_at": q.get("created_at"), "waiting_min": max(0, int((now - (q.get("created_at") or now)) // 60))}
                    for q in questions), key=lambda c: c["asked_at"] or now)
    queue = [{"id": t["id"], "title": t["title"], "lane": t["lane"], "priority": t.get("priority", 2)}
             for t in sorted(ready, key=task_order)[:5]]
    usage = None
    try:
        from . import autopilot, autopilot_loop as loop
        policy, _ = autopilot.load(store.path.parent)
        starts = [t for t in loop._load(store).get("starts", []) if now - t < 3600]
        usage = {"worker_starts_last_hour": len(starts), "max_starts_per_hour": policy.get("max_starts_per_hour")}
        used = obs.read_usage(store, now)
        if used:
            usage.update({k: used[k] for k in ("weekly_pct", "five_hour_pct") if k in used})
    except Exception:  # noqa: BLE001
        usage = None
    rep = {"generated_at": now, "generated_local": local(now), "tz": "Asia/Jerusalem",
           "window": {"since": since, "until": now, "hours": hours},
           "verdict": obs.verdict(store, now),
           "shipped": {"gh_ok": ok, "merged_prs": prs, "tasks_verified": verified},
           "stalled": stalled, "incidents": incidents, "workers": workers,
           "questions": {"count": len(cards), "oldest_wait_min": cards[0]["waiting_min"] if cards else 0, "cards": cards},
           "next": queue, "usage": usage}
    rep["he"] = text_he(rep)
    return rep


HE_VERDICT = {"working": "עובד", "idle_with_work": "בטלה עם עבודה ממתינה", "stopped": "עצר", "owner_wait": "ממתין לך", "unknown": "לא ידוע"}


def text_he(r: dict) -> str:
    v, w = r["verdict"], r["window"]
    lines = [f"דוח בוקר JARVES · {r['generated_local']} (שעון ישראל) · {w['hours']:g} שעות אחרונות",
             f"מצב המפעל: {HE_VERDICT.get(v['verdict'], v['verdict'])} ({v['reason']})", "", "מה שוחרר"]
    sh = r["shipped"]
    if sh["merged_prs"]:
        lines += [f"- PR {p['number']}: {p['title']} ({local(p['merged_at'])})" for p in sh["merged_prs"]]
    else:
        lines.append("- אין PR שמוזגו" if sh["gh_ok"] else "- נתוני GitHub לא זמינים")
    lines += [f"- משימה שנבדקה {t['id']}: {t['title']}" for t in sh["tasks_verified"]] or ["- אין משימות שנבדקו"]
    lines += ["", "מה נתקע"]
    lines += [f"- {g['layer']} {local(g['start'])}-{local(g['end'])} ({_dur(g['seconds'])}): {g['reason']}; עבודה ממתינה: {g['ready_work']}"
              for g in r["stalled"]] or ["- לא היו הפסקות"]
    lines += ["", "תקלות שנפתחו"]
    lines += [f"- {local(i['ts'])} {i['summary']}" for i in r["incidents"]] or ["- אין"]
    wk = r["workers"]
    lines += ["", f"עובדים: התחילו {wk['started']}, סיימו {wk['finished']}, נחסמו {wk['blocked']}", ""]
    q = r["questions"]
    lines.append(f"שאלות שמחכות לך: {q['count']}" + (f" (הוותיקה מחכה {_dur(q['oldest_wait_min'] * 60)})" if q["count"] else ""))
    lines += [f"- {c['id']}: {c['title']} (מחכה {_dur(c['waiting_min'] * 60)}, {c['minutes']} דק׳)" for c in q["cards"]]
    lines += ["", "מה המפעל יעשה הלאה"]
    lines += [f"- {n['id']} ({n['lane']}, P{n['priority']}): {n['title']}" for n in r["next"]] or ["- אין עבודה בתור"]
    if r["usage"]:
        lines += ["", f"שימוש: {r['usage']['worker_starts_last_hour']} הפעלות עובד בשעה האחרונה "
                      f"(תקרה {r['usage']['max_starts_per_hour']})"]
        if "weekly_pct" in r["usage"]:
            lines.append(f"מכסת Claude: {r['usage']['weekly_pct']:.0f}% שבועי, {r['usage'].get('five_hour_pct', 0):.0f}% ב־5 שעות")
    return "\n".join(lines)


def summary_lines(r: dict) -> str:
    """At most 5 short lines, for the manager message and the ledger event."""
    v = r["verdict"]
    return "\n".join([f"{r['generated_local']} {HE_VERDICT.get(v['verdict'], v['verdict'])}: {v['reason']}",
                      f"שוחרר: {len(r['shipped']['merged_prs'])} PR, {len(r['shipped']['tasks_verified'])} משימות נבדקו",
                      f"נתקע: {len(r['stalled'])} הפסקות, {len(r['incidents'])} תקלות",
                      f"שאלות שמחכות לך: {r['questions']['count']}",
                      f"הלאה: {len(r['next'])} משימות בתור"])


# ---- supervisor schedule ---------------------------------------------------------------------------------------
def due_date(now: float) -> str | None:
    """The Israel-local date (YYYY-MM-DD) whose 08:00 report is due now, else None. Uses zoneinfo, never a fixed offset."""
    dt = datetime.fromtimestamp(now, TZ)
    return dt.date().isoformat() if dt.hour >= DAILY_HOUR else None


def daily_tick(store, home: Path, now: float | None = None, gh=gh_merged) -> str:
    """Write today's report once (first tick at or after 08:00 Israel time). Returns the file path or '' when not due/done."""
    now = store.clock() if now is None else now
    day = due_date(now)
    if not day:
        return ""
    path = Path(home) / "reports" / f"{day}.md"
    if path.exists():
        return ""
    with store._tx() as db:
        done = db.execute("SELECT 1 FROM workflow_events WHERE kind='daily_report' AND summary LIKE ? LIMIT 1", (f"{day}%",)).fetchone()
    if done:
        return ""
    rep = build(store, 24, now, gh)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rep["he"] + "\n", encoding="utf-8")
    summary = summary_lines(rep)
    store.record_event("daily_report", f"{day} {path}\n{summary}")
    try:
        from . import messages
        messages.send(store, "supervisor", "manager", f"daily_report {day}", summary)
    except Exception:  # noqa: BLE001  the ledger event above is the durable record
        pass
    return str(path)
