"""Tier R review -> fix -> merge flow and merge-backlog focus (merge steward helper, read-only).

Standard library + gh via subprocess (plan/focus only read). It decides, it never merges, labels or pushes.

    python core/tools/tier_r_flow.py plan [--pr N ...]   -> JSON: per open PR the next action
                                                           {review|fix|queue|wait|owner} with reasons
    python core/tools/tier_r_flow.py focus [--max-workers K] -> JSON: backlog flag, line, ordered work

Rules (docs/workflow/OPERATING_MODEL.md, "Merge steward"): one Opus review per PR + head SHA; a verdict
binds to that SHA and a new push invalidates it; max 2 review rounds, then owner; excluded paths always owner;
only OWNER/MEMBER/COLLABORATOR comments count; the latest verdict on the head wins.
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

MAX_ROUNDS = 2
BACKLOG_OPEN = 8        # open non-draft PRs
BACKLOG_GREEN = 3       # green PRs not yet queued
# Machine-readable verdict: FIRST line "SIGN-OFF <40-hex head sha>" or "CHANGES REQUESTED <40-hex head sha>", plus
# marker lines "Review-Head: <same sha>", "Review-Task: <id>", "Reviewer-Agent: <id>", "Reviewer-Model: <model>".
# Quoted text and anything below line 1 never counts as the verdict.
VERDICT = re.compile(r"^(?:VERDICT:[ \t]*)?(SIGN-OFF|CHANGES REQUESTED)[ \t]+([0-9a-f]{40})\b", re.I)
MARK = {k: re.compile(r"^" + k + r":[ \t]*(\S+)[ \t]*$", re.I | re.M)
        for k in ("Review-Head", "Review-Task", "Reviewer-Agent", "Reviewer-Model")}
AUTHOR_AGENT = re.compile(r"^Author-Agent:[ \t]*(\S+)", re.I | re.M)     # first token only; extra text ignored
ROLE_NAMES = {"workflow-bugs", "workflow-current", "workflow-qa", "workflow-merger", "claude", "codex", "agent",
              "manager", "unknown", "none", "n/a", "tbd", "-"}                # a role is not an identity: unknown
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")     # GitHub author association; the repo is public
# Always owner: release/sign-off/tag/stable, Gate/approval, secrets, workflows, permission/settings/instruction files.
# Deliberately broad (anything with "release", "approval" or "signoff" in the path): unknown means owner.
EXCLUDED = (
    ".github/*", "CLAUDE.md", "*/CLAUDE.md", "AGENTS.md", "*/AGENTS.md", "CLAUDE.local.md", ".claude/*",
    "*releas*", "*approv*", "*signoff*", "*sign_off*", "*secrets*", "*permissions*", "*signing*",
    "*promote*", "*gate*", "*github-approve*", "core/tools/manual_test_sheet.py", "*dashboard_auth*", "*settings.json", "*settings.local.json",
    "core/src/jarves/drive_mode.py", "core/src/jarves/skills/__init__.py", "core/tools/risk_class.py",
    "docs/workflow/OPERATING_MODEL.md", "core/tools/tier_r_flow.py",
)
EXCLUDED_TITLE = re.compile(r"\b(release|sign-?off|tag|stable|promot\w*)\b", re.I)


def _match(path, patterns):
    path = path.replace("\\", "/").lower()
    return any(fnmatch.fnmatchcase(path, p.lower()) for p in patterns)


def author_agents(names):
    """Distinct lowercase author ids; role names and empties are unknown and dropped."""
    out = set()
    for n in names or []:
        tok = (str(n).split() or [""])[0].strip(",;:.").lower()
        if tok and tok not in ROLE_NAMES:
            out.add(tok)
    return sorted(out)


def excluded_reason(pr):
    """Why this PR may never be auto-merged, or ''."""
    if not pr.get("files"):
        return "no file list (unknown): owner"
    hit = [f for f in pr.get("files") or [] if _match(f, EXCLUDED)]
    if hit:
        return "excluded path: " + ", ".join(hit[:3])
    if pr.get("files_truncated"):
        return "file list truncated (unsure)"
    if EXCLUDED_TITLE.search(pr.get("title") or ""):
        return "release/sign-off/tag/stable wording in title"
    return ""


def parse_evidence(comments):
    """Trusted verdicts from PR comments, in order: [{verdict, sha, task, reviewer, model, complete}].
    A comment is a dict {body, login, association}; plain strings and untrusted associations are ignored.
    NOTE: every agent posts as one GitHub account, so authorship proves nothing about independence; the
    Reviewer-Agent / Review-Task markers are declarations the manager checks against the ledger."""
    out = []
    for c in comments or []:
        if not isinstance(c, dict) or str(c.get("association") or "").upper() not in TRUSTED:
            continue
        body = (c.get("body") or "").replace("\r\n", "\n").lstrip("\ufeff")
        m = VERDICT.match(body)
        if not m:
            continue
        sha = m.group(2).lower()
        mk = {k: (r.search(body).group(1) if r.search(body) else "") for k, r in MARK.items()}
        out.append({"verdict": m.group(1).upper().replace(" ", "_"), "sha": sha, "task": mk["Review-Task"],
                    "reviewer": mk["Reviewer-Agent"].lower(), "model": mk["Reviewer-Model"].lower(),
                    "complete": mk["Review-Head"].lower() == sha and bool(mk["Review-Task"] and mk["Reviewer-Agent"])})
    return out


def _at(ev, sha):
    sha = (sha or "").lower()
    return [e for e in ev if sha and e["sha"] == sha]


def decide(pr, main_red=False):
    """Next action for one PR dict: number, title, tier, draft, state, head_sha, files, authors,
    checks_green, checks_pending, merge_state, queued, comments. Returns {number, action, reasons}."""
    n = pr["number"]

    def out(action, *reasons):
        return {"number": n, "action": action, "reasons": list(reasons)}

    if pr.get("state", "OPEN") != "OPEN" or pr.get("draft"):
        return out("wait", "not open or draft")
    if pr.get("queued"):
        return out("wait", "already in merge queue")
    ex = excluded_reason(pr)
    if ex:
        return out("owner", ex, "keep today's rules: owner decides")
    if pr.get("tier") != "R":
        return out("wait", "not Tier R: ordinary steward rules apply")
    ev = parse_evidence(pr.get("comments"))
    sha = pr.get("head_sha") or ""
    if pr.get("merge_state") == "DIRTY":
        return out("fix", "conflict: fixer rebases the PR's own branch")
    if pr.get("checks_pending"):
        return out("wait", "checks pending on current head")
    if not pr.get("checks_green"):
        return out("fix", "required check red on current head")
    here = _at(ev, sha)
    changes = {e["sha"] for e in ev if e["verdict"] == "CHANGES_REQUESTED"}
    if len(changes) >= MAX_ROUNDS:      # round limit: a 3rd review round never happens, whatever it says
        return out("owner", f"{MAX_ROUNDS} review rounds used: record blocker in ledger and alert")
    authors = {a.lower() for a in pr.get("authors") or []} | set(author_agents(pr.get("author_agents")))
    if here and here[-1]["verdict"] == "CHANGES_REQUESTED":     # the LATEST verdict on this head wins
        return out("fix", "CHANGES REQUESTED on current head: Sonnet fixer pushes to the PR branch")
    if here and here[-1]["verdict"] == "SIGN-OFF":
        e = here[-1]
        if "opus" not in e["model"]:
            return out("owner", "sign-off is not from an Opus review", "model=" + (e["model"] or "unknown"))
        if not e["complete"]:
            return out("owner", "sign-off lacks Review-Head / Review-Task / Reviewer-Agent markers")
        if not author_agents(pr.get("author_agents")):
            return out("owner", "PR declares no Author-Agent: independence cannot be checked")
        if e["reviewer"] in authors:
            return out("owner", "reviewer is an author of the diff")
        if main_red:
            return out("wait", "main is red: only the fix PR is queued")
        if pr.get("merge_state") not in ("CLEAN", "BLOCKED", "HAS_HOOKS"):
            return out("wait", "merge state " + str(pr.get("merge_state")))
        return out("queue", "Opus SIGN-OFF bound to current head " + sha[:8] + " by task " + e["task"],
                   "steward checks Review-Task/Reviewer-Agent in the ledger, removes no-automerge, runs gh pr merge N --auto, "
                   "confirms merge-queue entry")
    return out("review", "green on head " + sha[:8] + " with no verdict for it: one Opus review task for this SHA")


def backlog(prs, max_workers=3):
    """Merge-backlog focus. prs: dicts with draft/state/queued/checks_green."""
    open_prs = [p for p in prs if p.get("state", "OPEN") == "OPEN" and not p.get("draft")]
    green = [p for p in open_prs if p.get("checks_green") and not p.get("queued")]
    on = len(open_prs) >= BACKLOG_OPEN or len(green) >= BACKLOG_GREEN
    return {"backlog": on, "open_prs": len(open_prs), "green_unqueued": len(green),
            "thresholds": {"open": BACKLOG_OPEN, "green_unqueued": BACKLOG_GREEN},
            "line": f"merge backlog: {len(open_prs)} PRs" if on else "",
            "worker_limit": max_workers,
            "priority": (["P0/P1 bug fixes", "running reviews and fixers", "steward queue",
                          "Tier R Opus reviews", "fixers for red or conflicted PRs"] if on else []),
            "pause_new_feature_work": on,
            "resume_when": f"open non-draft PRs < {BACKLOG_OPEN} and green unqueued < {BACKLOG_GREEN}"}


def focus(prs, max_workers=3, main_red=False):
    b = backlog(prs, max_workers)
    order = {"queue": 0, "fix": 1, "review": 2}
    work = sorted((x for x in (decide(p, main_red) for p in prs) if x["action"] in order),
                  key=lambda x: (order[x["action"]], x["number"]))
    b["merge_pipeline_work"] = work[:max_workers] if b["backlog"] else []
    b["slots_used_by_pipeline"] = len(b["merge_pipeline_work"])
    return b


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def _ib():
    spec = importlib.util.spec_from_file_location("jarves_ib_trf", Path(__file__).resolve().parent / "integration_branch.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_prs(numbers=None):
    """Read open PRs through gh (read-only)."""
    ib = _ib()
    nums = numbers or [x["number"] for x in json.loads(_run(["gh", "pr", "list", "--state", "open", "--limit", "100", "--json", "number"]))]
    prs = []
    for n in nums:
        info = ib.pr_info(n)
        d = json.loads(_run(["gh", "pr", "view", str(n), "--json", "headRefOid,author,body,commits,comments,autoMergeRequest,isInMergeQueue"]))
        authors = {d["author"]["login"]} | {a.get("login", "") for c in d.get("commits") or [] for a in c.get("authors") or []}
        checks = info["checks"]
        pend = any((c.get("status") or "").upper() in ("QUEUED", "IN_PROGRESS", "PENDING")
                   or (c.get("state") or "").upper() == "PENDING" for c in checks)
        prs.append({"number": n, "title": info["title"], "tier": info["tier"], "draft": info["draft"], "state": info["state"],
                    "labels": info["labels"], "files": info["files"], "files_truncated": info["files_truncated"],
                    "head_sha": d["headRefOid"], "authors": sorted(a for a in authors if a),
                    "checks_green": ib.checks_passed(checks), "checks_pending": pend, "merge_state": info["merge_state"],
                    "queued": bool(d.get("isInMergeQueue")) or bool(d.get("autoMergeRequest")),
                    "author_agents": author_agents(AUTHOR_AGENT.findall(d.get("body") or "")),
                    "comments": [{"body": c.get("body") or "", "login": (c.get("author") or {}).get("login", ""),
                                  "association": c.get("authorAssociation") or ""} for c in d.get("comments") or []]})
    return prs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "focus"):
        s = sub.add_parser(name)
        s.add_argument("--pr", type=int, nargs="*")
        s.add_argument("--main-red", action="store_true")
        s.add_argument("--max-workers", type=int, default=3)
    a = ap.parse_args(argv)
    prs = load_prs(a.pr)
    res = {"plan": [decide(p, a.main_red) for p in prs]} if a.cmd == "plan" else focus(prs, a.max_workers, a.main_red)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
