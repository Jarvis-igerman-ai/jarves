"""Proactive alerts (SW-V2 WP-M3, spec 6.7 / 7, decision D04): event-driven, never LLM polling.

Sources call `AlertEngine.raise_` when something happens (a reminder is due, a report is ready, a job failed or was
interrupted, a diagnostic proposal, a dashboard "read aloud"). The engine stores the alert (memory.db `alerts`)
and `flush()` delivers what is pending through the outputs the Core provides:

- popup   a quiet line on the face screen (no sound)
- speech  said by the open GPT-Live conversation (at a good moment) or by the Core's TTS
- os      an operating-system notification (platform adapter; WP-M1's tray provides it on Windows, none yet on Pi)

Policy (Dashboard Alerts page, agent_settings `alerts.*`):
- priority low | normal | high | urgent -> channel (default popup, popup, speech, speech); an explicit channel wins.
- quiet hours (default 22:00-07:00, local zone): speech becomes a quiet popup, except urgent when allowed.
- pause ("pause proactive alerts", tray / voice / dashboard): nothing but urgent is delivered until it ends;
  alerts wait (pending) instead of being lost.
- deduplication by key within alerts.dedupe_s and a rate limit (alerts.max_per_hour; urgent and user-requested
  alerts are exempt). Suppressed alerts are kept with the reason (visible on the dashboard).
- no output available (no screen, no speaker, conversation busy) -> it stays pending and is retried on every
  scheduler tick and when an output comes back; pending alerts older than alerts.pending_max_age_s expire.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field

from . import logs
from .userdata import UserDB, ctx_db

PRIORITIES = ("low", "normal", "high", "urgent")
RANK = {p: i for i, p in enumerate(PRIORITIES)}
CHANNELS = ("popup", "speech", "os")
FALLBACK = {"speech": ("speech", "popup", "os"), "popup": ("popup", "os"), "os": ("os", "popup")}
SOLICITED = ("reminder", "readback")     # the user asked for these; delivered even with proactive alerts off
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class Output(Protocol):
    def available(self) -> bool: ...

    async def deliver(self, alert: dict) -> bool: ...


def in_quiet(now: datetime, start: str, end: str) -> bool:
    """Is the local time inside [start, end) (wrapping midnight when start > end)?"""
    s = int(start[:2]) * 60 + int(start[3:])
    e = int(end[:2]) * 60 + int(end[3:])
    m = now.hour * 60 + now.minute
    if s == e:
        return False
    return s <= m < e if s < e else (m >= s or m < e)


class AlertEngine:
    def __init__(self, db: UserDB, *, outputs: dict[str, Output] | None = None, clock=time.time) -> None:
        self.db, self.outputs, self.clock = db, dict(outputs or {}), clock
        self.enabled = lambda: True      # the proactive_alerts feature switch (operations.py sets it)
        self._flushing = False

    # ------------------------------------------------------------------ settings
    def _tz(self):
        from .scheduler import zone
        return zone(self.db.setting("scheduler.timezone"))

    def quiet_now(self) -> bool:
        st = self.db.settings("alerts.")
        if not st["alerts.quiet_enabled"]:
            return False
        return in_quiet(datetime.fromtimestamp(self.clock(), self._tz()), st["alerts.quiet_start"],
                        st["alerts.quiet_end"])

    def paused(self) -> bool:
        return float(self.db.setting("alerts.paused_until") or 0) > self.clock()

    def pause(self, minutes: float, *, by: str) -> float:
        until = self.clock() + max(1.0, float(minutes)) * 60
        self.db.set_setting("alerts.paused_until", until, actor=by)
        logs.info("alerts", "ALERTS_PAUSED", "proactive alerts paused", minutes=minutes, by=by)
        return until

    def resume(self, *, by: str) -> None:
        self.db.set_setting("alerts.paused_until", 0.0, actor=by)
        logs.info("alerts", "ALERTS_RESUMED", "proactive alerts resumed", by=by)

    def update_settings(self, values: dict, *, by: str) -> dict:
        for k, v in values.items():
            key = k if k.startswith("alerts.") else f"alerts.{k}"
            if key in ("alerts.quiet_start", "alerts.quiet_end"):
                if not isinstance(v, str) or not _HHMM.match(v):
                    raise ValueError(f"{k} must be HH:MM")
            elif key in ("alerts.quiet_enabled", "alerts.urgent_in_quiet"):
                v = bool(v)
            elif key in ("alerts.max_per_hour",):
                v = max(1, min(int(v), 60))
            elif key in ("alerts.dedupe_s", "alerts.pending_max_age_s"):
                v = max(60.0, min(float(v), 7 * 86400.0))
            elif key == "alerts.delivery":
                if not isinstance(v, dict) or any(p not in PRIORITIES or c not in CHANNELS for p, c in v.items()):
                    raise ValueError(f"delivery maps {PRIORITIES} to one of {CHANNELS}")
                v = {**self.db.setting("alerts.delivery"), **v}
            else:
                raise ValueError(f"unknown alert setting {k}")
            self.db.set_setting(key, v, actor=by)
        return self.db.settings("alerts.")

    # ------------------------------------------------------------------ raising
    def raise_(self, source: str, title: str, text: str = "", *, priority: str = "normal", kind: str = "info",
               dedupe_key: str | None = None, channel: str | None = None, user_requested: bool = False,
               ref: str | None = None, expires_s: float | None = None) -> dict:
        if priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        if channel is not None and channel not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}")
        st = self.db.settings("alerts.")
        now = self.clock()
        status, reason = "pending", None
        if dedupe_key and not user_requested and self.db.row(
                "SELECT id FROM alerts WHERE dedupe_key=? AND ts>? AND status IN ('pending','delivered') LIMIT 1",
                (dedupe_key, now - float(st["alerts.dedupe_s"]))):
            status, reason = "suppressed", "duplicate"
        if status == "pending" and not user_requested and priority != "urgent":
            n = self.db.row("SELECT COUNT(*) n FROM alerts WHERE ts>? AND user_requested=0 AND status IN "
                            "('pending','delivered')", (now - 3600,))["n"]
            if n >= int(st["alerts.max_per_hour"]):
                status, reason = "suppressed", "rate_limit"
        ch = channel or (st["alerts.delivery"] or {}).get(priority, "popup")
        cur = self.db.x("INSERT INTO alerts (ts, source, kind, priority, title, text, dedupe_key, channel, ref, "
                        "user_requested, status, reason, expires) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (now, source, kind, priority, str(title)[:200], str(text)[:8000], dedupe_key, ch, ref,
                         int(bool(user_requested)), status, reason, None if expires_s is None else now + expires_s))
        level = logs.info if status == "pending" else logs.debug
        level("alerts", "ALERT_RAISED" if status == "pending" else "ALERT_SUPPRESSED",
              "alert recorded", status=status, reason=reason, alert_id=cur.lastrowid, source=source,
              alert_kind=kind, priority=priority, channel=ch)
        return self.db.row("SELECT * FROM alerts WHERE id=?", (cur.lastrowid,))

    async def notify(self, *args, **kw) -> dict:
        a = self.raise_(*args, **kw)
        await self.flush()
        return self.db.row("SELECT * FROM alerts WHERE id=?", (a["id"],))

    # ------------------------------------------------------------------ delivery
    def route(self, a: dict) -> tuple[str, ...]:
        if a["kind"] == "readback":
            return ("speech",)                    # "read this aloud" waits for a voice; never a silent popup
        ch = a["channel"] or "popup"
        if ch == "speech" and not a["user_requested"] and self.quiet_now() and not (
                a["priority"] == "urgent" and self.db.setting("alerts.urgent_in_quiet")):
            ch = "popup"                          # quiet hours: a quiet line on the screen instead of speech
        return FALLBACK.get(ch, ("popup",))

    async def flush(self) -> int:
        """Deliver pending alerts (highest priority first). Returns how many were delivered."""
        if self._flushing:
            return 0
        self._flushing = True
        delivered = 0
        try:
            now = self.clock()
            max_age = float(self.db.setting("alerts.pending_max_age_s"))
            paused = self.paused()
            try:
                off = not self.enabled()
            except Exception:
                off = False
            pend = self.db.rows("SELECT * FROM alerts WHERE status='pending'")
            pend.sort(key=lambda a: (-RANK.get(a["priority"], 1), a["ts"]))
            for a in pend:
                if (a["expires"] and a["expires"] < now) or now - a["ts"] > max_age:
                    self.db.x("UPDATE alerts SET status='expired', reason='not delivered in time' WHERE id=?",
                              (a["id"],))
                    continue
                if paused and not a["user_requested"] and a["priority"] != "urgent":
                    continue
                if off and not a["user_requested"] and a["kind"] not in SOLICITED:
                    continue                      # switched off: only what the user asked for (reminders...)
                a["quiet"] = self.quiet_now()
                via = None
                for ch in self.route(a):
                    out = self.outputs.get(ch)
                    try:
                        if out is not None and out.available() and await out.deliver(a):
                            via = ch
                            break
                    except Exception as e:     # a broken output must not lose the alert or stop the others
                        logs.warn("alerts", "ALERT_OUTPUT_FAILED", "alert output failed", channel=ch, error_type=type(e).__name__)
                if via is None:
                    self.db.x("UPDATE alerts SET attempts=attempts+1 WHERE id=?", (a["id"],))
                    continue
                self.db.x("UPDATE alerts SET status='delivered', delivered_via=?, delivered_at=?, attempts=attempts+1 "
                          "WHERE id=?", (via, self.clock(), a["id"]))
                delivered += 1
                logs.info("alerts", "ALERT_DELIVERED", "alert delivered", alert_id=a["id"], via=via)
        finally:
            self._flushing = False
        return delivered

    # ------------------------------------------------------------------ queries / control
    def pending(self) -> list[dict]:
        return self.db.rows("SELECT * FROM alerts WHERE status='pending' ORDER BY ts DESC")

    def recent(self, limit: int = 50) -> list[dict]:
        return self.db.rows("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))

    def dismiss(self, alert_id: int | None = None, *, by: str) -> int:
        if alert_id is None:
            n = self.db.x("UPDATE alerts SET status='dismissed', reason=? WHERE status='pending'", (f"by {by}",)).rowcount
        else:
            n = self.db.x("UPDATE alerts SET status='dismissed', reason=? WHERE id=? AND status='pending'",
                          (f"by {by}", int(alert_id))).rowcount
        return n


def speech_note(a: dict, limit: int = 600) -> str:
    """Commentary for GPT-Live (instructions first, content last: the note is limited to ~500 tokens)."""
    if a["kind"] == "readback":
        return ("[report] The user asked to hear a saved report. Read it now, naturally in Hebrew, as it is: do not "
                "search or regenerate it. If it is cut off, offer the rest (report_read).\n"
                f"Title: {a['title'][:120]}\nReport: {a['text'][:limit]}")
    return ("[alert] Tell the user this briefly and naturally in Hebrew (one or two sentences), at this pause. Do not "
            f"call tools for it.\n{a['title'][:160]}: {a['text'][:limit]}")


# ---------------------------------------------------------------------- voice tools
class AlertControlArgs(BaseModel):
    op: str = Field(..., pattern="^(pause|resume|dismiss_all)$",
                    description="pause = no proactive alerts for a while ('אל תפריע לי'), resume, dismiss_all")
    minutes: int = Field(60, ge=1, le=24 * 60, description="for pause: how long")


def make_skill(engine: AlertEngine):
    from .skills import NoArgs, Skill, Tool, ToolResult

    class AlertSkill(Skill):
        name = "alerts"
        description = "proactive alerts and quiet time"

        def tools(self) -> list:
            async def list_(_):
                pend = engine.pending()
                return ToolResult(True, f"{len(pend)} pending alert(s)" + (", paused" if engine.paused() else ""),
                                  {"pending": [{"id": a["id"], "title": a["title"], "text": a["text"][:300],
                                                "priority": a["priority"]} for a in pend[:10]],
                                   "paused": engine.paused(), "quiet_hours_now": engine.quiet_now()})

            async def control(a: AlertControlArgs):
                if a.op == "pause":
                    until = engine.pause(a.minutes, by="voice")
                    return ToolResult(True, f"proactive alerts paused for {a.minutes} min (urgent ones still come)",
                                      {"paused_until": until})
                if a.op == "resume":
                    engine.resume(by="voice")
                    return ToolResult(True, "proactive alerts are on again")
                return ToolResult(True, f"dismissed {engine.dismiss(by='voice')} pending alert(s)")

            return [Tool("alerts_list", "Pending proactive alerts (and whether alerts are paused / quiet hours).",
                         list_, NoArgs, permission="read"),
                    Tool("alerts_control", "Pause or resume proactive alerts, or dismiss the pending ones.", control,
                         AlertControlArgs, permission="action")]

    return AlertSkill()


ALLOWED_ACTIONS = {"alerts_control"}


# ---------------------------------------------------------------------- dashboard
def _engine(ctx) -> AlertEngine:
    e = getattr(ctx, "_alerts", None)
    if e is None:
        e = AlertEngine(ctx_db(ctx))       # records only; the Core delivers
        ctx._alerts = e
    return e


def api_alerts(ctx, m, body):
    e = _engine(ctx)
    return 200, {"pending": e.pending(), "recent": e.recent(100), "settings": e.db.settings("alerts."),
                 "paused": e.paused(), "quiet_now": e.quiet_now(), "priorities": PRIORITIES, "channels": CHANNELS,
                 "os_notifications": "provided by the Windows tray (WP-M1); not available on Pi yet"}


def api_alert_settings(ctx, m, body):
    try:
        return 200, {"settings": _engine(ctx).update_settings(body or {}, by="dashboard")}
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}


def api_alert_op(ctx, m, body):
    e, b = _engine(ctx), body or {}
    target = m.group(1)
    if target == "pause":
        return 200, {"paused_until": e.pause(float(b.get("minutes", 60)), by="dashboard")}
    if target == "resume":
        e.resume(by="dashboard")
        return 200, {"paused": False}
    if target == "test":
        return 200, e.raise_("dashboard", "בדיקת התראה", "זו התראת בדיקה מלוח הבקרה.", priority=str(
            b.get("priority", "normal")) if b.get("priority") in PRIORITIES else "normal", kind="test",
            user_requested=True)
    if target == "dismiss_all":
        return 200, {"dismissed": e.dismiss(by="dashboard")}
    if target.isdigit():
        return 200, {"dismissed": e.dismiss(int(target), by="dashboard")}
    return 404, {"error": "no such alert action"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/alerts", api_alerts)
    register("POST", r"/api/alerts/settings", api_alert_settings)
    register("POST", r"/api/alerts/(pause|resume|test|dismiss_all|\d+)", api_alert_op)
