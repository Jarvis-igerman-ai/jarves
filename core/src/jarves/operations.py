"""WP-M3 wiring: scheduler, reports, notes, file sandbox, proactive alerts and bounded autonomy in the Core.

`attach(core)` (called once from Core.start after the background tasks) opens the user database, registers the
Skills (scheduler, reports, notes, files, alerts, autonomy) and their low-risk actions, and starts ONE background
loop: the scheduler tick (local SQLite only, at most every 30 s), then alert delivery, then (every
AUTONOMY_SYNC_S) diagnostic proposals from the self-diagnosis issues. No voice session or model call is opened to
poll anything. `Operations.stop()` is awaited by Core.stop.

Alert outputs (alerts.py): popup = a quiet `assistant.text` line on the face (source "alert"); speech = the open
GPT-Live conversation (commentary at a good moment) or the Core's TTS when no conversation is open and nothing else
is talking; os = `core.os_notify(title, text, quiet)` when a platform adapter set it (WP-M1 tray on Windows).

Also: the M3 entries of the feature catalog (jarves.features) with their Windows / Raspberry Pi status, and
`dashboard_routes` for every M3 API (one dashboard plugin entry).
"""
from __future__ import annotations

import asyncio
import inspect
import time

from . import alerts as alerts_mod
from . import autonomy as autonomy_mod
from . import features as feat
from . import logs
from . import notes as notes_mod
from . import sandbox as sandbox_mod
from . import scheduler as sched_mod
from .userdata import open_db

AUTONOMY_SYNC_S = 600.0
ROADMAP_ISSUE = "https://github.com/igalgerman/JARVIS/issues/23"     # M3 Raspberry Pi verification (real issue)
_UNVERIFIED_PI = ("pure Python + SQLite + tzdata, no Windows-only code; not run on a Raspberry Pi yet "
                  "(no device in the test setup)")
_WIN_EVIDENCE = "core/tests/test_m3_*.py (unit + fault injection on Windows); no real-restart run yet"

# id -> (he, en, skills, about, windows status, pi status, pi blocker)
M3_FEATURES = {
    "scheduler": ("משימות מתוזמנות", "Scheduled jobs", ["scheduler"],
                  "reminders and recurring reports; missed jobs run once after start-up (asks before activating)",
                  "implemented_unverified", "implemented_unverified", _UNVERIFIED_PI),
    "reports": ("דוחות שמורים", "Saved reports", ["reports"], "results of scheduled jobs, read back on request",
                "implemented_unverified", "implemented_unverified", _UNVERIFIED_PI),
    "notes": ("פתקים", "Notes", ["notes"], "your notes: add, search, edit, export as Markdown",
              "implemented_unverified", "implemented_unverified", _UNVERIFIED_PI),
    "files": ("קבצים בסביבה פרטית", "File workspace", ["files"],
              "documents JARVES writes, only in its private folder; your folders only after you approve",
              "implemented_unverified", "implemented_unverified",
              _UNVERIFIED_PI + "; Linux symlink/permission guards covered by tests only on CI"),
    "proactive_alerts": ("התראות יזומות", "Proactive alerts", ["alerts"],
                         "reminders and news at the right time, with quiet hours and limits",
                         "implemented_unverified", "planned",
                         "popup + speech portable; no OS-notification adapter on Linux/Pi yet (desktop "
                         "notifications / headless equivalent not chosen)"),
    "autonomy": ("שיפור עצמי מבוקר", "Self-improvement proposals", ["autonomy"],
                 "diagnostics propose calibration changes; applied only with your approval or explicit grant",
                 "implemented_unverified", "implemented_unverified", _UNVERIFIED_PI),
}


def targets(fid: str) -> dict:
    _, _, _, _, win, pi, blocker = M3_FEATURES[fid]
    return {"windows": {"status": win, "evidence": _WIN_EVIDENCE},
            "linux_arm64_pi": {"status": pi, "blocker": blocker, "roadmap_issue": ROADMAP_ISSUE}}


def _register_features() -> None:
    takes_targets = "targets" in inspect.signature(feat.register).parameters    # WP-M4 capability matrix
    for fid, (he, en, skills, about, *_rest) in M3_FEATURES.items():
        kw = {"targets": targets(fid)} if takes_targets else {}
        feat.register(fid, he, en, True, skills=skills, about=about, **kw)


_register_features()


# ---------------------------------------------------------------------- alert outputs
class PopupOutput:
    def __init__(self, core) -> None:
        self.core = core

    def available(self) -> bool:
        link = getattr(self.core, "link", None)
        return link is not None and not self.core.stopping and link.face_client_id() is not None

    async def deliver(self, a: dict) -> bool:
        text = a["title"] + (f" — {a['text'][:400]}" if a["text"] and a["kind"] != "readback" else "")
        return bool(await self.core.link.send_face("assistant.text", {"text": text, "source": "alert", "ok": True,
                                                                      "code": "ALERT"}))


class SpeechOutput:
    ANNOUNCE_WAIT_S = 20.0           # wait this long for a good moment in the conversation, then retry next tick

    def __init__(self, core) -> None:
        self.core = core

    def _health(self, name: str) -> str:
        comp = getattr(getattr(self.core, "health", None), "components", {}).get(name)
        return getattr(comp, "status", "") if comp is not None else ""

    def available(self) -> bool:
        c = self.core
        if c.stopping or c.voice_mode == "off":
            return False
        live = c.live
        if live is not None:
            return hasattr(live, "announce")
        busy = c._busy is not None and not c._busy.done()
        return (not busy and not c._voice_connecting and not c.listening and c.dev.audio is not None
                and self._health("speech_out") != "unavailable" and self._health("audio") != "unavailable")

    async def deliver(self, a: dict) -> bool:
        live = self.core.live
        if live is not None:
            return bool(await live.announce(alerts_mod.speech_note(a), max_wait_s=self.ANNOUNCE_WAIT_S,
                                            tag=f"alert{a['id']}"))
        text = a["text"] if a["kind"] == "readback" else f"{a['title']}. {a['text']}".strip(". ")
        await self.core.speak(text[:4000])
        return self._health("speech_out") != "unavailable" and self._health("audio") != "unavailable"


class OSOutput:
    def __init__(self, core) -> None:
        self.core = core

    def available(self) -> bool:
        return callable(getattr(self.core, "os_notify", None))

    async def deliver(self, a: dict) -> bool:
        r = self.core.os_notify(a["title"], a["text"][:300], bool(a.get("quiet")))
        if inspect.isawaitable(r):
            r = await r
        return bool(r)


# ---------------------------------------------------------------------- Core wiring
class Operations:
    def __init__(self, core) -> None:
        self.core = core
        home = core.home
        self.db = open_db(home)
        self.alerts = alerts_mod.AlertEngine(self.db, outputs={"popup": PopupOutput(core), "speech": SpeechOutput(core),
                                                               "os": OSOutput(core)})
        self.alerts.enabled = lambda: core.features.active("proactive_alerts")
        self.sandbox = sandbox_mod.Sandbox(home / "files", self.db, home=home)
        self.notes = notes_mod.Notes(self.db)
        self.scheduler = sched_mod.Scheduler(self.db, research=sched_mod.make_research(getattr(core, "tasks", None)),
                                             registry=core.registry, alerts=self.alerts, budget=core.budget,
                                             features=core.features)
        self.autonomy = autonomy_mod.Autonomy(self.db, cfg=core.cfg, home=home, features=core.features,
                                              alerts=self.alerts)
        reg = core.registry
        for skill in [*sched_mod.make_skills(self.scheduler, reg), notes_mod.make_skill(self.notes, self.sandbox),
                      sandbox_mod.make_skill(self.sandbox), alerts_mod.make_skill(self.alerts),
                      autonomy_mod.make_skill(self.autonomy, reg)]:
            reg.register(skill)
        reg.allowed_actions |= (sched_mod.ALLOWED_ACTIONS | notes_mod.ALLOWED_ACTIONS | sandbox_mod.ALLOWED_ACTIONS
                                | alerts_mod.ALLOWED_ACTIONS | autonomy_mod.ALLOWED_ACTIONS)
        self._task: asyncio.Task | None = None
        self._synced = 0.0
        try:                                         # truthful health: no zone data = no correct schedule times
            tz = self.scheduler.tz()
            n = len(self.db.rows("SELECT id FROM jobs WHERE status='active'"))
            core.health.set("scheduler", "ok", f"{n} active job(s), time zone {tz.key}")
        except sched_mod.ScheduleError as e:
            core.health.set("scheduler", "unavailable", e.message, "uv sync (installs tzdata), or set the "
                                                                   "time zone on the dashboard Jobs page")

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.scheduler.run(self._after_tick), name="operations")

    async def _after_tick(self) -> None:
        await self.alerts.flush()
        now = time.time()
        if now - self._synced >= AUTONOMY_SYNC_S:
            self._synced = now
            try:
                st = getattr(getattr(self.core, "history", None), "store", None)
                issues = st.issues("open") if st is not None else autonomy_mod.open_issues(self.core.home)
                self.autonomy.sync(issues)
            except Exception:
                logs.error("autonomy", "AUTONOMY_SYNC_FAILED", "diagnostic proposals not updated", exc=True)

    async def stop(self) -> None:
        await self.scheduler.stop()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.db.close()


def attach(core) -> Operations:
    ops = Operations(core)
    ops.start()
    return ops


# ---------------------------------------------------------------------- dashboard
def api_platforms(ctx, m, body):
    return 200, {"features": [{"id": fid, "title_en": v[1], "title_he": v[0], **targets(fid)}
                              for fid, v in M3_FEATURES.items()]}


def dashboard_routes(register) -> None:
    for mod in (sched_mod, alerts_mod, notes_mod, sandbox_mod, autonomy_mod):
        mod.dashboard_routes(register)
    register("GET", r"/api/m3/platforms", api_platforms)
