"""Planning: the planner prompt, strict-JSON parsing and the plan validator (small tasks only, no cycles).

The planner is a read-only agent run (or the owner's own list). Its answer is untrusted data: nothing is run until the
owner approves the proposed tasks, and the validator below rejects anything that would break the small-context rules.
"""
from __future__ import annotations

import json
import re

from ..orchestrator.envelope import clean
from .cards import ACCEPT_MAX, SPEC_MAX

MAX_TASKS, MAX_SCOPE, TITLE_MAX = 12, 8, 100
_SCOPE_OK = re.compile(r"^[A-Za-z0-9_./*\-\[\] ]{1,120}$")

PLANNER_PROMPT = (
    "You are the planner of a software project. Read the project folder (read-only: do not change anything) and split "
    "the goal into SMALL tasks. Each task is done by a separate agent with a fresh, small context window, so each must "
    "fit in a few files and one sitting; tasks that can run in parallel must touch different files. "
    "Answer with ONLY a JSON object, no prose:\n"
    '{"tasks":[{"id":1,"title":"short","spec":"what to do, concrete, max 1000 characters",'
    '"acceptance":"how to tell it is done, max 250 characters","scope":["src/a.py","tests/test_a.py"],'
    '"deps":[],"size":"S"}]}\n'
    f"Rules: at most {MAX_TASKS} tasks; ids are 1..N; deps list ids of tasks that must finish first (no cycles); "
    f"scope lists up to {MAX_SCOPE} files or globs the task may change; size is S or M (never L: split it).")


class PlanError(ValueError):
    pass


def extract_json(text: str) -> dict:
    """The planner's answer -> a dict. Accepts fenced JSON or JSON inside prose; the first balanced object that has a
    'tasks' list wins."""
    s = text or ""
    for m in re.finditer(r"\{", s):
        depth, i = 0, m.start()
        in_str = esc = False
        for j in range(i, len(s)):
            ch = s[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        d = json.loads(s[i: j + 1])
                    except ValueError:
                        break
                    if isinstance(d, dict) and isinstance(d.get("tasks"), list):
                        return d
                    break
    raise PlanError("the planner did not return a JSON plan with a tasks list")


DEFAULT_ACCEPT = "the request in the task is satisfied"


def parse_spec(s: str) -> tuple[str, str]:
    """'codex' -> ('codex', ''); 'opencode:openrouter/x/y:free' -> ('opencode', 'openrouter/x/y:free')."""
    name, _, model = str(s or "").strip().partition(":")
    return name.strip().lower(), model.strip()


def planner_prompt(brief: str, goal: str, how: str = "", context: str = "") -> str:
    """The planner request. `how` is the owner's own instruction for splitting (it wins over the defaults);
    `context` is what already exists (finished work, open tasks, recent commits) so a started project is continued."""
    out = PLANNER_PROMPT
    if brief:
        out += f"\n\nProject brief:\n{brief}"
    if context:
        out += ("\n\nWhat already exists (do not redo finished work; plan only what is missing):\n" + context)
    if how:
        out += ("\n\nThe owner's instruction for HOW to split the work (follow it; it overrides the defaults above "
                "except the JSON format and the size rules):\n" + how)
    return out + f"\n\nGoal (from the owner):\n{goal}"


SYNTH_PROMPT = (
    "Several agents each proposed a split of the same goal into tasks (below, as data). Produce ONE final plan: keep "
    "what they agree on, resolve conflicts with your own judgement, drop duplicates, and keep tasks small and the "
    "dependencies correct. Use exactly the same JSON format as the proposals, no prose.")


def validate(raw: dict, lenient: bool = False) -> list[dict]:
    """-> normalized tasks [{id, title, spec, acceptance, scope, deps(list of plan ids), warn}] or PlanError.
    lenient (the owner's own list): a missing acceptance gets a default and size L is only a warning."""
    items = raw.get("tasks")
    if not isinstance(items, list) or not items:
        raise PlanError("the plan has no tasks")
    if len(items) > MAX_TASKS:
        raise PlanError(f"too many tasks ({len(items)}); at most {MAX_TASKS} per plan: plan the next phase later")
    out, ids = [], set()
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise PlanError(f"task {i} is not an object")
        tid = it.get("id", i)
        if not isinstance(tid, int) or isinstance(tid, bool) or tid in ids:
            raise PlanError(f"task {i}: bad or repeated id")
        ids.add(tid)
        title = clean(it.get("title", ""), TITLE_MAX)
        spec = clean(it.get("spec", ""), SPEC_MAX + 1, keep_code=True)
        acc = clean(it.get("acceptance", ""), ACCEPT_MAX + 1)
        if not title or not spec:
            raise PlanError(f"task {tid}: title and spec are required")
        if len(spec) > SPEC_MAX or len(str(it.get("spec", ""))) > SPEC_MAX + 200:
            raise PlanError(f"task {tid}: spec longer than {SPEC_MAX} characters: split the task")
        warn = []
        if not acc:
            if not lenient:
                raise PlanError(f"task {tid}: acceptance is required")
            acc = DEFAULT_ACCEPT
        if str(it.get("size", "S")).upper() not in ("S", "M"):
            if not lenient:
                raise PlanError(f"task {tid}: too big (size L): split it")
            warn.append("marked large: consider splitting it")
        scope = it.get("scope") or []
        if not isinstance(scope, list) or len(scope) > MAX_SCOPE or not all(
                isinstance(x, str) and _SCOPE_OK.match(x) and ".." not in x.split("/") and not x.startswith(("/", "\\"))
                for x in scope):
            raise PlanError(f"task {tid}: scope must be at most {MAX_SCOPE} relative paths or globs inside the project")
        deps = it.get("deps") or []
        if not isinstance(deps, list) or not all(isinstance(d, int) and not isinstance(d, bool) for d in deps):
            raise PlanError(f"task {tid}: deps must be a list of task ids")
        out.append({"id": tid, "title": title, "spec": spec, "acceptance": acc, "scope": scope, "deps": list(deps),
                    "warn": warn})
    for t in out:
        for d in t["deps"]:
            if d not in ids or d == t["id"]:
                raise PlanError(f"task {t['id']}: unknown or self dependency {d}")
    _no_cycle(out)
    return out


def _no_cycle(tasks: list[dict]) -> None:
    deps = {t["id"]: list(t["deps"]) for t in tasks}
    seen: dict[int, int] = {}                       # 1 = on the stack, 2 = done

    def visit(n: int) -> None:
        if seen.get(n) == 2:
            return
        if seen.get(n) == 1:
            raise PlanError("the plan has a dependency cycle")
        seen[n] = 1
        for d in deps[n]:
            visit(d)
        seen[n] = 2
    for n in deps:
        visit(n)


def scopes_overlap(a: list[str], b: list[str]) -> bool:
    """Two tasks may run in parallel only if their scopes cannot touch the same file. An empty scope means 'anywhere',
    so it overlaps with everything. Globs overlap when one's literal prefix contains the other's."""
    if not a or not b:
        return True
    def prefix(p: str) -> str:
        return re.split(r"[*?\[]", p, maxsplit=1)[0].rstrip("/")
    for x in a:
        for y in b:
            px, py = prefix(x), prefix(y)
            if not px or not py or px == py or px.startswith(py + "/") or py.startswith(px + "/") or \
                    px.startswith(py) and "*" in y or py.startswith(px) and "*" in x:
                return True
    return False
