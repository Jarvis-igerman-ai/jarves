"""Timers and alarms (V3 P1): "טיימר 10 דקות", "תעיר אותי בשבע וחצי". Stored in memory.db `timers` (survive a restart),
checked by one tiny local loop (a SQLite query each second, no network, no voice session) and spoken by the CORE
through `deliver` (Operations: the open GPT-Live conversation at a good moment, else the Core's own speech), so a
ring is heard even when the face / window is closed. A closed-window face popup is only an extra.

A timer or alarm that came due while the Core was off rings once if it is at most LATE_MAX_S late, otherwise it is
recorded as `missed` (never a surprise ring hours later). A ring that cannot be delivered right now (audio busy)
is retried every second for RETRY_S, then given up and recorded as fired-without-sound in the log.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from . import logs
from .userdata import UserDB

MAX_TIMER_S = 24 * 3600
MAX_PENDING = 30
LATE_MAX_S = 600.0
RETRY_S = 90.0
TICK_S = 1.0

Deliver = Callable[[dict, str], Awaitable[bool]]


def duration_he(s: float) -> str:
    s = int(round(s))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    parts = []
    if h:
        parts.append("שעה" if h == 1 else f"{h} שעות")
    if m:
        parts.append("דקה" if m == 1 else f"{m} דקות")
    if sec and not h:
        parts.append(f"{sec} שניות")
    return " ו".join(parts) or "רגע"


def ring_text(t: dict) -> str:
    label = (t.get("label") or "").strip()
    if t["kind"] == "alarm":
        return f"שעון מעורר{': ' + label if label else ''}"
    return f"הטיימר{' של ' + label if label else ''} הסתיים"


class Timers:
    def __init__(self, db: UserDB, *, clock=time.time, tz=None) -> None:
        self.db, self.clock = db, clock
        self._tz = tz                              # callable -> ZoneInfo (the scheduler's time zone)
        self._stop = asyncio.Event()

    def zone(self):
        if self._tz is not None:
            return self._tz()
        from .scheduler import zone
        return zone(self.db.setting("scheduler.timezone"))

    # ------------------------------------------------------------------ creating
    def _count(self) -> int:
        return len(self.db.rows("SELECT id FROM timers WHERE status='pending'"))

    def start_timer(self, seconds: float, label: str = "", *, by: str = "user") -> dict:
        seconds = float(seconds)
        if not 1 <= seconds <= MAX_TIMER_S:
            raise ValueError("a timer is between 1 second and 24 hours")
        return self._add("timer", self.clock() + seconds, label, by)

    def set_alarm(self, at: str, label: str = "", date: str = "", *, by: str = "user") -> dict:
        m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(at))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("the alarm time must be HH:MM (24-hour)")
        tz = self.zone()
        now = datetime.fromtimestamp(self.clock(), tz)
        h, mi = int(m.group(1)), int(m.group(2))
        if date:
            try:
                d = datetime.strptime(date.strip(), "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("the date must be YYYY-MM-DD") from None
            when = datetime(d.year, d.month, d.day, h, mi, tzinfo=tz)
        else:
            when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if when <= now:
                when = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
        if when.timestamp() <= self.clock():
            raise ValueError("that time has already passed")
        if when.timestamp() - self.clock() > 8 * 86400:
            raise ValueError("alarms are for the next week; use a reminder for later")
        return self._add("alarm", when.timestamp(), label, by)

    def _add(self, kind: str, due: float, label: str, by: str) -> dict:
        if self._count() >= MAX_PENDING:
            raise ValueError(f"at most {MAX_PENDING} timers and alarms at once")
        label = " ".join(str(label or "").split())[:80]
        cur = self.db.x("INSERT INTO timers (kind, label, due, created, status) VALUES (?,?,?,?, 'pending')",
                        (kind, label, due, self.clock()))
        logs.info("timers", "TIMER_SET", f"{kind} set", timer=cur.lastrowid, in_s=round(due - self.clock()), by=by)
        return self.get(cur.lastrowid)

    # ------------------------------------------------------------------ reading / cancelling
    def get(self, tid: int) -> dict | None:
        return self.db.row("SELECT * FROM timers WHERE id=?", (int(tid),))

    def pending(self) -> list[dict]:
        return self.db.rows("SELECT * FROM timers WHERE status='pending' ORDER BY due, id")

    def view(self, t: dict) -> dict:
        left = max(0.0, t["due"] - self.clock())
        out = {"id": t["id"], "kind": t["kind"], "label": t["label"], "seconds_left": round(left),
               "left_he": duration_he(left)}
        if t["kind"] == "alarm":
            out["at"] = datetime.fromtimestamp(t["due"], self.zone()).strftime("%Y-%m-%d %H:%M")
        return out

    def cancel(self, tid: int = 0, label: str = "", *, by: str = "user") -> list[dict]:
        rows = self.pending()
        if tid:
            hit = [t for t in rows if t["id"] == int(tid)]
        elif label.strip():
            lab = label.strip().lower()
            hit = [t for t in rows if lab in t["label"].lower() or lab in t["kind"]]
        else:
            hit = rows
        for t in hit:
            self.db.x("UPDATE timers SET status='cancelled' WHERE id=?", (t["id"],))
        if hit:
            logs.info("timers", "TIMER_CANCELLED", "timers cancelled", n=len(hit), by=by)
        return hit

    # ------------------------------------------------------------------ ringing
    async def poll(self, deliver: Deliver) -> int:
        """Ring everything that is due. Returns how many rang."""
        now = self.clock()
        rang = 0
        for t in self.db.rows("SELECT * FROM timers WHERE status='pending' AND due<=? ORDER BY due", (now,)):
            late = now - t["due"]
            if late > LATE_MAX_S:
                self.db.x("UPDATE timers SET status='missed', fired_at=? WHERE id=?", (now, t["id"]))
                logs.warn("timers", "TIMER_MISSED", f"{t['kind']} was due {int(late)} s ago (Core was off)", timer=t["id"])
                continue
            text = ring_text(t)
            try:
                ok = await deliver(t, text)
            except Exception:
                logs.error("timers", "TIMER_DELIVER_FAILED", "ringing failed", exc=True)
                ok = False
            if ok or late > RETRY_S:
                self.db.x("UPDATE timers SET status='fired', fired_at=? WHERE id=?", (now, t["id"]))
                logs.info("timers", "TIMER_RANG" if ok else "TIMER_UNHEARD", f"{t['kind']} {'rang' if ok else 'could not be spoken'}",
                          timer=t["id"], late_s=round(late))
                rang += 1
        return rang

    async def run(self, deliver: Deliver) -> None:
        while not self._stop.is_set():
            try:
                await self.poll(deliver)
            except Exception:
                logs.error("timers", "TIMER_LOOP_FAILED", "timer check failed", exc=True)
            try:
                await asyncio.wait_for(self._stop.wait(), TICK_S)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------- voice / agent tools
class TimerArgs(BaseModel):
    minutes: float = Field(0, ge=0, le=1440, description="length in minutes (can be fractional)")
    seconds: float = Field(0, ge=0, le=86400, description="extra seconds")
    label: str = Field("", max_length=80, description="what it is for, e.g. 'פסטה'")


class AlarmArgs(BaseModel):
    at: str = Field(..., description="clock time HH:MM, 24-hour, local ('07:30')")
    date: str = Field("", description="YYYY-MM-DD; '' = the next time that clock time comes")
    label: str = Field("", max_length=80)


class CancelArgs(BaseModel):
    timer_id: int = Field(0, ge=0, description="0 = by label or all")
    label: str = Field("", max_length=80, description="part of the label, or 'timer' / 'alarm'; '' with id 0 = all")


def make_skill(timers: Timers):
    from .skills import NoArgs, Skill, Tool, ToolResult

    class TimerSkill(Skill):
        name = "timers"
        description = "timers and alarms that ring aloud even when my window is closed"

        def tools(self) -> list:
            async def timer(a: TimerArgs):
                try:
                    t = timers.start_timer(a.minutes * 60 + a.seconds, a.label, by="voice")
                except ValueError as e:
                    return ToolResult(False, str(e), code="TIMER_BAD")
                v = timers.view(t)
                return ToolResult(True, f"timer set for {v['left_he']}", v)

            async def alarm(a: AlarmArgs):
                try:
                    t = timers.set_alarm(a.at, a.label, a.date, by="voice")
                except ValueError as e:
                    return ToolResult(False, str(e), code="ALARM_BAD")
                v = timers.view(t)
                return ToolResult(True, f"alarm set for {v['at']}", v)

            async def listing(_):
                items = [timers.view(t) for t in timers.pending()]
                return ToolResult(True, f"{len(items)} running" if items else "no timers or alarms", {"timers": items})

            async def cancel(a: CancelArgs):
                hit = timers.cancel(a.timer_id, a.label, by="voice")
                if not hit:
                    return ToolResult(False, "no such timer or alarm", code="TIMER_NOT_FOUND")
                return ToolResult(True, f"cancelled {len(hit)}", {"cancelled": [timers.view(t) for t in hit]})

            return [
                Tool("timer_start", "Start a countdown timer ('טיימר ל-10 דקות'). It rings aloud when done.", timer,
                     TimerArgs, permission="action"),
                Tool("alarm_set", "Set an alarm for a clock time ('תעיר אותי ב-7:30'). It rings aloud.", alarm,
                     AlarmArgs, permission="action"),
                Tool("timers_list", "List the running timers and alarms and the time left.", listing, NoArgs,
                     permission="read"),
                Tool("timer_cancel", "Cancel a timer or alarm by id or label, or all of them.", cancel, CancelArgs,
                     permission="action"),
            ]

    return TimerSkill()


ALLOWED_ACTIONS = {"timer_start", "alarm_set", "timer_cancel"}


# ---------------------------------------------------------------------- dashboard (V4 P0.3: the Timers page)
def _ctx_timers(ctx) -> Timers:
    from .userdata import ctx_db
    t = getattr(ctx, "_timers", None)
    if t is None:
        t = Timers(ctx_db(ctx))              # writes rows only; the Core's loop (every second) rings them
        ctx._timers = t
    return t


def api_timers(ctx, m, body):
    t = _ctx_timers(ctx)
    recent = t.db.rows("SELECT * FROM timers WHERE status!='pending' ORDER BY id DESC LIMIT 8")
    return 200, {"timers": [t.view(r) for r in t.pending()],
                 "recent": [{"id": r["id"], "kind": r["kind"], "label": r["label"], "status": r["status"],
                             "due": r["due"]} for r in recent],
                 "max_pending": MAX_PENDING,
                 "note": "The Core checks timers every second, so a change here applies within a second."}


def api_timer_add(ctx, m, body):
    t, b = _ctx_timers(ctx), body or {}
    try:
        if str(b.get("kind", "timer")) == "alarm":
            r = t.set_alarm(str(b.get("at", "")), str(b.get("label", "")), str(b.get("date", "")), by="dashboard")
        else:
            r = t.start_timer(float(b.get("minutes") or 0) * 60 + float(b.get("seconds") or 0),
                              str(b.get("label", "")), by="dashboard")
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}
    return 200, t.view(r)


def api_timer_op(ctx, m, body):
    t = _ctx_timers(ctx)
    if (body or {}).get("op") != "cancel":
        return 400, {"error": "op must be cancel"}
    hit = t.cancel(int(m.group(1)), by="dashboard")
    return (200, {"cancelled": len(hit)}) if hit else (404, {"error": "no such running timer"})


def dashboard_routes(register) -> None:
    register("GET", r"/api/timers", api_timers)
    register("POST", r"/api/timers", api_timer_add)
    register("POST", r"/api/timers/(\d+)", api_timer_op)
