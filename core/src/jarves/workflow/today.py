"""Short 'what needs me now' text for terminals (#732): the claude-j wrapper prints it and keeps it on screen.

Same rules as the dashboard Today screen (static/today.js): open questions oldest first, running tasks, blocked
tasks. Read-only, model-free, at most about ten lines, plain Hebrew. Never prints answer text.
"""
from __future__ import annotations

from .report import local

OPEN = {"pending", "deferred", "open"}
TOP = 3
TITLE_MAX = 60


def _short(text: str, limit: int = TITLE_MAX) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ago(seconds: float) -> str:
    minutes = max(int(seconds // 60), 0)
    if minutes < 1:
        return "עכשיו"
    if minutes < 60:
        return f"לפני {minutes} דק'"
    hours = minutes // 60
    return f"לפני {hours} שעות" if hours < 48 else f"לפני {hours // 24} ימים"


def build(snapshot: dict, now: float) -> dict:
    tasks = snapshot.get("tasks") or []
    by_id = {t.get("id"): t for t in tasks}
    questions = [q for q in snapshot.get("questions") or [] if q.get("held") or (q.get("status") or "pending") in OPEN]
    questions.sort(key=lambda q: (float(q.get("created_at") or 0), (by_id.get(q.get("task_id")) or {}).get("priority", 2)))
    running = sorted((t for t in tasks if t.get("state") == "running"), key=lambda t: str(t.get("id")))
    blocked = sorted((t for t in tasks if t.get("state") == "blocked"),
                     key=lambda t: (t.get("priority", 2), float(t.get("updated_at") or 0)))
    queued = sum(1 for t in tasks if t.get("state") == "queued" and t.get("execution_allowed") is not False)
    needs = [{"id": q.get("id"), "title": _short(q.get("title")), "minutes": int(q.get("minutes") or 0),
              "age_s": max(now - float(q.get("created_at") or now), 0), "task_id": q.get("task_id") or ""}
             for q in questions[:TOP]]
    return {"now": now, "running": [{"id": t.get("id"), "title": _short(t.get("title"))} for t in running],
            "needs_total": len(questions), "needs": needs, "blocked_total": len(blocked),
            "blocked": [{"id": t.get("id"), "title": _short(t.get("title"))} for t in blocked[:TOP]], "queued": queued}


def text(view: dict) -> str:
    lines = [f"JARVES עכשיו · {local(view['now'])} (שעון ישראל)"]
    run = view["running"]
    if run:
        ids = ", ".join(str(t["id"]) for t in run[:4]) + (f" ועוד {len(run) - 4}" if len(run) > 4 else "")
        lines.append(f"▶ עובד עכשיו: {len(run)} · {ids}")
    else:
        lines.append(f"▶ אין עובד פעיל · {view['queued']} משימות מוכנות בתור")
    if view["needs_total"]:
        lines.append(f"◆ צריך אותך: {view['needs_total']} שאלות (הוותיקה ראשונה)")
        for i, q in enumerate(view["needs"], 1):
            mins = f" · כ־{q['minutes']} דק'" if q["minutes"] else ""
            lines.append(f"  {i}. {q['title']}{mins} · {_ago(q['age_s'])}")
        if view["needs_total"] > TOP:
            lines.append(f"  ועוד {view['needs_total'] - TOP} בלוח הבקרה")
    else:
        lines.append("◆ צריך אותך: כלום כרגע")
    if view["blocked_total"]:
        ids = ", ".join(str(t["id"]) for t in view["blocked"]) + (" …" if view["blocked_total"] > TOP else "")
        lines.append(f"■ תקוע: {view['blocked_total']} · {ids}")
    else:
        lines.append("■ תקוע: אין")
    lines.append("הצעד הבא: " + ("לענות בלשונית שאלות · jarvis workflow open" if view["needs_total"]
                                  else "אין מה לעשות עכשיו · לוח הבקרה: jarvis workflow open"))
    return "\n".join(lines)
