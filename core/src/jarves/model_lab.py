"""V7 M3 (#417): Model Lab evaluation harness. Runs a FIXED small public task set (Hebrew, tool-call, coding) against
candidate models through `SpendPolicy.decide`, and archives quality / latency / cost per model as JSON and CSV.

Rules (a spend and privacy control; changes need review):
* Nothing runs unless the owner starts it (`jarvis modellab run`); no timer, no import-time call, no prompt addition.
* Every call is decided by the SpendPolicy first: a blocked model is SKIPPED with the reason, never replaced by another
  (no hidden paid fallback). Default policy = free-only, 0 USD. A run also has its own hard cap (`run_cap_usd`,
  never above MAX_RUN_CAP_USD = the approved 2 USD live-trial total), checked BEFORE each call with the worst case.
* The tasks are built in and public (no private code or conversation text ever reaches a model from here).
* The provider is injected (`call(model_id, prompt, task_id) -> Reply`). Tests use FakeProvider only; the live
  OpenRouter provider (M2) is not wired here. The archive holds a short, scrubbed snippet: no keys, no owner prompts.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import clilang
from . import features as feat
from .model_policy import ModelConfigError, ModelRegistry, SpendPolicy, load_registry

FEATURE = "model_lab"
MAX_RUN_CAP_USD = 2.0            # approved total for the live trial (work package M5); a hard ceiling in code
MAX_OUT_TOKENS = 300             # worst-case output assumed for the cap check
SNIPPET = 200
AGENT, PROJECT = "modellab", "modellab"


@dataclass(frozen=True)
class Task:
    id: str
    kind: str                    # hebrew | tool_call | coding
    prompt: str
    check: dict                  # {"contains": [..]} | {"regex": ".."} | {"json": {..}}


TASKS = (
    Task("he-1", "hebrew", "ענה במילה אחת בעברית: מהי בירת ישראל?", {"contains": ["ירושלים"]}),
    Task("he-2", "hebrew", "כתוב את המספר 7 במילים בעברית, במילה אחת בלבד.", {"contains": ["שבע"]}),
    Task("he-3", "hebrew", "תרגם לאנגלית: בוקר טוב. ענה בתרגום בלבד.", {"regex": r"(?i)good\s+morning"}),
    Task("tool-1", "tool_call", 'Reply with ONLY a JSON call to get_weather for Haifa. '
         'Shape: {"tool": "get_weather", "args": {"city": "..."}}',
         {"json": {"tool": "get_weather", "args": {"city": "Haifa"}}}),
    Task("tool-2", "tool_call", 'Reply with ONLY JSON that sets a timer of 5 minutes. '
         'Shape: {"tool": "set_timer", "args": {"minutes": N}}',
         {"json": {"tool": "set_timer", "args": {"minutes": 5}}}),
    Task("code-1", "coding", "Write a Python function named add that returns the sum of two numbers. Code only.",
         {"regex": r"def\s+add\s*\(.*\)\s*:[\s\S]*return"}),
    Task("code-2", "coding", "Write a Python expression that reverses the string s. Expression only.",
         {"regex": r"s\s*\[\s*::\s*-1\s*\]"}),
)


@dataclass(frozen=True)
class Reply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class FakeProvider:
    """Scripted provider for tests: `script` = {(model_id, task_id): text}; `latency_s` = {model_id: seconds} moves the
    injected fake clock (an object with `.t`); a model in `fail` raises. Records every call. No network."""

    def __init__(self, script: dict, clock=None, latency_s: dict | None = None, fail=(), tokens=(20, 20)):
        self.script, self.clock, self.latency_s = script, clock, latency_s or {}
        self.fail, self.tokens, self.calls = set(fail), tokens, []

    def call(self, model_id: str, prompt: str, task_id: str = "") -> Reply:
        self.calls.append((model_id, task_id))
        if model_id in self.fail:
            raise RuntimeError("fake provider failure")
        if self.clock is not None:
            self.clock.t += self.latency_s.get(model_id, 0.0)
        return Reply(self.script.get((model_id, task_id), ""), *self.tokens)


_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|github_pat_\w+|ghp_\w+|Bearer\s+\S+|[A-Za-z0-9_\-]{32,})")


def scrub(text: str) -> str:
    return _SECRET.sub("[redacted]", str(text))[:SNIPPET]


def grade(task: Task, text: str) -> bool:
    c = task.check
    if "contains" in c:
        return all(s in text for s in c["contains"])
    if "regex" in c:
        return re.search(c["regex"], text) is not None
    if "json" in c:
        m = re.search(r"\{[\s\S]*\}", text)
        try:
            return m is not None and json.loads(m.group(0)) == c["json"]
        except ValueError:
            return False
    return False


def _est_in(prompt: str) -> int:
    return len(prompt) // 2 + 1          # pessimistic (Hebrew counts more tokens per character)


def run_eval(registry: ModelRegistry, policy: SpendPolicy, provider, models, *, tasks=TASKS,
             run_cap_usd: float = 0.0, clock: Callable[[], float] = time.monotonic) -> dict:
    """One row per (model, task). Skipped rows carry the policy reason; an error row carries only the exception class."""
    cap = run_cap_usd
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or not 0 <= cap <= MAX_RUN_CAP_USD:
        raise ModelConfigError(f"run_cap_usd must be a number between 0 and {MAX_RUN_CAP_USD}")
    rows, spent = [], 0.0
    for mid in models:
        for t in tasks:
            row = dict(model=mid, task=t.id, kind=t.kind, status="", accepted=False, latency_s=0.0, cost_usd=0.0,
                       tokens_in=0, tokens_out=0, note="", snippet="")
            rows.append(row)
            d = policy.decide(registry, preferred=mid, agent=AGENT, project=PROJECT,
                              tokens_in=_est_in(t.prompt), tokens_out=MAX_OUT_TOKENS)
            if d.model is None:
                row.update(status="skipped", note=scrub(d.exception.reason))
                continue
            if d.model.paid and (not math.isfinite(d.est_cost_usd) or spent + d.est_cost_usd > cap):
                row.update(status="skipped", note="run cap would be exceeded")
                continue
            t0 = clock()
            try:
                r = provider.call(mid, t.prompt, t.id)
            except Exception as e:  # noqa: BLE001 - a provider failure is data; only the class name is kept
                row.update(status="error", latency_s=round(clock() - t0, 3), note=type(e).__name__)
                continue
            lat = round(clock() - t0, 3)
            cost = d.model.cost_usd(r.tokens_in, r.tokens_out)
            if not math.isfinite(cost):
                cost = d.est_cost_usd
            if cost:
                policy.record(AGENT, cost)
                spent += cost
            row.update(status="ok", accepted=grade(t, r.text), latency_s=lat, cost_usd=round(cost, 6),
                       tokens_in=r.tokens_in, tokens_out=r.tokens_out, snippet=scrub(r.text))
    return {"rows": rows, "summary": summarize(registry, rows), "spent_usd": round(spent, 6),
            "run_cap_usd": float(cap), "mode": policy.mode}


def summarize(registry: ModelRegistry, rows: list[dict]) -> list[dict]:
    out = []
    for mid in dict.fromkeys(r["model"] for r in rows):
        mine = [r for r in rows if r["model"] == mid]
        ran = [r for r in mine if r["status"] == "ok"]
        acc = [r for r in ran if r["accepted"]]
        cost = sum(r["cost_usd"] for r in ran)
        info = registry.get(mid)
        label = "unknown" if info is None else ("local" if info.local else ("paid" if info.paid else "free"))
        out.append(dict(model=mid, label=label, ran=len(ran), skipped=sum(r["status"] == "skipped" for r in mine),
                        errors=sum(r["status"] == "error" for r in mine), accepted=len(acc),
                        quality=round(len(acc) / len(ran), 3) if ran else None,
                        avg_latency_s=round(sum(r["latency_s"] for r in ran) / len(ran), 3) if ran else None,
                        cost_usd=round(cost, 6), cost_per_accepted_usd=round(cost / len(acc), 6) if acc else None))
    return out


COLS = ("model", "task", "kind", "status", "accepted", "latency_s", "cost_usd", "tokens_in", "tokens_out", "note", "snippet")


def _cell(v):
    s = str(v)
    return "'" + s if s.startswith(("=", "+", "-", "@")) else v      # no spreadsheet formula injection


def archive(home: Path, report: dict, wall: Callable[[], float] = time.time) -> Path:
    """Write <home>/modellab/<stamp>.json and .csv; returns the json path."""
    d = Path(home) / "modellab"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime(wall()))}.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLS)
    for r in report["rows"]:
        w.writerow([_cell(r[k]) for k in COLS])
    p.with_suffix(".csv").write_text(buf.getvalue(), encoding="utf-8", newline="")
    return p


def latest(home: Path) -> dict | None:
    files = sorted((Path(home) / "modellab").glob("*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "מעבדת מודלים", "Model Lab", False, probe=_probe, risk="medium", stage="beta",
              about="compare models on a small fixed public task set (Hebrew, tool calls, coding): quality, speed and cost per model, only when you start it, free models first, hard spend caps",
              permissions=["runs only when you start it"], settings="jarvis modellab status")


def add_cli(sub) -> None:
    m = sub.add_parser("modellab", help="Model Lab: `status`, `tasks`, `run --models a,b [--fake]`, `report`. "
                                        "Runs only when you start it; free-only, 0 USD unless you set --cap-usd")
    m.add_argument("action", choices=["status", "tasks", "run", "report"])
    m.add_argument("--models", default="", help="comma list of model ids from the registry")
    m.add_argument("--cap-usd", type=float, default=0.0,
                   help=f"paid cap for this run, at most {MAX_RUN_CAP_USD} (default 0 = free models only)")
    m.add_argument("--fake", action="store_true", help="offline demo with a scripted fake provider (no network)")


def cmd_modellab(a, cfg, home) -> int:
    from .cli import _cli_features
    home = Path(home)
    f = _cli_features(cfg, home)
    if a.action == "tasks":
        print(json.dumps([dict(id=t.id, kind=t.kind) for t in TASKS], ensure_ascii=False))
        return 0
    if a.action == "status":
        print(json.dumps({"switch_on": bool(f.active(FEATURE)), "tasks": len(TASKS), "max_run_cap_usd": MAX_RUN_CAP_USD,
                          "last_report": bool(latest(home))}, ensure_ascii=False))
        return 0
    if a.action == "report":
        rep = latest(home)
        print(json.dumps(rep["summary"], ensure_ascii=False, indent=1) if rep else
              clilang.bi("אין דוח עדיין. / No report yet."))
        return 0 if rep else 1
    if not f.active(FEATURE):
        print(f"MODELLAB_OFF: {FEATURE} is off (jarvis features beta on, then jarvis features on {FEATURE})")
        return 1
    ids = [x for x in a.models.split(",") if x]
    if not ids:
        print("MODELLAB_BAD_ARGUMENT: --models is required")
        return 1
    if not a.fake:
        print("MODELLAB_NO_PROVIDER: the live provider is not wired yet (OpenRouter, slice M2); use --fake")
        return 1
    try:
        reg = load_registry(home / "models.toml")
        cap = a.cap_usd
        policy = (SpendPolicy("free-only") if cap == 0 else
                  SpendPolicy("cheap", daily_cap_usd=cap, task_cap_usd=cap,
                              allowed_providers={PROJECT: sorted({m.provider for m in reg.all()})}))
        rep = run_eval(reg, policy, FakeProvider({}), ids, run_cap_usd=cap)
    except ModelConfigError as e:
        print(f"MODELLAB_BAD_ARGUMENT: {e}")
        return 1
    p = archive(home, rep)
    print(json.dumps({"archive": str(p), "summary": rep["summary"], "spent_usd": rep["spent_usd"]}, ensure_ascii=False))
    return 0


COMMANDS = {"modellab": cmd_modellab}
