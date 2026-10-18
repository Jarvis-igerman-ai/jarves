"""Central daily cost ceiling (CR-01). Every paid/metered request goes through Budget.

- ai_requests:   model requests: API Agents SDK calls, ChatGPT-plan Responses calls, GPT-Live delegated backend
                 responses, cloud speech for typed replies. Limit: ai.daily_request_limit.
- voice_seconds: live voice session time. Limit: ai.daily_realtime_minutes * 60.

Rules: reserve BEFORE sending (counts in-flight requests, so concurrent turns cannot overshoot); a reserved
request that reached the provider is counted even if it failed or was cancelled (conservative: it may have
been billed). Only a request proven never sent may be released uncounted. Usage reported after the fact
(server-side delegation, session seconds) is recorded with record(). The provider-side project spend limit
remains the final hard safety layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import logs
from .config import AIConfig
from .memory import MemoryStore

KINDS = ("ai_requests", "voice_seconds")


class BudgetExceeded(Exception):
    def __init__(self, kind: str, limit: int, used: int) -> None:
        super().__init__(f"daily {kind} limit {limit} reached ({used} used)")
        self.kind, self.limit, self.used = kind, limit, used
        self.code = "AI_BUDGET" if kind == "ai_requests" else "VOICE_BUDGET"
        self.action = ("raise ai.daily_request_limit" if kind == "ai_requests"
                       else "raise ai.daily_realtime_minutes")


@dataclass
class Reservation:
    budget: "Budget"
    kind: str
    n: int
    done: bool = field(default=False)

    def commit(self, actual: int | None = None) -> int:
        """Count the request(s); call once the request was sent (success, failure or cancel)."""
        if self.done:
            return self.budget.used(self.kind)
        self.done = True
        self.budget._inflight[self.kind] -= self.n
        return self.budget.memory.bump_usage(self.kind, self.n if actual is None else actual)

    def release(self) -> None:
        """Only for a request that was provably never sent to the provider."""
        if not self.done:
            self.done = True
            self.budget._inflight[self.kind] -= self.n

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *exc) -> None:
        self.commit()          # sent-or-unknown -> counted (conservative)


class Budget:
    def __init__(self, memory: MemoryStore, ai: AIConfig) -> None:
        self.memory, self.ai = memory, ai
        self._inflight = {k: 0 for k in KINDS}

    def limit(self, kind: str) -> int:
        return self.ai.daily_request_limit if kind == "ai_requests" else self.ai.daily_realtime_minutes * 60

    def used(self, kind: str) -> int:
        return self.memory.usage_today(kind)

    def remaining(self, kind: str) -> int:
        return max(0, self.limit(kind) - self.used(kind) - self._inflight[kind])

    def check(self, kind: str = "ai_requests", n: int = 1) -> None:
        if self.remaining(kind) < n:
            raise BudgetExceeded(kind, self.limit(kind), self.used(kind))

    def reserve(self, kind: str = "ai_requests", n: int = 1) -> Reservation:
        """Raise BudgetExceeded unless used + in-flight + n <= limit; then hold n units in flight."""
        try:
            self.check(kind, n)
        except BudgetExceeded as e:
            logs.warn("budget", e.code, str(e), action=e.action)
            raise
        self._inflight[kind] += n
        return Reservation(self, kind, n)

    def record(self, kind: str, n: int) -> int:
        """Usage known only afterwards (e.g. GPT-Live delegated backend responses, voice seconds)."""
        return self.memory.bump_usage(kind, n) if n > 0 else self.used(kind)

    def exhausted(self, kind: str) -> bool:
        return self.remaining(kind) <= 0
