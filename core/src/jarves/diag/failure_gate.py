"""Self-diagnosis slice 1 (issue #336): ask ONCE, short, when a tool fails in a way the owner can see.

`FailureGate.notify(tool, result, route)` is called by the SkillRegistry after every failed call. It decides (never
nags), asks ONE question through the existing approval path (popup / tray / dashboard card / chat card; spoken only
when the failure happened in a voice turn) and on "yes" runs the read-only self-check (diag/areas.py) and shows the
plain cause + next step. "No", no answer or a timeout = skip. Rules, all tested:

- switch `self_diagnosis_ask` (features, default ON): off = never asks;
- only user-visible failures: a turn the owner started (route text / voice / phone), never a background job or an
  automation; only codes a guide problem entry explains (those are failures the self-check can say something about);
- de-duplicated per (area, code) with a 10 minute cooldown (counted from the question, answered or not), at most 3
  questions per hour overall, one question open at a time, never while a diagnosis runs;
- slice B (ticket) and slice C (coding agent) are NOT here yet.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from .. import logs
from . import areas

COOLDOWN_S = 600.0
MAX_PER_HOUR = 3
ASK_TIMEOUT_S = 60.0
DECLINED_COOLDOWN_S = 86400.0     # after an explicit No / no answer: the same problem is not asked again for a day
COLLECT_TIMEOUT_S = 20.0
BACKGROUND_ROUTES = ("default", "")

TEXT = {
    "en": {"ask": "That failed. Do you want me to diagnose it?\nYes = quick self-check (read-only). No = skip.",
           "title": "Diagnose the failure?", "why": "A self-check reads local state only; nothing is sent anywhere.",
           "head": "Self-check", "none": "I could not run the self-check."},
    "he": {"ask": "זה נכשל. רוצה שאבדוק?\nכן = בדיקה עצמית מהירה (קריאה בלבד). לא = לדלג.",
           "title": "לבדוק את הכשל?", "why": "הבדיקה העצמית קוראת מצב מקומי בלבד; שום דבר לא נשלח החוצה.",
           "head": "בדיקה עצמית", "none": "לא הצלחתי להריץ את הבדיקה העצמית."},
}


class FailureGate:
    def __init__(self, *, enabled: Callable[[], bool], ask: Callable[..., Awaitable[bool]],
                 deliver: Callable[[str, areas.DiagReport], Any], collect: Callable[[str, str, dict], areas.DiagReport],
                 lang: Callable[[], str] = lambda: "en", ask_model: Callable[[str], Awaitable[str]] | None = None,
                 wording_enabled: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.monotonic, cooldown_s: float = COOLDOWN_S,
                 max_per_hour: int = MAX_PER_HOUR, timeout_s: float = ASK_TIMEOUT_S,
                 pending: Callable[[], bool] = lambda: False, declined_cooldown_s: float = DECLINED_COOLDOWN_S,
                 collect_timeout_s: float = COLLECT_TIMEOUT_S) -> None:
        self.pending, self.declined_cooldown_s, self.collect_timeout_s = pending, declined_cooldown_s, collect_timeout_s
        self.enabled, self._ask, self.deliver, self.collect = enabled, ask, deliver, collect
        self.lang, self.ask_model, self.wording_enabled = lang, ask_model, wording_enabled
        self.clock, self.cooldown_s, self.max_per_hour, self.timeout_s = clock, cooldown_s, max_per_hour, timeout_s
        self._seen: dict[tuple[str, str], float] = {}
        self._cool: dict[tuple[str, str], float] = {}
        self._asked: list[float] = []
        self._busy = False
        self._tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- the decision (pure, no I/O)
    def should_ask(self, tool: str, res, route: str) -> tuple[str, str] | None:
        """(area, code) when the owner should be asked about this failed result, else None."""
        if res is None or getattr(res, "ok", True):
            return None
        data = getattr(res, "data", None) or {}
        if data.get("safety_rule") or data.get("already_done") or data.get("needs_approval"):
            return None
        if route in BACKGROUND_ROUTES:                     # automations / jobs write findings, they never ask
            return None
        try:
            if not self.enabled():
                return None
        except Exception:
            return None
        code = str(getattr(res, "code", "") or "").upper()
        area = areas.area_for(code, tool)
        if area is None or areas.match_problem(code)[0] is None:
            return None
        if self._busy:
            return None
        try:
            if self.pending():                             # never next to a real approval: one "yes" must mean one thing
                return None
        except Exception:
            return None
        now = self.clock()
        if now - self._seen.get((area, code), -1e12) < self._cool.get((area, code), self.cooldown_s):
            return None
        self._asked = [t for t in self._asked if now - t < 3600]
        if len(self._asked) >= self.max_per_hour:
            return None
        return area, code

    # ---------------------------------------------------------------- called by the registry (never raises)
    def notify(self, tool: str, res, route: str) -> None:
        try:
            key = self.should_ask(tool, res, route)
            if key is None:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            now = self.clock()
            self._seen[key] = now                         # claimed at once: a second failure cannot ask again
            self._cool[key] = self.cooldown_s
            self._asked.append(now)
            self._busy = True
            t = loop.create_task(self._flow(key[0], key[1], dict(getattr(res, "data", None) or {}), route))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        except Exception:
            self._busy = False
            logs.error("diag", "DIAG_GATE_FAILED", "failure gate failed", exc=True)

    async def _flow(self, area: str, code: str, data: dict, route: str) -> None:
        lang = "en"
        s = TEXT[lang]
        try:
            lang = self.lang() if self.lang() in TEXT else "en"
            s = TEXT[lang]
            logs.info("diag", "DIAG_ASKED", "asked the owner whether to diagnose a failure", area=area, error_code=code,
                      route=route)
            yes = await self._ask(s["ask"], title=s["title"], why=s["why"], risk="low", kind="other",
                                  details={"area": area, "code": code}, timeout=self.timeout_s, source="diag",
                                  voice=(route == "voice"))
            if not yes:
                self._cool[(area, code)] = self.declined_cooldown_s
                logs.info("diag", "DIAG_SKIPPED", "owner skipped the diagnosis (no, no answer or timeout)", area=area,
                          error_code=code)
                return
            rep = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, self.collect, area, code, data),
                                         self.collect_timeout_s)
            text = rep.text(lang)
            if self.wording_enabled() and self.ask_model is not None:
                text = await areas.word(rep, lang, self.ask_model)
            logs.info("diag", "DIAG_RAN", "self-check finished", area=area, error_code=code, confidence=rep.confidence,
                      guide=rep.guide_id, ai_worded=rep.ai_worded)
            res = self.deliver(f"{s['head']}: {text}", rep)
            if asyncio.iscoroutine(res):
                await res
        except asyncio.CancelledError:
            raise
        except Exception:
            logs.error("diag", "DIAG_FLOW_FAILED", "self-diagnosis flow failed", area=area, error_code=code, exc=True)
            try:
                self.deliver(s["none"], areas.DiagReport(area, code))
            except Exception:
                pass
        finally:
            self._busy = False


# ---------------------------------------------------------------------- Core wiring
def _model_wording(core) -> Callable[[str], Awaitable[str]] | None:
    """One small model request (budgeted, redacted evidence only) or None offline / no API route / privacy mode."""
    from ..config import api_billing_allowed

    async def ask(prompt: str) -> str:
        if core.cfg.log.privacy or logs.runtime_privacy() or not api_billing_allowed(core.cfg.ai):
            return ""

        def call() -> str:
            import openai
            with core.budget.reserve("ai_requests"):
                r = openai.OpenAI(timeout=20).responses.create(
                    model=core.cfg.history.llm_review_model or core.cfg.ai.text_model, input=prompt, store=False,
                    max_output_tokens=200, reasoning={"effort": "low"})
            return str(getattr(r, "output_text", "") or "")
        return await asyncio.get_running_loop().run_in_executor(None, call)
    return ask


def attach(core) -> FailureGate:
    """Called once from Core.start after the registry, features, approvals and ops exist."""
    from ..skills import _display_seen

    def deliver(text: str, rep: areas.DiagReport) -> None:
        _display_seen(text)                                        # the chat that asked
        try:
            core.ops.alerts.raise_("diagnosis", text[:120], text, user_requested=True, kind="info",
                                   dedupe_key=f"diag:{rep.area}:{rep.code}")
        except Exception:
            logs.warn("diag", "DIAG_ALERT_FAILED", "could not post the self-check result to the alerts page")

    gate = FailureGate(
        enabled=lambda: core.features.enabled("self_diagnosis_ask"),
        ask=core.ask_confirm, deliver=deliver, pending=lambda: bool(core.approvals and core.approvals.has_pending()),
        collect=lambda area, code, data: areas.run(area, code, home=core.home, cfg=core.cfg, failure_data=data),
        lang=lambda: "he" if getattr(core.cfg.voice, "language", "he") == "he" else "en",
        ask_model=_model_wording(core), wording_enabled=lambda: core.features.enabled("self_diagnosis_ai_wording"))
    core.registry.on_failure = gate.notify
    core.failure_gate = gate
    return gate
