"""WP-M3 durable scheduler: cron + DST, Hebrew schedules, approval, idempotency, catch-up after a reboot, max age,
overlap, retries/backoff, job cost budget, crash mid-run (fault injection), tool jobs under the central policy,
report readback without regenerating. All offline: fake clock, fake research, no network, no audio."""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from jarves import scheduler as S
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult
from jarves.tasks import LookupFailed, LookupResult
from jarves.userdata import UserDB

JLM = ZoneInfo("Asia/Jerusalem")
NY = ZoneInfo("America/New_York")
DEAD_PID = 4_000_000          # a process id that does not exist: "the Core that crashed"


def ts(tz, *a) -> float:
    return datetime(*a, tzinfo=tz).timestamp()


def local(t: float, tz) -> datetime:
    return datetime.fromtimestamp(t, tz)


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeResearch:
    def __init__(self, *results, cost: int = 1) -> None:
        self.results, self.calls, self.cost = list(results), 0, cost

    async def __call__(self, task, budget):
        for _ in range(self.cost):
            budget.reserve("ai_requests")
        self.calls += 1
        r = self.results.pop(0) if self.results else f"result for {task}"
        if isinstance(r, BaseException):
            raise r
        if isinstance(r, asyncio.Event):
            await r.wait()
            r = "after the wait"
        return LookupResult(r, queries=["ai news today"])


class Alerts:
    def __init__(self) -> None:
        self.raised = []

    def raise_(self, source, title, text="", **kw):
        self.raised.append({"source": source, "title": title, "text": text, **kw})


@pytest.fixture
def db(tmp_path):
    d = UserDB(tmp_path / "memory.db")
    yield d
    d.close()


async def settle(sched: S.Scheduler) -> None:
    await asyncio.gather(*[t for s in sched._running.values() for t in s], return_exceptions=True)


def make(db, clock, research=None, **kw) -> S.Scheduler:
    sleeps = kw.pop("sleeps", None)

    async def fake_sleep(s):
        if sleeps is not None:
            sleeps.append(s)
    return S.Scheduler(db, research=research, clock=clock, sleep=fake_sleep, **kw)


# ---------------------------------------------------------------------- cron and DST
def test_cron_fields_and_vixie_day_rule():
    c = S.Cron("*/15 8-10 1,15 * 1")
    assert c.minutes == [0, 15, 30, 45] and c.hours == [8, 9, 10] and c.weekdays == [1]
    assert c.day_ok(datetime(2026, 10, 1).date())          # the 1st (a Thursday): day-of-month matches
    assert c.day_ok(datetime(2026, 10, 19).date())         # a Monday: weekday matches
    assert not c.day_ok(datetime(2026, 10, 20).date())
    assert S.Cron("0 0 * * 7").weekdays == [0]               # 7 = Sunday
    for bad in ("* * * *", "60 * * * *", "* 24 * * *", "a * * * *", "*/0 * * * *", "5-1 * * * *"):
        with pytest.raises(S.ScheduleError):
            S.Cron(bad)


def test_spring_forward_gap_runs_once_after_the_gap():
    # New York 2026-03-08: 02:00 -> 03:00. A daily 02:30 job runs at the first valid minute (03:00 EDT) once.
    after = ts(NY, 2026, 3, 7, 12, 0)
    runs = S.next_runs("cron", "30 2 * * *", NY, after, 3)
    got = [local(t, NY).strftime("%m-%d %H:%M %Z") for t in runs]
    assert got == ["03-08 03:00 EDT", "03-09 02:30 EDT", "03-10 02:30 EDT"]
    # every 15 minutes through the gap: 02:00..02:45 collapse into ONE run at 03:00, then 03:15
    t0 = ts(NY, 2026, 3, 8, 1, 50)
    got = [local(t, NY).strftime("%H:%M") for t in S.next_runs("cron", "*/15 * * * *", NY, t0, 4)]
    assert got == ["03:00", "03:15", "03:30", "03:45"]


def test_fall_back_repeated_hour_runs_once():
    # New York 2026-11-01: 02:00 EDT -> 01:00 EST; 01:30 happens twice -> the job runs ONCE (first occurrence)
    after = ts(NY, 2026, 10, 31, 12, 0)
    runs = S.next_runs("cron", "30 1 * * *", NY, after, 2)
    assert [local(t, NY).strftime("%m-%d %H:%M %Z") for t in runs] == ["11-01 01:30 EDT", "11-02 01:30 EST"]
    hourly = S.next_runs("cron", "0 * * * *", NY, ts(NY, 2026, 11, 1, 0, 30), 3)
    assert [round((b - a) / 3600, 2) for a, b in zip(hourly, hourly[1:])] == [2.0, 1.0]   # 01:00 not twice


def test_jerusalem_local_time_kept_across_dst_end():
    # Israel 2026: DST ends Sunday 25 Oct (02:00 -> 01:00). 08:00 stays 08:00 local; the UTC hour changes.
    runs = S.next_runs("cron", "0 8 * * *", JLM, ts(JLM, 2026, 10, 23, 12, 0), 3)
    assert [local(t, JLM).strftime("%d %H:%M") for t in runs] == ["24 08:00", "25 08:00", "26 08:00"]
    from datetime import timezone
    utc = timezone.utc
    assert [local(t, utc).hour for t in runs] == [5, 6, 6]           # IDT (UTC+3) -> IST (UTC+2)
    assert [round((b - a) / 3600) for a, b in zip(runs, runs[1:])] == [25, 24]


def test_once_and_bad_zone():
    assert S.next_after("once", "2026-10-20T09:00", JLM, ts(JLM, 2026, 10, 19, 10)) == ts(JLM, 2026, 10, 20, 9)
    assert S.next_after("once", "2026-10-20T09:00", JLM, ts(JLM, 2026, 10, 21, 10)) is None
    with pytest.raises(S.ScheduleError):
        S.zone("Mars/Olympus")


# ---------------------------------------------------------------------- human-readable schedules
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=JLM)       # a Monday


@pytest.mark.parametrize("text,kind,spec", [
    ("כל בוקר ב-8", "cron", "0 8 * * *"),
    ("כל ערב ב-9", "cron", "0 21 * * *"),
    ("כל יום בשעה 20:30", "cron", "30 20 * * *"),
    ("כל יום שני ורביעי בשמונה וחצי בבוקר", "cron", "30 8 * * 1,3"),
    ("בימי חול ב-7:15", "cron", "15 7 * * 0-4"),
    ("כל 15 דקות", "cron", "*/15 * * * *"),
    ("כל שעה", "cron", "0 * * * *"),
    ("כל חודש ב-1 לחודש ב-9", "cron", "0 9 1 * *"),
    ("every monday at 10", "cron", "0 10 * * 1"),
    ("every day at 7pm", "cron", "0 19 * * *"),
    ("0 6 * * 5", "cron", "0 6 * * 5"),
    ("מחר ב-9", "once", "2026-10-20T09:00"),
    ("מחר בשמונה בערב", "once", "2026-10-20T20:00"),
    ("בעוד 20 דקות", "once", "2026-10-19T10:20"),
    ("ב-25.12 ב-10", "once", "2026-12-25T10:00"),
    ("ביום שלישי ב-11", "once", "2026-10-20T11:00"),
    ("ב-9", "once", "2026-10-20T09:00"),                # 09:00 already passed today -> tomorrow
])
def test_parse_when(text, kind, spec):
    s = S.parse_when(text, NOW)
    assert (s.kind, s.spec) == (kind, spec)
    assert s.human


@pytest.mark.parametrize("text,code", [("", "SCHEDULE_EMPTY"), ("כל יום", "SCHEDULE_NEEDS_TIME"),
                                       ("כל 2 דקות", "SCHEDULE_TOO_OFTEN"), ("היום ב-8", "SCHEDULE_PAST"),
                                       ("כל 7 שעות", "BAD_INTERVAL")])
def test_parse_when_refuses(text, code):
    with pytest.raises(S.ScheduleError) as e:
        S.parse_when(text, NOW)
    assert e.value.code == code


def test_describe_hebrew():
    assert S.describe("cron", "0 8 * * *") == "כל יום ב-08:00"
    assert S.describe("cron", "15 7 * * 0-4") == "בימים א׳–ה׳ ב-07:15"
    assert "שני" in S.describe("cron", "0 10 * * 1")
    assert S.describe("cron", "*/15 * * * *") == "כל 15 דקות"


# ---------------------------------------------------------------------- lifecycle
async def test_preview_is_inactive_until_approved(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 10, 0))
    research = FakeResearch()
    s = make(db, clock, research)
    v = s.preview("כל בוקר ב-8", "חדשות AI", name="AI report")
    assert v["status"] == "draft" and v["human"] == "כל יום ב-08:00" and len(v["upcoming_local"]) == 3
    assert v["skills"] == ["web_search"] and v["max_requests"] == 2
    clock.t = ts(JLM, 2026, 10, 20, 8, 1)
    await s.tick()
    await settle(s)
    assert research.calls == 0 and db.rows("SELECT * FROM job_runs") == []      # a draft never runs
    s.approve(v["id"], by="dashboard")
    job = s.job(v["id"])
    assert job["status"] == "active" and job["next_run"] == ts(JLM, 2026, 10, 21, 8, 0)
    assert any(a["action"] == "job_approve" for a in db.audit_log())


async def test_due_job_runs_once_saves_report_and_is_idempotent(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    research, alerts = FakeResearch("שלושה דברים חדשים ב-AI"), Alerts()
    s = make(db, clock, research, alerts=alerts)
    jid = s.preview("כל בוקר ב-8", "חדשות AI", name="AI report")["id"]
    s.approve(jid, by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 20)
    await s.tick()
    await settle(s)
    runs = s.runs(jid)
    assert len(runs) == 1 and runs[0]["state"] == "done" and runs[0]["late"] == 0 and runs[0]["requests"] == 1
    rep = S.report_get(db, runs[0]["report_id"])
    assert rep["text"] == "שלושה דברים חדשים ב-AI" and rep["status"] == "ok"
    assert rep["sources"] == [{"type": "web_search", "query": "ai news today"}]
    assert s.job(jid)["next_run"] == ts(JLM, 2026, 10, 20, 8, 0)
    assert alerts.raised and alerts.raised[0]["kind"] == "report"
    # the same occurrence again (a second Core tick / process) is refused by its idempotency key
    s._start(s.job(jid), ts(JLM, 2026, 10, 19, 8, 0), "schedule", False, 0)
    await settle(s)
    assert len(s.runs(jid)) == 1 and research.calls == 1


async def test_reboot_catch_up_runs_once_late_then_resumes_schedule(db):
    """D14 + M3 exit: a missed daily report runs ONCE after power-on, is stored, labelled late, readable."""
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    s = make(db, clock, FakeResearch())
    jid = s.preview("כל בוקר ב-8", "חדשות AI", name="AI report")["id"]
    s.approve(jid, by="voice")
    await s.stop()                                   # power off before 08:00 on the 19th ...
    clock.t = ts(JLM, 2026, 10, 22, 9, 30)           # ... on again three days later at 09:30
    research = FakeResearch("הדוח של הבוקר")
    s2 = make(db, clock, research)
    await s2.tick()
    await settle(s2)
    await s2.tick()                                  # a second tick must not run anything else
    await settle(s2)
    runs = s2.runs(jid)
    assert len(runs) == 1 and research.calls == 1
    assert runs[0]["trigger"] == "catch_up" and runs[0]["late"] == 1 and runs[0]["missed"] == 3
    assert local(runs[0]["scheduled_for"], JLM).strftime("%d %H:%M") == "22 08:00"   # the LATEST missed one
    rep = S.report_get(db, runs[0]["report_id"])
    assert rep["late"] == 1 and "(באיחור)" in rep["title"]
    assert s2.job(jid)["next_run"] == ts(JLM, 2026, 10, 23, 8, 0)
    found = S.find_reports(db, period="this_morning", tz=JLM, now=clock.t)
    assert [r["id"] for r in found] == [rep["id"]]


async def test_catch_up_older_than_max_age_is_skipped(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    s = make(db, clock, FakeResearch())
    jid = s.preview("כל בוקר ב-8", "חדשות AI", max_age_s=6 * 3600)["id"]
    s.approve(jid, by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 20, 0)           # 12 h late
    research = FakeResearch()
    s2 = make(db, clock, research)
    await s2.tick()
    await settle(s2)
    runs = s2.runs(jid)
    assert research.calls == 0 and len(runs) == 1 and runs[0]["state"] == "skipped"
    assert runs[0]["error"].startswith("too_old") and db.rows("SELECT * FROM reports") == []
    assert s2.job(jid)["last_outcome"] == "skipped" and s2.job(jid)["next_run"] == ts(JLM, 2026, 10, 20, 8, 0)


async def test_one_off_reminder_needs_no_ai_and_finishes(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 10, 0))
    alerts = Alerts()
    s = make(db, clock, None, alerts=alerts)
    v = s.preview("בעוד 20 דקות", "להוציא את הכביסה", action="reminder")
    assert v["max_requests"] == 0 and v["skills"] == []
    s.approve(v["id"], by="voice")
    clock.t += 20 * 60 + 5
    await s.tick()
    await settle(s)
    assert s.job(v["id"])["status"] == "done"
    assert alerts.raised[0]["kind"] == "reminder" and alerts.raised[0]["priority"] == "high"
    assert alerts.raised[0]["text"] == "להוציא את הכביסה"


async def test_no_overlap_unless_allowed(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 10, 0))
    gate = asyncio.Event()
    s = make(db, clock, FakeResearch(gate))
    jid = s.preview("כל 5 דקות", "בדיקה ארוכה")["id"]
    s.approve(jid, by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 10, 5, 1)
    await s.tick()
    await asyncio.sleep(0)
    clock.t = ts(JLM, 2026, 10, 19, 10, 10, 1)
    await s.tick()                                   # the first run is still going
    states = [r["state"] for r in s.runs(jid)]
    assert sorted(states) == ["running", "skipped"]
    assert any("overlap" in (r["error"] or "") for r in s.runs(jid))
    gate.set()
    await settle(s)


async def test_retry_with_backoff_then_success(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    sleeps = []
    research = FakeResearch(LookupFailed("timeout"), "שני הצליח")
    s = make(db, clock, research, sleeps=sleeps)
    jid = s.preview("כל בוקר ב-8", "חדשות")["id"]
    s.approve(jid, by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 5)
    await s.tick()
    await settle(s)
    run = s.runs(jid)[0]
    assert run["state"] == "done" and run["attempts"] == 2 and run["requests"] == 2 and sleeps == [60.0]


async def test_permanent_error_is_not_retried_and_alerts(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    sleeps, alerts = [], Alerts()
    research = FakeResearch(LookupFailed("AI_BUDGET", "daily limit"))
    s = make(db, clock, research, sleeps=sleeps, alerts=alerts)
    jid = s.preview("כל בוקר ב-8", "חדשות")["id"]
    s.approve(jid, by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 5)
    await s.tick()
    await settle(s)
    run = s.runs(jid)[0]
    assert run["state"] == "failed" and run["attempts"] == 1 and sleeps == [] and research.calls == 1
    assert S.report_get(db, run["report_id"])["status"] == "failed"
    assert alerts.raised[-1]["kind"] == "job_failed"


async def test_job_cost_budget_caps_requests(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    research = FakeResearch(cost=2)                 # each attempt would need two AI requests
    s = make(db, clock, research)
    jid = s.preview("כל בוקר ב-8", "חדשות", max_requests=1)["id"]
    s.approve(jid, by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 5)
    await s.tick()
    await settle(s)
    run = s.runs(jid)[0]
    assert run["state"] == "failed" and run["error"].startswith("JOB_BUDGET") and run["attempts"] == 1


async def test_crash_mid_research_resumes_once_after_restart(db):
    """Fault injection: the Core dies while a research run is in flight (no orderly stop)."""
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    gate = asyncio.Event()
    s = make(db, clock, FakeResearch(gate), pid=DEAD_PID)
    jid = s.preview("כל בוקר ב-8", "חדשות")["id"]
    s.approve(jid, by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 5)
    await s.tick()
    await asyncio.sleep(0)
    assert s.runs(jid)[0]["state"] == "running"
    for t in [t for st in s._running.values() for t in st]:   # "power cut": the task vanishes, the row stays
        t.cancel()
    await asyncio.gather(*[t for st in s._running.values() for t in st], return_exceptions=True)
    db.x("UPDATE job_runs SET pid=?", (DEAD_PID,))             # crashed, not stopped: its pid is still recorded
    clock.t = ts(JLM, 2026, 10, 19, 8, 10)
    research = FakeResearch("אחרי ההפעלה מחדש")
    s2 = make(db, clock, research)
    await s2.tick()
    await settle(s2)
    runs = s2.runs(jid)
    assert len(runs) == 1 and runs[0]["state"] == "done" and runs[0]["trigger"] == "resume" and runs[0]["late"] == 1
    assert len(db.rows("SELECT * FROM reports")) == 1 and research.calls == 1


class WriteArgs(BaseModel):
    text: str = ""


class SideEffects(Skill):
    name = "sidefx"

    def __init__(self) -> None:
        self.done = []
        self.gate: asyncio.Event | None = None

    def tools(self):
        async def act(a):
            if self.gate is not None:
                await self.gate.wait()
            self.done.append(a.text)
            return ToolResult(True, f"did {a.text}")

        async def wipe(a):
            self.done.append("WIPED")
            return ToolResult(True, "wiped")

        async def impact(a):
            return None, "wipe everything?"
        return [Tool("side_act", "act", act, WriteArgs, permission="action"),
                Tool("side_wipe", "wipe", wipe, WriteArgs, permission="destructive", impact=impact)]


def registry_with(skill) -> SkillRegistry:
    reg = SkillRegistry(allowed_actions={"side_act"})
    reg.register(skill)
    return reg


async def test_tool_job_crash_is_never_repeated(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    fx = SideEffects()
    fx.gate = asyncio.Event()
    reg = registry_with(fx)
    s = make(db, clock, registry=reg, pid=DEAD_PID)
    v = s.preview("כל בוקר ב-8", "", action="tool", tool="side_act", tool_args={"text": "x"})
    assert v["skills"] == ["sidefx"] and v["retries"] == 0           # side-effect tools are never retried
    s.approve(v["id"], by="dashboard")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 5)
    await s.tick()
    await asyncio.sleep(0)
    for t in [t for st in s._running.values() for t in st]:
        t.cancel()
    await asyncio.gather(*[t for st in s._running.values() for t in st], return_exceptions=True)
    db.x("UPDATE job_runs SET pid=?", (DEAD_PID,))
    alerts = Alerts()
    fx.gate = None
    s2 = make(db, clock, registry=reg, alerts=alerts)
    await s2.tick()
    await settle(s2)
    assert s2.runs(v["id"])[0]["state"] == "interrupted" and fx.done == []
    assert alerts.raised[0]["kind"] == "job_interrupted"


async def test_tool_job_respects_permitted_skills_and_central_policy(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    fx = SideEffects()
    reg = registry_with(fx)
    s = make(db, clock, registry=reg)
    ok = s.preview("כל בוקר ב-8", "", action="tool", tool="side_act", tool_args={"text": "a"})
    other = s.preview("כל בוקר ב-8", "", action="tool", tool="side_act", tool_args={"text": "b"}, skills=["music"])
    wipe = s.preview("כל בוקר ב-8", "", action="tool", tool="side_wipe")
    for v in (ok, other, wipe):
        s.approve(v["id"], by="dashboard")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 5)
    await s.tick()
    await settle(s)
    assert fx.done == ["a"]                                              # only the permitted, allowed one ran
    assert s.runs(other["id"])[0]["error"].startswith("JOB_SKILL_NOT_PERMITTED")
    assert s.runs(wipe["id"])[0]["error"].startswith("TOOL_DECLINED")   # destructive: no approval at 08:00
    reg.disabled.add("sidefx")
    clock.t = ts(JLM, 2026, 10, 20, 8, 0, 5)
    await s.tick()
    await settle(s)
    assert fx.done == ["a"] and s.runs(ok["id"])[0]["error"].startswith("SKILL_DISABLED")


async def test_disabled_scheduler_feature_runs_nothing(db):
    class Off:
        def active(self, fid):
            return fid != "scheduler"
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    research = FakeResearch()
    s = make(db, clock, research, features=Off())
    s.approve(s.preview("כל בוקר ב-8", "חדשות")["id"], by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 5)
    await s.tick()
    await settle(s)
    assert research.calls == 0


async def test_idle_ticks_never_call_the_model(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    research = FakeResearch()
    s = make(db, clock, research)
    s.approve(s.preview("כל בוקר ב-8", "חדשות")["id"], by="voice")
    delays = []
    for i in range(50):                               # 50 ticks while nothing is due
        clock.t += 30
        delays.append(await s.tick())
    assert research.calls == 0 and max(delays) <= S.POLL_S


async def test_run_loop_real_time_and_run_now(db):
    research = FakeResearch("now")
    s = S.Scheduler(db, research=research)
    jid = s.preview("כל בוקר ב-8", "חדשות")["id"]
    s.approve(jid, by="voice")
    loop = asyncio.create_task(s.run())
    await asyncio.sleep(0.05)
    s.run_now(jid, by="dashboard")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if s.runs(jid) and s.runs(jid)[0]["state"] == "done":
            break
    await s.stop()
    loop.cancel()
    await asyncio.gather(loop, return_exceptions=True)
    assert s.runs(jid)[0]["trigger"] == "manual" and research.calls == 1


# ---------------------------------------------------------------------- voice tools
async def test_voice_preview_approve_and_report_readback(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    research = FakeResearch("ה-AI של הבוקר: שלוש ידיעות")
    s = make(db, clock, research)
    reg = SkillRegistry(allowed_actions=S.ALLOWED_ACTIONS)
    for sk in S.make_skills(s, reg):
        reg.register(sk)
    r = await reg.call("schedule_preview", {"when": "כל בוקר ב-8", "task": "חדשות AI", "name": "AI"})
    assert r.ok and "PREVIEW" in r.message and r.data["status"] == "draft"
    assert (await reg.call("jobs_list", {})).data["jobs"] == []          # drafts are not listed as jobs
    r2 = await reg.call("schedule_approve", {"job_id": r.data["id"]})
    assert r2.ok and s.job(r.data["id"])["approved_by"] == "voice"
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 5)
    await s.tick()
    await settle(s)
    clock.t = ts(JLM, 2026, 10, 19, 11, 0)
    calls = research.calls
    rd = await reg.call("report_read", {"period": "this_morning", "query": "AI"})
    assert rd.ok and rd.data["text"] == "ה-AI של הבוקר: שלוש ידיעות" and "Do NOT" in rd.message
    assert research.calls == calls                                        # read back, never regenerated
    assert (await reg.call("report_read", {"period": "yesterday"})).code == "REPORT_NOT_FOUND"
    # report text is third-party (web) content: no side effect in the same turn
    assert (await reg.call("job_delete", {"job_id": r.data["id"]})).code == "TOOL_UNTRUSTED_TURN"
    reg._untrusted_at = None                                              # the user speaks again
    assert (await reg.call("job_delete", {"job_id": r.data["id"]})).code == "TOOL_DECLINED"   # asks first
    assert s.job(r.data["id"]) is not None


def test_voice_cannot_create_tool_jobs():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        S.PreviewArgs(when="כל בוקר ב-8", task="x" * 3, kind="tool")


# ---------------------------------------------------------------------- review #6: resume = approve (confirm)
async def test_voice_resume_of_a_tool_job_needs_the_onscreen_confirm_and_never_activates_drafts(db):
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    fx = SideEffects()
    reg = SkillRegistry(allowed_actions=set(S.ALLOWED_ACTIONS) | {"side_act"})
    reg.register(fx)
    s = make(db, clock, FakeResearch(), registry=reg)
    for sk in S.make_skills(s, reg):
        reg.register(sk)
    asked, answer = [], [False]

    async def confirm(prompt):
        asked.append(prompt)
        return answer[0]
    reg.confirm = confirm
    tool_job = s.preview("כל בוקר ב-8", "", action="tool", tool="side_act", tool_args={"text": "a"})
    s.approve(tool_job["id"], by="dashboard")
    s.pause(tool_job["id"], by="dashboard")
    r = await reg.call("job_control", {"job_id": tool_job["id"], "op": "resume"})
    assert not r.ok and r.code == "JOB_NEEDS_APPROVAL" and len(asked) == 1
    assert s.job(tool_job["id"])["status"] == "paused"                  # declined on screen: stays paused
    answer[0] = True
    r = await reg.call("job_control", {"job_id": tool_job["id"], "op": "resume"})
    assert r.ok and s.job(tool_job["id"])["status"] == "active" and len(asked) == 2
    # a draft is never activated by "resume" (that is schedule_approve, after the user's clear yes)
    draft = s.preview("כל בוקר ב-9", "חדשות")
    r = await reg.call("job_control", {"job_id": draft["id"], "op": "resume"})
    assert not r.ok and r.code == "JOB_NOT_APPROVED" and s.job(draft["id"])["status"] == "draft"
    # a paused research job (no side effect) resumes by voice without a screen prompt
    s.approve(draft["id"], by="voice")
    s.pause(draft["id"], by="voice")
    r = await reg.call("job_control", {"job_id": draft["id"], "op": "resume"})
    assert r.ok and s.job(draft["id"])["status"] == "active" and len(asked) == 2


# ---------------------------------------------------------------------- review #4: plan research charged once
async def test_plan_research_job_is_charged_once_against_the_daily_limit(db):
    from types import SimpleNamespace
    from jarves.tasks import PlanResearchRunner

    class Shared:                                      # the Core's daily Budget: counts what is reserved
        def __init__(self):
            self.n = 0

        def reserve(self, kind="ai_requests", n=1):
            self.n += n
            return S._NoReservation()

    class Brain:                                       # ChatGPTBrain: _stream reserves the shared budget itself
        def __init__(self, shared):
            self.shared = shared

        async def lookup(self, question, instructions, on_event=None):
            self.shared.reserve("ai_requests")
            return "שלושה דברים חדשים"

    shared = Shared()
    research = S.make_research(SimpleNamespace(runner=PlanResearchRunner(Brain(shared), "inst", retries=0)))
    jb = S.JobBudget(shared, 2)
    assert (await research("חדשות AI", jb)).text
    assert shared.n == 1 and jb.used == 1              # one request: one daily unit, one job unit
    await research("חדשות AI", jb)
    with pytest.raises(S.BudgetExceeded):              # the job cap still applies on the plan
        await research("חדשות AI", jb)
    assert shared.n == 2
    # the same through a real scheduled run
    clock = Clock(ts(JLM, 2026, 10, 19, 7, 0))
    shared2 = Shared()
    s = S.Scheduler(db, research=S.make_research(SimpleNamespace(
        runner=PlanResearchRunner(Brain(shared2), "inst", retries=0))), clock=clock, budget=shared2)
    jid = s.preview("כל בוקר ב-8", "חדשות AI")["id"]
    s.approve(jid, by="voice")
    clock.t = ts(JLM, 2026, 10, 19, 8, 0, 20)
    await s.tick()
    await settle(s)
    assert s.runs(jid)[0]["state"] == "done" and shared2.n == 1


# ---------------------------------------------------------------------- review #3: catch-up must not stall the tick
def _walk_latest_due(kind, spec, tz, first_due, now):
    """The old reference: walk every missed occurrence (slow, but obviously right)."""
    occ, missed = first_due, 0
    while True:
        nxt = S.next_after(kind, spec, tz, occ)
        if nxt is None or nxt > now:
            return occ, missed
        occ, missed = nxt, missed + 1


@pytest.mark.parametrize("spec,tz,start,days", [
    ("*/5 * * * *", JLM, (2026, 3, 25, 0, 5), 4),          # Jerusalem spring forward (27 Mar 2026)
    ("*/5 * * * *", JLM, (2026, 10, 23, 0, 5), 4),         # Jerusalem fall back (25 Oct 2026)
    ("*/15 * * * *", NY, (2026, 3, 7, 0, 0), 3),           # New York gap: 02:00..02:45 collapse into 03:00
    ("30 2 * * *", NY, (2026, 3, 6, 2, 30), 5),            # the 02:30 that does not exist on 8 Mar
    ("30 1 * * *", NY, (2026, 10, 30, 1, 30), 5),          # the 01:30 that happens twice on 1 Nov
    ("0 * * * *", NY, (2026, 10, 31, 0, 0), 3),
    ("0 8 * * 1-5", JLM, (2026, 1, 5, 8, 0), 40),
    ("15 9 1 * *", JLM, (2026, 1, 1, 9, 15), 200),
])
def test_latest_due_matches_the_walk_across_dst(spec, tz, start, days):
    first = S.next_after("cron", spec, tz, ts(tz, *start) - 1)
    for k in range(1, days * 24, 9):                      # many "now" points, on and off occurrences
        for now in (first + k * 3600 + 17, S.next_after("cron", spec, tz, first + k * 3600)):
            assert S.latest_due("cron", spec, tz, first, now) == _walk_latest_due("cron", spec, tz, first, now)
    assert S.latest_due("cron", spec, tz, first, first) == (first, 0)
    assert S.latest_due("cron", spec, tz, first, first - 60) == (first, 0)
    once = "2026-10-20T09:00"
    assert S.latest_due("once", once, JLM, ts(JLM, 2026, 10, 20, 9), ts(JLM, 2026, 12, 1)) == \
        (ts(JLM, 2026, 10, 20, 9), 0)


def test_latest_due_is_fast_after_months_off():
    """A */5 job after 6 months off walked ~52k occurrences synchronously on the tick (18 s: voice/face stalled)."""
    import time as _t
    first = ts(JLM, 2026, 1, 1, 0, 5)
    now = ts(JLM, 2026, 7, 1, 12, 3)
    t0 = _t.perf_counter()
    occ, missed = S.latest_due("cron", "*/5 * * * *", JLM, first, now)
    assert _t.perf_counter() - t0 < 0.3
    assert local(occ, JLM).strftime("%d.%m %H:%M") == "01.07 12:00"
    # every 5-minute slot of real time counted (the spring-forward hour on 27 Mar has no wall-clock slots)
    assert missed == round((occ - first) / 300)


async def test_week_off_catch_up_runs_once_late_without_stalling_the_tick(db):
    import time as _t
    clock = Clock(ts(JLM, 2026, 10, 1, 7, 0))
    s = make(db, clock, FakeResearch())
    jid = s.preview("כל 15 דקות", "מחירים", max_age_s=6 * 3600)["id"]
    s.approve(jid, by="voice")
    await s.stop()
    clock.t = ts(JLM, 2026, 10, 8, 9, 7)               # a week off
    research = FakeResearch()
    s2 = make(db, clock, research)
    t0 = _t.perf_counter()
    await s2.tick()
    assert _t.perf_counter() - t0 < 0.5
    await settle(s2)
    await s2.tick()
    await settle(s2)
    runs = s2.runs(jid)
    assert len(runs) == 1 and research.calls == 1 and runs[0]["late"] == 1 and runs[0]["trigger"] == "catch_up"
    assert local(runs[0]["scheduled_for"], JLM).strftime("%d %H:%M") == "08 09:00"
    assert runs[0]["missed"] == _walk_latest_due("cron", "*/15 * * * *", JLM, ts(JLM, 2026, 10, 1, 7, 15),
                                                 clock.t)[1]
    assert s2.job(jid)["next_run"] == ts(JLM, 2026, 10, 8, 9, 15)
