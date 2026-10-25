"""V7 M1 (#415): ModelRegistry + SpendPolicy. Pure logic: no network, no keys, no provider calls, no wiring yet (M2).

Rules (a safety control; changes need review):
* Nothing here ever switches to a paid model silently. If the chosen model is unavailable or not allowed, `decide`
  returns a `PolicyException` request that the OWNER must approve; there is no automatic fallback to paid.
* Remote inference is never called private or local (`ModelInfo.privacy_label`).
* Zero paid spend by default: paid models need a daily cap above 0 USD set explicitly (V7 experiments run at 0).
* Registry source of truth: `models.toml` in the JARVES home (`load_registry`); the built-in table is the default.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MODES = ("balanced", "best-quality", "cheap", "free-only", "local-only")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider: str
    local: bool = False
    paid: bool = True
    languages: str = ""            # honest note, e.g. "Hebrew: untested"
    context: int = 0               # tokens
    input_per_1m: float = 0.0      # USD per 1M tokens
    output_per_1m: float = 0.0
    quality: int = 5               # 1..10 coarse rank for best-quality / balanced

    @property
    def privacy_label(self) -> str:
        return "local: stays on this device" if self.local else "remote: prompts leave this device (not private)"

    def cost_usd(self, tokens_in: int, tokens_out: int) -> float:
        if not self.paid:
            return 0.0
        return (tokens_in * self.input_per_1m + tokens_out * self.output_per_1m) / 1_000_000


DEFAULT_MODELS = (
    ModelInfo("local-small", "local", local=True, paid=False, languages="Hebrew: weak", context=8192, quality=3),
    ModelInfo("free-remote", "openrouter", paid=False, languages="Hebrew: untested", context=32768, quality=5),
    ModelInfo("gpt-6-luna", "openai", languages="Hebrew: good", context=400000, input_per_1m=0.10,
              output_per_1m=0.50, quality=6),
    ModelInfo("gpt-6-sol", "openai", languages="Hebrew: good", context=400000, input_per_1m=2.0,
              output_per_1m=10.0, quality=8),
)


class ModelRegistry:
    def __init__(self, models=DEFAULT_MODELS):
        self._m = {m.id: m for m in models}

    def get(self, model_id: str) -> ModelInfo | None:
        return self._m.get(model_id)

    def all(self) -> list[ModelInfo]:
        return list(self._m.values())

    def view(self) -> list[dict]:
        return [dict(id=m.id, provider=m.provider, local=m.local, paid=m.paid, context=m.context,
                     languages=m.languages, input_per_1m=m.input_per_1m, output_per_1m=m.output_per_1m,
                     privacy=m.privacy_label) for m in self._m.values()]


def load_registry(path: Path | None) -> ModelRegistry:
    """`models.toml`: [[model]] tables with the ModelInfo fields. Missing file = built-in defaults; a bad file raises."""
    if path is None or not Path(path).exists():
        return ModelRegistry()
    rows = tomllib.loads(Path(path).read_text(encoding="utf-8")).get("model", [])
    return ModelRegistry([ModelInfo(**r) for r in rows])


@dataclass(frozen=True)
class PolicyException:
    """An explicit request for the owner. Nothing happens until the owner approves it."""
    reason: str
    wanted: str
    proposed: str = ""             # a policy-clean alternative the owner may approve (never used automatically)
    needs_owner: bool = True


@dataclass(frozen=True)
class Decision:
    model: ModelInfo | None
    exception: PolicyException | None = None
    est_cost_usd: float = 0.0


@dataclass
class SpendPolicy:
    mode: str = "free-only"
    daily_cap_usd: float = 0.0                 # paid spend per day across all agents; 0 = no paid at all
    task_cap_usd: float = 0.0
    agent_cap_usd: dict = field(default_factory=dict)      # agent -> daily USD cap (absent = daily_cap_usd)
    allowed_providers: dict = field(default_factory=dict)  # project -> providers allowed for its (private) code
    spent_today: float = 0.0
    spent_by_agent: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}; one of {MODES}")

    def record(self, agent: str, usd: float) -> None:
        self.spent_today += usd
        self.spent_by_agent[agent] = self.spent_by_agent.get(agent, 0.0) + usd

    def _rank(self, m: ModelInfo):
        price = m.input_per_1m + m.output_per_1m
        if self.mode == "best-quality":
            return (-m.quality, price)
        if self.mode == "balanced":
            return (-(m.quality / (1.0 + price)), -m.quality)
        return (price, -m.quality)             # cheap / free-only / local-only

    def _blocked(self, m: ModelInfo, agent: str, project: str, est: float) -> str:
        if self.mode == "local-only" and not m.local:
            return "local-only mode: remote model refused"
        if self.mode == "free-only" and m.paid:
            return "free-only mode: paid model refused"
        allow = self.allowed_providers.get(project)
        if allow is not None and m.provider not in allow:
            return f"provider {m.provider} is not allowed for project {project}"
        if m.paid:
            cap = self.agent_cap_usd.get(agent, self.daily_cap_usd)
            if self.daily_cap_usd <= 0 or cap <= 0:
                return "paid models are off (cap is 0 USD)"
            if est > self.task_cap_usd:
                return f"task estimate ${est:.4f} is over the per-task cap ${self.task_cap_usd:.4f}"
            if self.spent_today + est > self.daily_cap_usd:
                return "daily cap would be exceeded"
            if self.spent_by_agent.get(agent, 0.0) + est > cap:
                return f"agent {agent} daily cap would be exceeded"
        return ""

    def decide(self, registry: ModelRegistry, *, preferred: str = "", available=None, agent: str = "default",
               project: str = "", tokens_in: int = 0, tokens_out: int = 0) -> Decision:
        """Pick a model. `available` = ids that are up (None = all). Never falls through to a paid model on its own."""
        up = (lambda m: True) if available is None else (lambda m: m.id in available)
        if preferred:
            m = registry.get(preferred)
            if m is None:
                return Decision(None, PolicyException(f"unknown model {preferred}", preferred))
            est = m.cost_usd(tokens_in, tokens_out)
            why = "" if up(m) else f"{m.id} is unavailable"
            why = why or self._blocked(m, agent, project, est)
            if not why:
                return Decision(m, est_cost_usd=est)
            # unavailable or blocked: an alternative is only offered as a request, never used silently
            alt = self._pick(registry, up, agent, project, tokens_in, tokens_out, exclude=m.id)
            return Decision(None, PolicyException(why, preferred, proposed=alt.id if alt else ""))
        m = self._pick(registry, up, agent, project, tokens_in, tokens_out)
        if m is None:
            return Decision(None, PolicyException(f"no model fits mode {self.mode} and the caps", ""))
        return Decision(m, est_cost_usd=m.cost_usd(tokens_in, tokens_out))

    def _pick(self, registry, up, agent, project, tin, tout, exclude=""):
        ok = [m for m in registry.all() if m.id != exclude and up(m)
              and not self._blocked(m, agent, project, m.cost_usd(tin, tout))]
        return min(ok, key=self._rank) if ok else None
