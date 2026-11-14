"""The daily voice-minute gate (JAR-262, R-086): may one more live voice conversation start today?

Limits are set through the existing path (`jarvis budget set --voice-minutes N --requests N` -> ai.daily_*).
Voice use is only known afterwards (Budget.record), so this check is its gate; AI requests are NOT gated here:
Budget.reserve (BudgetedModel) stays their atomic gate. One usage source: the shared Budget (used/remaining/limit).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import limits

KIND = "voice_seconds"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    view: dict = field(default_factory=dict)   # limits.view(...) at level "exhausted" when refused (announce_limit)


def admit_voice(budget) -> Decision:
    """Refused exactly when less than the useful minimum is left (a near-zero session would end at once and
    reconnect in a loop): the same test Core._start_live used before, `budget.remaining < min_useful`."""
    if budget.remaining(KIND) >= float(limits.SPECS[KIND]["min_useful"]):
        return Decision(True)
    v = limits.view(KIND, budget.used(KIND), budget.limit(KIND))
    v["level"] = "exhausted"
    return Decision(False, v)


def today_lines(budget_or_views) -> list[dict]:
    """The voice and request limits of today, ALWAYS (also when ok): what `jarvis budget limits` shows.
    Accepts a Budget or a list of limits.view dicts."""
    views = budget_or_views if isinstance(budget_or_views, list) else limits.views_from(budget_or_views.used,
                                                                                     budget_or_views.limit)
    return [v for v in views if v["kind"] in (KIND, "ai_requests")]


def selftest(ai, voice_minutes: int | None = None, requests: int | None = None) -> dict:
    """A FREE offline proof that the daily voice and request limits hold (JAR-321): a real Budget on a throw-away
    memory.db (the owner's counters are never touched), no model call, no microphone, no network.
    voice: used up to the limit -> the Core voice gate (admit_voice) refuses the next conversation.
    requests: `requests` reservations pass, the next one is refused before any model call and is announced."""
    import logging

    ai = ai.model_copy()
    if voice_minutes is not None:
        ai.daily_realtime_minutes = int(voice_minutes)
    if requests is not None:
        ai.daily_request_limit = int(requests)
    muted = logging.root.manager.disable          # the test refusals are not real ones: keep them out of jarves.log
    logging.disable(logging.CRITICAL)
    try:
        return _selftest(ai)
    finally:
        logging.disable(muted)


def _selftest(ai) -> dict:
    import tempfile
    from pathlib import Path

    from .budget import Budget, BudgetExceeded
    from .memory import MemoryStore

    checks: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="jarves-limits-") as d:
        m = MemoryStore(Path(d) / "memory.db")
        b = Budget(m, ai, alive=lambda pid: True)
        try:
            lim_v = b.limit(KIND)
            first = admit_voice(b)
            b.record(KIND, lim_v)
            after = admit_voice(b)
            ok_v = first.allowed == (lim_v >= limits.SPECS[KIND]["min_useful"]) and not after.allowed \
                and after.view.get("code") == "VOICE_DAILY_LIMIT"
            checks.append({"kind": KIND, "limit": ai.daily_realtime_minutes, "ok": ok_v,
                           "first_allowed": first.allowed, "refused_at_limit": not after.allowed,
                           "notice_en": limits.spoken(after.view, "en") if after.view else "",
                           "notice_he": limits.spoken(after.view, "he") if after.view else ""})
            n = b.limit("ai_requests")
            told: list = []
            b.on_exceeded = told.append
            passed = 0
            if n > 0:                              # the earlier requests of the day, then the last allowed one
                b.record("ai_requests", n - 1)
                b.reserve("ai_requests").commit()
                passed = n
            refused = False
            try:
                b.reserve("ai_requests")
            except BudgetExceeded:
                refused = True
            rv = limits.view("ai_requests", b.used("ai_requests"), n)
            ok_r = passed == n and refused and b.used("ai_requests") == n and len(told) == 1
            checks.append({"kind": "ai_requests", "limit": n, "ok": ok_r, "passed": passed,
                           "refused_at_limit": refused, "announced": len(told) == 1,
                           "notice_en": limits.spoken(rv, "en"), "notice_he": limits.spoken(rv, "he")})
        finally:
            b.close()
            m.close()
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
