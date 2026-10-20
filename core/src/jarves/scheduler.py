"""Durable scheduler and saved reports (SW-V2 WP-M3, spec section 7, decisions D06/D14).

A small persistent scheduler inside the Core, not a task broker. Jobs live in memory.db (userdata.py); the Core's
asyncio loop wakes at the next due time (at most every POLL_S seconds, a local SQLite read: never a metered voice
session or model call just to poll). The dashboard process only writes rows (approve, pause, "run now"); the Core
picks them up on its next tick.

Schedules
- `cron`: 5 fields (minute hour day-of-month month day-of-week, Sunday = 0/7; lists, ranges, steps; Vixie rule:
  when both day fields are restricted, either may match). `once`: a local wall-clock time "YYYY-MM-DDTHH:MM".
- Evaluated in the job's IANA zone (zoneinfo + tzdata; default setting scheduler.timezone = Asia/Jerusalem).
  DST: a wall-clock time that does not exist (spring forward) runs at the first valid minute after the gap; one that
  happens twice (fall back) runs ONCE, at its first occurrence.
- Spoken/typed instructions ("כל בוקר ב-8", "מחר בשמונה וחצי בערב", "every monday at 10", raw cron) -> `parse_when`
  -> a DRAFT job and a preview (Hebrew description, next local run times, permitted Skills, cost budget). Nothing
  runs until the job is approved.

Running a due occurrence
- Idempotency: every occurrence has a key `<job>:<UTC minute>`; its job_runs row is inserted (UNIQUE) BEFORE the work
  starts, so a restart can never run the same occurrence twice.
- Catch-up (D14): after a reboot / sleep, at most ONE missed occurrence per job runs (the latest), labelled late; one
  older than the job's max_age_s is recorded as skipped instead. `next_run` is advanced before the work starts.
- No overlap (unless allow_overlap); bounded retries with exponential backoff; permanent errors (forbidden tool,
  disabled Skill, budget) are not retried. A job's AI requests per run are capped (max_requests, JobBudget) on top
  of the Core's daily ceiling.
- Crash mid-run: a run left "running" by a dead process is resumed once when it has no external side effect
  (reminder / research) and is still fresh; a tool job is marked interrupted and NEVER repeated (no duplicate
  external action). Every outcome is saved as a report (text, status, sources, timestamps, late flag).

Actions: `reminder` (no AI: the text is delivered as an alert), `research` (tasks.py runner: Responses API + web
search, or the ChatGPT plan), `tool` (one registered tool through SkillRegistry.call: central permissions, feature
switches and the job's permitted Skills; only creatable from code/dashboard, never from a model).
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from . import logs
from .budget import BudgetExceeded
from .userdata import UserDB, ctx_db

POLL_S = 30.0                  # longest sleep: picks up dashboard writes and wall-clock jumps (sleep/resume)
LATE_GRACE_S = 120.0           # an occurrence started later than this after its time is labelled late
MAX_CATCHUP_STEPS = 50_000     # bound for walking missed occurrences (a */5 job off for months)
MIN_INTERVAL_MIN = 5           # "every N minutes" below this is refused (cost / noise)
DRAFT_TTL_S = 86_400           # unapproved drafts are removed after a day
BACKOFF_CAP_S = 3600.0
ACTIONS = ("reminder", "research", "tool")
DELIVERY = ("report", "alert", "speak")
# Tool result codes that a retry cannot fix.
PERMANENT = {"TOOL_FORBIDDEN", "SKILL_DISABLED", "FEATURE_OFF", "TOOL_DECLINED", "TOOL_BAD_ARGS", "TOOL_UNKNOWN",
             "TOOL_UNTRUSTED_TURN", "JOB_SKILL_NOT_PERMITTED", "JOB_BUDGET", "AI_BUDGET", "AI_UNAVAILABLE",
             "SANDBOX_DENIED", "SANDBOX_EXISTS", "SANDBOX_QUOTA", "SANDBOX_TYPE"}
_DAY_HE = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]
_DAY_LETTER = ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳"]


class ScheduleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ScheduleError("BAD_TIMEZONE", f"unknown time zone {name!r} (needs an IANA name such as "
                                            "Asia/Jerusalem; on Windows the tzdata package)") from e


# ---------------------------------------------------------------------- cron + DST
_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31), ("month", 1, 12), ("weekday", 0, 7))


class Cron:
    def __init__(self, expr: str) -> None:
        parts = str(expr).split()
        if len(parts) != 5:
            raise ScheduleError("BAD_CRON", "a cron expression has 5 fields: minute hour day month weekday")
        self.expr = " ".join(parts)
        sets = []
        for raw, (name, lo, hi) in zip(parts, _FIELDS):
            s: set[int] = set()
            for item in raw.split(","):
                body, _, step_s = item.partition("/")
                try:
                    step = int(step_s) if step_s else 1
                    if body == "*":
                        a, b = lo, hi
                    elif "-" in body:
                        a, b = (int(x) for x in body.split("-", 1))
                    else:
                        a = int(body)
                        b = hi if step_s else a
                except ValueError as e:
                    raise ScheduleError("BAD_CRON", f"bad cron {name} field {raw!r}") from e
                if step < 1 or a < lo or b > hi or a > b:
                    raise ScheduleError("BAD_CRON", f"cron {name} field {raw!r} out of range {lo}-{hi}")
                s.update(range(a, b + 1, step))
            sets.append(s)
        self.minutes, self.hours, self.days, self.months, dows = (sorted(x) for x in sets)
        self.weekdays = sorted({d % 7 for d in dows})
        self.day_any, self.weekday_any = parts[2] == "*", parts[4] == "*"

    def day_ok(self, d: date) -> bool:
        if d.month not in self.months:
            return False
        dom, dow = d.day in self.days, d.isoweekday() % 7 in self.weekdays
        if self.day_any and self.weekday_any:
            return True
        if self.day_any:
            return dow
        if self.weekday_any:
            return dom
        return dom or dow


def local_instant(naive: datetime, tz: ZoneInfo) -> float:
    """UTC epoch of a local wall-clock time. Ambiguous (fall back): the FIRST occurrence. Non-existent (spring
    forward): the first valid minute after the gap."""
    t = naive.replace(second=0, microsecond=0)
    for _ in range(24 * 60):
        cand = t.replace(tzinfo=tz, fold=0)
        ts = cand.timestamp()
        if datetime.fromtimestamp(ts, tz).replace(tzinfo=None) == t:
            return ts
        t += timedelta(minutes=1)
    raise ScheduleError("BAD_TIME", "no valid local time")


def next_after(kind: str, spec: str, tz: ZoneInfo, after: float) -> float | None:
    """The first occurrence strictly after `after` (UTC epoch), or None (a past one-off / impossible cron)."""
    if kind == "once":
        t = local_instant(datetime.fromisoformat(spec), tz)
        return t if t > after else None
    cron = Cron(spec)
    start = datetime.fromtimestamp(after, tz).replace(tzinfo=None, second=0, microsecond=0)
    d = start.date()
    for _ in range(366 * 5):
        if cron.day_ok(d):
            for h in cron.hours:
                for m in cron.minutes:
                    naive = datetime(d.year, d.month, d.day, h, m)
                    if naive < start:
                        continue
                    t = local_instant(naive, tz)
                    if t > after:
                        return t
        d += timedelta(days=1)
    return None


def next_runs(kind: str, spec: str, tz: ZoneInfo, after: float, n: int = 3) -> list[float]:
    out, t = [], after
    for _ in range(n):
        nxt = next_after(kind, spec, tz, t)
        if nxt is None:
            break
        out.append(nxt)
        t = nxt
    return out


def _count_between(cron: Cron, tz: ZoneInfo, a: float, b: float) -> int:
    """How many occurrences lie in (a, b], without walking them one by one: a whole local day with no UTC-offset
    change counts hours x minutes; the edge days and DST days count the distinct instants exactly (the same
    instants next_after yields: a skipped time moves to after the gap, an ambiguous one is taken once)."""
    if b <= a:
        return 0
    per_day = len(cron.hours) * len(cron.minutes)
    d, last = datetime.fromtimestamp(a, tz).date(), datetime.fromtimestamp(b, tz).date()
    n = 0
    while d <= last:
        if cron.day_ok(d):
            lo = datetime(d.year, d.month, d.day, tzinfo=tz)
            hi = datetime(d.year, d.month, d.day, 23, 59, tzinfo=tz)
            if lo.timestamp() > a and hi.timestamp() <= b and lo.utcoffset() == hi.utcoffset():
                n += per_day
            else:
                n += len({t for h in cron.hours for m in cron.minutes
                          if a < (t := local_instant(datetime(d.year, d.month, d.day, h, m), tz)) <= b})
        d += timedelta(days=1)
    return n


def latest_due(kind: str, spec: str, tz: ZoneInfo, first_due: float, now: float) -> tuple[float, int]:
    """(the latest occurrence <= now, how many earlier ones were missed) starting from a due `first_due`.
    Runs on the tick (event loop), so it never walks every missed occurrence: it looks back from `now` in a
    doubling window for the latest one and counts the rest per day (a */5 job off for months stays ~ms)."""
    if kind == "once" or now <= first_due:
        return first_due, 0
    cron, span = Cron(spec), 3600.0
    while True:
        start = max(first_due, now - span)
        occ, t = (first_due if start == first_due else None), start
        for _ in range(MAX_CATCHUP_STEPS):
            nxt = next_after(kind, spec, tz, t)
            if nxt is None or nxt > now:
                break
            occ = t = nxt
        if occ is not None:
            return occ, _count_between(cron, tz, first_due, occ)
        span *= 2


def fmt_local(ts: float | None, tz: ZoneInfo) -> str:
    if ts is None:
        return ""
    d = datetime.fromtimestamp(ts, tz)
    return f"יום {_DAY_LETTER[d.isoweekday() % 7]} {d:%d.%m.%Y %H:%M}"


# ---------------------------------------------------------------------- human-readable schedules
@dataclass
class Schedule:
    kind: str                  # cron | once
    spec: str
    human: str


_NIQQUD = re.compile(r"[֑-ׇ]")
_HE_NUM = {"אחת עשרה": 11, "אחת-עשרה": 11, "שתים עשרה": 12, "שתים-עשרה": 12, "אחת": 1, "שתיים": 2, "שתים": 2,
           "שלוש": 3, "ארבע": 4, "חמש": 5, "שש": 6, "שבע": 7, "שמונה": 8, "תשע": 9, "עשר": 10}
_EN_DAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
_HE_DAYS = {d: i for i, d in enumerate(_DAY_HE)}
_PART = [(re.compile(r"אחר הצהריים|אחרי הצהריים|אחה\"?צ|afternoon"), "pm"), (re.compile(r"צהריים|noon"), "noon"),
         (re.compile(r"בוקר|morning|(?:(?<=\d)|\b)am\b"), "am"),
         (re.compile(r"ערב|evening|(?:(?<=\d)|\b)pm\b|tonight"), "pm"),
         (re.compile(r"לילה|night"), "night")]
_PART_DEFAULT = {"am": (8, 0), "noon": (13, 0), "pm": (20, 0), "night": (22, 0)}
_DAY_TOKEN = r"(?:ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)"


def _norm(text: str) -> str:
    t = _NIQQUD.sub("", str(text)).lower().replace("־", "-").replace("״", "\"").replace("׳", "'")
    return " ".join(t.split())


def _part_of_day(t: str) -> str | None:
    for rx, part in _PART:
        if rx.search(t):
            return part
    return None


def _clock_time(t: str) -> tuple[int, int] | None:
    """Hour and minute named in the text (digits or Hebrew number words), adjusted for morning/evening."""
    hm = None
    m = (re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", t)
         or re.search(r"(?:(?<![\w])בשעה\s*|(?<![\w])ב-?|\bat\s+)(\d{1,2})(?:[:.](\d{2}))?(?![\d./])", t)
         or re.search(r"(?<![\w.\d])(\d{1,2})(?:[:.](\d{2}))?\s*(?=ב?בוקר|בערב|בלילה|בצהריים|אחר|am\b|pm\b)", t))
    if m:
        hm = [int(m.group(1)), int(m.group(2) or 0)]
    else:
        words = "|".join(sorted(_HE_NUM, key=len, reverse=True))
        w = re.search(rf"(?<![\w])(?:בשעה\s+|ב-?)({words})(?![\w])", t)
        if w:
            hm = [_HE_NUM[w.group(1)], 0]
    if hm is None:
        return None
    if re.search(r"(?<![\w])(?:ו)?חצי(?![\w])|\bhalf past\b", t) and hm[1] == 0:
        hm[1] = 30
    elif re.search(r"(?<![\w])ורבע(?![\w])|\bquarter past\b", t) and hm[1] == 0:
        hm[1] = 15
    part = _part_of_day(t)
    h = hm[0]
    if part in ("pm", "noon") and h < 12 and not (part == "noon" and h >= 11):
        h += 12
    elif part == "night" and 6 <= h < 12:
        h += 12
    elif part in ("am", "night") and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= hm[1] <= 59):
        raise ScheduleError("BAD_TIME", "השעה לא תקינה")
    return h, hm[1]


def _weekdays(t: str) -> list[int]:
    days = [_HE_DAYS[d] for d in re.findall(rf"(?<![\w])[וב]?({_DAY_TOKEN})(?![\w])", t)]
    days += [n for name, n in _EN_DAYS.items() if re.search(rf"\b{name}s?\b", t)]
    return sorted(set(days))


def _hhmm(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


def describe(kind: str, spec: str, tz: ZoneInfo | None = None) -> str:
    """Hebrew description of a schedule ("כל יום ב-08:00")."""
    if kind == "once":
        d = datetime.fromisoformat(spec)
        return f"פעם אחת, יום {_DAY_HE[d.isoweekday() % 7]} {d:%d.%m.%Y} ב-{d:%H:%M}"
    c = Cron(spec)
    f = spec.split()
    single = len(c.minutes) == 1 and len(c.hours) == 1
    at = f"ב-{_hhmm(c.hours[0], c.minutes[0])}" if single else ""
    if f[2:] == ["*", "*", "*"] and single:
        return f"כל יום {at}"
    if f[2:4] == ["*", "*"] and single and not c.weekday_any:
        if c.weekdays == [0, 1, 2, 3, 4]:
            return f"בימים א׳–ה׳ {at}"
        if c.weekdays == [1, 2, 3, 4, 5]:
            return f"בימים ב׳–ו׳ {at}"
        return "בכל יום " + " ו".join(_DAY_HE[d] for d in c.weekdays) + f" {at}"
    if f[0].startswith("*/") and f[1:] == ["*", "*", "*", "*"]:
        return f"כל {f[0][2:]} דקות"
    if len(c.minutes) == 1 and f[1:] == ["*", "*", "*", "*"]:
        return "כל שעה עגולה" if c.minutes[0] == 0 else f"כל שעה, {c.minutes[0]} דקות אחרי השעה העגולה"
    if f[1].startswith("*/") and len(c.minutes) == 1 and f[2:] == ["*", "*", "*"]:
        return f"כל {f[1][2:]} שעות"
    if single and f[3:] == ["*", "*"] and not c.day_any:
        return "בכל " + ", ".join(str(d) for d in c.days) + f" לחודש {at}"
    return f"לפי cron: {spec}"


WORKWEEKS = {"0-4": "Sunday-Thursday", "1-5": "Monday-Friday"}     # setting scheduler.workweek ("ימי חול")


def parse_when(text: str, now: datetime, workweek: str = "0-4") -> Schedule:
    """A spoken / typed schedule (Hebrew or English) -> Schedule. `now` is aware, in the job's zone.
    workweek: the cron weekdays of "ימי חול" (setting scheduler.workweek; Israel default Sunday-Thursday)."""
    raw = str(text or "").strip()
    if not raw:
        raise ScheduleError("SCHEDULE_EMPTY", "מתי? צריך זמן או תדירות")
    if re.fullmatch(r"[\d*/,\-]+(\s+[\d*/,\-]+){4}", raw):
        Cron(raw)
        return Schedule("cron", " ".join(raw.split()), describe("cron", raw))
    t = _norm(raw)
    tz = now.tzinfo

    def once(d: datetime) -> Schedule:
        d = d.replace(second=0, microsecond=0, tzinfo=None)
        if local_instant(d, tz) <= now.timestamp():
            raise ScheduleError("SCHEDULE_PAST", "הזמן הזה כבר עבר")
        spec = d.isoformat(timespec="minutes")
        return Schedule("once", spec, describe("once", spec))

    def cron(expr: str) -> Schedule:
        Cron(expr)
        return Schedule("cron", expr, describe("cron", expr))

    # relative one-off: "בעוד 20 דקות", "בעוד שעה", "in 2 hours"
    m = re.search(r"(?:בעוד|in)\s+(\d+|חצי|an?|one)?\s*-?\s*(דקה|דקות|minutes?|mins?|שעה|שעות|hours?|יום|ימים|days?)", t)
    if m:
        n_s, unit = m.group(1), m.group(2)
        n = 0.5 if n_s == "חצי" else 1 if n_s in (None, "a", "an", "one") else int(n_s)
        mins = n * (1 if unit.startswith(("דק", "min")) else 60 if unit.startswith(("שע", "hour")) else 1440)
        if mins < 1:
            raise ScheduleError("BAD_TIME", "מוקדם מדי")
        d = (now + timedelta(minutes=mins)).astimezone(tz)
        return once(d.replace(tzinfo=None))
    # intervals
    if re.search(r"כל רבע שעה|every quarter hour|every 15 min", t):
        return cron("*/15 * * * *")
    if re.search(r"כל חצי שעה|every half hour|every 30 min", t):
        return cron("*/30 * * * *")
    m = re.search(r"(?:כל|every)\s+(\d+)\s*(דקות|minutes?|mins?)", t)
    if m:
        n = int(m.group(1))
        if n < MIN_INTERVAL_MIN:
            raise ScheduleError("SCHEDULE_TOO_OFTEN", f"לכל היותר פעם ב-{MIN_INTERVAL_MIN} דקות")
        if n >= 60:
            if n % 60 or 24 % (n // 60):
                raise ScheduleError("BAD_INTERVAL", "מרווח בדקות צריך להיות פחות משעה או מספר שעות שלם")
            return cron(f"0 */{n // 60} * * *")
        if 60 % n:
            raise ScheduleError("BAD_INTERVAL", "מרווח הדקות צריך לחלק את השעה (5, 10, 15, 20, 30)")
        return cron(f"*/{n} * * * *")
    m = re.search(r"(?:כל|every)\s+(\d+)\s*(שעות|hours?)", t)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 12 or 24 % n:
            raise ScheduleError("BAD_INTERVAL", "מרווח השעות צריך לחלק את היממה (1, 2, 3, 4, 6, 8, 12)")
        return cron(f"0 */{n} * * *" if n > 1 else "0 * * * *")
    if re.search(r"כל שעה|every hour|hourly|מדי שעה", t):
        mm = re.search(r"(?:ב|בדקה|at minute|at :)\s*-?(\d{1,2})\b", t)
        return cron(f"{int(mm.group(1)) % 60 if mm else 0} * * * *")

    # dates and days-of-month are cut out first so "ב-25.12" or "ב-1 לחודש" is never read as a clock time
    date_rx = r"(?<![\d:])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d:])"
    dom_rx = r"(?<![\w:.])(?:ב-?)?(\d{1,2})\s*(?:ל?חודש|בחודש|לכל חודש)|\bon the (\d{1,2})(?:st|nd|rd|th)?\b"
    dm, dom_m = re.search(date_rx, t), re.search(dom_rx, t)
    t_time = t
    for mt in sorted([x for x in (dm, dom_m) if x], key=lambda x: -x.start()):
        t_time = t_time[:mt.start()] + " " + t_time[mt.end():]
    hm = _clock_time(t_time)
    part = _part_of_day(t)
    if hm is None and part is not None:
        hm = _PART_DEFAULT[part]
    recurring = bool(re.search(r"(?<![\w])(כל|בכל|מדי|בימי|ימי)(?![\w])|\bevery\b|\beach\b|\bdaily\b|\bweekdays\b|"
                               r"\bweekly\b|\bmonthly\b|יומי|שבועי|חודשי", t))
    if recurring:
        if hm is None:
            raise ScheduleError("SCHEDULE_NEEDS_TIME", "באיזו שעה?")
        h, mi = hm
        if re.search(r"ימי חול|ימי עבודה|יום עבודה|יום חול", t):
            ww = workweek if workweek in WORKWEEKS else "0-4"
            return cron(f"{mi} {h} * * {ww}")                # default Israeli work week: Sunday-Thursday
        if re.search(r"\bweekdays\b|\bworkdays\b", t):
            return cron(f"{mi} {h} * * 1-5")
        if re.search(r"סוף שבוע|סופ\"?ש", t):
            return cron(f"{mi} {h} * * 5,6")
        if re.search(r"\bweekends?\b", t):
            return cron(f"{mi} {h} * * 0,6")
        if re.search(r"חודש|\bmonth", t):
            dom = int(dom_m.group(1) or dom_m.group(2)) if dom_m else 1
            if not 1 <= dom <= 28:
                raise ScheduleError("BAD_DAY", "יום בחודש בין 1 ל-28 (כדי שירוץ בכל חודש)")
            return cron(f"{mi} {h} {dom} * *")
        days = _weekdays(t)
        if days:
            return cron(f"{mi} {h} * * {','.join(map(str, days))}")
        return cron(f"{mi} {h} * * *")

    # one-off
    if hm is None:
        raise ScheduleError("SCHEDULE_NEEDS_TIME", "באיזו שעה?")
    h, mi = hm
    base = now.replace(tzinfo=None)
    if dm:
        y = int(dm.group(3)) if dm.group(3) else base.year
        y = y + 2000 if y < 100 else y
        try:
            d = datetime(y, int(dm.group(2)), int(dm.group(1)), h, mi)
        except ValueError as e:
            raise ScheduleError("BAD_DATE", "התאריך לא תקין") from e
        if not dm.group(3) and local_instant(d, tz) <= now.timestamp():
            d = d.replace(year=y + 1)
        return once(d)
    if re.search(r"מחרתיים|day after tomorrow", t):
        return once(datetime.combine(base.date() + timedelta(days=2), datetime.min.time()).replace(hour=h, minute=mi))
    if re.search(r"מחר|tomorrow", t):
        return once(datetime.combine(base.date() + timedelta(days=1), datetime.min.time()).replace(hour=h, minute=mi))
    days = _weekdays(t)
    if days:
        target = days[0]
        for i in range(0, 8):
            d = datetime.combine(base.date() + timedelta(days=i), datetime.min.time()).replace(hour=h, minute=mi)
            if d.isoweekday() % 7 == target and local_instant(d, tz) > now.timestamp():
                return once(d)
    d = base.replace(hour=h, minute=mi, second=0, microsecond=0)
    if re.search(r"היום|today|tonight|הערב", t):
        return once(d)
    if local_instant(d, tz) <= now.timestamp():
        d += timedelta(days=1)
    return once(d)


# ---------------------------------------------------------------------- running jobs
class JobFailed(Exception):
    def __init__(self, code: str, message: str = "", *, permanent: bool = False) -> None:
        super().__init__(message or code)
        self.code, self.message, self.permanent = code, message or code, permanent or code in PERMANENT


class _NoReservation:
    def commit(self, actual: int | None = None) -> int:
        return 0

    def release(self) -> None:
        pass


class JobBudget:
    """A job's own cap on AI requests per run, in front of the Core's shared daily Budget (or alone on the
    ChatGPT plan, where the brain reserves the shared budget itself)."""

    def __init__(self, inner, limit: int) -> None:
        self.inner, self.limit, self.used = inner, max(0, int(limit)), 0

    def reserve(self, kind: str = "ai_requests", n: int = 1, *, shared: bool = True):
        """shared=False: count against the job cap only (the ChatGPT plan, where ChatGPTBrain._stream reserves
        the shared daily budget per request itself; reserving it here too charged each request twice)."""
        if self.used + n > self.limit:
            raise BudgetExceeded(f"job:{kind}", self.limit, self.used)
        res = self.inner.reserve(kind, n) if self.inner is not None and shared else _NoReservation()
        self.used += n
        return res


ResearchFn = Callable[[str, JobBudget], Awaitable[Any]]      # -> tasks.LookupResult


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    from .budget import pid_alive
    return pid_alive(int(pid))


def _j(v, default):
    try:
        return json.loads(v) if v else default
    except ValueError:
        return default


class Scheduler:
    def __init__(self, db: UserDB, *, research: ResearchFn | None = None, registry=None, alerts=None,
                 budget=None, features=None, clock: Callable[[], float] = time.time, sleep=asyncio.sleep,
                 pid: int | None = None) -> None:
        self.db, self.research, self.registry, self.alerts = db, research, registry, alerts
        self.budget, self.features, self.clock, self._sleep = budget, features, clock, sleep
        self.pid = os.getpid() if pid is None else pid
        self._running: dict[str, set[asyncio.Task]] = {}
        self._recovered = False
        self._stopped = False
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ jobs
    def tz(self, name: str | None = None) -> ZoneInfo:
        return zone(name or self.db.setting("scheduler.timezone"))

    def now_local(self, tz: ZoneInfo | None = None) -> datetime:
        return datetime.fromtimestamp(self.clock(), tz or self.tz())

    def job(self, job_id: str) -> dict | None:
        job_id = str(job_id or "").strip()
        if not job_id:
            return None
        return (self.db.row("SELECT * FROM jobs WHERE id = ?", (job_id,))
                or self.db.row("SELECT * FROM jobs WHERE id LIKE ? ORDER BY created DESC", (job_id + "%",)))

    def jobs(self, include_drafts: bool = True) -> list[dict]:
        q = "SELECT * FROM jobs" + ("" if include_drafts else " WHERE status != 'draft'") + " ORDER BY created DESC"
        return self.db.rows(q)

    def view(self, job: dict) -> dict:
        tz = self.tz(job["tz"])
        nxt = job["next_run"] if job["status"] == "active" else None
        upcoming = next_runs(job["kind"], job["spec"], tz, self.clock()) if job["status"] in ("draft", "paused") else []
        return {**{k: job[k] for k in ("id", "name", "status", "kind", "spec", "tz", "human", "action", "task", "tool",
                                       "max_requests", "retries", "backoff_s", "max_age_s", "allow_overlap",
                                       "keep_reports", "last_run", "last_outcome", "last_error", "created",
                                       "approved_at", "approved_by", "owner")},
                "skills": _j(job["skills"], []), "delivery": _j(job["delivery"], ["report"]),
                "next_run": nxt, "next_run_local": fmt_local(nxt, tz),
                "upcoming_local": [fmt_local(t, tz) for t in upcoming] if upcoming else
                [fmt_local(t, tz) for t in ([nxt] if nxt else [])],
                "last_run_local": fmt_local(job["last_run"], tz), "run_requested": job["run_requested"]}

    def preview(self, when: str, task: str, *, name: str = "", action: str = "research", tool: str | None = None,
                tool_args: dict | None = None, skills: list[str] | None = None, max_requests: int | None = None,
                delivery: list[str] | None = None, tz_name: str | None = None, owner: str = "user",
                by: str = "voice", max_age_s: float | None = None, retries: int | None = None,
                allow_overlap: bool = False, keep_reports: int = 30) -> dict:
        """Parse the schedule and store a DRAFT job (inactive until approve)."""
        task = " ".join(str(task or "").split())
        if action not in ACTIONS:
            raise ScheduleError("BAD_ACTION", f"action must be one of {ACTIONS}")
        if not task and action != "tool":
            raise ScheduleError("JOB_EMPTY", "מה לעשות בזמן הזה?")
        tz = self.tz(tz_name)
        sched = parse_when(when, self.now_local(tz), self.db.setting("scheduler.workweek"))
        if action == "tool":
            if not tool:
                raise ScheduleError("JOB_NO_TOOL", "a tool job needs the tool name")
            owner_skill = None
            if self.registry is not None and tool in self.registry._tools:
                owner_skill = self.registry._tools[tool][0].name
            skills = list(skills or ([owner_skill] if owner_skill else []))
        elif action == "research":
            skills = ["web_search"]
        else:
            skills = []
        delivery = [d for d in (delivery or ["report", "alert"]) if d in DELIVERY] or ["report"]
        side_effect = action == "tool" and self._tool_permission(tool) != "read"
        now = self.clock()
        jid = uuid.uuid4().hex[:8]
        self.db.x("INSERT INTO jobs (id, owner, name, status, kind, spec, tz, human, action, task, tool, tool_args, "
                  "skills, max_requests, retries, backoff_s, max_age_s, allow_overlap, keep_reports, delivery, "
                  "created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (jid, owner, (name or task or str(tool))[:80], "draft", sched.kind, sched.spec, tz.key, sched.human,
                   action, task, tool, json.dumps(tool_args or {}, ensure_ascii=False), json.dumps(skills),
                   int(max_requests if max_requests is not None else (0 if action != "research" else 2)),
                   0 if side_effect else int(1 if retries is None else retries), 60.0,
                   float(max_age_s if max_age_s is not None else self.db.setting("scheduler.max_age_s")),
                   int(bool(allow_overlap)), int(keep_reports), json.dumps(delivery), now, now))
        self.db.audit(by, "job_draft", jid, {"when": when, "spec": sched.spec, "action": action})
        logs.info("scheduler", "JOB_DRAFT", "schedule preview created", job=jid, kind=sched.kind, spec=sched.spec)
        return self.view(self.job(jid))

    def _tool_permission(self, tool: str | None) -> str | None:
        if self.registry is None or tool not in getattr(self.registry, "_tools", {}):
            return None
        return self.registry._tools[tool][1].permission

    def approve(self, job_id: str, *, by: str) -> dict:
        job = self.job(job_id)
        if job is None:
            raise ScheduleError("JOB_NOT_FOUND", "אין משימה כזאת")
        if job["status"] not in ("draft", "paused"):
            return self.view(job)
        nxt = next_after(job["kind"], job["spec"], self.tz(job["tz"]), self.clock())
        if nxt is None:
            raise ScheduleError("SCHEDULE_PAST", "הזמן הזה כבר עבר")
        now = self.clock()
        self.db.x("UPDATE jobs SET status='active', next_run=?, approved_at=COALESCE(approved_at, ?), "
                  "approved_by=COALESCE(approved_by, ?), updated=? WHERE id=?", (nxt, now, by, now, job["id"]))
        self.db.audit(by, "job_approve" if job["status"] == "draft" else "job_resume", job["id"])
        logs.info("scheduler", "JOB_ACTIVE", "job active", job=job["id"], by=by, next_run=nxt)
        self.poke()
        return self.view(self.job(job["id"]))

    def pause(self, job_id: str, *, by: str) -> dict:
        job = self._need(job_id)
        self.db.x("UPDATE jobs SET status='paused', next_run=NULL, updated=? WHERE id=? AND status='active'",
                  (self.clock(), job["id"]))
        self.db.audit(by, "job_pause", job["id"])
        return self.view(self.job(job["id"]))

    def run_now(self, job_id: str, *, by: str) -> dict:
        job = self._need(job_id)
        if job["status"] == "draft":
            raise ScheduleError("JOB_NOT_APPROVED", "המשימה עוד לא אושרה")
        self.db.x("UPDATE jobs SET run_requested=?, updated=? WHERE id=?", (self.clock(), self.clock(), job["id"]))
        self.db.audit(by, "job_run_now", job["id"])
        self.poke()
        return self.view(self.job(job["id"]))

    def delete(self, job_id: str, *, by: str) -> bool:
        job = self._need(job_id)
        self.db.x("DELETE FROM jobs WHERE id=?", (job["id"],))
        self.db.audit(by, "job_delete", job["id"], {"name": job["name"]})
        return True

    def edit(self, job_id: str, *, by: str, **fields) -> dict:
        """Dashboard edit. A new schedule is re-parsed; an active job keeps running on the new schedule."""
        job = self._need(job_id)
        sets: dict[str, Any] = {}
        if "when" in fields and fields["when"]:
            tz = self.tz(fields.get("tz") or job["tz"])
            s = parse_when(fields["when"], self.now_local(tz), self.db.setting("scheduler.workweek"))
            sets.update(kind=s.kind, spec=s.spec, human=s.human, tz=tz.key)
            if job["status"] == "active":
                sets["next_run"] = next_after(s.kind, s.spec, tz, self.clock())
        for k, conv in (("name", lambda v: str(v)[:80]), ("task", lambda v: " ".join(str(v).split())[:600]),
                        ("max_requests", lambda v: max(0, min(int(v), 10))),
                        ("retries", lambda v: max(0, min(int(v), 5))),
                        ("max_age_s", lambda v: max(0.0, float(v))), ("allow_overlap", lambda v: int(bool(v))),
                        ("keep_reports", lambda v: max(1, min(int(v), 500))),
                        ("delivery", lambda v: json.dumps([d for d in v if d in DELIVERY] or ["report"]))):
            if k in fields and fields[k] is not None:
                sets[k] = conv(fields[k])
        if not sets:
            return self.view(job)
        sets["updated"] = self.clock()
        self.db.x(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), job["id"]))
        self.db.audit(by, "job_edit", job["id"], {k: v for k, v in sets.items() if k != "updated"})
        self.poke()
        return self.view(self.job(job["id"]))

    def _need(self, job_id: str) -> dict:
        job = self.job(job_id)
        if job is None:
            raise ScheduleError("JOB_NOT_FOUND", "אין משימה כזאת")
        return job

    def runs(self, job_id: str | None = None, limit: int = 50) -> list[dict]:
        if job_id:
            return self.db.rows("SELECT * FROM job_runs WHERE job_id=? ORDER BY scheduled_for DESC, id DESC LIMIT ?",
                                (job_id, limit))
        return self.db.rows("SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ loop
    def poke(self) -> None:
        """Wake the loop now. Thread-safe: the dashboard (HTTP thread) shares this Scheduler with the Core."""
        if self._wake is None:
            return
        loop = getattr(self, "_loop", None)
        try:
            same = asyncio.get_running_loop() is loop
        except RuntimeError:
            same = False
        if loop is None or same:
            self._wake.set()
            return
        try:
            loop.call_soon_threadsafe(self._wake.set)
        except RuntimeError:                       # the Core's loop is closed (shutting down)
            pass

    def enabled(self) -> bool:
        if self.features is None:
            return True
        try:
            return self.features.active("scheduler")
        except Exception:
            return True

    async def run(self, on_tick: Callable[[], Awaitable[None]] | None = None) -> None:
        self._wake = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        logs.info("scheduler", "SCHEDULER_STARTED", "scheduler running", poll_s=POLL_S)
        while not self._stopped:
            delay = POLL_S
            try:
                delay = await self.tick()
                if on_tick is not None:
                    await on_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logs.error("scheduler", "SCHEDULER_TICK_FAILED", "scheduler tick failed", exc=True)
            try:
                await asyncio.wait_for(self._wake.wait(), max(1.0, delay))
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def stop(self) -> None:
        self._stopped = True
        self.poke()
        tasks = [t for s in self._running.values() for t in s if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def tick(self) -> float:
        """Start what is due (and recover interrupted runs once); returns the seconds until the next due job."""
        now = self.clock()
        if not self._recovered:
            self._recovered = True
            self._recover(now)
        self.db.x("DELETE FROM jobs WHERE status='draft' AND created < ?", (now - DRAFT_TTL_S,))
        if not self.enabled():
            return POLL_S
        for job in self.db.rows("SELECT * FROM jobs WHERE run_requested IS NOT NULL AND status IN ('active','paused',"
                                "'done')"):
            self.db.x("UPDATE jobs SET run_requested=NULL WHERE id=?", (job["id"],))
            self._start(job, job["run_requested"], "manual", False, 0, idem=f"{job['id']}:manual:"
                                                                          f"{int(job['run_requested'] * 1000)}")
        for job in self.db.rows("SELECT * FROM jobs WHERE status='active' AND next_run IS NOT NULL AND next_run <= ?",
                                (now,)):
            self._due(job, now)
        nxt = self.db.row("SELECT MIN(next_run) t FROM jobs WHERE status='active' AND next_run IS NOT NULL")
        return POLL_S if not nxt or nxt["t"] is None else max(0.5, min(POLL_S, nxt["t"] - self.clock()))

    def _due(self, job: dict, now: float) -> None:
        tz = self.tz(job["tz"])
        occ, missed = latest_due(job["kind"], job["spec"], tz, job["next_run"], now)
        nxt = next_after(job["kind"], job["spec"], tz, now)
        # advance FIRST: a crash from here on can never start this occurrence again (and the idem key guards it)
        self.db.x("UPDATE jobs SET next_run=?, status=CASE WHEN ? IS NULL THEN 'done' ELSE status END, updated=? "
                  "WHERE id=?", (nxt, nxt, now, job["id"]))
        lateness = now - occ
        late = lateness > LATE_GRACE_S
        if lateness > job["max_age_s"]:
            self._record_skip(job, occ, "too_old", f"missed while JARVES was off ({lateness / 3600:.1f} h late, "
                                                   f"max {job['max_age_s'] / 3600:.1f} h)", missed)
            return
        if self._busy(job["id"]) and not job["allow_overlap"]:
            self._record_skip(job, occ, "overlap", "the previous run is still going", missed)
            return
        self._start(job, occ, "catch_up" if late else "schedule", late, missed)

    def _busy(self, job_id: str) -> bool:
        return any(not t.done() for t in self._running.get(job_id, ()))

    def _record_skip(self, job: dict, occ: float, code: str, why: str, missed: int) -> None:
        try:
            self.db.x("INSERT INTO job_runs (job_id, idem, scheduled_for, trigger, state, late, missed, started, "
                      "finished, error) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (job["id"], f"{job['id']}:{int(occ // 60)}", occ, "schedule", "skipped", 1, missed,
                       self.clock(), self.clock(), f"{code}: {why}"))
        except sqlite3.IntegrityError:
            return
        self.db.x("UPDATE jobs SET last_outcome='skipped', last_error=? WHERE id=?", (why, job["id"]))
        logs.warn("scheduler", "JOB_SKIPPED", "job occurrence skipped", error_code=code, job=job["id"], why=why)

    def _start(self, job: dict, occ: float, trigger: str, late: bool, missed: int, *, idem: str | None = None,
               run: dict | None = None) -> asyncio.Task | None:
        idem = idem or f"{job['id']}:{int(occ // 60)}"
        if run is None:
            try:
                cur = self.db.x("INSERT INTO job_runs (job_id, idem, scheduled_for, trigger, state, late, missed, "
                                "started, pid) VALUES (?,?,?,?,?,?,?,?,?)",
                                (job["id"], idem, occ, trigger, "running", int(late), missed, self.clock(), self.pid))
            except sqlite3.IntegrityError:
                logs.info("scheduler", "JOB_ALREADY_RAN", "occurrence already started once; not repeated",
                          job=job["id"], idem=idem)
                return None
            run = self.db.row("SELECT * FROM job_runs WHERE id=?", (cur.lastrowid,))
        try:
            loop_task = asyncio.get_running_loop().create_task(self._execute(job, run), name=f"job.{job['id']}")
        except RuntimeError:
            return None
        self._running.setdefault(job["id"], set()).add(loop_task)
        loop_task.add_done_callback(lambda t, jid=job["id"]: self._running.get(jid, set()).discard(t))
        logs.info("scheduler", "JOB_STARTED", "job run started", trigger=trigger, job=job["id"], run=run["id"],
                  late=late, missed=missed)
        return loop_task

    def _recover(self, now: float) -> None:
        """Runs left 'running' by a process that died (crash, power cut): resume once when safe, else mark."""
        for run in self.db.rows("SELECT * FROM job_runs WHERE state='running'"):
            if (run["pid"] == self.pid and self._busy(run["job_id"])) or (
                    run["pid"] and run["pid"] != self.pid and _pid_alive(run["pid"])):
                continue                                   # still running here / in another live Core
            job = self.db.row("SELECT * FROM jobs WHERE id=?", (run["job_id"],))
            fresh = job is not None and now - run["scheduled_for"] <= job["max_age_s"]
            if job is not None and job["action"] in ("reminder", "research") and fresh \
                    and run["attempts"] < 1 + job["retries"]:
                self.db.x("UPDATE job_runs SET pid=?, late=1, trigger='resume' WHERE id=?", (self.pid, run["id"]))
                logs.warn("scheduler", "JOB_RESUMED", "run interrupted by a stop/crash: resuming once",
                          job=job["id"], run=run["id"])
                self._start(job, run["scheduled_for"], "resume", True, run["missed"],
                            run=self.db.row("SELECT * FROM job_runs WHERE id=?", (run["id"],)))
                continue
            why = ("JARVES stopped during the run; not repeated automatically (the action may already have "
                   "happened)" if job is not None and job["action"] == "tool" else "JARVES stopped during the run")
            self.db.x("UPDATE job_runs SET state='interrupted', finished=?, error=? WHERE id=?", (now, why, run["id"]))
            if job is not None:
                self.db.x("UPDATE jobs SET last_outcome='interrupted', last_error=? WHERE id=?", (why, job["id"]))
                self._alert(f"jobint:{run['idem']}", "normal", "job_interrupted", f"המשימה \"{job['name']}\" נקטעה",
                            "JARVES נעצר באמצע הריצה ולא חזרתי עליה אוטומטית. אפשר להריץ שוב מלוח הבקרה.")
            logs.warn("scheduler", "JOB_INTERRUPTED", "run interrupted by a stop/crash; not repeated",
                      job=run["job_id"], run=run["id"])

    async def _execute(self, job: dict, run: dict) -> None:
        budget = JobBudget(self.budget, max(0, job["max_requests"] - (run["requests"] or 0)))
        attempts, err, result = int(run["attempts"] or 0), None, None
        try:
            while attempts < 1 + job["retries"]:
                attempts += 1
                self.db.x("UPDATE job_runs SET attempts=? WHERE id=?", (attempts, run["id"]))
                try:
                    result = await self._action(job, budget)
                    err = None
                    break
                except JobFailed as e:
                    err = e
                except BudgetExceeded as e:
                    err = JobFailed("JOB_BUDGET", f"cost budget of this job reached ({e})", permanent=True)
                except asyncio.CancelledError:
                    raise
                except Exception as e:                     # a broken action never takes the scheduler down
                    logs.error("scheduler", "JOB_CRASHED", "job action crashed", exc=True)
                    err = JobFailed(type(e).__name__, str(e)[:160])
                finally:
                    self.db.x("UPDATE job_runs SET requests=? WHERE id=?", ((run["requests"] or 0) + budget.used,
                                                                          run["id"]))
                if err.permanent or attempts >= 1 + job["retries"]:
                    break
                wait = min(job["backoff_s"] * 2 ** (attempts - 1), BACKOFF_CAP_S)
                logs.warn("scheduler", "JOB_RETRY", "job attempt failed; retrying", error_code=err.code, retry_s=wait,
                          job=job["id"], attempt=attempts)
                await self._sleep(wait)
        except asyncio.CancelledError:
            self.db.x("UPDATE job_runs SET pid=NULL WHERE id=?", (run["id"],))   # next start resumes / marks it
            raise
        self._finish(job, run, result, err)

    async def _action(self, job: dict, budget: JobBudget) -> tuple[str, str, list[dict]]:
        if job["action"] == "reminder":
            return "ok", job["task"], []
        if job["action"] == "research":
            if self.research is None:
                raise JobFailed("AI_UNAVAILABLE", "no AI route for research (offline or no key/plan)", permanent=True)
            from .tasks import LookupFailed
            try:
                out = await self.research(job["task"], budget)
            except LookupFailed as e:
                raise JobFailed(e.code, str(e), permanent=e.code in PERMANENT) from e
            sources = [{"type": "web_search", "query": q} for q in (getattr(out, "queries", None) or [])]
            return "ok", out.text, sources
        if self.registry is None:
            raise JobFailed("AI_UNAVAILABLE", "no tool registry", permanent=True)
        st = self.registry._tools.get(job["tool"])
        if st is None:
            raise JobFailed("TOOL_UNKNOWN", f"unknown tool {job['tool']}", permanent=True)
        if st[0].name not in _j(job["skills"], []):
            raise JobFailed("JOB_SKILL_NOT_PERMITTED", f"skill {st[0].name} is not permitted for this job",
                            permanent=True)
        res = await self.registry.call(job["tool"], _j(job["tool_args"], {}), origin="model")
        if not res.ok:
            raise JobFailed(res.code, res.message)
        return ("partial" if res.data.get("partial") else "ok"), res.message, [{"type": "tool", "name": job["tool"]}]

    def _finish(self, job: dict, run: dict, result, err: JobFailed | None) -> None:
        now = self.clock()
        tz = self.tz(job["tz"])
        late = bool(run["late"])
        status, text, sources = result if err is None else ("failed", f"לא הצלחתי: {err.message}", [])
        title = f"{job['name']} · {fmt_local(run['scheduled_for'], tz)}" + (" (באיחור)" if late else "")
        cur = self.db.x("INSERT INTO reports (job_id, run_id, title, text, status, sources, created, scheduled_for, "
                        "late) VALUES (?,?,?,?,?,?,?,?,?)",
                        (job["id"], run["id"], title, text, status, json.dumps(sources, ensure_ascii=False), now,
                         run["scheduled_for"], int(late)))
        rid = cur.lastrowid
        state = "done" if status == "ok" else status
        self.db.x("UPDATE job_runs SET state=?, finished=?, error=?, report_id=? WHERE id=?",
                  (state, now, None if err is None else f"{err.code}: {err.message}"[:300], rid, run["id"]))
        self.db.x("UPDATE jobs SET last_run=?, last_outcome=?, last_error=? WHERE id=?",
                  (now, state, None if err is None else err.code, job["id"]))
        keep = max(1, int(job["keep_reports"]))
        self.db.x("DELETE FROM reports WHERE job_id=? AND id NOT IN (SELECT id FROM reports WHERE job_id=? ORDER BY "
                  "created DESC, id DESC LIMIT ?)", (job["id"], job["id"], keep))
        level = logs.info if err is None else logs.warn
        level("scheduler", f"JOB_{state.upper()}", "job run finished", state=state, job=job["id"], run=run["id"],
              report=rid, late=late, attempts=run["attempts"], error_code=None if err is None else err.code)
        delivery = _j(job["delivery"], ["report"])
        self.poke()                                  # the loop delivers the alert now, not at the next poll
        if err is not None:
            self._alert(f"jobfail:{job['id']}", "normal", "job_failed", f"המשימה \"{job['name']}\" נכשלה",
                        f"{err.code}. הפרטים בלוח הבקרה.")
        elif job["action"] == "reminder":
            self._alert(run["idem"], "high", "reminder", f"תזכורת: {job['name']}", text)
        elif "speak" in delivery:
            self._alert(run["idem"], "high", "report", title, text, report_id=rid)
        elif "alert" in delivery:
            self._alert(run["idem"], "normal", "report", f"הדוח מוכן: {job['name']}",
                        "אפשר לבקש ממני להקריא אותו." + (" (רץ באיחור)" if late else ""), report_id=rid)

    def _alert(self, key: str, priority: str, kind: str, title: str, text: str, report_id: int | None = None) -> None:
        if self.alerts is None:
            return
        try:
            self.alerts.raise_("scheduler", title, text, priority=priority, kind=kind, dedupe_key=key,
                               ref=None if report_id is None else f"report:{report_id}")
        except Exception:
            logs.error("scheduler", "JOB_ALERT_FAILED", "could not raise the job alert", exc=True)


# ---------------------------------------------------------------------- reports
PERIODS = ("latest", "this_morning", "today", "yesterday", "this_week")


def period_bounds(period: str, tz: ZoneInfo, now: float) -> tuple[float, float]:
    d = datetime.fromtimestamp(now, tz).replace(tzinfo=None)
    midnight = d.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "this_morning":
        return local_instant(midnight, tz), local_instant(midnight.replace(hour=12), tz)
    if period == "today":
        return local_instant(midnight, tz), now + 1
    if period == "yesterday":
        return local_instant(midnight - timedelta(days=1), tz), local_instant(midnight, tz)
    if period == "this_week":
        return now - 7 * 86400, now + 1
    return 0.0, now + 1


def find_reports(db: UserDB, *, period: str = "latest", query: str = "", job_id: str = "", tz: ZoneInfo,
                 now: float, limit: int = 20) -> list[dict]:
    t0, t1 = period_bounds(period, tz, now)
    sql = ("SELECT r.*, j.name job_name FROM reports r LEFT JOIN jobs j ON j.id = r.job_id WHERE r.created >= ? "
           "AND r.created < ?")
    args: list[Any] = [t0, t1]
    if job_id:
        sql += " AND r.job_id LIKE ?"
        args.append(job_id + "%")
    if query:
        sql += " AND (r.title LIKE ? OR r.text LIKE ? OR j.name LIKE ? OR j.task LIKE ?)"
        args += [f"%{query}%"] * 4
    sql += " ORDER BY r.created DESC, r.id DESC LIMIT ?"
    args.append(limit)
    out = db.rows(sql, tuple(args))
    for r in out:
        r["sources"] = _j(r["sources"], [])
        r["created_local"] = fmt_local(r["created"], tz)
    return out


def report_get(db: UserDB, rid: int, tz: ZoneInfo | None = None) -> dict | None:
    r = db.row("SELECT r.*, j.name job_name FROM reports r LEFT JOIN jobs j ON j.id = r.job_id WHERE r.id = ?",
               (int(rid),))
    if r is not None:
        r["sources"] = _j(r["sources"], [])
        if tz is not None:
            r["created_local"] = fmt_local(r["created"], tz)
    return r


# ---------------------------------------------------------------------- voice / agent tools
class PreviewArgs(BaseModel):
    when: str = Field(..., min_length=1, max_length=120,
                      description="When, in the user's words: 'כל בוקר ב-8', 'מחר ב-9:30', 'every monday at 10', "
                                  "'בעוד 20 דקות', or a 5-field cron expression")
    task: str = Field(..., min_length=2, max_length=600,
                      description="What to do then, self-contained (research question, or the reminder text)")
    kind: str = Field("research", pattern="^(research|reminder)$",
                      description="research = look it up and save a report; reminder = just remind the user")
    name: str = Field("", max_length=80, description="Short job name (optional)")
    speak: bool = Field(False, description="Read the result aloud when ready (else: save it and tell briefly)")


class JobIdArgs(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=40, description="job id from jobs_list / schedule_preview")


class JobControlArgs(JobIdArgs):
    op: str = Field(..., pattern="^(pause|resume|run_now)$", description="pause, resume or run_now")


class ReportFindArgs(BaseModel):
    period: str = Field("latest", pattern="^(latest|this_morning|today|yesterday|this_week)$")
    query: str = Field("", max_length=80, description="words from the report or job name, e.g. 'AI', 'מזג אוויר'")


class ReportReadArgs(ReportFindArgs):
    report_id: int = Field(0, ge=0, description="report id from report_find; 0 = the newest match")


def make_skills(sched: Scheduler, registry=None) -> list:
    from .skills import Skill, Tool, ToolResult

    def err(e: ScheduleError) -> ToolResult:
        return ToolResult(False, e.message, code=e.code)

    def brief(v: dict) -> dict:
        return {k: v[k] for k in ("id", "name", "status", "human", "action", "task", "next_run_local",
                                  "upcoming_local", "skills", "max_requests", "last_outcome", "last_run_local")}

    class SchedulerSkill(Skill):
        name = "scheduler"
        description = "scheduled jobs: reminders and recurring reports"

        def tools(self) -> list:
            async def preview(a: PreviewArgs):
                try:
                    v = sched.preview(a.when, a.task, name=a.name, action=a.kind,
                                      delivery=["report", "speak" if a.speak else "alert"], by="voice")
                except ScheduleError as e:
                    return err(e)
                cost = (f"up to {v['max_requests']} AI request(s) per run" if v["action"] == "research"
                        else "no AI cost")
                return ToolResult(True, f"PREVIEW (not active yet): {v['human']} — {v['task']}. Next: "
                                        f"{'; '.join(v['upcoming_local'])}. {cost}. Read this to the user and ask "
                                        "whether to activate it; only after a clear yes call schedule_approve.",
                                  brief(v))

            async def activate(job: dict) -> ToolResult:
                """schedule_approve and job_control resume share this: a side-effect (tool) job is activated only
                after the user approves it on the screen/dashboard, never by voice alone."""
                if job["action"] == "tool":
                    confirm = getattr(registry, "confirm", None)
                    ok = await confirm(f"להפעיל משימה מתוזמנת: {job['human']} — {job['name']}?") if confirm else False
                    if not ok:
                        return ToolResult(False, "needs approval on the screen or the dashboard Jobs page",
                                          code="JOB_NEEDS_APPROVAL")
                try:
                    v = sched.approve(job["id"], by="voice")
                except ScheduleError as e:
                    return err(e)
                return ToolResult(True, f"active: {v['human']}; next run {v['next_run_local']}", brief(v))

            async def approve(a: JobIdArgs):
                job = sched.job(a.job_id)
                if job is None:
                    return ToolResult(False, "no such job", code="JOB_NOT_FOUND")
                return await activate(job)

            async def list_(_):
                items = [brief(sched.view(j)) for j in sched.jobs(include_drafts=False)][:20]
                return ToolResult(True, f"{len(items)} job(s)" if items else "no scheduled jobs", {"jobs": items})

            async def control(a: JobControlArgs):
                if a.op == "resume":               # review #6: resume used to skip the on-screen confirm and
                    job = sched.job(a.job_id)      # silently activated drafts too
                    if job is None:
                        return ToolResult(False, "no such job", code="JOB_NOT_FOUND")
                    if job["status"] == "draft":
                        return ToolResult(False, "this job is an unapproved preview: read it back and ask; after a "
                                                 "clear yes call schedule_approve", code="JOB_NOT_APPROVED")
                    if job["status"] != "paused":
                        return ToolResult(True, f"resume: {job['name']} ({job['status']})", brief(sched.view(job)))
                    return await activate(job)
                try:
                    if a.op == "pause":
                        v = sched.pause(a.job_id, by="voice")
                    else:
                        v = sched.run_now(a.job_id, by="voice")
                except ScheduleError as e:
                    return err(e)
                return ToolResult(True, f"{a.op}: {v['name']} ({v['status']})", brief(v))

            async def delete(a: JobIdArgs):
                try:
                    sched.delete(a.job_id, by="voice")
                except ScheduleError as e:
                    return err(e)
                return ToolResult(True, "deleted")

            async def delete_impact(a: JobIdArgs):
                job = sched.job(a.job_id)
                return 1, f"למחוק את המשימה \"{job['name'] if job else a.job_id}\"? / Delete this scheduled job?"

            from .skills import NoArgs
            return [
                Tool("schedule_preview", "Plan a scheduled job from the user's words (reminder or recurring/one-off "
                     "research report). Creates an INACTIVE preview; read it back and ask before approving.",
                     preview, PreviewArgs, permission="action"),
                Tool("schedule_approve", "Activate a previewed job after the user clearly said yes.", approve,
                     JobIdArgs, permission="action"),
                Tool("jobs_list", "List scheduled jobs with their next run times and last outcome.", list_, NoArgs,
                     permission="read"),
                Tool("job_control", "Pause, resume or run a scheduled job now.", control, JobControlArgs,
                     permission="action"),
                Tool("job_delete", "Delete a scheduled job (asks the user first).", delete, JobIdArgs,
                     permission="destructive", impact=delete_impact),
            ]

    class ReportSkill(Skill):
        name = "reports"
        description = "saved results of scheduled jobs"

        def tools(self) -> list:
            def found(a: ReportFindArgs) -> list[dict]:
                return find_reports(sched.db, period=a.period, query=a.query, tz=sched.tz(), now=sched.clock())

            async def find(a: ReportFindArgs):
                items = [{"id": r["id"], "title": r["title"], "status": r["status"], "late": bool(r["late"]),
                          "created_local": r["created_local"], "read": r["read_at"] is not None} for r in found(a)]
                return ToolResult(True, f"{len(items)} saved report(s)" if items else "no saved report matches",
                                  {"reports": items[:10]})

            async def read(a: ReportReadArgs):
                r = report_get(sched.db, a.report_id, sched.tz()) if a.report_id else (found(a) or [None])[0]
                if r is None:
                    return ToolResult(False, "no saved report matches; say so (do not research it now unless the "
                                             "user asks)", code="REPORT_NOT_FOUND")
                sched.db.x("UPDATE reports SET read_at=? WHERE id=?", (sched.clock(), r["id"]))
                return ToolResult(True, "Saved report: read it to the user as it is, naturally in Hebrew. Do NOT "
                                        "regenerate it or search again.",
                                  {"id": r["id"], "title": r["title"], "created_local": r.get("created_local"),
                                   "status": r["status"], "late": bool(r["late"]), "text": r["text"][:4000],
                                   "sources": r["sources"][:8]})

            return [
                Tool("report_find", "Find saved reports from scheduled jobs (by period and words).", find,
                     ReportFindArgs, permission="read"),
                Tool("report_read", "Get the text of a saved report to read aloud ('read this morning's AI "
                     "report'). Reads the stored result; never regenerates it.", read, ReportReadArgs,
                     permission="read", untrusted_output=True),
            ]

    return [SchedulerSkill(), ReportSkill()]


ALLOWED_ACTIONS = {"schedule_preview", "schedule_approve", "job_control"}


# ---------------------------------------------------------------------- dashboard
def _ctx_sched(ctx) -> Scheduler:
    s = getattr(ctx, "_scheduler", None)
    if s is None:
        s = Scheduler(ctx_db(ctx))           # writes rows only; the Core's loop runs them
        ctx._scheduler = s
    return s


def api_jobs(ctx, m, body):
    s = _ctx_sched(ctx)
    jobs = []
    for j in s.jobs():
        v = s.view(j)
        v["runs"] = s.runs(j["id"], 10)
        jobs.append(v)
    return 200, {"jobs": jobs, "timezone": s.tz().key, "now_local": fmt_local(s.clock(), s.tz()),
                 "max_age_s": s.db.setting("scheduler.max_age_s"), "workweek": s.db.setting("scheduler.workweek"),
                 "note": "The Core runs due jobs (checked at least every 30 s); missed jobs run once after start-up "
                         "when not older than their max age, labelled late."}


def api_job_create(ctx, m, body):
    s, b = _ctx_sched(ctx), body or {}
    try:
        v = s.preview(str(b.get("when", "")), str(b.get("task", "")), name=str(b.get("name", "")),
                      action=str(b.get("action", "research")) if b.get("action") in ("research", "reminder")
                      else "research", delivery=b.get("delivery"), max_requests=b.get("max_requests"),
                      tz_name=b.get("tz") or None, by="dashboard")
    except ScheduleError as e:
        return 400, {"error": e.message, "code": e.code}
    return 200, v


def api_schedule_parse(ctx, m, body):
    s = _ctx_sched(ctx)
    try:
        tz = s.tz((body or {}).get("tz") or None)
        sc = parse_when(str((body or {}).get("when", "")), s.now_local(tz), s.db.setting("scheduler.workweek"))
    except ScheduleError as e:
        return 400, {"error": e.message, "code": e.code}
    return 200, {"kind": sc.kind, "spec": sc.spec, "human": sc.human,
                 "next": [fmt_local(t, tz) for t in next_runs(sc.kind, sc.spec, tz, s.clock())]}


def api_job_op(ctx, m, body):
    s, b = _ctx_sched(ctx), body or {}
    op = b.get("op")
    try:
        if op == "approve" or op == "resume":
            return 200, s.approve(m.group(1), by="dashboard")
        if op == "pause":
            return 200, s.pause(m.group(1), by="dashboard")
        if op == "run_now":
            return 200, s.run_now(m.group(1), by="dashboard")
        if op == "delete":
            from . import approvals
            ok, _w = approvals.ask_owner(ctx.home, f"Delete scheduled job {m.group(1)}?", title="dashboard_delete_job",
                                         risk="medium", why="You pressed Delete on the dashboard Jobs page.",
                                         details={"job": m.group(1)})
            if not ok:
                return 403, dict(approvals.APPROVAL_REFUSED)
            return 200, {"deleted": s.delete(m.group(1), by="dashboard")}
        if op == "edit":
            return 200, s.edit(m.group(1), by="dashboard", **{k: b.get(k) for k in (
                "when", "name", "task", "max_requests", "retries", "max_age_s", "allow_overlap", "keep_reports",
                "delivery", "tz")})
    except ScheduleError as e:
        return (404 if e.code == "JOB_NOT_FOUND" else 400), {"error": e.message, "code": e.code}
    return 400, {"error": "op must be approve, resume, pause, run_now, delete or edit"}


def api_reports(ctx, m, body, q=None):
    s, q = _ctx_sched(ctx), q or {}
    period = (q.get("period") or ["latest"])[0]
    return 200, {"reports": find_reports(s.db, period=period if period in PERIODS else "latest",
                                         query=(q.get("q") or [""])[0][:80], job_id=(q.get("job") or [""])[0][:40],
                                         tz=s.tz(), now=s.clock(), limit=100)}


def api_report(ctx, m, body):
    s = _ctx_sched(ctx)
    r = report_get(s.db, int(m.group(1)), s.tz())
    return (200, r) if r else (404, {"error": "no such report"})


def api_report_op(ctx, m, body):
    s, op = _ctx_sched(ctx), (body or {}).get("op")
    r = report_get(s.db, int(m.group(1)), s.tz())
    if r is None:
        return 404, {"error": "no such report"}
    if op == "speak":
        from .alerts import AlertEngine
        a = AlertEngine(s.db).raise_("dashboard", r["title"], r["text"], priority="normal", kind="readback",
                                     channel="speech", user_requested=True, ref=f"report:{r['id']}")
        s.db.x("UPDATE reports SET read_at=? WHERE id=?", (s.clock(), r["id"]))
        return 200, {"queued": True, "alert": a,
                     "note": "JARVES reads it aloud as soon as he can speak (the Core picks it up within 30 s)."}
    if op == "delete":
        from . import approvals
        ok, _w = approvals.ask_owner(ctx.home, f"Delete report #{r['id']}?", title="dashboard_delete_report",
                                     risk="medium", why="You pressed Delete on the dashboard Reports page.",
                                     details={"report": r["id"]})
        if not ok:
            return 403, dict(approvals.APPROVAL_REFUSED)
        s.db.x("DELETE FROM reports WHERE id=?", (r["id"],))
        s.db.audit("dashboard", "report_delete", r["id"])
        return 200, {"deleted": True}
    return 400, {"error": "op must be speak or delete"}


def api_sched_settings(ctx, m, body):
    s, b = _ctx_sched(ctx), body or {}
    try:
        if "timezone" in b:
            s.db.set_setting("scheduler.timezone", zone(str(b["timezone"])).key, actor="dashboard")
        if "max_age_s" in b:
            s.db.set_setting("scheduler.max_age_s", max(0.0, min(float(b["max_age_s"]), 7 * 86400.0)),
                             actor="dashboard")
        if "workweek" in b:                       # WP-L guide: "ימי חול" = Sunday-Thursday or Monday-Friday
            if b["workweek"] not in WORKWEEKS:
                return 400, {"error": "workweek must be 0-4 (Sunday-Thursday) or 1-5 (Monday-Friday)"}
            s.db.set_setting("scheduler.workweek", b["workweek"], actor="dashboard")
    except ScheduleError as e:
        return 400, {"error": e.message, "code": e.code}
    except (TypeError, ValueError):
        return 400, {"error": "max_age_s must be a number of seconds"}
    return 200, {"settings": s.db.settings("scheduler."),
                 "note": "applies to NEW jobs; existing jobs keep their own zone (edit them to change it)"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/jobs", api_jobs)
    register("POST", r"/api/scheduler/settings", api_sched_settings)
    register("POST", r"/api/jobs", api_job_create)
    register("POST", r"/api/schedule/parse", api_schedule_parse)
    register("POST", r"/api/jobs/([0-9a-f]{1,40})", api_job_op)
    register("GET", r"/api/reports", api_reports)
    register("GET", r"/api/reports/(\d+)", api_report)
    register("POST", r"/api/reports/(\d+)", api_report_op)


def make_research(tasks_manager) -> ResearchFn | None:
    """Scheduled research on the same route as background tasks (tasks.py): API key or ChatGPT plan. Each scheduler
    attempt is ONE runner attempt (the scheduler owns retries/backoff) and pays through the job's JobBudget."""
    if tasks_manager is None:
        return None
    from .tasks import PlanResearchRunner, ResearchRunner
    base = tasks_manager.runner

    async def research(task: str, budget: JobBudget):
        r = copy.copy(base)
        r.retries = 0
        if isinstance(base, ResearchRunner):
            r.budget, r.area, r.prefix = budget, "scheduler", "JOB_BACKEND"
            return await r(task)
        if isinstance(base, PlanResearchRunner):
            # the job cap only: the brain reserves the shared daily budget per request (review #4: no double charge)
            res = budget.reserve("ai_requests", shared=False)
            try:
                return await r(task)
            finally:
                res.commit()
        return await base(task)
    return research
