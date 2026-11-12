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
