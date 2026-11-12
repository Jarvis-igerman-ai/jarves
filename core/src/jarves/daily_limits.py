"""Owner-set daily limits for voice and AI requests, and the gate that refuses at the limit (JAR-262, R-086).

`set_daily` applies the owner's numbers to an AIConfig (same fields as `jarvis budget set`); `admit` asks the
shared Budget whether one more voice conversation / AI request fits today and, when it does not, returns a clear
refusal: which limit, used/max, and how to raise it (words from `limits`, so every surface says the same).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import limits


@dataclass(frozen=True)
class Decision:
    allowed: bool
    kind: str
    code: str = ""
    text_en: str = ""
    text_he: str = ""
    spoken: str = ""


def set_daily(ai, *, voice_minutes: int | None = None, requests: int | None = None) -> None:
    """Set the daily caps (whole numbers, 0 = nothing allowed). Unknown/negative values raise ValueError."""
    for name, value in (("voice_minutes", voice_minutes), ("requests", requests)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"{name} must be a whole number >= 0, got {value!r}")
    if voice_minutes is not None:
        ai.daily_realtime_minutes = voice_minutes
    if requests is not None:
        ai.daily_request_limit = requests


def admit(budget, kind: str, language: str = "he") -> Decision:
    """May one more voice conversation (`voice_seconds`) or AI request (`ai_requests`) start today?
    Voice needs at least the useful minimum left (a near-zero session would end at once and reconnect)."""
    need = max(1.0, float(limits.SPECS[kind]["min_useful"]))
    if budget.remaining(kind) >= need:
        return Decision(True, kind)
    v = limits.view(kind, budget.used(kind), budget.limit(kind))
    return Decision(False, kind, v["code"], f"{v['text_en']} - {v['action_en']}",
                    f"{v['text_he']} - {v['action_he']}", limits.spoken(v, language))
