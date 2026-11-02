"""Self-test and speed test: history, schedule policy, voice tools, dashboard page, CLI, feature (owner request 21 Oct 2026).

The checks themselves are `selftest.run(deep=False)` and `speedtest.run()` (plain Python, dataclasses; the
self-management skill calls them directly). This module is everything around them:

- History     ONE small JSON file (selfspeed.json in the state folder), written once at the END of a run (atomic
              replace): the full latest result per kind plus a compact row per run (60 kept). Nothing is written while
              idle, so the idle-writes gate stays green. `trend()` / `spark()` feed the dashboard sparkline.
- Policy      scheduled runs only: nothing in quiet hours (alerts.quiet_start / quiet_end, default 22:00-07:00, local
              zone) and nothing heavy (speed test, deep self-test) while a voice session is open. A skipped run is
              logged (SELFSPEED_SKIPPED) and NOT queued: the next slot simply comes.
- Jobs        selftest daily 08:15, speedtest weekly (Sunday 13:00) as scheduler jobs that call the voice tools.
- Tools       run_selftest / run_speedtest (read-only, no approval): one spoken sentence + "details on the dashboard".
- Dashboard   GET /api/selfspeed, POST /api/selfspeed/run (Health & speed page).
- CLI         jarvis selftest [--deep] [--audio] [--json]; jarvis speedtest [--class] [--bench] [--gate] [--json].
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from . import features as feat
from . import logs

FEATURE = "self_tests"
HISTORY_FILE = "selfspeed.json"
KEEP = 60
SELFTEST_SPEC = "15 8 * * *"          # daily, after quiet hours end (07:00) and before the work day
SPEEDTEST_SPEC = "0 13 * * 0"         # weekly, Sunday (the self-check is Sunday 10:00)
SPEED_TIMEOUT_S = 240.0


# ---------------------------------------------------------------------- history
class History:
    """selfspeed.json: {"latest": {kind: full result}, "selftest": [compact rows], "speedtest": [compact rows]}."""

    def __init__(self, home: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(home) / HISTORY_FILE
        self.clock = clock

    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d.setdefault("latest", {})
        d.setdefault("selftest", [])
        d.setdefault("speedtest", [])
        return d

    def rows(self, kind: str, n: int = KEEP) -> list[dict]:
        return self.load().get(kind, [])[-n:]

    def latest(self, kind: str) -> dict | None:
        return self.load()["latest"].get(kind)

    @staticmethod
    def compact(kind: str, full: dict) -> dict:
        base = {"ts": full["ts"], "status": full["status"], "trigger": full.get("trigger", "")}
        if kind == "selftest":
            base.update(deep=full.get("deep", False), counts=full.get("counts", {}), ms=full.get("ms"),
                        problems=[[c["id"], c["status"]] for c in full.get("checks", []) if c["status"] in ("warn", "fail")])
        else:
            base.update(values=full.get("values", {}), cls=full.get("class", ""), ms=full.get("ms"),
                        statuses={m["name"]: m["status"] for m in full.get("metrics", []) if m["status"] in ("warn", "fail")})
        return base

    def append(self, kind: str, full: dict) -> None:
        """One atomic write per finished run (never from a timer or a sampler)."""
        d = self.load()
        d["latest"][kind] = full
        d[kind] = (d[kind] + [self.compact(kind, full)])[-KEEP:]
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            logs.warn("selfspeed", "SELFSPEED_HISTORY_FAILED", "result history not saved")
        try:                                           # the issues registry learns from every recorded run (content-free)
            from . import issue_registry
            reg = issue_registry.Registry(self.path.parent, clock=self.clock)
            (issue_registry.ingest_selftest if kind == "selftest" else issue_registry.ingest_speedtest)(reg, full)
        except Exception:
            logs.warn("selfspeed", "SELFSPEED_ISSUES_FAILED", "issues registry not updated from the run")
        if kind == "speedtest":
            from . import perf_advisor
            perf_advisor.on_speedtest(self.path.parent)

    def spark(self, metric: str, n: int = 20) -> list[float]:
        """Values of one speed metric over the last runs, oldest first (the dashboard sparkline)."""
        return [r["values"][metric] for r in self.rows("speedtest", n) if metric in r.get("values", {})]

    def view(self) -> dict:
        d = self.load()
        names = sorted({n for r in d["speedtest"] for n in r.get("values", {})})
        from . import perf_advisor
        return {"latest": d["latest"], "selftest": d["selftest"][-20:], "speedtest": d["speedtest"][-20:],
                "recommendations": perf_advisor.recommendations(d["speedtest"]),
                "auto_github": perf_advisor.options(self.path.parent)["auto_github"],
                "performance_issue_url": perf_advisor.options(self.path.parent)["tracker_url"],
                "spark": {n: self.spark(n) for n in names},
                "selftest_spark": [{"ts": r["ts"], "status": r["status"]} for r in d["selftest"][-30:]]}


# ---------------------------------------------------------------------- when a scheduled run may happen
class Policy:
    """Scheduled runs only (a request by voice / text / CLI / the dashboard button always runs)."""

    def __init__(self, db, *, clock: Callable[[], float] = time.time, voice_active: Callable[[], bool] = lambda: False) -> None:
        self.db, self.clock, self.voice_active = db, clock, voice_active

    def quiet_now(self) -> bool:
        from .alerts import in_quiet
        from .scheduler import zone
        st = self.db.settings("alerts.")
        return in_quiet(datetime.fromtimestamp(self.clock(), zone(self.db.setting("scheduler.timezone"))),
                        st["alerts.quiet_start"], st["alerts.quiet_end"])

    def skip_reason(self, kind: str, trigger: str, *, deep: bool = False) -> str | None:
        if trigger != "schedule":
            return None
        if self.quiet_now():
            return "quiet_hours"
        if (kind == "speedtest" or deep) and self.voice_active():
            return "voice_session"
        return None


def log_skip(kind: str, reason: str) -> None:
    logs.info("selfspeed", "SELFSPEED_SKIPPED", "scheduled run skipped, not queued", kind=kind, reason=reason)


# ---------------------------------------------------------------------- running (shared by tool, dashboard, CLI)
def run_speedtest_isolated(*, trigger: str, cls: str | None = None, timeout: float = SPEED_TIMEOUT_S) -> dict:
    """The speed test in its own process: it temporarily sets JARVES_HOME for the in-process timings and starts a
    throw-away dashboard, which must never happen inside the live Core. The child records the history itself."""
    cmd = [sys.executable, "-m", "jarves.cli", "speedtest", "--json", "--trigger", trigger] + (["--class", cls] if cls else [])
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}          # JARVES_HOME / JARVES_PROFILE are inherited: same state
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", "")])
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    out = p.stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except (IndexError, ValueError) as e:
        raise RuntimeError(f"speed test produced no result (exit {p.returncode})") from e


class SelfSpeed:
    """What the tools, the dashboard and the CLI call. Every collaborator is injectable (tests)."""

    def __init__(self, home: Path, cfg, *, db=None, features=None, registry=None, clock: Callable[[], float] = time.time,
                 voice_active: Callable[[], bool] = lambda: False,
                 selftest_fn=None, speedtest_fn=None) -> None:
        self.home, self.cfg, self.features, self.registry, self.clock = Path(home), cfg, features, registry, clock
        self.history = History(self.home, clock=clock)
        self.policy = Policy(db, clock=clock, voice_active=voice_active) if db is not None else None
        self._selftest, self._speedtest = selftest_fn, speedtest_fn
        self.running: dict[str, float] = {}

    def skip_reason(self, kind: str, trigger: str, deep: bool = False) -> str | None:
        return self.policy.skip_reason(kind, trigger, deep=deep) if self.policy else None

    def selftest(self, deep: bool = False, trigger: str = "manual", audio: bool = False) -> dict:
        from . import selftest
        fn = self._selftest or selftest.run
        self.running["selftest"] = self.clock()
        try:
            return fn(deep, audio=audio, home=self.home, cfg=self.cfg, features=self.features, registry=self.registry,
                      trigger=trigger, clock=self.clock).as_dict()
        finally:
            self.running.pop("selftest", None)

    def speedtest(self, trigger: str = "manual", cls: str | None = None) -> dict:
        self.running["speedtest"] = self.clock()
        try:
            if self._speedtest is not None:
                return self._speedtest(trigger=trigger, cls=cls)
            return run_speedtest_isolated(trigger=trigger, cls=cls)
        finally:
            self.running.pop("speedtest", None)


# ---------------------------------------------------------------------- voice tools
class SelfTestArgs(BaseModel):
    trigger: str = "voice"
    deep: bool = False


class SpeedTestArgs(BaseModel):
    trigger: str = "voice"


def make_skill(svc: SelfSpeed, lang: Callable[[], str]):
    from .skills import Skill, Tool, ToolResult, NoArgs

    def spoken(sentence: str) -> str:
        return sentence + (" פרטים בלוח הבקרה." if lang() == "he" else " Details on the dashboard.")

    class SelfSpeedSkill(Skill):
        name = "selfspeed"
        description = "self-test and speed test"

        def tools(self) -> list:
            async def selftest_(a: SelfTestArgs) -> ToolResult:
                why = svc.skip_reason("selftest", a.trigger, a.deep)
                if why:
                    log_skip("selftest", why)
                    return ToolResult(True, f"skipped ({why})", {"skipped": why}, code="SELFTEST_SKIPPED")
                d = await asyncio.to_thread(svc.selftest, a.deep, a.trigger)
                from .selftest import Check, SelfTestResult
                r = SelfTestResult(d["ts"], d["deep"], d["audio"], d["ms"], [Check(**c) for c in d["checks"]], d["trigger"])
                return ToolResult(d["status"] != "fail", spoken(r.summary(lang())),
                                  {"status": d["status"], "counts": d["counts"], "dashboard": "#/speed"},
                                  code="SELFTEST_" + d["status"].upper())

            async def speedtest_(a: SpeedTestArgs) -> ToolResult:
                why = svc.skip_reason("speedtest", a.trigger)
                if why:
                    log_skip("speedtest", why)
                    return ToolResult(True, f"skipped ({why})", {"skipped": why}, code="SPEEDTEST_SKIPPED")
                try:
                    d = await asyncio.to_thread(svc.speedtest, a.trigger)
                except Exception as e:
                    logs.warn("selfspeed", "SPEEDTEST_FAILED", "speed test did not run", error_type=type(e).__name__)
                    return ToolResult(False, "the speed test could not run", code="SPEEDTEST_FAILED")
                from .speedtest import Metric, SpeedResult
                r = SpeedResult(d["ts"], d["class"], d["ms"], [Metric(**m) for m in d["metrics"]], d["trigger"])
                return ToolResult(d["status"] != "fail", spoken(r.summary(lang())),
                                  {"status": d["status"], "counts": d["counts"], "dashboard": "#/speed"},
                                  code="SPEEDTEST_" + d["status"].upper())

            async def advice_(_: NoArgs) -> ToolResult:
                from . import perf_advisor
                findings = perf_advisor.recommendations(svc.history.rows("speedtest", 30))
                if not findings:
                    sentence = ("אין כרגע המלצות שיפור מבוססות מדידה." if lang() == "he"
                                else "No measured performance recommendations yet.")
                else:
                    top = findings[0]
                    sentence = (f"מדד {top['metric']}: {top['advice_he']}" if lang() == "he"
                                else f"Metric {top['metric']}: {top['advice']}")
                return ToolResult(True, spoken(sentence),
                                  {"recommendations": findings, "dashboard": "#/speed"}, code="PERF_ADVICE_READY")

            return [Tool("performance_advice",
                         "'be faster', 'שפר ביצועים'.",
                         advice_, NoArgs, permission="read"),
                    Tool("run_selftest", "'is anything broken', 'בדיקת תקינות'.", selftest_, SelfTestArgs, permission="read",
                         timeout_s=300),
                    Tool("run_speedtest", "'how fast are you', 'בדיקת מהירות'.", speedtest_, SpeedTestArgs,
                         permission="read", timeout_s=SPEED_TIMEOUT_S + 30)]

    return SelfSpeedSkill()


INSTRUCTION = ("Health: run_selftest; speed: run_speedtest; advice: performance_advice. "
               "Never invent results.")


# ---------------------------------------------------------------------- scheduler jobs
def ensure_jobs(scheduler, dbs) -> dict:
    """Create the daily self-test and weekly speed-test jobs once (a job the owner deleted is not recreated)."""
    out = {}
    for key, spec, name, tool in (
            ("selftest.job_id", SELFTEST_SPEC, "בדיקה עצמית יומית / daily self-test", "run_selftest"),
            ("speedtest.job_id", SPEEDTEST_SPEC, "בדיקת מהירות שבועית / weekly speed test", "run_speedtest")):
        jid = dbs.setting(key) or ""
        if jid:
            out[key] = jid if scheduler.job(jid) else None
            continue
        v = scheduler.preview(spec, name, name=name, action="tool", tool=tool, tool_args={"trigger": "schedule"},
                              skills=["selfspeed"], owner="system", by="system", delivery=["report"])
        scheduler.approve(v["id"], by="system (owner request 21 Oct 2026: self-test daily, speed test weekly)")
        dbs.set_setting(key, v["id"], actor="system")
        out[key] = v["id"]
        logs.info("selfspeed", "SELFSPEED_JOB", "self-test job created", job=v["id"], spec=spec)
    return out


def attach(core, ops) -> SelfSpeed:
    """Called by operations.start: the voice tools, the two scheduler jobs, a line in the agent instructions."""
    def lang() -> str:
        return getattr(core.cfg.voice, "language", "he")

    svc = SelfSpeed(core.home, core.cfg, db=ops.db, features=core.features, registry=core.registry,
                    voice_active=lambda: getattr(core, "live", None) is not None)
    core.registry.register(make_skill(svc, lang))
    agent = getattr(core, "agent", None)
    if agent is not None and hasattr(agent, "instructions"):
        base = agent.instructions

        def instructions() -> str:
            try:
                on = core.features.active(FEATURE)
            except Exception:
                on = False
            return f"{base()}\n\n{INSTRUCTION}" if on else base()
        agent.instructions = instructions
    try:
        ensure_jobs(ops.scheduler, ops.db)
    except Exception:
        logs.error("selfspeed", "SELFSPEED_JOB_FAILED", "self-test jobs not created", exc=True)
    return svc


# ---------------------------------------------------------------------- feature
def _targets() -> dict:
    from .capabilities import PI_ROADMAP
    return {"windows": {"status": "implemented_unverified",
                        "evidence": "UNIT test_selfspeed.py (planted faults, fake clock, fake Core, real dashboard HTTP)",
                        "blocker": "not yet run on the owner's PC for a week"},
            "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                               "blocker": "pure Python; /proc counters; never run on a Raspberry Pi (no device in the test setup)"}}


feat.register(FEATURE, "בדיקה עצמית ובדיקת מהירות", "Self-test & speed test", True, skills=["selfspeed"],
              about="checks his own installation (daily) and his speed (weekly), tells you what is slow or broken and "
                    "whether it got worse than before; free, read-only, never at night or during a conversation",
              targets=_targets(), permissions=["read"],
              settings="dashboard Health & speed page, jarvis selftest, jarvis speedtest, Jobs page (times)")


# ---------------------------------------------------------------------- dashboard
SPEED_RUNNER: Callable[..., dict] | None = None        # tests replace the isolated process


def _service(ctx) -> SelfSpeed:
    from .userdata import ctx_db
    return SelfSpeed(ctx.home, ctx.cfg, db=ctx_db(ctx), features=feat._ctx_features(ctx), clock=getattr(ctx, "clock", time.time),
                     speedtest_fn=SPEED_RUNNER)


def api_get(ctx, m, body, q=None):
    svc = _service(ctx)
    v = svc.history.view()
    v["enabled"] = feat._ctx_features(ctx).enabled(FEATURE)
    v["running"] = sorted(svc.running)
    v["schedule"] = {"selftest": SELFTEST_SPEC, "speedtest": SPEEDTEST_SPEC,
                     "quiet": [svc.policy.db.setting("alerts.quiet_start"), svc.policy.db.setting("alerts.quiet_end")]}
    v["summary"] = {k: _summary(k, d, "en") for k, d in v["latest"].items()}
    v["summary_he"] = {k: _summary(k, d, "he") for k, d in v["latest"].items()}
    return 200, v


def _summary(kind: str, d: dict, lang: str) -> str:
    if kind == "selftest":
        from .selftest import Check, SelfTestResult
        return SelfTestResult(d["ts"], d["deep"], d["audio"], d["ms"], [Check(**c) for c in d["checks"]]).summary(lang)
    from .speedtest import Metric, SpeedResult
    return SpeedResult(d["ts"], d["class"], d["ms"], [Metric(**m) for m in d["metrics"]]).summary(lang)


def api_run(ctx, m, body):
    b = body or {}
    kind = str(b.get("kind") or "")
    svc = _service(ctx)
    if kind not in ("selftest", "speedtest"):
        return 400, {"error": "kind must be selftest or speedtest"}
    if kind in svc.running:
        return 409, {"error": "already running", "error_he": "כבר רץ"}
    try:
        d = svc.selftest(bool(b.get("deep")), "dashboard") if kind == "selftest" else svc.speedtest("dashboard")
    except Exception as e:
        logs.warn("selfspeed", "SELFSPEED_RUN_FAILED", "dashboard run failed", kind=kind, error_type=type(e).__name__)
        return 500, {"error": "the run failed; see the System log", "error_he": "ההרצה נכשלה; ראו את יומן המערכת"}
    return 200, d


def api_autoreport(ctx, m, body):
    from . import perf_advisor
    if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
        return 400, {"error": "enabled must be a boolean"}
    state = perf_advisor.configure(ctx.home, body["enabled"])
    return 200, {"auto_github": state["auto_github"], "notice": "One shared performance issue receives material updates no more than once per day"}


def dashboard_routes(register) -> None:
    register("POST", r"/api/selfspeed/auto-github", api_autoreport)
    register("GET", r"/api/selfspeed", api_get)
    register("POST", r"/api/selfspeed/run", api_run)


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    st = sub.add_parser("selftest", help="self-test: config, databases, permissions, connections, guides, languages, "
                                         "log privacy, disk, updates (read-only, ~10 s); --deep adds a Core start/stop")
    st.add_argument("--deep", action="store_true", help="also: Core start/stop in a temp home, wake model load, speech and "
                                                        "audio device checks (nothing spoken or recorded)")
    st.add_argument("--audio", action="store_true", help="with --deep: one quiet tone and a 0.5 s microphone level")
    st.add_argument("--json", action="store_true")
    st.add_argument("--trigger", default="cli", help=argparse.SUPPRESS)
    sp = sub.add_parser("speedtest", help="speed test: startup, idle CPU/RAM/disk, dashboard, tool p50/p95, voice latency, "
                                          "API round trip (HEAD only), installer size; against budgets and the last runs")
    sp.add_argument("--class", dest="speed_class", choices=["owner", "ci"], default=None,
                    help="budget class (default: ci when the CI variable is set)")
    sp.add_argument("--bench", action="store_true", help="also start a real Core in a temp home (slow; source checkout)")
    sp.add_argument("--gate", action="store_true", help="exit 1 when a budget is exceeded (CI)")
    sp.add_argument("--no-net", action="store_true", help="skip the API round trip (CI: a runner's network is not a budget)")
    sp.add_argument("--no-store", action="store_true", help="do not record this run in the history")
    sp.add_argument("--idle-window", type=float, default=10.0, help="seconds to watch the running Core (default 10)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--trigger", default="cli", help=argparse.SUPPRESS)
    pa = sub.add_parser("performance", help="measured performance recommendations and optional owner-controlled GitHub issues")
    pa.add_argument("--auto-github", choices=["on", "off"], help="explicit owner opt-in or disable (no network call here)")
    pa.add_argument("--voice-stages", action="store_true",
                    help="voice reply latency by stage (median, p95, bottleneck, confidence) from the local log")
    pa.add_argument("--json", action="store_true")


_MARK = {"pass": "ok  ", "warn": "WARN", "fail": "FAIL", "skip": "skip", "n/a": "n/a "}


def cmd_selftest(a, cfg, home: Path, *, out=print) -> int:
    from . import selftest
    res = selftest.run(a.deep or a.audio, audio=a.audio, home=home, cfg=cfg, trigger=a.trigger)
    if a.json:
        out(json.dumps(res.as_dict(), ensure_ascii=False))
        return 1 if res.status == "fail" else 0
    out(f"self-test ({'deep' if res.deep else 'quick'}, {res.ms / 1000:.1f} s): {res.status.upper()}")
    for c in res.checks:
        out(f"  [{_MARK[c.status]}] {c.title}: {c.detail}")
        if c.status in ("warn", "fail") and c.fix:
            out(f"         fix: {c.fix}")
    out(res.summary("en"))
    return 1 if res.status == "fail" else 0


def cmd_speedtest(a, cfg, home: Path, *, out=print) -> int:
    from . import speedtest
    groups = tuple(g for g in speedtest.GROUPS if not (a.no_net and g == "net"))
    res = speedtest.run(home=home, cls=a.speed_class, bench=a.bench, groups=groups, trigger=a.trigger,
                        record=not a.no_store, idle_window_s=a.idle_window)
    if a.json:
        out(json.dumps(res.as_dict(), ensure_ascii=False))
    else:
        out(f"speed test (class {res.cls}, {res.ms / 1000:.1f} s): {res.status.upper()}")
        w = max((len(m.name) for m in res.metrics), default=10)
        for m in res.metrics:
            trend = "" if m.trend_pct is None else f"  vs last 5: {m.trend_pct:+.0f} %"
            lim = "" if m.max is None else f"  max {m.max:g}"
            val = "-" if m.value is None else f"{m.value:g}"
            out(f"  [{_MARK[m.status]}] {m.name:<{w}} {val:>10} {m.unit}{lim}{trend}  {m.detail}".rstrip())
        out(res.summary("en"))
    return 1 if (a.gate and res.status == "fail") else 0


def cmd_performance(a, cfg, home: Path, *, out=print) -> int:
    from . import perf_advisor
    if a.auto_github is not None:
        perf_advisor.configure(home, a.auto_github == "on")
    if getattr(a, "voice_stages", False):
        from . import voice_stages
        res = voice_stages.run(home)
        if a.json:
            out(json.dumps({"voice_stages": res}, ensure_ascii=False))
        else:
            for line in voice_stages.render(res):
                out(line)
        return 0
    state = perf_advisor.options(home)
    advice = perf_advisor.recommendations(History(home).rows("speedtest", 30))
    if a.json:
        out(json.dumps({"auto_github": state["auto_github"], "recommendations": advice}, ensure_ascii=False))
        return 0
    out("GitHub auto-issues: " + ("enabled" if state["auto_github"] else "disabled") +
        " (needs a local token; one performance tracker with changes at most daily)")
    if not advice:
        out("No repeated measured regression yet; scheduled speed tests build the baseline.")
    for finding in advice:
        out(f"[{finding['severity']}] {finding['metric']}: {finding['advice']}")
    return 0


COMMANDS = {"selftest": cmd_selftest, "speedtest": cmd_speedtest, "performance": cmd_performance}
