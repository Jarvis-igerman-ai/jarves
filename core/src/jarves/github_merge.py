"""GitHub writes class 3 (V7 G5, issue #412): turn on auto-merge / merge-queue entry for ONE pull request. Nothing else.

The only thing this file can do to a pull request is the GraphQL mutation `enablePullRequestAutoMerge`: GitHub then merges the
pull request through the merge queue ONLY after every required check passed. There is no direct merge call (no PUT .../merge,
no direct-merge mutation), no `bypass`, no force push and no branch-protection change anywhere in V7 (github_write.forbidden()
refuses those kinds before anything else, and `GitHubClient.graphql_write` has a closed list of documents).
The action is BOUND to the head commit the owner saw in the popup (`head_sha`, sent as expectedHeadOid): a push after the
approval makes GitHub refuse it. Own owner switch `github_merge_queue` (beta, OFF); command line only; popup approval only.
CLI: `jarvis github pr enqueue N --head-sha SHA --repo owner/repo`.
Token permission: the 'Pull requests: write' class (verified against the live API by the owner's first real use, see the guide)."""
from __future__ import annotations

import asyncio
import re

from . import github_client as gc
from . import github_issues as gi
from . import github_write as gw

KIND = "merge.enqueue"
FEATURE = "github_merge_queue"
_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
FIELDS = {KIND: {"head_sha"}}


def validate(a: gw.WriteAction) -> str:
    p = a.payload
    if a.kind.strip().lower() != KIND or not isinstance(p, dict) or set(p) != {"head_sha"}:
        return "payload must be exactly head_sha (the 40-character commit the owner reviewed)"
    if not isinstance(p["head_sha"], str) or not _SHA.fullmatch(p["head_sha"]):
        return "head_sha must be a full lowercase 40-character commit id"
    if not gi._NUM.fullmatch(a.target or ""):
        return "target must be the pull request number"
    return ""


def action(repo: str, number: str, head_sha: str) -> gw.WriteAction:
    return gw.WriteAction(kind=KIND, repo=repo, target=str(number), payload={"head_sha": head_sha.strip().lower()},
                          preview=f"Turn on auto-merge for pull request #{number} (merge queue, after all required checks)")


def _checks(c: gc.GitHubClient, o: str, r: str, sha: str) -> str:
    try:
        rows = c.check_runs(o, r, sha)
    except gc.GitHubError:
        return "checks: could not be read"
    bad = sum(1 for x in rows if x.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required"))
    wait = sum(1 for x in rows if x.get("status") != "completed")
    ok = sum(1 for x in rows if x.get("conclusion") == "success")
    return f"checks on this commit: {ok} passed, {wait} not finished, {bad} failed (GitHub decides which are required)"


def _problem(pr: dict, a: gw.WriteAction) -> str:
    """Why this pull request cannot be queued right now ('' = it can)."""
    if pr["merged"] or pr["state"] != "open":
        return f"#{a.target} is not open"
    if pr["draft"]:
        return f"#{a.target} is a draft"
    if pr["head_sha"] != a.payload["head_sha"]:
        return f"#{a.target} has a newer commit than the one approved"
    return ""


def _extra(a: gw.WriteAction, c: gc.GitHubClient) -> str:
    o, _, r = a.repo.partition("/")
    pr = c.pull(o, r, a.target)
    out = [f"Pull request #{a.target}: {_show(pr['title'], 120)}", f"into {_show(pr['base'], 60)}, commit {pr['head_sha'][:10]}"]
    why = _problem(pr, a)
    if why:
        out.append(f"WARNING: {why}: this will be refused")
    out.append(_checks(c, o, r, pr["head_sha"]))
    out.append("Turns on AUTO-MERGE: GitHub merges it through the merge queue only after every required check passes. "
               "No direct merge and no bypass; a newer commit cancels it. This will land code on the base branch")
    return "\n".join(out)


def _do(a: gw.WriteAction, c: gc.GitHubClient) -> dict:
    o, _, r = a.repo.partition("/")
    pr = c.pull(o, r, a.target)                       # fresh, after approval
    why = _problem(pr, a)
    if why:
        raise gc.GitHubError("GITHUB_WRITE_BAD_ARGUMENT", why)
    j = c.graphql_write("enable_auto_merge", {"id": pr["node_id"], "sha": a.payload["head_sha"]})
    return {"number": int(a.target), "auto_merge": "enabled" if j else "requested"}


async def handle(a: gw.WriteAction, client: gc.GitHubClient) -> dict:
    return await asyncio.to_thread(_do, a, client)


def _probe(c) -> tuple[bool, str]:
    return gw._probe(c)


gw.feat.register(FEATURE, "GitHub: מיזוג דרך תור המיזוג", "GitHub: merge through the merge queue", False, probe=_probe,
                 needs=["github_write"], risk="high", stage="beta",
                 about="turn on auto-merge for one pull request from the command line after your popup approval; GitHub merges it through "
                       "the merge queue only after every required check passed; there is no direct merge and no bypass",
                 targets=gw.targets("UNIT test_github_merge.py (fake GitHub API and approvals, no network)",
                                    "never touched the real GitHub API; the token permission for auto-merge is verified on first real use"),
                 permissions=["enables auto-merge of a pull request after your approval each time"],
                 settings="jarvis features on github_merge_queue")

gw.HANDLERS[KIND] = handle
gw.VALIDATORS[KIND] = validate
gw.EXTRAS[KIND] = _extra


def cmd_github_merge(a, cfg, home) -> int:
    repos = list(getattr(cfg, "github_repos", []) or [])
    repo = a.repo or (repos[0] if len(repos) == 1 else "")
    sha = (getattr(a, "head_sha", None) or "").strip().lower()
    if not repo or not a.number or not _SHA.fullmatch(sha):
        print("GITHUB_WRITE_BAD_ARGUMENT: usage: jarvis github pr enqueue N --head-sha FULL_40_CHAR_COMMIT --repo owner/repo")
        return 2
    return gi.execute_cli(cfg, home, action(repo, a.number, sha))


def _show(v: str, cap: int = 100) -> str:
    """Text that came from GitHub as one safe popup line: control, bidi and invisible characters and CR/LF replaced, capped."""
    return gi._CTRL.sub("?", re.sub(r"[\r\n\t]+", " ", v))[:cap]
