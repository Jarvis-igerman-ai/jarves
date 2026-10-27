"""GitHub / agents link (V7 G6, issue #413): coding-agent session -> branch -> pull request -> failing check -> next action.

READ ONLY. For every coding-agent session that has a branch (the Orchestrator's own database) it looks for an open pull request
from that branch in the repos the owner listed (`github_repos`) and, for that pull request's head commit, at the check runs.
The answer is one row per session with a state and ONE plain next step ("2 checks failed: ci, lint. Re-run them or ask the agent
to fix it"). Nothing is written to GitHub; the next-step texts only NAME the owner's own commands (`jarvis github pr rerun`, `enqueue`).
Surfaces: `jarvis github work`, the dashboard `GET /api/github/work` (owner's computer only) and the lazy read-only tool `github_work`
("what blocks my pull request?"). Text from GitHub (a title) is shown as one cleaned line, capped; check names are cleaned the same way.
Beta, OFF by default (switch `github_work`; needs the GitHub token and the coding agents)."""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from . import clilang, logs
from . import features as feat
from .calm import targets
from .skills import Skill, Tool, ToolResult

FEATURE = "github_work"
LAZY = (r"what (is )?(block|stop)\w* (my |the )?(pr|pull)|(pr|pull request)s? (status|blockers?)|is my (pr|pull request)|"
        r"מה (חוסם|עוצר)|סטטוס (ה)?(pr|בקשת)")
MAX_ROWS = 10
_BAD_CHECK = ("failure", "cancelled", "timed_out", "action_required", "stale")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁩﻿]")

TEXT = {
    "no_pr": ("אין עדיין בקשת מיזוג לענף {branch}. אפשר לבקש מהסוכן לפתוח אחת (jarvis github pr open).",
              "No pull request yet for branch {branch}. Ask the agent to open one (jarvis github pr open)."),
    "draft": ("בקשת מיזוג #{n} היא טיוטה. כשהסוכן מסיים, היא צריכה לעבור ל'מוכנה לבדיקה'.",
              "Pull request #{n} is a draft. When the agent is done it has to be marked ready for review."),
    "checks_failing": ("בקשת מיזוג #{n}: {k} בדיקות נכשלו ({names}). אפשר להריץ שוב (jarvis github pr rerun) או לבקש מהסוכן לתקן.",
                       "Pull request #{n}: {k} check(s) failed ({names}). Re-run them (jarvis github pr rerun) or ask the agent to fix it."),
    "checks_running": ("בקשת מיזוג #{n}: {k} בדיקות עדיין רצות. מחכים.",
                       "Pull request #{n}: {k} check(s) still running. Wait."),
    "ready": ("בקשת מיזוג #{n}: כל הבדיקות עברו. נשאר רק להפעיל מיזוג אוטומטי אם רוצים (jarvis github pr enqueue).",
              "Pull request #{n}: all checks passed. Only turning on auto-merge is left, if you want it (jarvis github pr enqueue)."),
    "no_checks": ("בקשת מיזוג #{n}: עוד אין בדיקות לקומיט הזה.", "Pull request #{n}: there are no checks for this commit yet."),
    "unknown": ("לא הצלחתי לקרוא את המצב ב-GitHub ({code}).", "I could not read the state on GitHub ({code})."),
    "none": ("אין עכשיו סשן של סוכן קוד עם ענף.", "No coding-agent session with a branch right now."),
    "off": ("GitHub לא מחובר או שאין repos ברשימה (github_repos).", "GitHub is not connected, or no repo is listed (github_repos)."),
}


def clean(s: object, cap: int = 60) -> str:
    """One safe line from text GitHub controls: control, bidi and invisible characters removed, spaces collapsed, capped."""
    t = " ".join(_CTRL.sub(" ", unicodedata.normalize("NFKC", str(s or ""))).split())
    return t[:cap]


def _phrase(key: str, lang: str, **kw) -> str:
    he, en = TEXT[key]
    return (he if lang == "he" else en).format(**kw)


def sessions_with_branch(home: Path, limit: int = MAX_ROWS) -> list[dict]:
    """The Orchestrator's sessions that work on a branch (not main), newest first. Read only."""
    p = Path(home) / "agents.db"
    if not p.exists():
        return []
    from .orchestrator.db import AgentDB
    db = AgentDB(p)
    try:
        rows = db.sessions()
    finally:
        try:
            db.close()
        except Exception:                                   # noqa: BLE001
            pass
    out = []
    for r in rows:
        b = clean(r.get("branch"), 120)
        if b and b not in ("main", "master", "HEAD"):
            out.append({"id": str(r.get("id") or "")[:12], "provider": clean(r.get("provider"), 20), "project": clean(r.get("project"), 40),
                        "branch": b, "state": clean(r.get("state"), 20)})
        if len(out) >= limit:
            break
    return out


def view(home: Path, repos: list[str], client, *, lang: str = "en", sessions: list[dict] | None = None) -> list[dict]:
    """One row per session with a branch: {session, provider, project, branch, repo, pr, state, failing, next}. Never raises."""
    rows_in = sessions if sessions is not None else sessions_with_branch(home)
    prs: dict[str, tuple[str, dict]] = {}            # branch -> (repo, pr)
    err = ""
    for repo in repos:
        o, _, r = repo.partition("/")
        try:
            for p in client.pulls(o, r):
                ref = clean(p.get("ref"), 120)
                if ref and ref not in prs:
                    prs[ref] = (repo, p)
        except Exception as e:                             # noqa: BLE001 - a repo that cannot be read becomes 'unknown', not a crash
            err = str(getattr(e, "code", "") or type(e).__name__)[:40]
            logs.warn("github", "GITHUB_WORK_READ_FAILED", "could not read pull requests for a repo", error=err)
    out = []
    for s in rows_in:
        row = {**s, "repo": "", "pr": 0, "state": "no_pr", "failing": [], "next": ""}
        hit = prs.get(s["branch"])
        if hit:
            repo, p = hit
            row.update(repo=repo, pr=int(p.get("number") or 0), title=clean(p.get("title")))
            if p.get("draft"):
                row["state"] = "draft"
            else:
                o, _, r = repo.partition("/")
                try:
                    checks = client.check_runs(o, r, str(p.get("head") or ""))
                except Exception as e:                      # noqa: BLE001
                    row["state"], err = "unknown", str(getattr(e, "code", "") or type(e).__name__)[:40]
                    checks = None
                if checks is not None:
                    bad = [clean(c.get("name"), 40) for c in checks if c.get("conclusion") in _BAD_CHECK]
                    wait = [c for c in checks if c.get("status") != "completed"]
                    row["failing"] = bad[:5]
                    row["state"] = ("checks_failing" if bad else "checks_running" if wait else "ready" if checks else "no_checks")
                    row["_k"] = len(bad) if bad else len(wait)
        elif err and not prs:
            row["state"] = "unknown"
        k = row.pop("_k", 0)
        row["next"] = _phrase(row["state"], lang, n=row["pr"], branch=row["branch"], k=k, names=", ".join(row["failing"]), code=err or "?")
        out.append(row)
    return out


def text(rows: list[dict], lang: str = "en") -> str:
    if not rows:
        return _phrase("none", lang)
    return "\n".join(f"{r['provider'] or 'agent'} / {r['branch']}: {r['next']}" for r in rows)


def _client_and_repos(home: Path, cfg):
    from . import github_client as gc
    repos = [r for r in (getattr(cfg, "github_repos", []) or []) if "/" in r]
    if not repos or not gc.connected(home):
        return None, []
    return gc.GitHubClient(home), repos


def compute(home: Path, cfg, lang: str = "en") -> dict:
    client, repos = _client_and_repos(home, cfg)
    if client is None:
        return {"ok": False, "code": "GITHUB_WORK_OFF", "rows": [], "text": _phrase("off", lang)}
    rows = view(home, repos, client, lang=lang)
    return {"ok": True, "rows": rows, "text": text(rows, lang)}


# ------------------------------------------------------------------------------------------ the lazy read-only tool
class NoArgs(BaseModel):
    pass


class WorkSkill(Skill):
    name = "github_work"
    description = "Say what blocks the coding agents' pull requests (read only)"

    def __init__(self, home: Path, cfg_fn: Callable[[], object], lang_fn: Callable[[], str] = lambda: "en") -> None:
        self.home, self.cfg_fn, self.lang_fn = Path(home), cfg_fn, lang_fn

    def tools(self) -> list[Tool]:
        async def run(_a: NoArgs) -> ToolResult:
            r = await asyncio.to_thread(compute, self.home, self.cfg_fn(), self.lang_fn())
            if not r["ok"]:
                return ToolResult(False, r["text"], code=r["code"])
            return ToolResult(True, r["text"], {"sessions": len(r["rows"]), "states": sorted({x["state"] for x in r["rows"]})})
        return [Tool("github_work", "What blocks my agents' pull requests? 'מה חוסם את ה-PR'.", run, NoArgs, permission="read", lazy=LAZY)]


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "מצב בקשות המיזוג של הסוכנים", "Agents' pull request status", False, probe=_probe,
              needs=["github", "agent_orchestrator"], risk="low", skills=["github_work"],
              about="for each coding-agent session that works on a branch, say whether its pull request exists, is a draft, has failing "
                    "checks or is ready, and what to do next; read only",
              targets=targets("test_github_work.py"), permissions=["read"], settings="jarvis features on github_work")


# ------------------------------------------------------------------------------------------ CLI and dashboard
def cmd_github_work(a, cfg, home) -> int:
    lang = "he" if clilang.current() == "he" else "en"
    r = compute(Path(home), cfg, lang)
    if getattr(a, "json", False):
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(r["text"])
    return 0 if r["ok"] else 1


def dashboard_routes(register) -> None:
    def get(ctx, _m, _b):
        who = getattr(ctx, "principal", None)
        if not (isinstance(who, dict) and who.get("kind") == "local"):
            return 403, {"error": "only on the computer itself"}
        try:
            on = feat._ctx_features(ctx).active(FEATURE)
        except Exception:                                   # noqa: BLE001
            on = False
        if not on:
            return 200, {"enabled": False, "rows": [], "text": ""}
        r = compute(ctx.home, ctx.cfg, "en")
        return 200, {"enabled": True, **{k: r[k] for k in ("ok", "rows", "text")}}
    register("GET", r"/api/github/work$", get)
