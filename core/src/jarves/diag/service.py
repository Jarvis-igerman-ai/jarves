"""Self-Diagnostics 2.0 surfaces: the Core service, voice tools, the Health page API, `jarvis health`, the feature.

Core service (HealthService, attached by operations.start next to the V3 self-check):
- the monitor (log sink + health listener + 60 s sampler), the config snapshot, a detection pass every 10 minutes
  (findings stored, score recorded hourly for the 30-day trend), new HIGH findings raise an alert (popup; at most
  one spoken health alert per day, quiet hours respected by the alert engine);
- two scheduler jobs created once: the nightly canary (03:30) and the daily AI-review check (11:30; the review
  itself runs only every health.ai_review_every_days days and only when something is new);
- voice tools: health_summary ("מה מצבך?" / "how are you doing?"), health_ai_review_summary ("מה מצא הבודק?"),
  health_remediate (closed list, ALWAYS through approvals), and the two job tools.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

from .. import features as feat
from .. import logs
from . import FEATURE, ai_review, anomaly, canary, db, preflight, regression, remediation, tickets
from .logindex import LogIndex
from .monitor import Monitor, config_snapshot, record_config

DETECT_EVERY_S = 600.0
CANARY_SPEC_DEFAULT = "30 3 * * *"
REVIEW_SPEC_DEFAULT = "30 11 * * *"
_SERVICE: "HealthService | None" = None


def current() -> "HealthService | None":
    return _SERVICE


def _hhmm_cron(hhmm: str, default: str) -> str:
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return f"{m} {h} * * *"
    except ValueError:
        pass
    return default


# ---------------------------------------------------------------------- the report (one view for every surface)
def score_of(found: list[dict]) -> int:
    from ..selfcheck import health_score
    return health_score([f for f in found if f.get("state") in ("open", "accepted")])


def report(store, *, home: Path, cfg, dbs, components: dict | None = None, now: float | None = None,
           days: int = 30) -> dict:
    """Everything the Health page, `jarvis health --json` and the spoken summary show."""
    now = time.time() if now is None else now
    found = anomaly.findings(store, now=now)
    sc_latest = None
    try:
        from ..selfcheck import run_view
        sc_latest = run_view(store, now=now)
    except Exception:
        sc_latest = None
    sc_open = [dict(f, source="selfcheck") for f in (sc_latest or {}).get("findings", [])
               if f["state"] in ("open", "accepted")]
    allf = [dict(f) for f in found] + sc_open
    from ..selfcheck import CATEGORY_WEIGHT, SEV
    allf.sort(key=lambda d: (d.get("state") not in ("open", "accepted"), -SEV.get(d["severity"], 0),
                             -CATEGORY_WEIGHT.get(d["category"], 3), -int(d.get("count") or 0)))
    for i, f in enumerate(allf, 1):
        f["rank"] = i
    score = score_of(allf)
    trend = [{"ts": t, "score": v} for t, v in _daily(store, "health_score", now - days * 86400, now)]
    tiles = []
    for name, c in sorted((components or {}).items()):
        st = c.get("status") if isinstance(c, dict) else getattr(c, "status", "unknown")
        detail = c.get("detail") if isinstance(c, dict) else getattr(c, "detail", "")
        tiles.append({"name": name, "status": st, "detail": logs.redact(str(detail or ""))[:160]})
    review = ai_review.run_view(store, now=now)
    s = ai_review._settings(dbs)
    spent = ai_review.month_spent(store, now)
    return {
        "score": score, "trend": trend, "tiles": tiles, "findings": allf,
        "open": sum(f.get("state") in ("open", "accepted") for f in allf),
        "version_report": regression.version_report(store, now=now),
        "preflight": preflight.latest(store), "canary": canary.latest(store, 5),
        "ai_review": review, "ai_review_runs": ai_review.runs(store, 10),
        "ai_settings": {**s, "month_spent_usd": round(spent, 5), "never_shown": list(ai_review.NEVER_SHOWN),
                        "never_shown_he": list(ai_review.NEVER_SHOWN_HE)},
        "remediations": remediation.history(store), "remedies": remediation.catalog(),
        "tickets": tickets.listing(store), "selfcheck_run": (sc_latest or {}).get("id"),
        "settings": {k: dbs.setting(k) for k in db.SETTINGS}, "log_index": LogIndex().built,
    }


def _daily(store, name: str, a: float, b: float) -> list[tuple[int, float]]:
    from .metrics import series
    by: dict[int, float] = {}
    for t, v in series(store, name, a, b + 1):
        by[int(t // 86400 * 86400)] = v                    # the last value of each day
    return sorted(by.items())


def spoken(rep: dict) -> tuple[str, str]:
    """2-3 sentences: the score, the main issue, what to do. The full list stays on the screen."""
    open_ = [f for f in rep["findings"] if f.get("state") in ("open", "accepted")]
    s = rep["score"]
    if not open_:
        en = f"I'm doing well: health score {s} out of 100, nothing open."
        he = f"אני במצב טוב: ציון תקינות {s} מתוך 100, אין שום דבר פתוח."
    else:
        top = open_[0]
        en = (f"Health score {s} out of 100 with {len(open_)} open item{'s' if len(open_) != 1 else ''}. "
              f"The main one: {top['title']}. Suggested: {top.get('fix') or 'see the Health page'}.")
        he = (f"ציון התקינות שלי {s} מתוך 100, עם {len(open_)} דברים פתוחים. העיקרי: {top['title_he']}. "
              f"מה לעשות: {top.get('fix_he') or 'ראו בעמוד התקינות'}.")
    rv = rep.get("ai_review")
    if rv and rv.get("ready"):
        n = sum(i["state"] in ("open", "stale") for i in rv["items_list"])
        en += f" An AI review is ready ({n} items, ${rv['cost_usd']:.3f})."
        he += f" יש גם דוח של הבודק ({n} נקודות)."
    return en, he


# ---------------------------------------------------------------------- the Core service
class RemediateArgs(BaseModel):
    remedy: str = Field(..., description="one of: " + ", ".join(remediation.REMEDIES))
    finding: str = Field("", description="the finding fingerprint it is for (optional)")
    feature: str = Field("", description="rerun_feature_setup only: the feature id")


class JobArgs(BaseModel):
    trigger: str = Field("schedule", description="leave as schedule")


class HealthService:
    def __init__(self, core, ops) -> None:
        self.core, self.ops = core, ops
        self.db = ops.db
        from ..store import Store
        self.store = Store(core.home / "store.db")
        db.ensure(self.store)
        self.index = LogIndex()
        cfg = core.cfg
        self.monitor = Monitor(self.store, home=core.home, cfg=cfg)
        self.clock = time.time
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    def active(self) -> bool:
        try:
            return self.core.features.active(FEATURE)
        except Exception:
            return True

    def start(self) -> None:
        global _SERVICE
        self._loop = asyncio.get_running_loop()
        _SERVICE = self
        self.monitor.attach(self.core.health)
        self.monitor.start(float(self.db.setting("health.monitor_interval_s") or 60))
        try:
            record_config(self.store, config_snapshot(self.core.cfg), self.clock())
            ai_review.note_persona(self.store, self.core.home, self.clock())
        except Exception:
            logs.warn("health", "HEALTH_SNAPSHOT_FAILED", "config / persona snapshot not stored")
        try:
            ensure_jobs(self.ops.scheduler, self.db)
        except Exception:
            logs.error("health", "HEALTH_JOBS_FAILED", "health jobs not created", exc=True)
        self._task = asyncio.get_running_loop().create_task(self._detect_loop(), name="health-detect")

    async def stop(self) -> None:
        global _SERVICE
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.monitor.stop()
        if _SERVICE is self:
            _SERVICE = None
        self.store.close()

    def components(self) -> dict:
        try:
            return self.core._health_components() or {}
        except Exception:
            return {}

    def report(self) -> dict:
        return report(self.store, home=self.core.home, cfg=self.core.cfg, dbs=self.db, components=self.components(),
                      now=self.clock())

    def detect(self) -> list:
        now = self.clock()
        self.monitor.metrics.flush()
        dead = float(getattr(self.core.cfg.history, "dead_air_s", 4.0) or 4.0)
        found = anomaly.Detector(self.store, clock=lambda: now, dead_air_s=dead, index=self.index).run(86400.0, now)
        before = {r["fingerprint"] for r in db.rows(self.store, "SELECT fingerprint FROM diag_findings WHERE active=1")}
        anomaly.save(self.store, found, now)
        rep = self.report()
        try:                  # issues registry: open findings, incl. the self-check crash finding (content-free)
            from .. import issue_registry
            issue_registry.ingest_findings(issue_registry.Registry(self.core.home, clock=self.clock),
                                           [f for f in rep["findings"] if f.get("state") in ("open", "accepted")
                                            and f.get("severity") in ("high", "medium")])
        except Exception:
            logs.warn("health", "HEALTH_ISSUES_FAILED", "issues registry not updated from the findings")
        self.monitor.metrics.put_hour(now, "health_score", rep["score"])
        return [f for f in found if f.fingerprint not in before]

    async def _detect_loop(self) -> None:
        while True:
            await asyncio.sleep(DETECT_EVERY_S)
            if not self.active():
                continue
            try:
                new = await asyncio.to_thread(self.detect)
                self._alert(new)
            except Exception:
                logs.warn("health", "HEALTH_DETECT_FAILED", "health detection pass failed")

    def _alert(self, new: list) -> None:
        high = [f for f in new if f.severity == "high"]
        alerts = getattr(self.ops, "alerts", None)
        if not high or alerts is None:
            return
        now = self.clock()
        speak = now - float(self.db.setting("health.spoken_alert_last") or 0) >= 86400
        f = high[0]
        try:
            alerts.raise_("health", f"בעיה בתקינות: {f.title_he}"[:120], f.fix_he[:400],
                          priority="high" if speak else "normal", kind="health", channel=None if speak else "popup",
                          dedupe_key=f"health:{f.fingerprint}", ref="#/health")
            if speak:
                self.db.set_setting("health.spoken_alert_last", now, actor="health")
        except Exception:
            logs.warn("health", "HEALTH_ALERT_FAILED", "health alert not raised")

    # ---- approvals-backed actions
    async def ask(self, prompt: str, **kw) -> bool:
        svc = getattr(self.core, "approvals", None)
        if svc is None:
            return False
        return await svc.ask(prompt, source="health", **kw)

    async def remediate(self, rid: str, *, by: str, finding: str | None = None, args: dict | None = None,
                        automatic: bool = False) -> dict:
        c = remediation.RemedyCtx(self.core.home, self.store, core=self.core, features=self.core.features,
                                  cfg=self.core.cfg, ask=self.ask, args=args or {}, clock=self.clock)
        return await remediation.run(rid, c, by=by, finding=finding, automatic=automatic, db_settings=self.db)

    async def canary(self, trigger: str = "schedule") -> dict:
        async with self._lock:
            rep = await canary.run_all("owner", real_home=self.core.home,
                                       paid_voice=bool(self.db.setting("health.canary_paid_voice")))
            canary.save(self.store, rep)
            logs.info("health", "CANARY_DONE", "canary done", trigger=trigger, passed=rep["passed"],
                      failed=rep["failed"], blocked=rep["blocked"])
            await asyncio.to_thread(self.detect)
            return rep

    async def ai_review_run(self, trigger: str = "schedule", force: bool = False) -> dict:
        async with self._lock:
            return await asyncio.to_thread(ai_review.run, self.store, home=self.core.home, cfg=self.core.cfg,
                                           dbs=self.db, budget=getattr(self.core, "budget", None), now=self.clock(),
                                           trigger=trigger, index=self.index, force=force)


def ensure_jobs(scheduler, dbs) -> dict:
    """Create the canary and AI-review jobs once (a job the user deleted is not recreated by itself)."""
    out = {}
    for key, spec, name, tool in (
            ("health.canary_job_id", _hhmm_cron(dbs.setting("health.canary_time"), CANARY_SPEC_DEFAULT),
             "בדיקת לילה / nightly health canary", "health_canary_run"),
            ("health.ai_review_job_id", _hhmm_cron(dbs.setting("health.ai_review_time"), REVIEW_SPEC_DEFAULT),
             "בדיקת AI סלקטיבית / selective AI review", "health_ai_review_run")):
        jid = dbs.setting(key) or ""
        if jid:
            out[key] = jid if scheduler.job(jid) else None
            continue
        v = scheduler.preview(spec, name, name=name, action="tool", tool=tool, tool_args={"trigger": "schedule"},
                              skills=["health"], owner="system", by="system", delivery=["report"])
        scheduler.approve(v["id"], by="system (SW-V4 O: health jobs by default)")
        dbs.set_setting(key, v["id"], actor="system")
        out[key] = v["id"]
        logs.info("health", "HEALTH_JOB", "health job created", job=v["id"], spec=spec)
    return out


def set_review_every(scheduler, dbs, every: str, *, by: str) -> dict:
    """`--every 2d | 3 | off`: the setting, and the job paused / resumed (OO-10: the job row shows it)."""
    w = str(every).strip().lower()
    jid = dbs.setting("health.ai_review_job_id") or (ensure_jobs(scheduler, dbs).get("health.ai_review_job_id"))
    if w in ("off", "never", "0", "כבוי"):
        dbs.set_setting("health.ai_review_enabled", False, actor=by)
        if jid and scheduler.job(jid) and scheduler.job(jid)["status"] == "active":
            scheduler.pause(jid, by=by)
        return {"enabled": False, "every_days": dbs.setting("health.ai_review_every_days"), "job": jid}
    n = float(w.rstrip("d").strip())
    if not 1 <= n <= 60:
        raise ValueError("every N days with N between 1 and 60, or off")
    dbs.set_setting("health.ai_review_every_days", n, actor=by)
    dbs.set_setting("health.ai_review_enabled", True, actor=by)
    if jid and scheduler.job(jid):
        nm = f"בדיקת AI סלקטיבית כל {n:g} ימים / selective AI review every {n:g} days"
        scheduler.edit(jid, by=by, name=nm)
        if scheduler.job(jid)["status"] == "paused":
            scheduler.approve(jid, by=by)
    return {"enabled": True, "every_days": n, "job": jid}


def make_skill(svc: HealthService):
    from ..skills import NoArgs, Skill, Tool, ToolResult

    def lang(en: str, he: str) -> str:
        return he if getattr(svc.core.cfg.voice, "language", "he") == "he" else en

    class HealthSkill(Skill):
        name = "health"
        description = "self-diagnostics 2.0: health summary, AI review summary, approved repairs"

        def tools(self) -> list:
            async def summary_(_) -> ToolResult:
                rep = await asyncio.to_thread(svc.report)
                en, he = spoken(rep)
                top = [{"title": f["title_he"], "severity": f["severity"]} for f in rep["findings"]
                       if f.get("state") in ("open", "accepted")][:3]
                return ToolResult(True, lang(en, he), {"score": rep["score"], "open": rep["open"], "top": top,
                                                       "rule": "Say this in 2-3 sentences; the full list is on the "
                                                               "Health page.", "dashboard": "#/health"},
                                  code="HEALTH_SUMMARY")

            async def review_(_) -> ToolResult:
                v = await asyncio.to_thread(ai_review.run_view, svc.store)
                en, he = ai_review.summary(v)
                return ToolResult(True, lang(en, he), {"run": (v or {}).get("id"), "dashboard": "#/health"},
                                  code="HEALTH_AI_REVIEW_SUMMARY")

            async def remediate_(a: RemediateArgs) -> ToolResult:
                if a.remedy not in remediation.REMEDIES:
                    return ToolResult(False, "unknown repair", code="HEALTH_REMEDY_UNKNOWN")
                r = await svc.remediate(a.remedy, by="voice", finding=a.finding or None,
                                        args={"feature": a.feature} if a.feature else {})
                ok = r.get("status") == "done"
                he = {"done": "תיקנתי ובדקתי שזה עובד.", "denied": "לא עשיתי כלום: זה לא אושר.",
                      "precheck_failed": "אי אפשר לעשות את זה עכשיו: ", "failed": "התיקון נכשל: ",
                      "post_check_failed": "ביצעתי, אבל הבדיקה אחרי זה נכשלה: "}.get(r.get("status"), "")
                detail = r.get("precheck") if r.get("status") == "precheck_failed" else r.get("postcheck") or r.get("result")
                return ToolResult(ok, lang(f"{r.get('status')}: {detail}", he + (detail or "" if not ok else "")), r,
                                  code="HEALTH_REMEDY_" + str(r.get("status", "")).upper())

            async def canary_(a: JobArgs) -> ToolResult:
                rep = await svc.canary(a.trigger)
                return ToolResult(rep["failed"] == 0, f"canary: {rep['passed']} PASS, {rep['failed']} FAIL, "
                                  f"{rep['blocked']} BLOCKED", {"failed": rep["failed"]}, code="HEALTH_CANARY")

            async def ai_(a: JobArgs) -> ToolResult:
                v = await svc.ai_review_run(a.trigger)
                st = v.get("status") if v else "none"
                return ToolResult(st in ("done", "skipped", "not_due"), f"AI review: {st}",
                                  {"status": st, "cost_usd": (v or {}).get("cost_usd", 0.0)}, code="HEALTH_AI_REVIEW")

            return [Tool("health_summary", "How JARVES is doing: a 2-3 sentence health summary (score, the main issue,"
                         " what to do). Use for 'מה מצבך?', 'איך אתה מרגיש?', 'how are you doing?'.", summary_, NoArgs,
                         permission="read", timeout_s=30),
                    Tool("health_ai_review_summary", "What the last AI review found (spoken summary). Use for "
                         "'מה מצא הבודק?', 'what did the reviewer find?'.", review_, NoArgs, permission="read"),
                    Tool("health_remediate", "Run ONE repair from the closed list, only after the user asked for it; "
                         "it always asks for approval on screen first.", remediate_, RemediateArgs, permission="action",
                         timeout_s=180),
                    Tool("health_canary_run", "Run the nightly health canary journeys now (scheduler job).", canary_,
                         JobArgs, permission="read", timeout_s=600),
                    Tool("health_ai_review_run", "Run the scheduled selective AI review if it is due (scheduler job).",
                         ai_, JobArgs, permission="read", timeout_s=180)]

    return HealthSkill()


ALLOWED_ACTIONS = {"health_remediate"}
INSTRUCTION = ("Your own health: for 'מה מצבך?', 'מה שלומך הטכני', 'how is your health / what is your status' call "
               "health_summary and say its 2-3 sentences in the user's language; for 'מה מצא הבודק?' / 'what did the "
               "reviewer find' call health_ai_review_summary (say honestly if it has not run). Never invent a score "
               "or a finding.")


def attach(core, ops) -> HealthService:
    svc = HealthService(core, ops)
    core.registry.register(make_skill(svc))
    core.registry.allowed_actions |= ALLOWED_ACTIONS
    agent = getattr(core, "agent", None)
    if agent is not None and hasattr(agent, "instructions"):
        base = agent.instructions

        def instructions() -> str:                      # V4-VC-8: live 21 Oct, 'מה מצבך?' was answered as small talk
            try:
                on = core.features.active(FEATURE)
            except Exception:
                on = False
            return f"{base()}\n\n{INSTRUCTION}" if on else base()
        agent.instructions = instructions
    svc.start()
    return svc


# ---------------------------------------------------------------------- feature registration
def _targets() -> dict:
    from ..capabilities import PI_ROADMAP
    return {"windows": {"status": "implemented_unverified",
                        "evidence": "UNIT test_diag_*.py, test_preflight.py, test_remediation.py, "
                                    "test_ai_review_selection.py, test_canary.py, test_health_api.py (real SQLite, "
                                    "replay harness, fake review client)",
                        "blocker": "not yet soaked 24 h nor run 5 nights on the owner's PC"},
            "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                               "blocker": "pure Python + SQLite (/proc for process stats); never run on a Raspberry "
                                          "Pi (no device in the test setup)"}}


feat.register(FEATURE, "אבחון עצמי 2.0 (תקינות)", "Self-diagnostics 2.0 (Health)", True, skills=["health"],
              about="watches his own health all the time (free), finds anomalies and regressions with the exact code "
                    "and log lines, runs nightly checks and a selective AI review every 2 days within a strict cap; "
                    "repairs only after you approve",
              targets=_targets(), permissions=["read", "action (approval first)"],
              settings="dashboard Health page, jarvis health, health.* settings")


# ---------------------------------------------------------------------- dashboard API
def _ctx_db(ctx):
    from ..userdata import ctx_db
    return ctx_db(ctx)


def _now(ctx) -> float:
    return getattr(ctx, "clock", time.time)()


def _components(ctx) -> dict:
    svc = current()
    if svc is not None and Path(svc.core.home).resolve() == Path(ctx.home).resolve():
        return svc.components()
    from ..health import read_snapshot
    snap = read_snapshot(Path(ctx.home) / "health.json") or {}
    return (snap.get("components") or {}) if snap.get("age_s", 1e9) < 120 else {}


def api_health(ctx, m, body, q=None):
    db.ensure(ctx.store)
    rep = report(ctx.store, home=ctx.home, cfg=ctx.cfg, dbs=_ctx_db(ctx), components=_components(ctx), now=_now(ctx))
    rep["enabled"] = feat._ctx_features(ctx).enabled(FEATURE)
    rep["summary"], rep["summary_he"] = spoken(rep)
    return 200, rep


def api_health_decide(ctx, m, body):
    b = body or {}
    try:
        fp, op = str(b.get("fingerprint") or ""), str(b.get("op") or "")
        if b.get("source") == "selfcheck":
            from ..selfcheck import decide
            return 200, decide(ctx.store, fp, op, by="dashboard", home=ctx.home, cfg=ctx.cfg, db=_ctx_db(ctx),
                               features=feat._ctx_features(ctx), days=float(b.get("days") or 7), clock=ctx.clock)
        return 200, anomaly.decide(ctx.store, fp, op, by="dashboard", days=float(b.get("days") or 7), now=_now(ctx))
    except KeyError:
        return 404, {"error": "no such finding"}
    except ValueError as e:
        return 400, {"error": str(e)}


def api_ai_decide(ctx, m, body):
    b = body or {}
    try:
        return 200, ai_review.decide(ctx.store, int(b.get("id") or 0), str(b.get("op") or ""), by="dashboard",
                                     days=float(b.get("days") or 7), home=ctx.home, cfg=ctx.cfg, dbs=_ctx_db(ctx),
                                     now=_now(ctx))
    except KeyError:
        return 404, {"error": "no such review item"}
    except ValueError as e:
        return 400, {"error": str(e)}


def api_ai_preview(ctx, m, body, q=None):
    return 200, ai_review.preview(ctx.store, home=ctx.home, cfg=ctx.cfg, dbs=_ctx_db(ctx), now=_now(ctx),
                                  index=LogIndex())


def api_ai_run(ctx, m, body):
    """On demand: the page shows the estimate first (preview) and sends confirm=true with the estimate it showed."""
    b = body or {}
    if not b.get("confirm"):
        return 400, {"error": "show the estimate first (GET /api/health/ai/preview), then confirm", "needs_confirm": True}
    svc = current()
    if svc is not None and svc._loop is not None and Path(svc.core.home).resolve() == Path(ctx.home).resolve():
        v = asyncio.run_coroutine_threadsafe(svc.ai_review_run("dashboard", force=True), svc._loop).result(240)
    else:
        v = ai_review.run(ctx.store, home=ctx.home, cfg=ctx.cfg, dbs=_ctx_db(ctx), now=_now(ctx), trigger="dashboard",
                          index=LogIndex(), force=True)
    return 200, v


def api_health_settings(ctx, m, body):
    dbs = _ctx_db(ctx)
    b = body or {}
    try:
        for k, v in b.items():
            if k not in db.SETTINGS or k.endswith("_job_id") or k in ("health.ai_review_last", "health.spoken_alert_last"):
                raise ValueError(f"unknown setting {k}")
            if k == "health.ai_review_every_days":
                from ..scheduler import Scheduler
                set_review_every(Scheduler(dbs), dbs, str(v), by="dashboard")
                continue
            d = db.SETTINGS[k]
            if isinstance(d, bool):
                v = bool(v)
            elif isinstance(d, float):
                v = float(v)
                if k.endswith("_usd") and not 0 <= v <= 20:
                    raise ValueError(f"{k} must be between 0 and 20 USD")
            elif isinstance(d, int):
                v = int(v)
            else:
                v = str(v)[:40]
            dbs.set_setting(k, v, actor="dashboard")
    except (TypeError, ValueError) as e:
        return 400, {"error": str(e)}
    return 200, {k: dbs.setting(k) for k in db.SETTINGS}


def api_remediate(ctx, m, body):
    b = body or {}
    rid = str(b.get("remedy") or "")
    if rid not in remediation.REMEDIES:
        return 400, {"error": "unknown remedy", "remedies": list(remediation.REMEDIES)}
    args = {"feature": str(b["feature"])} if b.get("feature") else {}
    svc = current()
    if svc is not None and svc._loop is not None and Path(svc.core.home).resolve() == Path(ctx.home).resolve():
        fut = asyncio.run_coroutine_threadsafe(svc.remediate(rid, by="dashboard", finding=b.get("finding"),
                                                             args=args), svc._loop)
        return 200, fut.result(660)
    r = remediation.REMEDIES[rid]
    if r.needs_core:
        return 409, {"error": "this repair needs the running JARVES: start it, then try again",
                     "error_he": "התיקון הזה צריך את ג'ארביס פועל: הפעילו אותו ונסו שוב"}
    from ..approvals import ask_owner

    async def ask(prompt, **kw):
        ok, _ = await asyncio.to_thread(ask_owner, ctx.home, prompt, title=kw.get("title", ""), why=kw.get("why", ""),
                                        kind=kw.get("kind", "other"), risk=kw.get("risk", "medium"),
                                        details=kw.get("details"))
        return ok
    c = remediation.RemedyCtx(ctx.home, ctx.store, features=feat._ctx_features(ctx), cfg=ctx.cfg, ask=ask, args=args,
                              clock=ctx.clock)
    return 200, asyncio.run(remediation.run(rid, c, by="dashboard", finding=b.get("finding"), db_settings=_ctx_db(ctx)))


def _find(ctx, fp: str) -> dict | None:
    rep = report(ctx.store, home=ctx.home, cfg=ctx.cfg, dbs=_ctx_db(ctx), now=_now(ctx))
    f = next((x for x in rep["findings"] if x["fingerprint"] == fp), None)
    if f is None:
        it = db.rows(ctx.store, "SELECT * FROM diag_review_items WHERE fingerprint=? AND rank > 0 ORDER BY id DESC "
                                "LIMIT 1", (fp,))
        if it:
            f = {"fingerprint": fp, "title": it[0]["title"], "severity": it[0]["severity"], "category": "ai_review",
                 "detail": it[0]["hypothesis"], "evidence": [it[0]["evidence"] or ""], "fix": it[0]["fix"], "count": 1}
    return f


def api_ticket_draft(ctx, m, body):
    fp = str((body or {}).get("fingerprint") or "")
    f = _find(ctx, fp)
    if f is None:
        return 404, {"error": "no such finding"}
    d = tickets.draft(f)
    tid = tickets.save_draft(ctx.store, d, now=_now(ctx))
    return 200, {**d, "id": tid, "status": "draft"}


def api_ticket_file(ctx, m, body):
    tid = int((body or {}).get("id") or 0)
    repo = str(_ctx_db(ctx).setting("health.ticket_repo") or "")
    from ..approvals import ask_owner

    async def ask(prompt, **kw):
        ok, _ = await asyncio.to_thread(ask_owner, ctx.home, prompt, title=kw.get("title", ""), why=kw.get("why", ""),
                                        kind=kw.get("kind", "message"), risk=kw.get("risk", "medium"),
                                        details=kw.get("details"), timeout=300)
        return ok
    try:
        return 200, asyncio.run(tickets.file(ctx.store, tid, ask=ask, repo=repo, by="dashboard"))
    except KeyError:
        return 404, {"error": "no such ticket draft"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/health", api_health)
    register("POST", r"/api/health/decide", api_health_decide)
    register("POST", r"/api/health/settings", api_health_settings)
    register("POST", r"/api/health/remediate", api_remediate)
    register("GET", r"/api/health/ai/preview", api_ai_preview)
    register("POST", r"/api/health/ai/run", api_ai_run)
    register("POST", r"/api/health/ai/decide", api_ai_decide)
    register("POST", r"/api/health/ticket", api_ticket_draft)
    register("POST", r"/api/health/ticket/file", api_ticket_file)


# ---------------------------------------------------------------------- CLI: jarvis health ...
def add_cli(sub) -> None:
    h = sub.add_parser("health", help="Self-diagnostics 2.0: health score, findings, version report, preflight, "
                                      "canary, AI review, approved repairs, ticket drafts")
    h.add_argument("--json", action="store_true")
    h.add_argument("--days", type=int, default=30, help="trend window (days)")
    hs = h.add_subparsers(dest="action")
    hs.add_parser("report", help="the full report (findings with evidence and code links)")
    r = hs.add_parser("remediate", help="run ONE repair from the closed list (asks first)")
    r.add_argument("remedy", choices=sorted(remediation.REMEDIES))
    r.add_argument("--finding", default="")
    r.add_argument("--feature", default="", help="rerun_feature_setup: the feature id")
    t = hs.add_parser("ticket", help="make a ticket draft of a finding; --file files it on the repo set in health.ticket_repo (asks; no repo = stays local)")
    t.add_argument("finding", help="the finding fingerprint (see: jarvis health report)")
    t.add_argument("--file", action="store_true")
    a = hs.add_parser("ai-review", help="the selective AI review: --every 2d | off, --now, --preview")
    a.add_argument("--every", metavar="Nd|off")
    a.add_argument("--now", action="store_true", help="run now (shows the estimate and asks first)")
    a.add_argument("--preview", action="store_true", help="what would be sent and its cost; sends nothing")
    a.add_argument("--yes", action="store_true", help="--now: you already saw and approved the estimate")
    c = hs.add_parser("canary", help="run the synthetic user-journey canaries now (throw-away test profile)")
    # dest differs from the global `--profile NAME` (the JARVES profile): sharing "profile" made the canary default
    # ("owner") select a JARVES profile of that name and fail with PROFILE ERROR before anything ran (V4-HE-1)
    c.add_argument("--profile", dest="canary_profile", choices=["ci", "owner"], default="owner",
                   help="ci = every external service is a fake; owner = this PC's real local parts (default)")
    c.add_argument("--report", type=Path, help="also write the JSON report here")
    hs.add_parser("preflight", help="run the start-up self-check now (read-only)")
    d = hs.add_parser("decide", help="accept / dismiss / snooze a finding or an AI review item")
    d.add_argument("target", help="a finding fingerprint, or ai:<item id>")
    d.add_argument("--op", choices=["accept", "dismiss", "snooze", "reopen"], default="accept")
    d.add_argument("--days", type=float, default=7)


def _tty_ask(home: Path, ask=input):
    from ..approvals import audited

    async def confirm(prompt: str) -> bool:
        print(prompt)
        return (ask("Approve? type yes: ") or "").strip().lower() in ("y", "yes", "כן")
    return audited(home, confirm, channel="terminal")


def _eof_is_no(ask):
    """A prompt with no terminal behind it (stdin closed / piped / a scheduled run) is a NO, never a traceback."""
    def safe(prompt: str = "") -> str:
        try:
            return ask(prompt)
        except EOFError:
            return ""
    return safe


def cmd_health(a, cfg, home: Path, *, out=print, ask=input) -> int:
    from ..store import Store
    from ..userdata import open_db
    ask = _eof_is_no(ask)
    st = Store(home / "store.db")
    dbs = open_db(home)
    try:
        db.ensure(st)
        act = getattr(a, "action", None)
        if act == "remediate":
            r = remediation.REMEDIES[a.remedy]
            if r.needs_core:
                out(f"{a.remedy} needs the running JARVES: ask by voice, or use the dashboard Health page while it "
                    "runs. Nothing was done.")
                return 2
            from ..setup_wizard import load_addons
            load_addons()
            c = remediation.RemedyCtx(home, st, features=feat.Features(cfg, home), cfg=cfg,
                                      ask=_tty_ask(home, ask), args={"feature": a.feature} if a.feature else {})
            res = asyncio.run(remediation.run(a.remedy, c, by="cli", finding=a.finding or None, db_settings=dbs))
            out(f"{a.remedy}: {res['status']} - {res.get('postcheck') or res.get('result') or res.get('precheck')}")
            return 0 if res["status"] == "done" else 1
        if act == "ticket":
            rep = report(st, home=home, cfg=cfg, dbs=dbs)
            f = next((x for x in rep["findings"] if x["fingerprint"] == a.finding), None)
            if f is None:
                out(f"no finding {a.finding!r} (see: jarvis health report)")
                return 2
            d = tickets.draft(f)
            tid = tickets.save_draft(st, d)
            out(f"--- ticket draft #{tid} (not filed) ---\n# {d['title']}\n\n{d['body']}")
            if a.file:
                res = asyncio.run(tickets.file(st, tid, ask=_tty_ask(home, ask),
                                               repo=str(dbs.setting("health.ticket_repo") or ""), by="cli"))
                out(f"ticket: {res['status']} {res.get('issue_url') or res.get('reason') or ''}")
            return 0
        if act == "ai-review":
            if a.every:
                from ..scheduler import Scheduler
                try:
                    v = set_review_every(Scheduler(dbs), dbs, a.every, by="cli")
                except ValueError as e:
                    out(str(e))
                    return 2
                out("AI review: off" if not v["enabled"] else f"AI review: every {v['every_days']:g} day(s)")
                return 0
            p = ai_review.preview(st, home=home, cfg=cfg, dbs=dbs, now=time.time(), index=LogIndex())
            out(f"AI review slice: {len(p['items'])} item(s), score {p['score']:g} (minimum {p['min_score']:g}); "
                f"estimate ${p['est_usd']:.4f} (cap ${p['cap_usd']:.3f}; this month ${p['month_spent_usd']:.4f}); "
                f"model {p['model']}" + ("; would be SKIPPED: nothing new ($0)" if p["would_skip"] else ""))
            for i in p["items"][:12]:
                out(f"  [{i['severity']:<6}] {i['kind']:<20} {i['title']}")
            out("never shown to the reviewer: " + "; ".join(p["never_shown"]))
            if a.preview or not a.now:
                return 0
            if not a.yes and (ask(f"Run the AI review now (up to ${p['est_usd']:.4f})? type yes: ") or "").strip(). \
                    lower() not in ("y", "yes", "כן"):
                out("not run")
                return 1
            v = ai_review.run(st, home=home, cfg=cfg, dbs=dbs, trigger="cli", index=LogIndex(), force=True)
            out(f"AI review: {v.get('status')} ({v.get('reason') or ''}) cost ${v.get('cost_usd', 0):.4f}")
            for it in (v.get("items_list") or []):
                out(f"  {it['rank']}. [{it['severity']}] {it['title']} - {it['fix']}")
            return 0 if v.get("status") in ("done", "skipped") else 1
        if act == "canary":
            rep = asyncio.run(canary.run_all(a.canary_profile, real_home=home if a.canary_profile == "owner" else None))
            canary.save(st, rep)
            for r in rep["results"]:
                out(f"{r['status']:<8} {r['name']:<16} {r['ms']:>8.0f} ms  {r['detail']}")
            if a.report:
                a.report.write_text(json.dumps(rep, indent=1), encoding="utf-8")
            return 1 if rep["failed"] else 0
        if act == "preflight":
            from ..setup_wizard import load_addons
            load_addons()
            res = preflight.run(preflight.Ctx(home=home, cfg=cfg, config_path=home / "config.toml",
                                              features=feat.Features(cfg, home), ports=[],
                                              previous=preflight.previous_state(home), need_godot=True,
                                              need_audio=cfg.voice.mode != "off"))
            for it in res["items"]:
                out(f"{it['status']:<8} {it['id']:<13} {it['detail']}" + (f"  -> {it['fix']}" if it["fix"] and
                                                                          it["status"] in ("red", "amber") else ""))
            out(f"preflight {res['ms']:.0f} ms (budget {preflight.BUDGET_S * 1000:.0f} ms)")
            return 0 if res["ok"] else 1
        if act == "decide":
            try:
                if a.target.startswith("ai:"):
                    r = ai_review.decide(st, int(a.target[3:]), a.op, by="cli", days=a.days, home=home, cfg=cfg, dbs=dbs)
                else:
                    r = anomaly.decide(st, a.target, a.op, by="cli", days=a.days)
            except (KeyError, ValueError) as e:
                out(f"not changed: {e}")
                return 2
            out(f"{a.target}: {r['state']}" + (" (a proposal waits for your approval)" if r.get("proposal_id") else ""))
            return 0
        rep = report(st, home=home, cfg=cfg, dbs=dbs, days=a.days)
        if a.json:
            out(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
            return 0
        en, _he = spoken(rep)
        out(f"HEALTH {rep['score']}/100, {rep['open']} open. {en}")
        for f in rep["findings"][: (50 if act == "report" else 10)]:
            if f.get("state") not in ("open", "accepted"):
                continue
            out(f"{f['rank']:>2}. [{f['severity']:<6}] {f['title']}  ({f['fingerprint']})")
            if act == "report":
                for e in (f.get("evidence") or [])[:3]:
                    out(f"      evidence: {e}")
                for h in (f.get("hints") or [])[:2]:
                    out(f"      code: {h['file']}:{h['line']} ({h.get('function')}) commit {h.get('commit') or '?'}")
                if f.get("fix"):
                    out(f"      fix: {f['fix']}")
        vr = rep.get("version_report")
        if vr:
            out(f"version report: {vr['previous']} -> {vr['version']}: " + (
                "; ".join(regression._line(i) for i in vr["items"]) or "no regression"))
        pf = rep.get("preflight")
        if pf:
            out(f"preflight: {'ok' if pf['ok'] else 'RED items'} in {pf['ms']:.0f} ms")
        cn = rep.get("canary") or []
        if cn:
            out(f"canary (last): {cn[0]['passed']} pass, {cn[0]['failed']} fail, {cn[0]['blocked']} blocked")
        rv = rep.get("ai_review")
        if rv:
            out("AI review: " + ai_review.summary(rv)[0])
        return 0
    finally:
        dbs.close()
        st.close()


# ---------------------------------------------------------------------- jarvis diag --ci
LEFTOVER = r"sk-[A-Za-z0-9_-]{16,}|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|" \
           r"AIza[0-9A-Za-z_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY"


def ci_bundle(home: Path, cfg, out_dir: Path, *, out=print) -> int:
    """`jarvis diag --ci`: the redacted bundle of the TEST home plus the health summary, scanned before upload.
    A secret-looking string anywhere = exit 2 and nothing kept (the CI upload step then has nothing to upload)."""
    import re
    import shutil
    import zipfile

    from .. import diagbundle
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / "jarves-diag-ci.zip"
    diagbundle.build(home, cfg, zpath, lines=2000)
    from ..store import Store
    from ..userdata import open_db
    st, dbs = Store(Path(home) / "store.db"), open_db(home)
    try:
        rep = report(st, home=home, cfg=cfg, dbs=dbs)
    finally:
        st.close()
        dbs.close()
    summary = {k: rep[k] for k in ("score", "open", "version_report", "preflight", "canary")}
    summary["findings"] = [{k: f.get(k) for k in ("fingerprint", "severity", "title", "count", "hints")}
                           for f in rep["findings"][:30]]
    def clean(v):                                    # sanitize every string VALUE (the JSON structure stays valid)
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        return diagbundle.sanitize(v) if isinstance(v, str) else v
    (out_dir / "health-summary.json").write_text(json.dumps(clean(json.loads(json.dumps(summary, default=str))),
                                                            indent=1, ensure_ascii=False), encoding="utf-8")
    leak = re.compile(LEFTOVER)
    texts = [(out_dir / "health-summary.json").read_text(encoding="utf-8")]
    with zipfile.ZipFile(zpath) as z:
        texts += [z.read(n).decode("utf-8", "replace") for n in z.namelist()]
    if any(leak.search(t) for t in texts):
        shutil.rmtree(out_dir, ignore_errors=True)
        out("diag --ci: a secret-looking string survived redaction; nothing kept")
        return 2
    out(f"diag --ci: {zpath} and health-summary.json (redacted, scanned)")
    return 0
