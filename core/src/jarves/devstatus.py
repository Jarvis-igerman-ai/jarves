"""Development Manager status layer (V5 pillar 5): what the delegated Claude Code / Codex sessions are doing, matched to
the open pull requests, issues and CI runs of the JARVES repository, with a suggested next action per row.

READ-ONLY by default. GitHub is only read (GET, through JARVES_UPDATE_TOKEN / GH_TOKEN / GITHUB_TOKEN or the `gh` CLI),
cached (TTL + on-disk copy), rate-limit aware (stops asking when the limit is used up and serves the cached copy) and
content-minimal (number, short redacted title, branch, short sha, state, check names; never bodies, comments or diffs).
Agents are read from the orchestrator's agents.db (provider, project, branch, state, age; never their text).

A WRITE action (rerun a job, merge, comment, label) is only a PROPOSAL with the exact command. It runs ONLY after the
owner approves it on the approval popup (`apply`); nothing is executed by a status call, a digest or the voice tool.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from . import calm, features as feat, logs

FEATURE = "dev_status"
feat.register(FEATURE, "סטטוס פיתוח (קריאה בלבד)", "Development status (read-only)", True, skills=["devstatus"],
              about="shows what the coding agents, pull requests and CI of JARVES are doing and suggests the next step; "
                    "it only reads, and any change on GitHub asks on the screen first",
              targets=calm.targets('test_v5_devstatus.py'))

DEFAULT_REPO = "igalgerman/jarves"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TTL_S = 120.0
STUCK_S = 1800.0                 # an agent RUNNING without progress this long is stuck
IDLE_LONG_S = 7200.0             # an agent waiting for a prompt this long is idle too long
FINISHED_S = 86400.0             # a stopped agent counts as "finished" for a day
MAX_PRS, MAX_ISSUES, MAX_CHECKED = 30, 30, 12
FAIL = ("failure", "timed_out", "action_required", "startup_failure")
OK = ("success", "neutral", "skipped")
Fetch = Callable[[str], "tuple[int, object, dict]"]          # path -> (http status, parsed json, lower-case headers)
Runner = Callable[[list], "tuple[int, str]"]                 # gh argv (without "gh") -> (returncode, output)


# ---------------------------------------------------------------------- reading GitHub (GET only)
def _token() -> str:
    for k in ("JARVES_UPDATE_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return ""


def default_fetch(path: str) -> tuple[int, object, dict]:
    tok = _token()
    if tok:
        import httpx
        try:
            r = httpx.get("https://api.github.com" + path, timeout=20, headers={
                "Accept": "application/vnd.github+json", "User-Agent": "jarves-dev-status",
                "Authorization": f"Bearer {tok}"})
            body = r.json() if r.content else {}
            return r.status_code, body, {k.lower(): v for k, v in r.headers.items()}
        except (httpx.HTTPError, ValueError):
            return 0, None, {}
    from .diag.tickets import gh_runner
    rc, out = gh_runner(["api", path])
    try:
        return (200, json.loads(out), {}) if rc == 0 else (403 if "rate limit" in out.lower() else 0, None, {})
    except ValueError:
        return 0, None, {}


class GitHub:
    """Cached, rate-limit-aware GET client. `get` returns (json or None, note) where note is "", "stale",
    "rate_limited" or "unavailable"."""

    def __init__(self, fetch: Fetch | None = None, *, cache_path: Path | None = None, ttl: float = TTL_S,
                 clock: Callable[[], float] = time.time) -> None:
        self.fetch, self.ttl, self.clock, self.cache_path = fetch or default_fetch, ttl, clock, cache_path
        self.cache: dict[str, list] = {}
        self.blocked_until = 0.0
        if cache_path and cache_path.is_file():
            try:
                d = json.loads(cache_path.read_text(encoding="utf-8"))
                self.cache = {k: v for k, v in d.get("cache", {}).items() if isinstance(v, list) and len(v) == 2}
                self.blocked_until = float(d.get("blocked_until", 0))
            except (OSError, ValueError, TypeError):
                self.cache = {}

    def save(self) -> None:
        if self.cache_path:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps({"cache": self.cache, "blocked_until": self.blocked_until}),
                                           encoding="utf-8")
            except OSError:
                logs.warn("devstatus", "DEV_CACHE_WRITE_FAILED", "could not save the status cache")

    def get(self, path: str) -> tuple[object, str]:
        now = self.clock()
        hit = self.cache.get(path)
        if hit and now - hit[0] < self.ttl:
            return hit[1], ""
        if now < self.blocked_until:
            return (hit[1], "rate_limited") if hit else (None, "rate_limited")
        code, body, hdr = self.fetch(path)
        remaining, reset = hdr.get("x-ratelimit-remaining"), hdr.get("x-ratelimit-reset")
        if code in (403, 429) or (remaining is not None and str(remaining) == "0"):
            try:
                self.blocked_until = float(reset) if reset else now + 300.0
            except ValueError:
                self.blocked_until = now + 300.0
            logs.warn("devstatus", "DEV_RATE_LIMITED", "GitHub rate limit reached, serving the cache", status=code)
            if code in (403, 429):
                return (hit[1], "rate_limited") if hit else (None, "rate_limited")
        if code != 200:
            return (hit[1], "stale") if hit else (None, "unavailable")
        self.cache[path] = [now, body]
        return body, ""


def _title(s: object) -> str:
    return " ".join(logs.redact(str(s or "")).split())[:90]


def _prs(gh: GitHub, repo: str, notes: list) -> list[dict]:
    body, note = gh.get(f"/repos/{repo}/pulls?state=open&per_page={MAX_PRS}")
    if note:
        notes.append(note)
    out = []
    for p in body if isinstance(body, list) else []:
        head = p.get("head") or {}
        out.append({"number": int(p.get("number", 0)), "title": _title(p.get("title")), "draft": bool(p.get("draft")),
                    "head": str(head.get("ref", ""))[:80], "sha": str(head.get("sha", "")), "updated": p.get("updated_at") or ""})
    return out


def _issues(gh: GitHub, repo: str, notes: list) -> list[dict]:
    body, note = gh.get(f"/repos/{repo}/issues?state=open&per_page={MAX_ISSUES}")
    if note:
        notes.append(note)
    return [{"number": int(i.get("number", 0)), "title": _title(i.get("title")), "updated": i.get("updated_at") or ""}
            for i in (body if isinstance(body, list) else []) if "pull_request" not in i]


def _checks(gh: GitHub, repo: str, sha: str, notes: list) -> list[dict]:
    body, note = gh.get(f"/repos/{repo}/commits/{sha}/check-runs?per_page=50")
    if note:
        notes.append(note)
    out = []
    for c in (body or {}).get("check_runs", []) if isinstance(body, dict) else []:
        m = re.search(r"/actions/runs/(\d+)", str(c.get("details_url") or ""))
        out.append({"name": _title(c.get("name"))[:60], "status": c.get("status") or "", "conclusion": c.get("conclusion") or "",
                    "run_id": int(m.group(1)) if m else 0})
    return out


# ---------------------------------------------------------------------- agents (from agents.db, minimal fields)
def read_agents(home: Path, now: float) -> list[dict]:
    path = Path(home) / "agents.db"
    if not path.is_file():
        return []
    from .orchestrator.db import AgentDB
    from .orchestrator.envelope import LIVE_STATES
    db = AgentDB(path)
    try:
        rows = db.sessions()
    finally:
        db.close()
    out = []
    for s in rows:
        last = s.get("last_progress_at") or s.get("last_seen_at") or s.get("started_at") or 0
        age = max(0.0, now - float(last)) if last else 0.0
        st = s.get("state") or "UNKNOWN"
        if st in LIVE_STATES:
            health = ("stuck" if st == "RUNNING" and age > STUCK_S else
                      "idle_long" if st == "IDLE" and age > IDLE_LONG_S else
                      "blocked" if st in ("WAITING_APPROVAL", "WAITING_INPUT") else "working" if st == "RUNNING" else "idle")
        elif st == "STOPPED" and s.get("ended_at") and now - float(s["ended_at"]) < FINISHED_S:
            health = "finished"
        else:
            continue
        out.append({"id": s["id"], "provider": s.get("provider") or "", "project": s.get("project") or "",
                    "branch": s.get("branch") or "", "name": _title(s.get("name")), "state": st, "health": health,
                    "age_s": int(age)})
    return out


def _linked(agents: list[dict], number: int, head: str = "") -> list[dict]:
    pat = re.compile(rf"(?<!\d)#{number}(?!\d)|issues?[/-]{number}(?!\d)|pull/{number}(?!\d)")
    return [a for a in agents if (head and a["branch"] == head) or pat.search(a["name"] + " " + a["branch"])]


# ---------------------------------------------------------------------- classification and suggestions
def _cmd(argv: list) -> str:
    return "gh " + shlex.join(argv)


def _prop(pid: str, action: str, argv: list, en: str, he: str) -> dict:
    return {"id": pid, "action": action, "argv": argv, "command": _cmd(argv), "text_en": en, "text_he": he,
            "needs_approval": True}


def _sug(kind: str, en: str, he: str) -> dict:
    return {"kind": kind, "text_en": en, "text_he": he}


def classify_pr(repo: str, pr: dict, checks: list[dict], agents: list[dict]) -> dict:
    n = pr["number"]
    failed = [c for c in checks if c["conclusion"] in FAIL]
    running = [c for c in checks if c["status"] != "completed"]
    names = ", ".join(sorted({c["name"] for c in failed}))
    props: list[dict] = []
    if failed:
        state, blocker = "ci_failed", "failed: " + names
        if len(failed) == 1 and len(checks) > 1 and all(c["conclusion"] in OK for c in checks if c is not failed[0]) and failed[0]["run_id"]:
            sug = _sug("rerun", f"Only the job {names} failed: rerun it once in case it is flaky, else repair it.",
                       f"רק המשימה {names} נכשלה: להריץ אותה שוב למקרה שהיא לא יציבה, אחרת לתקן.")
            props.append(_prop(f"pr{n}-rerun", "rerun", ["run", "rerun", str(failed[0]["run_id"]), "--failed", "-R", repo],
                               f"Rerun the failed job {names}", f"להריץ שוב את המשימה שנכשלה {names}"))
        else:
            sug = _sug("repair", f"CI failed in {names}: send an agent to repair it.",
                       f"ה-CI נכשל ב-{names}: לשלוח סוכן לתקן.")
        props.append(_prop(f"pr{n}-comment", "comment", ["pr", "comment", str(n), "--body", f"CI failed in {names}; being repaired.", "-R", repo],
                           "Comment that CI failed", "להגיב שה-CI נכשל"))
    elif running:
        state, blocker = "ci_running", "waiting for CI"
        sug = _sug("wait", "CI is still running: wait.", "ה-CI עדיין רץ: להמתין.")
    elif not checks:
        state, blocker = "no_ci", "no CI result yet"
        sug = _sug("review", "No CI result yet: review the change.", "אין עדיין תוצאת CI: לבדוק את השינוי.")
    elif pr["draft"]:
        state, blocker = "draft", "draft"
        sug = _sug("review", "CI is green on a draft: review it and mark it ready.", "ה-CI ירוק על טיוטה: לבדוק ולסמן כמוכנה.")
    else:
        state, blocker = "green", "waiting for review or merge"
        sug = _sug("merge_ready", "CI is green and it is waiting: review it, then merge (merging asks you first).",
                   "ה-CI ירוק והיא ממתינה: לבדוק ואז למזג (המיזוג מבקש אישור).")
        props.append(_prop(f"pr{n}-merge", "merge", ["pr", "merge", str(n), "--squash", "-R", repo],
                           "Merge this pull request", "למזג את בקשת המשיכה"))
    bad = [a for a in agents if a["health"] in ("stuck", "idle_long", "blocked")]
    if bad and state != "green":
        a = bad[0]
        words = {"stuck": ("the agent is stuck", "הסוכן תקוע"), "idle_long": ("the agent has been idle a long time", "הסוכן ממתין זמן רב"),
                 "blocked": ("the agent waits for an answer", "הסוכן ממתין לתשובה")}[a["health"]]
        blocker = (blocker + "; " if blocker else "") + words[0]
        sug = _sug("unblock", f"{words[0].capitalize()}: check on it.", f"{words[1]}: לבדוק מה איתו.")
    elif agents and any(a["health"] == "working" for a in agents) and state in ("ci_failed", "no_ci"):
        sug = _sug("wait", "An agent is working on it: wait.", "סוכן עובד על זה: להמתין.")
    return {"type": "pr", "number": n, "title": pr["title"], "state": state, "blocker": blocker, "commit": pr["sha"][:10],
            "branch": pr["head"], "agents": [a["id"] for a in agents], "suggestion": sug, "proposals": props}


def classify_issue(repo: str, iss: dict, agents: list[dict]) -> dict:
    n = iss["number"]
    props = []
    if any(a["health"] in ("stuck", "idle_long", "blocked") for a in agents):
        state, blocker, sug = "stuck", "the agent needs attention", _sug("unblock", "The agent on it needs attention: check on it.", "הסוכן שעובד על זה צריך תשומת לב: לבדוק.")
    elif agents:
        state, blocker, sug = "in_progress", "", _sug("wait", "An agent is working on it: wait.", "סוכן עובד על זה: להמתין.")
    else:
        state, blocker = "open", "no agent or pull request yet"
        sug = _sug("assign", "Nobody is working on it: start an agent if it matters.", "אף אחד לא עובד על זה: להפעיל סוכן אם זה חשוב.")
        props.append(_prop(f"issue{n}-label", "label", ["issue", "edit", str(n), "--add-label", "needs-triage", "-R", repo],
                           "Label it needs-triage", "לתייג כ-needs-triage"))
    return {"type": "issue", "number": n, "title": iss["title"], "state": state, "blocker": blocker, "commit": "",
            "branch": "", "agents": [a["id"] for a in agents], "suggestion": sug, "proposals": props}


def build(home: Path, repo: str = DEFAULT_REPO, *, fetch: Fetch | None = None, now: float | None = None,
          gh: GitHub | None = None) -> dict:
    """The whole status. Never raises for a GitHub problem: it reports it in `notes`."""
    now = time.time() if now is None else now
    if not REPO_RE.match(repo or ""):
        raise ValueError("the repository must look like owner/name")
    gh = gh or GitHub(fetch, cache_path=Path(home) / "dev_status_cache.json", clock=lambda: now)
    notes: list[str] = []
    agents = read_agents(home, now)
    items = []
    for i, pr in enumerate(_prs(gh, repo, notes)):
        checks = _checks(gh, repo, pr["sha"], notes) if pr["sha"] and i < MAX_CHECKED else []
        items.append(classify_pr(repo, pr, checks, _linked(agents, pr["number"], pr["head"])))
    for iss in _issues(gh, repo, notes):
        items.append(classify_issue(repo, iss, _linked(agents, iss["number"])))
    gh.save()
    count = lambda k, v: sum(1 for x in items if x["type"] == "pr" and x[k] == v)       # noqa: E731
    summary = {"prs": sum(1 for x in items if x["type"] == "pr"), "issues": sum(1 for x in items if x["type"] == "issue"),
               "ci_failed": count("state", "ci_failed"), "green": count("state", "green"),
               "ci_running": count("state", "ci_running"),
               "agents_working": sum(1 for a in agents if a["health"] == "working"),
               "agents_stuck": sum(1 for a in agents if a["health"] in ("stuck", "idle_long")),
               "agents_blocked": sum(1 for a in agents if a["health"] == "blocked"),
               "agents_finished": sum(1 for a in agents if a["health"] == "finished")}
    logs.info("devstatus", "DEV_STATUS_BUILT", "development status built", n=len(items), count=len(agents))
    return {"repo": repo, "at": now, "items": items, "agents": agents, "summary": summary,
            "notes": sorted(set(notes)), "read_only": True}


# ---------------------------------------------------------------------- the one-sentence answer and the digest
def sentence(st: dict, lang: str = "en") -> str:
    s, notes = st["summary"], st["notes"]
    if "unavailable" in notes and not st["items"]:
        return ("אי אפשר לקרוא עכשיו את GitHub, אז אין לי סטטוס פיתוח." if lang == "he"
                else "I cannot read GitHub right now, so I have no development status.")
    if lang == "he":
        bits = [f"{s['prs']} בקשות משיכה פתוחות"]
        if s["ci_failed"]:
            bits.append(f"{s['ci_failed']} עם CI שנכשל")
        if s["green"]:
            bits.append(f"{s['green']} ירוקות וממתינות")
        bits.append(f"{s['agents_working']} סוכנים עובדים")
        if s["agents_stuck"] or s["agents_blocked"]:
            bits.append(f"{s['agents_stuck'] + s['agents_blocked']} סוכנים תקועים או ממתינים")
        return ", ".join(bits) + "."
    bits = [f"{s['prs']} open pull requests"]
    if s["ci_failed"]:
        bits.append(f"{s['ci_failed']} with failing CI")
    if s["green"]:
        bits.append(f"{s['green']} green and waiting")
    bits.append(f"{s['agents_working']} agents working")
    if s["agents_stuck"] or s["agents_blocked"]:
        bits.append(f"{s['agents_stuck'] + s['agents_blocked']} stuck or waiting")
    return ", ".join(bits) + "."


def digest(st: dict, lang: str = "en") -> str:
    """On request only (`jarvis dev digest`): no scheduler job creates this."""
    lines = [sentence(st, lang)]
    for it in st["items"]:
        if it["suggestion"]["kind"] in ("wait", "assign") and it["type"] == "issue":
            continue
        lines.append(f"{'PR' if it['type'] == 'pr' else 'Issue'} #{it['number']}: " + it["suggestion"]["text_he" if lang == "he" else "text_en"])
    return "\n".join(lines)


# ---------------------------------------------------------------------- write actions: proposals only, approval first
def proposals(st: dict) -> list[dict]:
    return [dict(p, item=f"{it['type']} #{it['number']}") for it in st["items"] for p in it["proposals"]]


def default_runner(argv: list) -> tuple[int, str]:
    from .diag.tickets import gh_runner
    return gh_runner(argv)


def apply(home: Path, st: dict, pid: str, *, ask: Callable[..., "tuple[bool, str]"] | None = None,
          runner: Runner | None = None) -> dict:
    """Run ONE proposal of this status, only after the owner approved the exact command. `ask` returns (ok, reason);
    the default is the approval popup. Anything that is not an offered proposal is refused."""
    p = next((x for x in proposals(st) if x["id"] == pid), None)
    if p is None:
        return {"ok": False, "status": "unknown", "reason": "no such proposal in the current status"}
    if ask is None:
        from .approvals import ask_owner
        ask = lambda prompt, **kw: ask_owner(Path(home), prompt, **kw)               # noqa: E731
    ok, _why = ask("Run this command on GitHub?\n" + p["command"], title="Development Manager: " + p["text_en"],
                   why="proposed by the Development Manager status", kind="tool", risk="high",
                   details={"command": p["command"]})
    if not ok:
        logs.info("devstatus", "DEV_ACTION_DENIED", "development action not approved", action=p["action"])
        return {"ok": False, "status": "not_approved", "command": p["command"]}
    rc, out = (runner or default_runner)(p["argv"])
    logs.info("devstatus", "DEV_ACTION_RUN", "development action ran after approval", action=p["action"], rc=rc)
    return {"ok": rc == 0, "status": "done" if rc == 0 else "failed", "command": p["command"], "output": logs.redact(out)[:300]}


# ---------------------------------------------------------------------- voice tool
class NoArgs(BaseModel):
    pass


def repo_of(home: Path) -> str:
    try:
        from .userdata import open_db
        db = open_db(Path(home))
        try:
            v = str(db.setting("dev.repo") or "").strip()
        finally:
            db.close()
    except Exception:                                           # a missing setting store must not break the status
        v = ""
    return v if REPO_RE.match(v) else DEFAULT_REPO


def make_skill(home: Path, fetch: Fetch | None = None):
    from .skills import Skill, Tool, ToolResult

    class DevStatusSkill(Skill):
        name = "devstatus"
        description = "development status, read only"

        def tools(self) -> list:
            async def status(_a: NoArgs):
                import asyncio
                st = await asyncio.to_thread(build, Path(home), repo_of(home), fetch=fetch)
                return ToolResult(True, sentence(st, "en"), {"summary": st["summary"], "notes": st["notes"]})
            return [Tool("dev_status", "Dev status, read only: 'how is development going', 'מה מצב הפיתוח'.", status, NoArgs, permission="read")]
    return DevStatusSkill()


# ---------------------------------------------------------------------- dashboard
def api_status(ctx, m, body, q=None):
    try:
        st = build(ctx.home, repo_of(ctx.home))
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, {**st, "sentence_en": sentence(st, "en"), "sentence_he": sentence(st, "he")}


def api_apply(ctx, m, body):
    pid = str((body or {}).get("id", ""))
    if not re.fullmatch(r"(pr|issue)\d+-[a-z]+", pid):
        return 400, {"error": "bad proposal id"}
    res = apply(ctx.home, build(ctx.home, repo_of(ctx.home)), pid)
    return (200 if res["status"] != "unknown" else 404), res


def dashboard_routes(register) -> None:
    register("GET", r"/api/dev/status", api_status)
    register("POST", r"/api/dev/status/apply", api_apply)
