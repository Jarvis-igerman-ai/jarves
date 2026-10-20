"""Dashboard Home page data (SW-V2 WP-K, Issue #17): one simple screen. What is connected, what is on/off, today's
cost, open issues, the last conversation, the privacy switch and ONE "what to do next" hint, in plain Hebrew and
English. Local files only: never contacts Google or OpenAI.
"""
from __future__ import annotations

import time
from datetime import datetime

from .health import read_snapshot
from .store import daily_costs


def _features(ctx) -> list[dict]:
    try:
        from .features import _ctx_features
        return _ctx_features(ctx).list()
    except Exception:                                 # the Home page must render even if a probe breaks
        return []


def _last_conversation(ctx) -> dict | None:
    from .dashboard import _session_summary
    rows = ctx.store.rows("SELECT * FROM sessions ORDER BY started DESC LIMIT 1")
    if not rows:
        return None
    s = _session_summary(ctx, rows[0])
    return {"id": s["id"], "started": s["started"], "duration_s": s["duration_s"], "typed": s["typed"],
            "live": s["live"], "user_turns": s["user_turns"], "first_user": s["first_user"], "issues": s["issues"],
            "cost": s["cost"]["total"]}


def api_home(ctx, _m, _body):
    from .google_setup import checklist
    from .privacy import state as privacy_state
    snap = read_snapshot(ctx.home / "health.json") or {}
    comp = snap.get("components") or {}
    fresh = snap.get("age_s", 1e9) < 120
    core = snap.get("core") if fresh else None
    g = checklist(ctx.home)
    feats = _features(ctx)
    today = datetime.now().date().isoformat()
    day = next((d for d in daily_costs(ctx.store, ctx.cfg.pricing, 1) if d["day"] == today), None)
    issues = ctx.store.issues("open")
    high = sum(i["severity"] == "high" for i in issues)
    ai = comp.get("ai") or {}
    music = next((f for f in feats if f["id"] == "music"), None)
    connections = [
        {"id": "google", "title_he": "גוגל (יומן + מייל)", "title_en": "Google (Calendar + Gmail)",
         "state": {"connected": "ok", "needs_reconnect": "warn"}.get(g["state"], "off"),
         "detail_en": {"connected": "connected", "needs_reconnect": "needs reconnect",
                       "ready_to_sign_in": "ready to sign in"}.get(g["state"], "not connected"),
         "detail_he": {"connected": "מחובר", "needs_reconnect": "צריך לחבר מחדש",
                       "ready_to_sign_in": "מוכן להתחברות"}.get(g["state"], "לא מחובר"),
         "link": "#/connections", "days_left": g["testing_days_left"]},
        {"id": "ai", "title_he": "בינה מלאכותית (OpenAI)", "title_en": "AI (OpenAI)",
         "state": "ok" if ai.get("status") == "ok" else "warn" if ai else "unknown",
         "detail_en": ai.get("detail") or ("not checked yet: start JARVES" if not fresh else "unknown"),
         "detail_he": "תקין" if ai.get("status") == "ok" else "לא ידוע עד שמפעילים את JARVES" if not ai else "בעיה",
         "link": "#/skills"},
        {"id": "music", "title_he": "מוזיקה (Pear)", "title_en": "Music (Pear Desktop)",
         "state": "ok" if music and music["active"] else "off",
         "detail_en": "ready" if music and music["active"] else (music or {}).get("reason") or "off",
         "detail_he": "מוכן" if music and music["active"] else "כבוי או לא זמין", "link": "#/features"},
    ]
    on = [f for f in feats if f["active"]]
    off = [f for f in feats if not f["active"]]
    # one hint, most important first
    if not fresh or core is None:
        nxt = ("JARVES לא רץ כרגע. מפעילים בפקודה jarvis.", "JARVES is not running. Start it with: jarvis", "")
    elif high:
        nxt = (f"יש {high} בעיות חשובות. כדאי להציץ בעמוד הבעיות.", f"{high} important issue(s): have a look.",
               "#/issues")
    elif g["state"] == "needs_reconnect":
        nxt = ("צריך לחבר מחדש את גוגל (פעם בשבוע). לוחצים Reconnect.", "Google needs its weekly reconnect.",
               "#/connections")
    elif g["state"] in ("not_connected", "ready_to_sign_in"):
        nxt = ("רוצה יומן ומייל? מחברים את גוגל: jarvis google start.",
               "Want calendar and mail? Connect Google: jarvis google start.", "#/connections")
    else:
        nxt = ("הכל תקין. אומרים \"היי ג'ארביס\" ומדברים.", "All good. Say \"Hey JARVES\" and talk.", "")
    status = ("ok" if fresh and core == "ready" and not high else "warn" if fresh and core else "off")
    return 200, {
        "status": status,
        "status_he": {"ok": "הכל עובד", "warn": "עובד, עם משהו לבדוק", "off": "JARVES כבוי"}[status],
        "status_en": {"ok": "Everything works", "warn": "Running, something to check", "off": "JARVES is off"}[status],
        "core": core, "live": bool(ctx.store.live_session()),
        "next": {"he": nxt[0], "en": nxt[1], "link": nxt[2]},
        "connections": connections,
        "features": {"on": [{"id": f["id"], "title_en": f["title_en"], "title_he": f["title_he"]} for f in on],
                     "off": [{"id": f["id"], "title_en": f["title_en"], "title_he": f["title_he"],
                              "reason": f["reason"] or "turned off",
                              "reason_he": f.get("reason_he") or ("" if f["reason"] else "כבוי")} for f in off]},
        "today": {"cost": round((day or {}).get("total", 0.0), 4), "conversations": (day or {}).get("sessions", 0),
                  "voice_minutes": (day or {}).get("voice_minutes", 0.0)},
        "issues": {"open": len(issues), "high": high},
        "last_conversation": _last_conversation(ctx),
        "privacy": privacy_state(ctx),
        "at": time.time()}


def dashboard_routes(register) -> None:
    register("GET", r"/api/home", api_home)
