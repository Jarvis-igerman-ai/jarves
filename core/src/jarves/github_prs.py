"""GitHub writes class 2 (V7 G4, issue #411): open/update a pull request, PR comment, PR review, CI re-run. Nothing else.

Same discipline as github_issues.py: strict payload schema checked BEFORE the owner is asked, fixed endpoint templates in
`GitHubClient.write` (verb bound), the ONLY entry is `GitHubWriter.execute()` (forbidden kinds, switch, untrusted turn,
repo allow-list, token probe, rate limit, popup-only approval bound to the action hash, audit chain).
Never here: merge (G5, merge queue only), force push, branch protection, settings, secrets, org, permissions, inline review
comments, workflow dispatch or cancel. A review can only be COMMENT, REQUEST_CHANGES or APPROVE (APPROVE needs its own owner switch github_approve_reviews,
default OFF, and its popup says it can satisfy a required review).
CLI: `jarvis github pr open|update|comment|review|rerun` (CLI only: NO model tool until the owner decides).
"""
from __future__ import annotations

import asyncio
import re

from . import github_client as gc
from . import github_issues as gi
from . import github_write as gw

TITLE_MAX, BODY_MAX = 200, 800
_RUN = re.compile(r"[1-9][0-9]{0,14}", re.ASCII)
FIELDS = {"pr.open": {"title", "body", "head", "base", "draft"}, "pr.update": {"title", "body", "state"},
          "pr.comment": {"body"}, "pr.review": {"event", "body"}, "ci.rerun": {"failed_only"}}
EVENTS = ("COMMENT", "REQUEST_CHANGES", "APPROVE")


def _ref(v, what: str) -> str:
    if not isinstance(v, str) or not gc._REF.fullmatch(v) or v.endswith(".lock") or v.startswith("-") or "@{" in v:
        return f"{what} must be a plain branch name of this repo"
    return ""


def validate(a: gw.WriteAction) -> str:
    """"" if the payload fits the kind's schema, else why not (nothing unknown passes)."""
    kind, p = a.kind.strip().lower(), a.payload
    if not isinstance(p, dict) or kind not in FIELDS or set(p) - FIELDS[kind] or not p:
        return "payload fields are not allowed for this kind"
    if (a.target == "") != (kind == "pr.open") or (a.target and not (_RUN if kind == "ci.rerun" else gi._NUM).fullmatch(a.target)):
        return "target must be the pull request number (the run id for ci.rerun; empty only for a new pull request)"
    checks = {"title": lambda v: gi._text(v, TITLE_MAX, "title"), "body": lambda v: gi._text(v, BODY_MAX, "body"),
              "state": lambda v: "" if v in ("open", "closed") else "state must be open or closed",
              "head": lambda v: _ref(v, "head"), "base": lambda v: _ref(v, "base"),
              "draft": lambda v: "" if isinstance(v, bool) else "draft must be true or false",
              "failed_only": lambda v: "" if isinstance(v, bool) else "failed_only must be true or false",
              "event": lambda v: "" if v in EVENTS else "event must be COMMENT, REQUEST_CHANGES or APPROVE"}
    for k, v in p.items():
        why = checks[k](v)
        if why:
            return why
    if kind == "pr.open" and not {"title", "head", "base"} <= set(p):
        return "a new pull request needs title, head and base"
    if kind == "pr.open" and p["head"] == p["base"]:
        return "head and base must differ"
    if kind == "pr.comment" and "body" not in p:
        return "a comment needs a body"
    if kind == "pr.review" and ("event" not in p or (p["event"] != "APPROVE" and "body" not in p)):
        return "a review needs an event, and a body unless it only approves"
    if kind == "ci.rerun" and "failed_only" not in p:
        return "failed_only is needed"
    return ""


def action(kind: str, repo: str, number: str = "", **payload) -> gw.WriteAction:
    """Build an exact action; the preview is generated here from the payload (short, plain)."""
    what = {"pr.open": "Open a pull request", "pr.update": "Update pull request", "pr.comment": "Comment on pull request",
            "pr.review": "Review pull request", "ci.rerun": "Re-run CI run"}[kind]
    head = f"{what} #{number}" if number else what
    if kind == "pr.open":
        detail = f"{payload.get('title', '')} ({payload.get('head', '')} -> {payload.get('base', '')})"
    elif kind == "pr.review":
        detail = f"{payload.get('event', '')} {payload.get('body', '')}"
    elif kind == "ci.rerun":
        detail = "failed jobs only" if payload.get("failed_only") else "all jobs"
    else:
        detail = payload.get("title") or payload.get("body") or ""
    return gw.WriteAction(kind=kind, repo=repo, target=str(number), payload=payload, preview=f"{head}: {detail}"[:200])


def _extra(a: gw.WriteAction, c: gc.GitHubClient) -> str:
    """Lines the owner must see, from a READ: is the number really a pull request, and what the action can cause."""
    o, _, r = a.repo.partition("/")
    kind, p, out = a.kind.strip().lower(), a.payload, []
    if kind in ("pr.update", "pr.comment", "pr.review") and not c.issue(o, r, a.target)["is_pr"]:
        out.append(f"WARNING: #{a.target} is NOT a pull request: this will be refused")
    if kind == "pr.open":
        out.append("Opens a pull request" + (" (DRAFT)" if p.get("draft") else " (ready for review: CI and reviewers start)")
                   + "; it is never merged by this action")
    if kind == "pr.update" and p.get("state") == "closed":
        out.append("WARNING: closes the pull request")
    if kind == "pr.review" and p.get("event") == "APPROVE":
        out.append("WARNING: APPROVE can satisfy a required review for merging")
    if kind == "pr.review" and p.get("event") == "REQUEST_CHANGES":
        out.append("WARNING: REQUEST_CHANGES can block merging until it is dismissed")
    if kind == "ci.rerun":
        run = c.workflow_run(o, r, a.target)          # a READ before the popup; a failure refuses (fail closed, see _extra)
        out.append(f"Run {a.target}: workflow {run['name'] or '?'}, event {run['event'] or '?'}, branch {run['branch'] or '?'}, "
                   f"from {run['head_repo'] or '?'}{' (FORK)' if run['fork'] or (run['head_repo'] and run['head_repo'].lower() != a.repo.lower()) else ''}, "
                   f"started by {run['actor'] or '?'}")
        if run["fork"] or (run["head_repo"] and run["head_repo"].lower() != a.repo.lower()) or run["event"] == "pull_request_target":
            out.append("WARNING: this run comes from a FORK or runs with pull_request_target: it re-executes code that is not yours "
                       "and may have access to secrets")
        out.append("Re-runs a workflow run: this uses CI minutes and re-executes the workflow code of the repo")
    return "\n".join(out)


def _do(a: gw.WriteAction, c: gc.GitHubClient) -> dict:
    o, _, r = a.repo.partition("/")
    base, kind, p = f"/repos/{o}/{r}", a.kind.strip().lower(), dict(a.payload)
    if kind in ("pr.update", "pr.comment", "pr.review") and not c.issue(o, r, a.target)["is_pr"]:   # fresh, after approval
        raise gc.GitHubError("GITHUB_WRITE_BAD_ARGUMENT", f"#{a.target} is not a pull request")
    if kind == "pr.open":
        j = c.write("POST", f"{base}/pulls", p)
    elif kind == "pr.update":
        j = c.write("PATCH", f"{base}/pulls/{a.target}", p)
    elif kind == "pr.comment":
        j = c.write("POST", f"{base}/issues/{a.target}/comments", p)
    elif kind == "pr.review":
        j = c.write("POST", f"{base}/pulls/{a.target}/reviews", p)
    else:
        j = c.write("POST", f"{base}/actions/runs/{a.target}/" + ("rerun-failed-jobs" if p["failed_only"] else "rerun"))
    j = j if isinstance(j, dict) else {}
    return {k: v for k, v in {"number": j.get("number") or (int(a.target) if a.target and kind != "ci.rerun" else None),
                              "url": gi._clean(j.get("html_url"))}.items() if v}


async def handle(a: gw.WriteAction, client: gc.GitHubClient) -> dict:
    return await asyncio.to_thread(_do, a, client)


def _approve_probe(c) -> tuple[bool, str]:
    return gw._probe(c)


gw.feat.register(gw.APPROVE_FEATURE, "GitHub: אישור בקשות מיזוג (APPROVE)", "GitHub: approve pull requests (APPROVE)", False,
                 probe=_approve_probe, needs=["github_write"], risk="high", stage="beta",
                 about="let a review with the event APPROVE be sent from the command line (after your popup approval); an APPROVE can satisfy a "
                       "required review for merging, so it has its own switch; comments and change requests do not need it",
                 targets=gw.targets("UNIT test_github_prs.py (fake GitHub API and approvals, no network)",
                                    "never touched the real GitHub API"),
                 permissions=["approves a pull request after your approval each time"], settings="jarvis features on github_approve_reviews")


for _k in FIELDS:
    gw.HANDLERS[_k] = handle
    gw.VALIDATORS[_k] = validate
    gw.EXTRAS[_k] = _extra


# ------------------------------------------------------------------------------------------ CLI
def cmd_github_pr(a, cfg, home) -> int:
    """jarvis github pr open|update|comment|review|rerun [number] --repo owner/repo ...: the SAME execute() path and popup."""
    mode, repos = a.write_action or "", list(getattr(cfg, "github_repos", []) or [])
    kind = {"open": "pr.open", "update": "pr.update", "comment": "pr.comment", "review": "pr.review",
            "rerun": "ci.rerun"}.get(mode)
    repo = a.repo or (repos[0] if len(repos) == 1 else "")
    if not kind or not repo or (kind != "pr.open") == (not a.number):
        print("GITHUB_WRITE_BAD_ARGUMENT: usage: jarvis github pr open --title T --head BRANCH --base BRANCH [--body B] [--draft]"
              " | update N [--title T] [--body B] [--state open|closed] | comment N --body B | review N --event "
              "COMMENT|REQUEST_CHANGES|APPROVE [--body B] | rerun RUN_ID [--failed-only]   (all with --repo owner/repo)")
        return 2
    p = {k: v for k, v in {"title": a.title, "body": a.body, "state": a.state, "head": a.head, "base": a.base,
                           "event": a.event}.items() if v is not None}
    if kind == "pr.open" and a.draft:
        p["draft"] = True
    if kind == "ci.rerun":
        p["failed_only"] = bool(a.failed_only)
    return gi.execute_cli(cfg, home, action(kind, repo, a.number or "", **p))
