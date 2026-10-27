"""GitHub writes class 3 (V7 G5, issue #412): turn on auto-merge for ONE pull request whose base branch has a MERGE QUEUE.

The only thing this file can do to a pull request is the GraphQL mutation `enablePullRequestAutoMerge`, and only when the base
branch has a merge queue (checked live, fail closed): GitHub then puts the pull request into the queue once the required
checks pass and merges it from there. There is no direct merge call (no PUT .../merge, no direct-merge mutation), no `bypass`,
no force push and no branch-protection change anywhere in V7 (github_write.forbidden() refuses those kinds before anything
else, and `GitHubClient.graphql_write` has a closed list of documents).
What it does NOT promise (GitHub's rules, stated in the popup): the head commit is checked once, when auto-merge is turned on;
a later push by someone with write access does NOT cancel it, so it is merged after the queue's checks without a new look from
you. The action is bound to the head commit and the base branch the owner saw (head_sha, base): a change of either before the
write refuses it. Own owner switch `github_merge_queue` (beta, OFF); command line only; popup approval only.
CLI: `jarvis github pr enqueue N --head-sha SHA [--base main] --repo owner/repo`.
Token permission: Pull requests: write (a fine-grained token may need more; unverified, see the guide). No live call was made."""
from __future__ import annotations

import asyncio
import re

from . import github_client as gc
from . import github_issues as gi
from . import github_write as gw

KIND = "merge.enqueue"
FEATURE = "github_merge_queue"
_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
FIELDS = {KIND: {"head_sha", "base"}}


def validate(a: gw.WriteAction) -> str:
    p = a.payload
    if a.kind.strip().lower() != KIND or not isinstance(p, dict) or set(p) != {"head_sha", "base"}:
        return "payload must be exactly head_sha (the 40-character commit the owner reviewed) and base (the branch it merges into)"
    if not isinstance(p["head_sha"], str) or not _SHA.fullmatch(p["head_sha"]):
        return "head_sha must be a full lowercase 40-character commit id"
    if not isinstance(p["base"], str) or not gc._REF.fullmatch(p["base"]) or p["base"].startswith("-"):
        return "base must be a plain branch name of this repo"
    if not gi._NUM.fullmatch(a.target or ""):
        return "target must be the pull request number"
    return ""


def action(repo: str, number: str, head_sha: str, base: str = "main") -> gw.WriteAction:
    return gw.WriteAction(kind=KIND, repo=repo, target=str(number), payload={"head_sha": head_sha.strip().lower(), "base": base},
                          preview=f"Turn on auto-merge for pull request #{number} into {base} (merge queue)")


def _checks(c: gc.GitHubClient, o: str, r: str, sha: str) -> str:
    try:
        rows = c.check_runs(o, r, sha)
    except gc.GitHubError:
        return "check runs: could not be read"
    bad = sum(1 for x in rows if x.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required"))
    wait = sum(1 for x in rows if x.get("status") != "completed")
    ok = sum(1 for x in rows if x.get("conclusion") == "success")
    return f"check runs on this commit (may be partial): {ok} passed, {wait} not finished, {bad} failed; GitHub decides which are required"


def _problem(pr: dict, a: gw.WriteAction) -> str:
    """Why this pull request cannot be queued right now ('' = it can)."""
    if pr["merged"] or pr["state"] != "open":
        return f"#{a.target} is not open"
    if pr["draft"]:
        return f"#{a.target} is a draft"
    if pr["head_sha"] != a.payload["head_sha"]:
        return f"#{a.target} has a newer commit than the one approved"
    if pr["base"] != a.payload["base"]:
        return f"#{a.target} now merges into a different branch than the one approved"
    if pr["base_repo"].lower() != a.repo.lower():
        return f"#{a.target} does not belong to {a.repo}"
    return ""


def _extra(a: gw.WriteAction, c: gc.GitHubClient) -> str:
    o, _, r = a.repo.partition("/")
    pr = c.pull(o, r, a.target)
    fork = pr["head_repo"].lower() != a.repo.lower()
    out = [f"Pull request #{a.target}: {_show(pr['title'], 120)} by {_show(pr['author'], 40) or '?'}",
           f"from {_show(pr['head_repo'], 80) or '?'} branch {_show(pr['head_ref'], 60) or '?'}{' (FORK)' if fork else ''} "
           f"into {_show(pr['base'], 60)}, commit {pr['head_sha'][:10]}"]
    if fork:
        out.append("WARNING: this pull request comes from a FORK: code that is not yours will be merged")
    why = _problem(pr, a)
    if why:
        out.append(f"WARNING: {why}: this will be refused")
    try:
        queue = c.has_merge_queue(o, r, a.payload["base"])
    except gc.GitHubError:
        queue = None
    if queue is not True:
        out.append("WARNING: " + ("the base branch has NO merge queue" if queue is False else "the merge queue could not be read")
                   + ": this will be refused (auto-merge is only turned on for a branch with a merge queue)")
    out.append(_checks(c, o, r, pr["head_sha"]))
    out.append("Turns on AUTO-MERGE: GitHub queues it after the required checks pass and merges it from the queue. No direct merge, no bypass. "
               "The commit is checked only now: a LATER push by anyone with write access does NOT cancel it, and it then merges "
               "without a new look from you. A later change of the base branch does not cancel it either: on a branch without a merge "
               "queue it could merge directly once the checks pass. This will land code on the base branch")
    return "\n".join(out)


def _do(a: gw.WriteAction, c: gc.GitHubClient) -> dict:
    o, _, r = a.repo.partition("/")
    pr = c.pull(o, r, a.target)                       # fresh, after approval
    why = _problem(pr, a)
    if why:
        raise gc.GitHubError("GITHUB_WRITE_BAD_ARGUMENT", why)
    if c.has_merge_queue(o, r, a.payload["base"]) is not True:
        raise gc.GitHubError("GITHUB_WRITE_BAD_ARGUMENT", f"{a.payload['base']} has no merge queue: auto-merge is not turned on")
    c.graphql_write("enable_auto_merge", {"id": pr["node_id"], "sha": a.payload["head_sha"]})      # raises unless GitHub confirms
    return {"number": int(a.target), "auto_merge": "enabled"}


async def handle(a: gw.WriteAction, client: gc.GitHubClient) -> dict:
    return await asyncio.to_thread(_do, a, client)


def _probe(c) -> tuple[bool, str]:
    return gw._probe(c)


gw.feat.register(FEATURE, "GitHub: מיזוג דרך תור המיזוג", "GitHub: merge through the merge queue", False, probe=_probe,
                 needs=["github_write"], risk="high", stage="beta",
                 about="turn on auto-merge for one pull request from the command line after your popup approval; only for a branch with a merge "
                       "queue; there is no direct merge and no bypass",
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
    base = getattr(a, "base", None) or "main"
    if not repo or not a.number or not _SHA.fullmatch(sha):
        print("GITHUB_WRITE_BAD_ARGUMENT: usage: jarvis github pr enqueue N --head-sha FULL_40_CHAR_COMMIT [--base main] --repo owner/repo")
        return 2
    return gi.execute_cli(cfg, home, action(repo, a.number, sha, base))


def _show(v: str, cap: int = 100) -> str:
    """Text that came from GitHub as one safe popup line: control, bidi and invisible characters and CR/LF replaced, capped."""
    return gi._CTRL.sub("?", re.sub(r"[\r\n\t]+", " ", v))[:cap]
