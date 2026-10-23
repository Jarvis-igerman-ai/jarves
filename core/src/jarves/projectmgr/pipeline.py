"""Pipelines: the ordered stages a task goes through. Generic: any order, any stage skipped, any actor per stage.

A stage is a dict {kind, by, skip, on_fail, require, rounds, judge, question, cmd}. `by` is ONE actor spec string:
  jarves | owner | <agent> | team:<agent> | a comma list of agents ("claude,codex").
The flow string the owner can type or say:  "plan:claude work:team:codex test review:claude,codex merge"
(stage words separated by spaces; `kind:by`; a leading `-` skips a stage: "-review"). Hebrew words work too.

on_fail: retry (a fresh attempt from the nearest earlier code stage, bounded by projects.max_attempts — exactly the
original behaviour), back:<kind> (go back to an earlier code stage on the SAME branch, bounded by
projects.max_review_rounds), skip, or stop (the task waits for the owner).
"""
from __future__ import annotations

import re

KINDS = ("plan", "work", "test", "review", "merge", "release", "reproduce", "diagnose", "verify", "debate", "gate", "cmd")
CODE_KINDS = ("work", "reproduce")                       # stages that change code in the task worktree
READONLY_KINDS = ("plan", "review", "diagnose", "debate")
CHECK_KINDS = ("test", "verify", "cmd")                  # JARVES runs a command
DEFAULT_BY = {"plan": "jarves", "work": "jarves", "test": "jarves", "review": "jarves", "merge": "jarves",
              "release": "owner", "reproduce": "jarves", "diagnose": "jarves", "verify": "jarves",
              "debate": "jarves", "gate": "owner", "cmd": "jarves"}
DEFAULT_ON_FAIL = {"test": "retry", "verify": "retry", "cmd": "retry", "review": "back:work"}
TEMPLATES = {
    "quick": "work test merge",
    "feature": "plan work test review merge",
    "bugfix": "reproduce diagnose work verify review merge",
    "release": "test gate release",
    "decide": "debate gate",
}
DEFAULT_TEMPLATE = "quick"
MAX_STAGES, MAX_AGENTS = 12, 4
AGENTS = ("claude", "codex", "opencode", "openrouter")
AGENT_WORDS = {"קלוד": "claude", "קלאוד": "claude", "claude-code": "claude", "קודקס": "codex", "אופנקוד": "opencode",
               "אופן-קוד": "opencode", "open-code": "opencode", "אופנראוטר": "openrouter", "open-router": "openrouter"}
STAGE_WORDS = {"תכנון": "plan", "עבודה": "work", "בדיקה": "test", "בדיקות": "test", "סקירה": "review",
               "ריוויו": "review", "מיזוג": "merge", "שחרור": "release", "שחזור": "reproduce", "אבחון": "diagnose",
               "אימות": "verify", "ויכוח": "debate", "דיון": "debate", "אישור": "gate", "פקודה": "cmd"}
ACTOR_WORDS = {"אני": "owner", "אני-בעצמי": "owner", "me": "owner", "myself": "owner", "ג'ארביס": "jarves",
               "jarvis": "jarves", "ג׳ארביס": "jarves"}
_MODEL = re.compile(r"^[\w./:\-]{1,80}$")


class FlowError(ValueError):
    pass


def norm_agent(a: str) -> str:
    a = str(a or "").strip()
    name, sep, model = a.partition(":")
    low = name.lower()
    return AGENT_WORDS.get(name, AGENT_WORDS.get(low, low)) + (sep + model if sep else "")


def actor(a: str) -> str:
    """One actor -> canonical form, or FlowError. jarves | owner | agent[:model] | team:agent[:model]."""
    a = str(a or "").strip()
    a = ACTOR_WORDS.get(a, ACTOR_WORDS.get(a.lower(), a))
    if a.lower() in ("jarves", "owner"):
        return a.lower()
    team = a.lower().startswith("team:")
    rest = a[5:] if team else a
    rest = norm_agent(rest)
    name, _, model = rest.partition(":")
    if name not in AGENTS:
        raise FlowError(f"unknown agent {name[:30]} ({', '.join(AGENTS)})")
    if model and not _MODEL.match(model):
        raise FlowError(f"bad model {model[:40]}")
    return ("team:" if team else "") + rest


def actors(by: str) -> list[str]:
    """'claude,codex' -> ['claude', 'codex']; each validated."""
    out = [actor(a) for a in str(by or "").split(",") if a.strip()]
    if len(out) > MAX_AGENTS:
        raise FlowError(f"at most {MAX_AGENTS} agents in one stage")
    if any(a in ("jarves", "owner") for a in out) and len(out) > 1:
        raise FlowError("jarves / owner cannot be mixed with agents in one stage")
    return out


def stage(kind: str = "", by: str = "", *, trusted: bool = False, **kw) -> dict:
    """One validated stage. trusted=True only for the owner's PC surfaces (cmd argv)."""
    kind = STAGE_WORDS.get(str(kind or "").strip(), str(kind or "").strip().lower())
    if kind not in KINDS:
        raise FlowError(f"unknown stage {kind[:20] or '(none)'} (one of: {', '.join(KINDS)})")
    acts = actors(by or DEFAULT_BY[kind])
    if kind in CHECK_KINDS + ("merge",) and acts[0] not in ("jarves", "owner"):
        raise FlowError(f"{kind} is done by jarves or owner, not by an agent")
    if kind == "gate" and acts != ["owner"]:
        raise FlowError("a gate is always the owner's")
    if kind == "release" and acts[0] not in ("owner", "jarves"):
        raise FlowError("release is done by the owner or by your release command (jarves)")
    if kind in ("plan", "diagnose") and len(acts) > 1:
        raise FlowError(f"{kind} is done by one agent (consult several when you plan the project)")
    out = {"kind": kind, "by": ",".join(acts), "skip": bool(kw.get("skip", False))}
    on_fail = str(kw.get("on_fail") or DEFAULT_ON_FAIL.get(kind, "stop"))
    if kind == "release":
        on_fail = "stop"                                   # a release is never retried or skipped by itself
    if not re.match(r"^(stop|skip|retry|back:(work|reproduce))$", on_fail):
        raise FlowError(f"on_fail is stop, skip, retry or back:work ({on_fail[:30]})")
    out["on_fail"] = on_fail
    if kind == "review":
        req = str(kw.get("require") or "all")
        if req not in ("all", "any", "majority"):
            raise FlowError("require is all, any or majority")
        out["require"] = req
    if kind == "debate":
        out["rounds"] = max(1, min(3, int(kw.get("rounds") or 2)))
        judge = str(kw.get("judge") or "")
        if judge:
            js = actors(judge)
            if len(js) != 1:
                raise FlowError("the judge is one agent or owner")
            out["judge"] = js[0]
        if kw.get("question"):
            out["question"] = str(kw["question"])[:600]
    if kind in ("gate",) and kw.get("question"):
        out["question"] = str(kw["question"])[:300]
    if kind in ("cmd",) or (kind in ("reproduce", "verify") and kw.get("cmd")):
        cmd = kw.get("cmd") or []
        if cmd and not trusted:
            raise FlowError("a command can only be set from the PC (jarvis project flow ... --cmd)")
        if kind == "cmd" and (not isinstance(cmd, list) or not cmd):
            raise FlowError("cmd needs a command (argv list)")
        if cmd:
            if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
                raise FlowError("cmd is an argv list")
            out["cmd"] = [x[:200] for x in cmd[:20]]
    return out


def parse(flow: str | list | None, *, trusted: bool = False) -> list[dict]:
    """A template name, a flow string, or a list of stage dicts -> validated stages (FlowError when invalid)."""
    return parse_with_warnings(flow, trusted=trusted)[0]


def parse_with_warnings(flow: str | list | None, *, trusted: bool = False) -> tuple[list[dict], list[str]]:
    if flow is None or flow == "":
        flow = TEMPLATES[DEFAULT_TEMPLATE]
    if isinstance(flow, list):
        st = []
        for x in flow:
            if isinstance(x, dict):
                if "kind" not in x:
                    raise FlowError("every stage needs a kind")
                st.append(stage(**{**x, "trusted": trusted}))
            else:
                st.append(_word(str(x), trusted))
    else:
        f = str(flow).strip()
        f = TEMPLATES.get(f.lower(), f)
        st = [_word(w, trusted) for w in f.replace("→", " ").replace("->", " ").split()]
    if not st:
        raise FlowError("the flow has no stages")
    if len(st) > MAX_STAGES:
        raise FlowError(f"at most {MAX_STAGES} stages")
    warns: list[str] = []
    active = [(i, s) for i, s in enumerate(st) if not s["skip"]]
    kinds = [s["kind"] for _, s in active]
    if kinds.count("merge") > 1:
        raise FlowError("one merge per flow")
    if "merge" in kinds:
        m = kinds.index("merge")
        if any(k in CODE_KINDS for k in kinds[m + 1:]):
            raise FlowError("code stages after the merge would never be merged: put them before it")
    elif any(k in CODE_KINDS for k in kinds):
        warns.append("no merge stage: the work stays on its branch and later tasks do not get it")
    for j, (i, s) in enumerate(active):
        rule = s["on_fail"]
        if rule.startswith("back:") and rule[5:] not in kinds[:j]:
            warns.append(f"{s['kind']}: no earlier {rule[5:]} stage to go back to; it stops instead")
            s["on_fail"] = "stop"
        if rule == "retry" and not any(k in CODE_KINDS for k in kinds[:j]):
            s["on_fail"] = "stop"                         # nothing to retry before it (e.g. a release flow's test)
    if "verify" in kinds and "reproduce" not in kinds:
        warns.append("verify without reproduce only runs the check")
    return st, warns


def _word(w: str, trusted: bool) -> dict:
    skip = w.startswith("-")
    w = w.lstrip("-")
    kind, _, by = w.partition(":")
    return stage(kind, by, skip=skip, trusted=trusted)


def describe(stages: list[dict]) -> str:
    """A short flow string for status lines (the stages themselves are stored as JSON)."""
    return " ".join(("-" if s.get("skip") else "") + s["kind"] + ("" if s["by"] == DEFAULT_BY[s["kind"]] else
                                                                  ":" + s["by"]) for s in stages)


def next_index(stages: list[dict], i: int) -> int:
    """The next stage that is not skipped after i (len = finished)."""
    j = i + 1
    while j < len(stages) and stages[j].get("skip"):
        j += 1
    return j


def first_index(stages: list[dict]) -> int:
    return next_index(stages, -1)


def find(stages: list[dict], kinds: tuple[str, ...], before: int) -> int | None:
    """The nearest non-skipped stage of one of `kinds` before index `before`."""
    for j in range(before - 1, -1, -1):
        if stages[j]["kind"] in kinds and not stages[j].get("skip"):
            return j
    return None


def remap(old: list[dict], new: list[dict], i: int) -> int:
    """Editing a flow mid-task: the current stage keeps pointing at the same kind (its n-th occurrence)."""
    if i >= len(old):
        return len(new)
    kind = old[i]["kind"]
    n = sum(1 for s in old[:i] if s["kind"] == kind)
    seen = 0
    for j, s in enumerate(new):
        if s["kind"] == kind:
            if seen == n:
                return j
            seen += 1
    return first_index(new)
