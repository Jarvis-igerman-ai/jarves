"""Goals (V5 pillar, software slice): outcomes the owner sets by voice, text, dashboard or CLI; the Core tracks progress.

A goal has a title, optional details and target date, and optional steps. Progress is steps done / steps (or a
percent the owner sets when there are no steps). JARVES does NOT run goals on its own: it never calls a tool, sends a
message or spends money because of a goal. A step that involves other people or the outside world is marked
`outward`; the voice tool then asks the owner before recording it as done, and the actual action still goes through
the normal tools and their own approvals. The weekly check-in exists only as a tool/page the owner asks for: no
scheduler job, no alert, no notification. Local only, in memory.db `goals` / `goal_steps`, editable and deletable.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

from pydantic import BaseModel, Field

from . import calm
from . import features as feat
from . import logs
from .preferences import looks_sensitive
from .userdata import UserDB

MAX_TITLE = 120
MAX_DETAILS = 500
MAX_STEP = 160
MAX_STEPS = 30
MAX_GOALS = 50
STATUSES = ("active", "paused", "done", "dropped")
FEATURE = "goal_done_line"
feat.register(FEATURE, "משפט קצר כשיעד הושלם", "One calm line when a goal is finished", True,
              about="when you finish a goal JARVES says one short calm line, only while you are at the PC and never "
                    "between 22:00 and 07:00; switch off for silence",
              targets=calm.TARGETS)
CHEER = {"en": "Done, nice work.", "he": "סיימת, כל הכבוד."}      # fixed lines, nothing else is ever said
CHEER_MAX_AGE_S = 600                                              # a finished goal older than this is not announced
STALE_DAYS = 7                      # "no progress for a week" in a check-in


def _clean(text: str, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


def _date(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise ValueError("the target date must look like 2026-12-31") from None


class Goals:
    def __init__(self, db: UserDB, *, clock=time.time) -> None:
        self.db, self.clock = db, clock

    # ------------------------------------------------------------------ writing
    def add(self, title: str, details: str = "", target_date: str = "", steps: list[str] | None = None, *,
            by: str = "user") -> dict:
        t, d = _clean(title, MAX_TITLE), _clean(details, MAX_DETAILS)
        if not t:
            raise ValueError("a goal needs a title")
        if looks_sensitive(t) or looks_sensitive(d) or any(looks_sensitive(s) for s in steps or []):
            raise ValueError("this looks sensitive (secret, number, health or money detail): not stored")
        if len(self.db.rows("SELECT id FROM goals WHERE status IN ('active','paused')")) >= MAX_GOALS:
            raise ValueError(f"at most {MAX_GOALS} open goals")
        if self.db.row("SELECT id FROM goals WHERE lower(title)=? AND status IN ('active','paused')", (t.lower(),)):
            raise ValueError("a goal with this title is already open")
        td, now = _date(target_date), self.clock()
        gid = self.db.x("INSERT INTO goals (title, details, target_date, created, updated) VALUES (?,?,?,?,?)",
                        (t, d, td, now, now)).lastrowid
        for s in (steps or [])[:MAX_STEPS]:
            if _clean(s, MAX_STEP):
                self.add_step(gid, s, by=by)
        logs.info("goals", "GOAL_ADDED", "goal added", id=gid, by=by)
        return self.get(gid)

    def update(self, gid: int, *, title: str | None = None, details: str | None = None,
               target_date: str | None = None, percent: int | None = None, by: str = "user") -> dict:
        g = self._row(gid)
        t = _clean(title, MAX_TITLE) if title is not None else g["title"]
        d = _clean(details, MAX_DETAILS) if details is not None else g["details"]
        if not t:
            raise ValueError("a goal needs a title")
        if looks_sensitive(t) or looks_sensitive(d):
            raise ValueError("this looks sensitive (secret, number, health or money detail): not stored")
        td = _date(target_date) if target_date is not None else g["target_date"]
        pc = g["percent"] if percent is None else max(0, min(100, int(percent)))
        self.db.x("UPDATE goals SET title=?, details=?, target_date=?, percent=?, updated=? WHERE id=?",
                  (t, d, td, pc, self.clock(), gid))
        logs.info("goals", "GOAL_UPDATED", "goal updated", id=gid, by=by)
        return self.get(gid)

    def set_status(self, gid: int, status: str, *, by: str = "user") -> dict:
        if status not in STATUSES:
            raise ValueError("status must be one of: " + ", ".join(STATUSES))
        self._row(gid)
        now = self.clock()
        self.db.x("UPDATE goals SET status=?, updated=?, done_at=? WHERE id=?",
                  (status, now, now if status == "done" else None, gid))
        logs.info("goals", "GOAL_STATUS", "goal status changed", id=gid, status=status, by=by)
        return self.get(gid)

    def add_step(self, gid: int, text: str, outward: bool = False, *, by: str = "user") -> dict:
        self._row(gid)
        t = _clean(text, MAX_STEP)
        if not t:
            raise ValueError("a step needs text")
        if looks_sensitive(t):
            raise ValueError("this looks sensitive (secret, number, health or money detail): not stored")
        if len(self.db.rows("SELECT id FROM goal_steps WHERE goal_id=?", (gid,))) >= MAX_STEPS:
            raise ValueError(f"at most {MAX_STEPS} steps per goal")
        now = self.clock()
        self.db.x("INSERT INTO goal_steps (goal_id, text, outward, created) VALUES (?,?,?,?)",
                  (gid, t, 1 if outward else 0, now))
        self.db.x("UPDATE goals SET updated=? WHERE id=?", (now, gid))
        logs.info("goals", "GOAL_STEP_ADDED", "goal step added", id=gid, by=by)
        return self.get(gid)

    def step(self, sid: int) -> dict | None:
        return self.db.row("SELECT * FROM goal_steps WHERE id=?", (sid,))

    def set_step(self, sid: int, done: bool, *, by: str = "user") -> dict:
        s = self.step(sid)
        if not s:
            raise KeyError(sid)
        now = self.clock()
        self.db.x("UPDATE goal_steps SET done=?, done_at=? WHERE id=?", (1 if done else 0, now if done else None, sid))
        self.db.x("UPDATE goals SET updated=? WHERE id=?", (now, s["goal_id"]))
        logs.info("goals", "GOAL_STEP_SET", "goal step changed", id=sid, by=by)
        return self.get(s["goal_id"])

    def remove_step(self, sid: int, *, by: str = "user") -> dict:
        s = self.step(sid)
        if not s:
            raise KeyError(sid)
        self.db.x("DELETE FROM goal_steps WHERE id=?", (sid,))
        self.db.x("UPDATE goals SET updated=? WHERE id=?", (self.clock(), s["goal_id"]))
        logs.info("goals", "GOAL_STEP_REMOVED", "goal step removed", id=sid, by=by)
        return self.get(s["goal_id"])

    def delete(self, gid: int, *, by: str = "user") -> bool:
        self.db.x("DELETE FROM goal_steps WHERE goal_id=?", (gid,))
        n = self.db.x("DELETE FROM goals WHERE id=?", (gid,)).rowcount
        logs.info("goals", "GOAL_DELETED", "goal deleted", id=gid, by=by)
        return bool(n)

    # ------------------------------------------------------------------ reading
    def _row(self, gid: int) -> dict:
        g = self.db.row("SELECT * FROM goals WHERE id=?", (gid,))
        if not g:
            raise KeyError(gid)
        return g

    def get(self, gid: int) -> dict | None:
        g = self.db.row("SELECT * FROM goals WHERE id=?", (gid,))
        return self._view(g) if g else None

    def _view(self, g: dict) -> dict:
        steps = self.db.rows("SELECT id, text, done, outward FROM goal_steps WHERE goal_id=? ORDER BY id", (g["id"],))
        for s in steps:
            s["done"], s["outward"] = bool(s["done"]), bool(s["outward"])
        done = sum(1 for s in steps if s["done"])
        progress = round(100 * done / len(steps)) if steps else int(g["percent"])
        if g["status"] == "done":
            progress = 100
        return {**g, "steps": steps, "progress": progress, "steps_done": done,
                "days_left": self._days_left(g["target_date"])}

    def _days_left(self, target: str) -> int | None:
        if not target:
            return None
        today = datetime.fromtimestamp(self.clock(), timezone.utc).date()
        return (date.fromisoformat(target) - today).days

    def list(self, status: str = "") -> list[dict]:
        rows = self.db.rows("SELECT * FROM goals" + (" WHERE status=?" if status else "") + " ORDER BY id",
                            (status,) if status else ())
        return [self._view(g) for g in rows]

    def checkin(self, *, by: str = "user") -> dict:
        """The weekly check-in, produced ONLY when asked (voice tool, dashboard button, CLI): a read-only summary."""
        now = self.clock()
        out = []
        for g in self.list("active"):
            idle = (now - g["updated"]) / 86400
            remaining = [s["text"] for s in g["steps"] if not s["done"]][:3]
            out.append({"id": g["id"], "title": g["title"], "progress": g["progress"], "days_left": g["days_left"],
                        "idle_days": int(idle), "stalled": idle >= STALE_DAYS, "overdue": (g["days_left"] or 0) < 0
                        and g["days_left"] is not None, "next_steps": remaining})
            self.db.x("UPDATE goals SET last_checkin=? WHERE id=?", (now, g["id"]))
        logs.info("goals", "GOAL_CHECKIN", "goal check-in on request", n=len(out), by=by)
        return {"goals": out}


class GoalCheer:
    """ONE short calm line when a goal has been finished (status done), whoever finished it (voice, dashboard, CLI):
    polled from the Core tick. Spoken only while `at_pc()` and not `quiet()`; a goal finished at night or while the
    owner is away is simply not announced once CHEER_MAX_AGE_S has passed. Never touches the goal."""
    CURSOR = "goals.cheer_cursor"

    def __init__(self, goals: Goals, *, quiet, at_pc, say, clock=time.time) -> None:
        self.goals, self.quiet, self.at_pc, self.say, self.clock = goals, quiet, at_pc, say, clock
        if not float(goals.db.setting(self.CURSOR) or 0):
            goals.db.set_setting(self.CURSOR, clock(), actor="system")      # goals finished before now: never cheered

    async def check(self) -> str:
        """The line that was spoken ('' = nothing)."""
        db, now = self.goals.db, self.clock()
        cursor = float(db.setting(self.CURSOR) or 0)
        rows = db.rows("SELECT id, title, done_at FROM goals WHERE status='done' AND done_at > ? ORDER BY done_at",
                       (cursor,))
        fresh = [r for r in rows if now - r["done_at"] <= CHEER_MAX_AGE_S]
        if not fresh:
            if rows:
                db.set_setting(self.CURSOR, rows[-1]["done_at"], actor="system")
            return ""
        if self.quiet() or not self.at_pc():
            return ""                                      # wait; it expires by itself
        from .calm import uses_hebrew
        last = fresh[-1]
        steps = " ".join(x["text"] for x in db.rows("SELECT text FROM goal_steps WHERE goal_id=?", (last["id"],)))
        line = CHEER["he" if uses_hebrew(last["title"], steps) else "en"]
        if not await self.say(line):
            return ""
        db.set_setting(self.CURSOR, last["done_at"], actor="system")
        logs.info("goals", "GOAL_DONE_LINE", "goal finished line spoken", n=len(fresh))
        return line


def checkin_text(c: dict) -> str:
    if not c["goals"]:
        return "no active goals"
    parts = []
    for g in c["goals"]:
        s = f"{g['title']}: {g['progress']}%"
        if g["overdue"]:
            s += ", past its date"
        elif g["days_left"] is not None:
            s += f", {g['days_left']} days left"
        if g["stalled"]:
            s += f", no movement for {g['idle_days']} days"
        if g["next_steps"]:
            s += ", next: " + g["next_steps"][0]
        parts.append(s)
    return "; ".join(parts)


# ---------------------------------------------------------------------- voice / agent tools
class AddArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE, description="the outcome, e.g. 'run a 10k by March'")
    details: str = Field("", max_length=MAX_DETAILS)
    target_date: str = Field("", max_length=10, description="YYYY-MM-DD or empty")
    steps: list[str] = Field(default_factory=list, max_length=MAX_STEPS, description="optional first steps")


class GoalArgs(BaseModel):
    id: int = Field(..., ge=1)


class StepAddArgs(BaseModel):
    id: int = Field(..., ge=1, description="goal id")
    text: str = Field(..., min_length=1, max_length=MAX_STEP)
    outward: bool = Field(False, description="True when the step contacts other people or acts outside the PC")


class StepArgs(BaseModel):
    step_id: int = Field(..., ge=1)
    done: bool = True


class StatusArgs(BaseModel):
    id: int = Field(..., ge=1)
    status: str = Field(..., max_length=10, description="active, paused, done or dropped")


class ProgressArgs(BaseModel):
    id: int = Field(..., ge=1)
    percent: int = Field(..., ge=0, le=100, description="only for a goal without steps")


class NoArgs(BaseModel):
    pass


def make_skill(goals: Goals, registry=None):
    from .skills import Skill, Tool, ToolResult

    def missing():
        return ToolResult(False, "no such goal", code="GOAL_NOT_FOUND")

    class GoalSkill(Skill):
        name = "goals"
        description = "the owner's goals: set, track progress with steps, check in on request, edit, delete"

        def tools(self) -> list:
            async def add(a: AddArgs):
                try:
                    g = goals.add(a.title, a.details, a.target_date, a.steps, by="voice")
                except ValueError as e:
                    return ToolResult(False, str(e), code="GOAL_BAD")
                return ToolResult(True, f"goal set: {g['title']}", {"goal": g})

            async def show(_a: NoArgs):
                rows = [g for g in goals.list() if g["status"] in ("active", "paused")]
                data = {"goals": [{k: g[k] for k in ("id", "title", "status", "progress", "days_left")} for g in rows]}
                return ToolResult(True, "; ".join(f"{g['title']} {g['progress']}%" for g in rows) or "no open goals",
                                  data)

            async def checkin(_a: NoArgs):
                c = goals.checkin(by="voice")
                return ToolResult(True, checkin_text(c), c)

            async def step_add(a: StepAddArgs):
                try:
                    return ToolResult(True, "step added", {"goal": goals.add_step(a.id, a.text, a.outward, by="voice")})
                except KeyError:
                    return missing()
                except ValueError as e:
                    return ToolResult(False, str(e), code="GOAL_BAD")

            async def step_done(a: StepArgs):
                s = goals.step(a.step_id)
                if not s:
                    return ToolResult(False, "no such step", code="GOAL_NOT_FOUND")
                if s["outward"] and a.done and registry is not None:
                    # contacting people / acting outside is never recorded on the model's word alone
                    if not await registry.ask("לסמן את הצעד כבוצע? / Mark this outward-facing step done?\n" + s["text"],
                                              title="goal_outward_step", risk="medium", kind="tool",
                                              why="A goal step that involves other people or the outside world."):
                        return ToolResult(False, "not recorded: the owner did not approve", code="NOT_APPROVED")
                g = goals.set_step(a.step_id, a.done, by="voice")
                return ToolResult(True, f"{g['title']}: {g['progress']}%", {"goal": g})

            async def status(a: StatusArgs):
                try:
                    g = goals.set_status(a.id, a.status, by="voice")
                except KeyError:
                    return missing()
                except ValueError as e:
                    return ToolResult(False, str(e), code="GOAL_BAD")
                return ToolResult(True, f"{g['title']}: {g['status']}", {"goal": g})

            async def progress(a: ProgressArgs):
                try:
                    g = goals.update(a.id, percent=a.percent, by="voice")
                except KeyError:
                    return missing()
                return ToolResult(True, f"{g['title']}: {g['progress']}%", {"goal": g})

            async def delete(a: GoalArgs):
                return (ToolResult(True, "goal deleted") if goals.delete(a.id, by="voice") else missing())

            async def delete_impact(a: GoalArgs):
                g = goals.get(a.id)
                return (1 if g else 0), f"למחוק את היעד '{g['title'] if g else a.id}'? / Delete this goal?"

            return [
                Tool("goal_add", "Set a goal the owner asked for ('אני רוצה לרוץ 10 ק\"מ עד מרץ'), optionally with steps.",
                     add, AddArgs, permission="action"),
                Tool("goal_show", "Read the owner's open goals and their progress.", show, NoArgs, permission="read"),
                Tool("goal_checkin", "The owner ASKED for a check-in on their goals: progress, stalled or overdue ones. "
                     "Never offer or run one unprompted.", checkin, NoArgs, permission="action"),
                Tool("goal_step_add", "Add a step to a goal (outward=true if it contacts others or acts outside).",
                     step_add, StepAddArgs, permission="action"),
                Tool("goal_step_done", "Mark a goal step done (or not done). Outward-facing steps ask the owner.",
                     step_done, StepArgs, permission="action", timeout_s=120.0),
                Tool("goal_status", "Pause, resume, finish or drop a goal.", status, StatusArgs, permission="action"),
                Tool("goal_progress", "Set the percent of a goal that has no steps.", progress, ProgressArgs,
                     permission="action"),
                Tool("goal_delete", "Delete a goal and its steps (asks the owner first).", delete, GoalArgs,
                     permission="destructive", impact=delete_impact),
            ]

    return GoalSkill()


ALLOWED_ACTIONS = {"goal_add", "goal_checkin", "goal_step_add", "goal_step_done", "goal_status", "goal_progress"}


# ---------------------------------------------------------------------- dashboard: the Goals page
def _ctx(ctx) -> Goals:
    from .userdata import ctx_db
    g = getattr(ctx, "_goals", None)
    if g is None:
        g = Goals(ctx_db(ctx))              # the same store the voice tools use
        ctx._goals = g
    return g


def api_goals(ctx, m, body, q=None):
    return 200, {"goals": _ctx(ctx).list()}


def api_goals_checkin(ctx, m, body):
    c = _ctx(ctx).checkin(by="dashboard")          # a click on the button = the owner asked
    return 200, {**c, "text": checkin_text(c)}


def api_goal_add(ctx, m, body):
    b = body or {}
    steps = b.get("steps")
    if isinstance(steps, str):
        steps = [s for s in steps.replace("\n", ",").split(",") if s.strip()]
    try:
        return 200, {"goal": _ctx(ctx).add(str(b.get("title", "")), str(b.get("details", "")),
                                           str(b.get("target_date", "")), steps or [], by="dashboard")}
    except ValueError as e:
        return 400, {"error": str(e)}


def api_goal_op(ctx, m, body):
    g, b = _ctx(ctx), body or {}
    op = b.get("op")
    try:
        gid = int(b.get("id", 0) or 0)
        sid = int(b.get("step_id", 0) or 0)
    except (TypeError, ValueError):
        return 400, {"error": "id must be a number"}
    try:
        if op == "delete":
            goal = g.get(gid)
            if not goal:
                return 404, {"error": "no such goal"}
            from . import approvals
            ok, _why = approvals.ask_owner(
                ctx.home, f"Delete the goal '{goal['title']}' and its {len(goal['steps'])} steps?",
                title="dashboard_delete_goal", risk="medium", kind="tool",
                why="You pressed Delete on the dashboard Goals page.", details={"goal": gid})
            if not ok:
                return 403, dict(approvals.APPROVAL_REFUSED)
            return 200, {"deleted": g.delete(gid, by="dashboard")}
        if op == "update":
            return 200, {"goal": g.update(gid, title=b.get("title"), details=b.get("details"),
                                          target_date=b.get("target_date"), percent=b.get("percent"), by="dashboard")}
        if op == "status":
            return 200, {"goal": g.set_status(gid, str(b.get("status", "")), by="dashboard")}
        if op == "step_add":
            return 200, {"goal": g.add_step(gid, str(b.get("text", "")), bool(b.get("outward")), by="dashboard")}
        if op == "step_set":
            return 200, {"goal": g.set_step(sid, bool(b.get("done", True)), by="dashboard")}
        if op == "step_remove":
            return 200, {"goal": g.remove_step(sid, by="dashboard")}
    except KeyError:
        return 404, {"error": "no such goal"}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 400, {"error": "op must be update, status, step_add, step_set, step_remove or delete"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/goals", api_goals)
    register("POST", r"/api/goals/checkin", api_goals_checkin)
    register("POST", r"/api/goals", api_goal_add)
    register("POST", r"/api/goals/op", api_goal_op)
