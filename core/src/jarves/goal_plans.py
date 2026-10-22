"""Goal plans (V5 pillar 2, executor): an owner-requested outcome becomes a bounded, inspectable, resumable plan.

A plan is a short list of steps; each step is ONE existing Skill tool with fixed arguments (written when the plan is
proposed, reviewed by the owner, never taken from tool output). Lifecycle::

    draft --(owner approves the plan once)--> ready --> running --> done
                                                 |         |-> paused (owner) / blocked (needs the owner) / failed
    cancelled (owner, any time)                  '- restart: running -> paused, nothing is repeated silently

Safety rules (enforced here and by the Skill registry, never by prompt text):

- A step calls ``registry.call`` as origin "model" on the route "plan": the central permission policy applies (a tool
  that is not allowed stays denied) and so does the untrusted-content guard (after a tool that returned text written
  by others, a later step that changes something is BLOCKED until the owner resumes the plan).
- Every consequential step (any tool that is not read-only) goes through the approval popup before it runs, unless
  the tool asks for its own confirmation (destructive tools, sends).
- Caps per plan: number of steps, total run time (time a human spent deciding is not counted) and AI requests
  (counted from the shared daily budget, which also stays in force). A cap stops the plan as ``blocked``.
- Checkpoint after each step in memory.db. After a Core restart a running plan is paused; a read-only step that was
  in flight is simply run again, a consequential one becomes ``unknown`` and the owner must retry or skip it, so an
  action is never repeated on a guess.
- Nothing runs without the owner's approval of the plan; pause / cancel / retry / skip / resume are the owner's.
"""
from __future__ import annotations

import asyncio
import json
import time

from pydantic import BaseModel, Field

from . import calm
from . import features as feat
from . import logs
from .preferences import looks_sensitive

FEATURE = "goal_plans"
feat.register(FEATURE, "תוכניות ליעדים", "Goal plans", True, skills=["goal_plans"],
              about="turns a goal into a short plan of existing tools; runs only after you approve it, asks before "
                    "every step that changes something, and can be paused, cancelled or resumed",
              targets=calm.TARGETS)

MAX_TITLE = 120
MAX_STEPS = 12
MAX_WHY = 160
MAX_ARGS_CHARS = 1500
MAX_OPEN = 20
DEFAULT_TIMEOUT_S = 1800.0          # run time of a whole plan
DEFAULT_MAX_AI = 20                 # AI requests the plan may use (on top of the daily budget)
STEP_GRACE_S = 5.0
EVIDENCE_CHARS = 240
RESULT_CHARS = 900
ROUTE = "plan"
OPEN = ("draft", "ready", "running", "paused", "blocked")
TERMINAL = ("done", "failed", "cancelled")
STATES = OPEN + TERMINAL
_RECURSION = ("goal_plan_",)        # a plan never plans


def _clean(text: object, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


def _short(text: object, n: int = EVIDENCE_CHARS) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


class PlanStore:
    """Persistence and the owner's controls. All state lives in memory.db (goal_plans / goal_plan_steps)."""

    def __init__(self, db, *, clock=time.time, known=None) -> None:
        self.db, self.clock = db, clock
        self.known = known                    # optional (tool name) -> Tool | None: validate against the live registry

    # ------------------------------------------------------------------ reading
    def _plan(self, pid: int) -> dict:
        r = self.db.row("SELECT * FROM goal_plans WHERE id=?", (int(pid),))
        if not r:
            raise KeyError(pid)
        return r

    def steps(self, pid: int) -> list[dict]:
        rows = self.db.rows("SELECT * FROM goal_plan_steps WHERE plan_id=? ORDER BY idx", (int(pid),))
        for s in rows:
            s["args"] = json.loads(s["args"] or "{}")
        return rows

    def get(self, pid: int) -> dict | None:
        try:
            p = self._plan(pid)
        except KeyError:
            return None
        st = self.steps(pid)
        p["steps"] = st
        p["progress"] = round(100 * sum(1 for s in st if s["status"] in ("done", "skipped")) / len(st)) if st else 0
        p["pending"] = [s["idx"] for s in st if s["status"] == "pending"]
        p["blocked_steps"] = [s["idx"] for s in st if s["status"] in ("blocked", "unknown", "failed")]
        return p

    def list(self, state: str = "") -> list[dict]:
        if state:
            rows = self.db.rows("SELECT id FROM goal_plans WHERE state=? ORDER BY id DESC", (state,))
        else:
            rows = self.db.rows("SELECT id FROM goal_plans ORDER BY id DESC LIMIT 100")
        return [self.get(r["id"]) for r in rows]

    # ------------------------------------------------------------------ creating
    def propose(self, title: str, steps: list[dict], *, goal_id: int = 0, timeout_s: float = DEFAULT_TIMEOUT_S,
                max_ai: int = DEFAULT_MAX_AI, by: str = "user") -> dict:
        t = _clean(title, MAX_TITLE)
        if not t:
            raise ValueError("a plan needs a title")
        if looks_sensitive(t):
            raise ValueError("this looks sensitive (secret, number, health or money detail): not stored")
        if not steps:
            raise ValueError("a plan needs at least one step")
        if len(steps) > MAX_STEPS:
            raise ValueError(f"at most {MAX_STEPS} steps in a plan")
        if len(self.db.rows("SELECT id FROM goal_plans WHERE state IN ('draft','ready','running','paused','blocked')")) >= MAX_OPEN:
            raise ValueError(f"at most {MAX_OPEN} open plans")
        if goal_id and not self.db.row("SELECT id FROM goals WHERE id=?", (int(goal_id),)):
            raise ValueError("no such goal")
        clean = []
        for i, s in enumerate(steps):
            tool, args, why = str(s.get("tool", "")).strip(), s.get("args") or {}, _clean(s.get("why", ""), MAX_WHY)
            if not tool or len(tool) > 64 or not isinstance(args, dict):
                raise ValueError(f"step {i + 1}: a tool name and its arguments (an object) are needed")
            if tool.startswith(_RECURSION):
                raise ValueError(f"step {i + 1}: a plan cannot contain plan tools")
            blob = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
            if len(blob) > MAX_ARGS_CHARS:
                raise ValueError(f"step {i + 1}: arguments too long")
            if looks_sensitive(why):
                raise ValueError(f"step {i + 1}: this looks sensitive: not stored")
            if self.known is not None:
                tl = self.known(tool)
                if tl is None:
                    raise ValueError(f"step {i + 1}: unknown or unavailable tool {tool}")
                try:
                    tl.args.model_validate(args)
                except Exception:
                    raise ValueError(f"step {i + 1}: invalid arguments for {tool}") from None
            clean.append((i, tool, blob, why))
        now = self.clock()
        pid = self.db.x("INSERT INTO goal_plans (goal_id, title, state, created, updated, timeout_s, max_ai) "
                        "VALUES (?,?,?,?,?,?,?)", (int(goal_id or 0), t, "draft", now, now,
                                                   max(30.0, min(float(timeout_s), 6 * 3600.0)),
                                                   max(0, min(int(max_ai), 200)))).lastrowid
        for i, tool, blob, why in clean:
            self.db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, args, why, status) VALUES (?,?,?,?,?,'pending')",
                      (pid, i, tool, blob, why))
        logs.info("goals", "PLAN_PROPOSED", "plan proposed", id=pid, count=len(clean), by=by)
        return self.get(pid)

    # ------------------------------------------------------------------ the owner's controls
    def _set(self, pid: int, state: str, **extra) -> None:
        cols = {"state": state, "updated": self.clock(), **extra}
        self.db.x(f"UPDATE goal_plans SET {', '.join(k + '=?' for k in cols)} WHERE id=?", (*cols.values(), int(pid)))

    def approve(self, pid: int, *, by: str = "user") -> dict:
        """The owner approved the whole plan, once. The caller has already shown the steps and got the approval."""
        p = self._plan(pid)
        if p["state"] != "draft":
            raise ValueError("only a draft plan can be approved")
        self._set(pid, "ready", approved_at=self.clock(), note="")
        logs.info("goals", "PLAN_APPROVED", "plan approved", id=pid, by=by)
        return self.get(pid)

    def pause(self, pid: int, *, by: str = "user") -> dict:
        p = self._plan(pid)
        if p["state"] not in ("ready", "running"):
            raise ValueError("only a ready or running plan can be paused")
        self._set(pid, "paused", note="paused by the owner")
        logs.info("goals", "PLAN_PAUSED", "plan paused", id=pid, by=by)
        return self.get(pid)

    def resume(self, pid: int, *, by: str = "user") -> dict:
        p = self._plan(pid)
        if p["state"] not in ("paused", "blocked"):
            raise ValueError("only a paused or blocked plan can be resumed")
        for s in self.steps(pid):
            if s["status"] == "unknown":
                raise ValueError(f"step {s['idx'] + 1} may already have run before the stop: retry or skip it first")
        self.db.x("UPDATE goal_plan_steps SET status='pending', error='' WHERE plan_id=? AND status='blocked'", (int(pid),))
        self._set(pid, "ready", note="", tainted=0)          # the owner's resume is a new turn: the guard is lifted
        logs.info("goals", "PLAN_RESUMED", "plan resumed", id=pid, by=by)
        return self.get(pid)

    def cancel(self, pid: int, *, by: str = "user") -> dict:
        p = self._plan(pid)
        if p["state"] in ("done", "cancelled"):
            raise ValueError("this plan is already finished")
        self.db.x("UPDATE goal_plan_steps SET status='skipped' WHERE plan_id=? AND status IN ('pending','blocked','unknown')",
                  (int(pid),))
        self._set(pid, "cancelled", note="cancelled by the owner", finished=self.clock())
        logs.info("goals", "PLAN_CANCELLED", "plan cancelled", id=pid, by=by)
        return self.get(pid)

    def retry(self, pid: int, step_idx: int | None = None, *, by: str = "user") -> dict:
        """Run a failed / blocked / unknown step again (the owner decided), then continue the plan."""
        p = self._plan(pid)
        if p["state"] not in ("failed", "blocked", "paused"):
            raise ValueError("only a failed, blocked or paused plan can be retried")
        n = 0
        for s in self.steps(pid):
            if s["status"] in ("failed", "blocked", "unknown") and step_idx in (None, s["idx"]):
                if s["attempts"] >= MAX_ATTEMPTS:
                    raise ValueError(f"step {s['idx'] + 1} was tried {MAX_ATTEMPTS} times already: skip it or cancel")
                self.db.x("UPDATE goal_plan_steps SET status='pending', error='' WHERE id=?", (s["id"],))
                n += 1
        if not n:
            raise ValueError("no failed or blocked step to retry")
        self._set(pid, "ready", note="", tainted=0, finished=None)
        logs.info("goals", "PLAN_RETRIED", "plan step retried", id=pid, count=n, by=by)
        return self.get(pid)

    def skip(self, pid: int, step_idx: int, *, by: str = "user") -> dict:
        p = self._plan(pid)
        if p["state"] in ("done", "cancelled"):
            raise ValueError("this plan is already finished")
        s = self.db.row("SELECT * FROM goal_plan_steps WHERE plan_id=? AND idx=?", (int(pid), int(step_idx)))
        if not s or s["status"] in ("done", "running"):
            raise ValueError("no such step to skip")
        self.db.x("UPDATE goal_plan_steps SET status='skipped', error='' WHERE id=?", (s["id"],))
        self._set(pid, "paused" if p["state"] == "failed" else p["state"], finished=None)
        logs.info("goals", "PLAN_STEP_SKIPPED", "plan step skipped", id=pid, step=int(step_idx), by=by)
        return self.get(pid)

    def delete(self, pid: int, *, by: str = "user") -> bool:
        p = self.db.row("SELECT state FROM goal_plans WHERE id=?", (int(pid),))
        if not p:
            return False
        if p["state"] in ("ready", "running"):
            raise ValueError("pause or cancel the plan first")
        self.db.x("DELETE FROM goal_plan_steps WHERE plan_id=?", (int(pid),))
        self.db.x("DELETE FROM goal_plans WHERE id=?", (int(pid),))
        logs.info("goals", "PLAN_DELETED", "plan deleted", id=pid, by=by)
        return True

    # ------------------------------------------------------------------ restart
    def recover(self) -> int:
        """After a Core start: a plan that was running is paused (the checkpoint is the step table). A read-only step
        that was in flight is run again; a consequential one is 'unknown' (the owner decides)."""
        n = 0
        for p in self.db.rows("SELECT id FROM goal_plans WHERE state='running'"):
            for s in self.db.rows("SELECT * FROM goal_plan_steps WHERE plan_id=? AND status='running'", (p["id"],)):
                st = "unknown" if s["consequential"] else "pending"
                self.db.x("UPDATE goal_plan_steps SET status=?, error=? WHERE id=?",
                          (st, "interrupted by a restart" if st == "unknown" else "", s["id"]))
            self._set(p["id"], "paused", note="interrupted by a restart: resume when ready")
            n += 1
        if n:
            logs.warn("goals", "PLAN_RECOVERED", "running plans paused after a restart", count=n)
        return n


MAX_ATTEMPTS = 3


def describe(plan: dict) -> str:
    """The text the owner approves: every step with its tool, arguments and whether it asks again."""
    lines = [f"{plan['title']} ({len(plan['steps'])} steps, up to {int(plan['timeout_s'] // 60)} min, "
             f"{plan['max_ai']} AI requests)"]
    for s in plan["steps"]:
        lines.append(f"{s['idx'] + 1}. {s['tool']} {_short(json.dumps(s['args'], ensure_ascii=False), 120)}"
                     + (f"  - {s['why']}" if s["why"] else ""))
    return "\n".join(lines)


def summary(plan: dict) -> str:
    done = sum(1 for s in plan["steps"] if s["status"] in ("done", "skipped"))
    out = f"{plan['id']}: {plan['title']} [{plan['state']}] {done}/{len(plan['steps'])}"
    if plan.get("note"):
        out += f" - {plan['note']}"
    return out


class PlanRunner:
    """Executes approved plans one step at a time against the Skill registry."""

    def __init__(self, store: PlanStore, registry, *, budget=None, clock=time.time, features=None,
                 sleep=asyncio.sleep) -> None:
        self.store, self.registry, self.budget, self.clock, self.features = store, registry, budget, clock, features
        self._sleep = sleep
        self._busy: set[int] = set()
        self._stop = False

    def _on(self) -> bool:
        return self.features is None or self.features.active(FEATURE)

    async def tick(self) -> int:
        """Advance every ready / running plan; returns how many were touched."""
        if not self._on():
            return 0
        n = 0
        for r in self.store.db.rows("SELECT id FROM goal_plans WHERE state IN ('ready','running') ORDER BY id"):
            if r["id"] not in self._busy:
                await self.run_plan(r["id"])
                n += 1
        return n

    async def run(self, poll_s: float = 2.0) -> None:
        while not self._stop:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logs.error("goals", "PLAN_TICK_FAILED", "plan executor tick failed", exc=True)
            await self._sleep(poll_s)

    def stop(self) -> None:
        self._stop = True

    def _stop_plan(self, pid: int, state: str, note: str, **extra) -> None:
        self.store._set(pid, state, note=_short(note, 200), **extra)
        logs.info("goals", "PLAN_STOPPED", "plan stopped", id=pid, state=state)

    async def run_plan(self, pid: int) -> dict:
        from .skills import ROUTE as SKILL_ROUTE
        if pid in self._busy:
            return self.store.get(pid)
        self._busy.add(pid)
        token = SKILL_ROUTE.set(ROUTE)
        try:
            return await self._run(pid)
        finally:
            SKILL_ROUTE.reset(token)
            self._busy.discard(pid)

    def _spent_ai(self, p: dict) -> int:
        if self.budget is None:
            return 0
        return max(0, self.budget.used("ai_requests") - int(p["ai_base"]))

    async def _run(self, pid: int) -> dict:
        st = self.store
        p = st._plan(pid)
        if p["state"] not in ("ready", "running"):
            return st.get(pid)
        if not p["started"]:
            st._set(pid, "running", started=self.clock(),
                    ai_base=self.budget.used("ai_requests") if self.budget is not None else 0)
        else:
            st._set(pid, "running")
        if not p["tainted"]:
            self.registry.clear_taint(ROUTE)       # a resumed plan is a new turn for the untrusted-content guard
        while True:
            p = st._plan(pid)
            if p["state"] != "running":                       # paused / cancelled by the owner between two steps
                return st.get(pid)
            step = next((s for s in st.steps(pid) if s["status"] == "pending"), None)
            if step is None:
                return self._finish(pid)
            if p["spent_s"] >= p["timeout_s"]:
                self._stop_plan(pid, "blocked", f"time cap of {int(p['timeout_s'] // 60)} min reached")
                return st.get(pid)
            if self.budget is not None:
                if 0 < self._spent_ai(p) >= p["max_ai"]:
                    self._stop_plan(pid, "blocked", f"cost cap of {p['max_ai']} AI requests reached")
                    return st.get(pid)
                if self.budget.remaining("ai_requests") < 1:
                    self._stop_plan(pid, "blocked", "the daily AI budget is used up")
                    return st.get(pid)
            tool = self.registry.get(step["tool"])
            if tool is None:
                self._block(pid, step, "tool not available (switched off or removed)")
                return st.get(pid)
            consequential = tool.permission != "read"
            if consequential and p["tainted"]:
                self._block(pid, step, "text written by others was read earlier in this plan: resume to continue")
                return st.get(pid)
            if consequential and not tool.confirm and tool.permission != "destructive":
                ok = await self.registry.ask(
                    f"Plan '{p['title']}', step {step['idx'] + 1}/{len(st.steps(pid))}: {step['tool']} "
                    f"{_short(json.dumps(step['args'], ensure_ascii=False), 160)}\nRun this step?",
                    title="goal_plan_step", risk="medium", kind="tool",
                    why=step["why"] or "A step of a plan you approved.")
                if not ok:
                    self._block(pid, step, "the owner did not approve this step")
                    return st.get(pid)
                p = st._plan(pid)
                if p["state"] != "running":                   # cancelled / paused while the popup was open
                    return st.get(pid)
            st.db.x("UPDATE goal_plan_steps SET status='running', consequential=?, started=?, attempts=attempts+1 "
                    "WHERE id=?", (1 if consequential else 0, self.clock(), step["id"]))
            t0 = self.clock()
            remaining = max(1.0, p["timeout_s"] - p["spent_s"])
            try:
                res = await asyncio.wait_for(self.registry.call(step["tool"], step["args"], origin="model"),
                                             min(remaining, tool.timeout_s + STEP_GRACE_S))
            except (asyncio.TimeoutError, TimeoutError):
                self._fail(pid, step, "the step timed out", t0)
                return st.get(pid)
            except asyncio.CancelledError:
                raise                                          # shutdown: the step stays 'running' for recover()
            except Exception:
                logs.error("goals", "PLAN_STEP_CRASH", "plan step raised", tool=step["tool"], exc=True)
                self._fail(pid, step, "the step failed unexpectedly", t0)
                return st.get(pid)
            ran = self.clock() - t0
            st.db.x("UPDATE goal_plans SET spent_s=spent_s+? WHERE id=?", (max(0.0, ran), pid))
            if res.ok:
                if tool.untrusted_output:
                    st.db.x("UPDATE goal_plans SET tainted=1 WHERE id=?", (pid,))
                st.db.x("UPDATE goal_plan_steps SET status='done', finished=?, evidence=?, error='' WHERE id=?",
                        (self.clock(), _short(res.message), step["id"]))
                logs.info("goals", "PLAN_STEP_DONE", "plan step done", id=pid, step=step["idx"], tool=step["tool"])
                continue
            if res.code == "TOOL_UNTRUSTED_TURN":
                st.db.x("UPDATE goal_plans SET tainted=1 WHERE id=?", (pid,))
                self._block(pid, step, "text written by others was read earlier in this plan: resume to continue")
                return st.get(pid)
            if res.code in ("NOT_APPROVED", "APPROVAL_REFUSED", "TOOL_NOT_CONFIRMED"):
                self._block(pid, step, "the owner did not approve this step")
                return st.get(pid)
            self._fail(pid, step, f"{res.code}: {_short(res.message, 120)}", t0, evidence=res.message)
            return st.get(pid)

    def _block(self, pid: int, step: dict, why: str) -> None:
        self.store.db.x("UPDATE goal_plan_steps SET status='blocked', error=? WHERE id=?", (_short(why, 200), step["id"]))
        self._stop_plan(pid, "blocked", f"step {step['idx'] + 1}: {why}")

    def _fail(self, pid: int, step: dict, why: str, t0: float, evidence: str = "") -> None:
        ran = max(0.0, self.clock() - t0)
        self.store.db.x("UPDATE goal_plans SET spent_s=spent_s+? WHERE id=? AND state='running'", (ran, pid))
        self.store.db.x("UPDATE goal_plan_steps SET status='failed', finished=?, error=?, evidence=? WHERE id=?",
                        (self.clock(), _short(why, 200), _short(evidence), step["id"]))
        self._stop_plan(pid, "failed", f"step {step['idx'] + 1}: {why}", finished=self.clock())

    def _finish(self, pid: int) -> dict:
        st = self.store
        steps = st.steps(pid)
        lines = [f"{s['idx'] + 1}. {s['tool']}: " + (s["evidence"] or s["status"]) for s in steps]
        text = _short("; ".join(lines), RESULT_CHARS)
        done = sum(1 for s in steps if s["status"] == "done")
        st._set(pid, "done", finished=self.clock(), note="", result=f"{done}/{len(steps)} steps done. {text}")
        logs.info("goals", "PLAN_DONE", "plan done", id=pid, count=done)
        return st.get(pid)


# ---------------------------------------------------------------------- voice / agent tools (tiny on purpose)
class ProposeArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE)
    steps: list[dict] = Field(..., min_length=1, max_length=MAX_STEPS, description="[{tool, args, why}]")


class PlanIdArgs(BaseModel):
    id: int = Field(..., ge=1)


class ShowArgs(BaseModel):
    id: int = Field(0, ge=0)


def make_skill(store: PlanStore, registry=None):
    from .skills import Skill, Tool, ToolResult

    class PlanSkill(Skill):
        name = "goal_plans"
        description = "plans of tool steps for the owner's goals"

        def tools(self) -> list:
            async def propose(a: ProposeArgs):
                for s in a.steps:
                    t = registry.get(str(s.get("tool", ""))) if registry is not None else None
                    if t is not None and t.permission == "action" and t.name not in registry.allowed_actions:
                        return ToolResult(False, f"{t.name} is not allowed in a plan", code="PLAN_BAD")
                try:
                    p = store.propose(a.title, a.steps, by="voice")
                except ValueError as e:
                    return ToolResult(False, str(e), code="PLAN_BAD")
                return ToolResult(True, f"plan {p['id']} drafted; it runs only after the owner approves it",
                                  {"plan": p["id"], "steps": describe(p)})

            async def show(a: ShowArgs):
                if a.id:
                    p = store.get(a.id)
                    if not p:
                        return ToolResult(False, "no such plan", code="PLAN_NOT_FOUND")
                    return ToolResult(True, summary(p), {"plan": p["id"], "result": p["result"],
                                                         "pending": p["pending"], "blocked": p["blocked_steps"]})
                rows = [p for p in store.list() if p["state"] in OPEN]
                return ToolResult(True, "; ".join(summary(p) for p in rows) or "no open plans",
                                  {"plans": [{"id": p["id"], "state": p["state"], "progress": p["progress"]} for p in rows]})

            async def run(a: PlanIdArgs):
                p = store.get(a.id)
                if not p:
                    return ToolResult(False, "no such plan", code="PLAN_NOT_FOUND")
                if p["state"] != "draft":
                    return ToolResult(False, f"plan is {p['state']}, not a draft", code="PLAN_BAD")
                if registry is None or not await registry.ask("Run this plan?\n" + describe(p), title="goal_plan",
                                                              risk="medium", kind="tool",
                                                              why="You asked JARVES to carry out a plan."):
                    return ToolResult(False, "not started: the owner did not approve the plan", code="NOT_APPROVED")
                store.approve(a.id, by="voice")
                return ToolResult(True, "plan approved; it starts now and asks before each step that changes something")

            return [
                Tool("goal_plan_propose", "Draft a plan of tool steps; does not run it.",
                     propose, ProposeArgs, permission="action"),
                Tool("goal_plan_show", "Read plan status and result.", show, ShowArgs,
                     permission="read"),
                Tool("goal_plan_run", "Owner approves a drafted plan, then it runs.", run, PlanIdArgs,
                     permission="action", timeout_s=300.0),
            ]

    return PlanSkill()


ALLOWED_ACTIONS = {"goal_plan_propose", "goal_plan_run"}


# ---------------------------------------------------------------------- dashboard: the Goal plans page
def _store(ctx) -> PlanStore:
    from .userdata import ctx_db
    s = getattr(ctx, "_plan_store", None)
    if s is None:
        s = PlanStore(ctx_db(ctx))
        ctx._plan_store = s
    return s


def api_plans(ctx, m, body, q=None):
    return 200, {"plans": _store(ctx).list()}


def api_plan_add(ctx, m, body):
    b = body or {}
    try:
        return 200, {"plan": _store(ctx).propose(str(b.get("title", "")), list(b.get("steps") or []),
                                                 goal_id=int(b.get("goal_id") or 0), by="dashboard")}
    except (ValueError, TypeError, AttributeError) as e:
        return 400, {"error": str(e)}


def api_plan_op(ctx, m, body):
    st, b = _store(ctx), body or {}
    op = b.get("op")
    try:
        pid = int(b.get("id", 0) or 0)
        idx = b.get("step")
        idx = int(idx) if idx is not None else None
    except (TypeError, ValueError):
        return 400, {"error": "id must be a number"}
    try:
        if op == "approve":
            p = st.get(pid)
            if not p:
                return 404, {"error": "no such plan"}
            if p["state"] != "draft":
                return 400, {"error": "only a draft plan can be approved"}
            from . import approvals
            ok, _why = approvals.ask_owner(ctx.home, "Run this plan?\n" + describe(p), title="dashboard_goal_plan",
                                           risk="medium", kind="tool",
                                           why="You pressed Approve on the dashboard Goal plans page.",
                                           details={"plan": pid})
            if not ok:
                return 403, dict(approvals.APPROVAL_REFUSED)
            return 200, {"plan": st.approve(pid, by="dashboard")}
        if op == "pause":
            return 200, {"plan": st.pause(pid, by="dashboard")}
        if op == "resume":
            return 200, {"plan": st.resume(pid, by="dashboard")}
        if op == "cancel":
            return 200, {"plan": st.cancel(pid, by="dashboard")}
        if op == "retry":
            return 200, {"plan": st.retry(pid, idx, by="dashboard")}
        if op == "skip":
            return 200, {"plan": st.skip(pid, idx if idx is not None else -1, by="dashboard")}
        if op == "delete":
            return 200, {"deleted": st.delete(pid, by="dashboard")}
    except KeyError:
        return 404, {"error": "no such plan"}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 400, {"error": "op must be approve, pause, resume, cancel, retry, skip or delete"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/goal-plans", api_plans)
    register("POST", r"/api/goal-plans", api_plan_add)
    register("POST", r"/api/goal-plans/op", api_plan_op)
