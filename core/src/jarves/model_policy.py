"""V7 M1 (#415): ModelRegistry + SpendPolicy. Pure logic: no network, no keys, no provider calls, no wiring yet (M2).

Rules (a safety control; changes need review):
* Nothing here ever switches to a paid model silently. If the chosen model is unavailable or not allowed, `decide`
  returns a `PolicyException` request that the OWNER must approve; there is no automatic fallback to paid.
* Remote inference is never called private or local (`ModelInfo.privacy_label`).
* Zero paid spend by default: paid models need a daily cap above 0 USD set explicitly (V7 experiments run at 0).
* Fail closed: NaN/inf/negative/non-numeric values raise ModelConfigError; a paid model without a price is blocked;
  `models.toml` cannot make a model free or local; a project without an allow-list gets local or free models only.
* Registry source of truth: `models.toml` in the JARVES home (`load_registry`); the built-in table is the default.
"""
from __future__ import annotations

import math
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ModelConfigError(ValueError):
    """A model, price, cap or config value is invalid. Policy code fails closed on it."""


def _num(v, name: str) -> float:
    """Finite, non-negative real number (bool, str, NaN, inf, negative all rejected)."""
    try:
        ok = not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
        out = float(v) if ok else 0.0                  # float(10**400) raises OverflowError (#449)
    except (OverflowError, ValueError):
        ok = False
    if not ok:
        raise ModelConfigError(f"{name} must be a finite number >= 0, got {str(v)[:40]!r}")
    return out


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

    def __post_init__(self):
        for n in ("id", "provider"):
            if not isinstance(getattr(self, n), str) or not getattr(self, n):
                raise ModelConfigError(f"{n} must be a non-empty string")
        for n in ("local", "paid"):
            if not isinstance(getattr(self, n), bool):
                raise ModelConfigError(f"{n} must be true or false, got {getattr(self, n)!r}")
        if not isinstance(self.languages, str):
            raise ModelConfigError("languages must be a string")
        for n in ("context", "quality"):
            v = getattr(self, n)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ModelConfigError(f"{n} must be an integer >= 0, got {v!r}")
        _num(self.input_per_1m, "input_per_1m")
        _num(self.output_per_1m, "output_per_1m")
        if not self.paid and (self.input_per_1m or self.output_per_1m):
            raise ModelConfigError(f"{self.id}: a free model must have price 0")

    @property
    def priced(self) -> bool:
        """A paid model with no or zero price has UNKNOWN cost: it is never estimated as 0."""
        return (not self.paid) or (self.input_per_1m > 0 and self.output_per_1m > 0)

    @property
    def privacy_label(self) -> str:
        return "local: stays on this device" if self.local else "remote: prompts leave this device (not private)"

    def cost_usd(self, tokens_in: int, tokens_out: int) -> float:
        if not self.paid:
            return 0.0
        if not self.priced:
            return math.inf
        return (tokens_in * self.input_per_1m + tokens_out * self.output_per_1m) / 1_000_000


DEFAULT_MODELS = (
    ModelInfo("local-small", "local", local=True, paid=False, languages="Hebrew: weak", context=8192, quality=3),
    ModelInfo("free-remote", "openrouter", paid=False, languages="Hebrew: untested", context=32768, quality=5),
    ModelInfo("openrouter/free", "openrouter", paid=False, languages="Hebrew: untested (random free model)",
              context=32768, quality=4),     # M2: the free router picks a free model at random: experiment mode only
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


def load_registry(path: Path | None, free_overrides=()) -> ModelRegistry:
    """`models.toml`: [[model]] tables with the ModelInfo fields. Missing file = built-in defaults.

    The file cannot make a model free or local: `paid = false` / `local = true` is accepted only for an id (same
    provider) that is free/local in the built-in table, or an id the owner listed in `free_overrides`.
    Any invalid row raises ModelConfigError (fail closed, never a bare TypeError)."""
    if path is None or not Path(path).exists():
        return ModelRegistry()
    builtin = {m.id: m for m in DEFAULT_MODELS}
    approved = set(free_overrides)
    models = []
    try:
        rows = tomllib.loads(Path(path).read_text(encoding="utf-8")).get("model", [])
        for r in rows:
            m = ModelInfo(**r)
            b = builtin.get(m.id)
            same = b is not None and b.provider == m.provider
            if not m.paid and not (m.id in approved or (same and not b.paid)):
                raise ModelConfigError(f"{m.id}: models.toml cannot mark a model free without owner approval")
            if m.local and not (m.id in approved or (same and b.local)):
                raise ModelConfigError(f"{m.id}: models.toml cannot mark a model local without owner approval")
            models.append(m)
    except (TypeError, tomllib.TOMLDecodeError) as e:
        raise ModelConfigError(f"bad models.toml: {e}") from e
    return ModelRegistry(models)


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


class SpendPolicy:
    """Counters are guarded by a lock. A project without an allow-list entry may use local or free models only."""

    def __init__(self, mode: str = "free-only", daily_cap_usd: float = 0.0, task_cap_usd: float = 0.0,
                 agent_cap_usd: dict | None = None, allowed_providers: dict | None = None):
        self._lock = threading.Lock()
        self.mode = mode
        self.daily_cap_usd = _num(daily_cap_usd, "daily_cap_usd")      # 0 = no paid at all
        self.task_cap_usd = _num(task_cap_usd, "task_cap_usd")
        for n, v in (("agent_cap_usd", agent_cap_usd), ("allowed_providers", allowed_providers)):
            if v is not None and not isinstance(v, dict):
                raise ModelConfigError(f"{n} must be a table (name -> value), not {type(v).__name__} (#449)")
        self.agent_cap_usd = {str(k): _num(v, f"agent cap {k}") for k, v in (agent_cap_usd or {}).items()}
        self.allowed_providers = dict(allowed_providers or {})        # project -> list/set of provider names
        for v in self.allowed_providers.values():
            self._allow(v)
        self.spent_today = 0.0
        self.spent_by_agent: dict = {}

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, v: str) -> None:
        if v not in MODES:
            raise ModelConfigError(f"unknown mode {v!r}; one of {MODES}")
        self._mode = v

    @staticmethod
    def _allow(v):
        if not isinstance(v, (list, tuple, set, frozenset)) or not all(isinstance(x, str) for x in v):
            raise ModelConfigError("an allow-list must be a list or set of provider names, not a string")
        return v

    def record(self, agent: str, usd: float) -> None:
        usd = _num(usd, "usd")
        with self._lock:
            self.spent_today += usd
            self.spent_by_agent[agent] = self.spent_by_agent.get(agent, 0.0) + usd

    def _rank(self, m: ModelInfo):
        price = m.input_per_1m + m.output_per_1m
        if self._mode == "best-quality":
            return (-m.quality, price)
        if self._mode == "balanced":
            return (-(m.quality / (1.0 + price)), -m.quality)
        return (price, -m.quality)             # cheap / free-only / local-only

    def _blocked(self, m: ModelInfo, agent: str, project: str, est: float) -> str:
        if self._mode not in MODES:
            return "unknown mode: refused"
        if self._mode == "local-only" and not m.local:
            return "local-only mode: remote model refused"
        if self._mode == "free-only" and m.paid:
            return "free-only mode: paid model refused"
        allow = self.allowed_providers.get(project)
        if allow is None:
            if m.paid:
                return f"project {project or '(none)'} has no allow-list: local or free models only"
        else:
            try:
                self._allow(allow)
            except ModelConfigError:
                return f"allow-list of project {project} is invalid: refused"
            if m.provider not in allow:
                return f"provider {m.provider} is not allowed for project {project}"
        if m.paid:
            if not m.priced or not math.isfinite(est):
                return f"{m.id} has no known price: owner exception needed"
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
        for n, v in (("tokens_in", tokens_in), ("tokens_out", tokens_out)):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                return Decision(None, PolicyException(f"{n} must be an integer >= 0", preferred))
        with self._lock:
            return self._decide(registry, preferred, available, agent, project, tokens_in, tokens_out)

    def _decide(self, registry, preferred, available, agent, project, tin, tout) -> Decision:
        up = (lambda m: True) if available is None else (lambda m: m.id in available)
        if preferred:
            m = registry.get(preferred)
            if m is None:
                return Decision(None, PolicyException(f"unknown model {preferred}", preferred))
            est = m.cost_usd(tin, tout)
            why = "" if up(m) else f"{m.id} is unavailable"
            why = why or self._blocked(m, agent, project, est)
            if not why:
                return Decision(m, est_cost_usd=est)
            # unavailable or blocked: an alternative is only offered as a request, never used silently
            alt = self._pick(registry, up, agent, project, tin, tout, exclude=m.id)
            return Decision(None, PolicyException(why, preferred, proposed=alt.id if alt else ""))
        m = self._pick(registry, up, agent, project, tin, tout)
        if m is None:
            return Decision(None, PolicyException(f"no model fits mode {self._mode} and the caps", ""))
        return Decision(m, est_cost_usd=m.cost_usd(tin, tout))

    def _pick(self, registry, up, agent, project, tin, tout, exclude=""):
        ok = [m for m in registry.all() if m.id != exclude and up(m)
              and not self._blocked(m, agent, project, m.cost_usd(tin, tout))]
        return min(ok, key=self._rank) if ok else None
