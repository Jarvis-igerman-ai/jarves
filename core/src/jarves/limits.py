"""No silent limits: ONE helper that turns "used / max" of every daily cap into a state and the words for it.

Incident 23 Oct: the 30 live-voice minutes were used up; each GPT-Live session then started with ~0 s left, ended by
the time limit one second later and reconnected until LIVE_RESUME_LIMIT, and nothing told the user it was a limit.
Owner rule: a limit that stops or degrades a feature must say WHICH limit, used/max, and how to raise it, on every
surface. The Core (spoken notice, face error, health component, snapshot), `jarvis status`, `jarvis doctor`,
`jarvis selftest`, the tray tooltip and the dashboard all read this module, so they all say the same thing.
Pure functions: no I/O.
"""
from __future__ import annotations

WARN_FRACTION = 0.8          # "about to run out" starts here

# kind (Budget kind) -> spec. unit: how used/max are shown. min_useful: below this much left a start is pointless.
SPECS: dict[str, dict] = {
    "voice_seconds": {"id": "voice", "component": "voice_budget", "code": "VOICE_DAILY_LIMIT", "key": "ai.daily_realtime_minutes",
                      "scale": 60.0, "unit_en": "min", "unit_he": "דקות", "min_useful": 20.0, "suggest": 2, "floor": 60,
                      "what_en": "voice", "what_he": "הקול החי", "set": "jarvis budget set --voice-minutes {n}",
                      "fallback_en": "or use push-to-talk", "fallback_he": "או לדבר בלחיצה"},
    "ai_requests": {"id": "requests", "component": "request_budget", "code": "AI_DAILY_LIMIT", "key": "ai.daily_request_limit",
                    "scale": 1.0, "unit_en": "requests", "unit_he": "בקשות", "min_useful": 1.0, "suggest": 2, "floor": 200,
                    "what_en": "AI request", "what_he": "בקשות ה-AI", "set": "jarvis budget set --requests {n}",
                    "fallback_en": "or wait until tomorrow", "fallback_he": "או לחכות למחר"},
    "delegation_usd_micros": {"id": "delegation", "component": "delegation_budget", "code": "DELEGATION_DAILY_LIMIT",
                              "key": "ai.delegation_daily_usd", "scale": 1_000_000.0, "unit_en": "USD", "unit_he": "דולר",
                              "min_useful": 0.0, "suggest": 2, "floor": 1, "what_en": "spoken-lookup spend",
                              "what_he": "עלות החיפושים בקול", "set": "jarvis budget set --daily-usd {n}",
                              "fallback_en": "lookups fall back to the server path", "fallback_he": "החיפושים עוברים לנתיב השרת"},
}
KINDS = tuple(SPECS)


def _fmt(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else (f"{x:.2f}" if x < 10 else f"{x:.1f}")


def view(kind: str, used: float, limit: float) -> dict:
    """used/limit are in the Budget's raw units (seconds, requests, micro-USD). level: ok | warn | exhausted."""
    s = SPECS[kind]
    used, limit = max(0.0, float(used)), max(0.0, float(limit))
    left = max(0.0, limit - used)
    level = "ok"
    if left < s["min_useful"] or left <= 0:
        level = "exhausted"
    elif used >= WARN_FRACTION * limit:
        level = "warn"
    u, m = _fmt(used / s["scale"]), _fmt(limit / s["scale"])
    suggest = max(int(round(limit / s["scale"] * s["suggest"])), s["floor"])
    how = s["set"].format(n=suggest)
    unit_en, unit_he = s["unit_en"], s["unit_he"]
    pct = f"{u}/{m} {unit_en} today"
    pct_he = f"{u}/{m} {unit_he} היום"
    action_en = f"raise {s['key']} ({how}) {s['fallback_en']}"
    action_he = f"להעלות את {s['key']} ({how}) {s['fallback_he']}"
    if level == "exhausted":
        en, he = f"{s['what_en']} limit reached ({pct})", f"הגעת למגבלה היומית של {s['what_he']} ({pct_he})"
    elif level == "warn":
        en, he = f"{s['what_en']} limit nearly used ({pct})", f"המגבלה היומית של {s['what_he']} כמעט נוצלה ({pct_he})"
    else:
        en, he = f"{s['what_en']} {pct}", f"{s['what_he']} {pct_he}"
    return {"kind": kind, "id": s["id"], "component": s["component"], "code": s["code"], "key": s["key"],
            "level": level, "used": round(used / s["scale"], 2), "max": round(limit / s["scale"], 2),
            "used_min": round(used / 60, 1) if kind == "voice_seconds" else None,
            "left": round(left / s["scale"], 2), "left_s": round(left, 1) if kind == "voice_seconds" else None,
            "text_en": en, "text_he": he, "action_en": action_en, "action_he": action_he,
            "line_en": f"{en} - {action_en}" if level != "ok" else en}


def spoken(v: dict, language: str = "he") -> str:
    """The short sentence played locally (no cloud) when a limit is hit. Hebrew or English by voice.language."""
    s = SPECS[v["kind"]]
    u, m = _fmt(v["used"]), _fmt(v["max"])
    if language == "en":
        return (f"The daily {s['what_en']} limit is used up: {u} of {m} {s['unit_en']} today. "
                f"To raise it, set {s['key']} in config.toml, {s['fallback_en']}.")
    return (f"המגבלה היומית של {s['what_he']} נגמרה: {u} מתוך {m} {s['unit_he']} היום. "
            f"כדי להעלות אותה, לשנות את {s['key']} בקובץ ההגדרות, {s['fallback_he']}.")


def health_entry(v: dict) -> tuple[str, str, str]:
    """(status, detail, action) for the component: degraded only while exhausted; the 80% warning is shown
    (detail + action) without making the whole Core look broken."""
    if v["level"] == "exhausted":
        return "degraded", v["text_en"], v["action_en"]
    if v["level"] == "warn":
        return "ok", v["text_en"], v["action_en"]
    return "ok", v["text_en"], ""


def views_from(used_of, limit_of) -> list[dict]:
    """used_of(kind) / limit_of(kind) in raw units -> the view of every cap that has a limit (> 0 or exhausted-by-0)."""
    return [view(k, used_of(k), limit_of(k)) for k in KINDS]


def worst(views: list[dict]) -> list[dict]:
    """The non-ok views, exhausted first (what `jarvis status` / the tray / the dashboard show)."""
    return sorted((v for v in views if v["level"] != "ok"), key=lambda v: v["level"] != "exhausted")


def status_lines(views: list[dict]) -> list[str]:
    return [("LIMIT REACHED: " if v["level"] == "exhausted" else "WARNING: ") + v["line_en"] for v in worst(views)]


def limit_of(ai, kind: str) -> int:
    """The configured ceiling in raw units (same numbers as Budget.limit) from an AIConfig."""
    if kind == "voice_seconds":
        return int(ai.daily_realtime_minutes) * 60
    if kind == "ai_requests":
        return int(ai.daily_request_limit)
    return int(round(float(ai.delegation_daily_usd) * 1_000_000))


def views_from_home(home, ai) -> list[dict]:
    """Views read straight from memory.db (no Core needed): `jarvis doctor`, selftest, `jarvis status` offline."""
    from pathlib import Path
    from .memory import MemoryStore
    p = Path(home) / "memory.db"
    if not p.is_file():
        return views_from(lambda k: 0, lambda k: limit_of(ai, k))
    m = MemoryStore(p)
    try:
        return views_from(m.usage_today, lambda k: limit_of(ai, k))
    finally:
        getattr(m, "close", lambda: None)()
