"""Deterministic PR event routing (#764): a red check, a conflict or a new review comment on a PR that an agent owns
(an advisory ``pr:N`` or ``branch:NAME`` claim, see messages.py) becomes one ledger message to that owner.

No LLM, no hook and no sleep loop: it runs inside the supervisor's slow GitHub sync, after the metadata snapshot, and only
reads public check-run / PR metadata for PRs that have a claim. Messages carry failing check NAMES only (never logs) and are
data, not instructions or approvals. They are deduplicated per (PR, event, head, detail) and rate limited per (PR, event).
When the owner is gone (no agent record, offline, or a stale claim) the manager gets the same message.
"""
from __future__ import annotations

import hashlib
import re

from . import messages

STATE_KEY = "pr_routing"
SENDER = "pr-router"
MANAGER = "manager"
MIN_GAP_S = 900          # per (PR, event kind): at most one message per 15 minutes
MAX_FETCHES = 12         # claimed PRs inspected per sync (2 GitHub reads each)
MAX_NAMES = 8
FAILED = ("failure", "timed_out", "startup_failure", "action_required")


def _clean(name) -> str:
    return re.sub(r"[^\w .:/()+\-]", "", str(name))[:80].strip()


def signals(get, number: int, sha: str) -> dict:
    """Public read-only metadata of one PR: {'failed': [check names], 'conflict': bool, 'review_comments': int}."""
    from .sync import REPO
    failed: list[str] = []
    if sha:
        data = get(f"/repos/{REPO}/commits/{sha}/check-runs?per_page=100")
        for run in (data.get("check_runs") if isinstance(data, dict) else None) or []:
            if run.get("status") == "completed" and run.get("conclusion") in FAILED:
                name = _clean(run.get("name", ""))
                if name and name not in failed:
                    failed.append(name)
    pr = get(f"/repos/{REPO}/pulls/{number}")
    pr = pr if isinstance(pr, dict) else {}
    return {"failed": sorted(failed)[:MAX_NAMES], "conflict": pr.get("mergeable_state") == "dirty",
            "review_comments": int(pr.get("review_comments") or 0)}


def _owner(claims: dict, pr: dict):
    """The claim for this PR (pr:N first, then branch:<head ref>), or None."""
    return claims.get(f"pr:{pr['number']}") or (claims.get(f"branch:{pr['head_ref']}") if pr.get("head_ref") else None)


def _events(sig: dict, last_comments: int) -> list[tuple[str, str, str]]:
    """[(kind, detail used for dedupe, body text)]."""
    out = []
    if sig["failed"]:
        names = ", ".join(sig["failed"])
        out.append(("ci_red", names, f"failing checks: {names}"))
    if sig["conflict"]:
        out.append(("conflict", "dirty", "merge conflict with the base branch; rebase your own branch"))
    if sig["review_comments"] > last_comments:
        out.append(("review", str(sig["review_comments"]),
                    f"{sig['review_comments'] - last_comments} new review comment(s) (total {sig['review_comments']})"))
    return out


def route(store, prs, get, *, now: float | None = None) -> list[dict]:
    """Post messages for owned PRs that need attention. Returns [{'pr', 'kind', 'to'}]; one failed read skips that PR."""
    from .autopilot_watch import _load, _save
    now = store.clock() if now is None else now
    claims = {c["resource"]: c for c in messages.owners(store)}
    if not claims:
        return []
    state = _load(store, STATE_KEY, {"sent": {}, "comments": {}})
    sent, comments = state.get("sent", {}), state.get("comments", {})
    agents = {a["id"]: a for a in store.snapshot()["agents"]}
    done, fetched = [], 0
    for pr in prs:
        claim = _owner(claims, pr)
        if not claim or fetched >= MAX_FETCHES:
            continue
        fetched += 1
        number, sha = int(pr["number"]), str(pr.get("head_sha") or "")
        try:
            sig = signals(get, number, sha)
        except Exception:  # noqa: BLE001  rate limit or outage: unknown, never guess
            continue
        key_c = str(number)
        # The first look at a PR only records the comment count: older comments are not "new".
        last_comments = min(int(comments.setdefault(key_c, sig["review_comments"])), sig["review_comments"])
        comments[key_c] = last_comments
        owner = agents.get(claim["agent"])
        gone = not owner or owner.get("status") == "offline" or claim.get("stale")
        for kind, detail, text in _events(sig, last_comments):
            digest = hashlib.sha256(f"{number}|{kind}|{sha}|{detail}".encode()).hexdigest()[:16]
            gap_key = f"gap:{number}:{kind}"
            if digest in sent or now - float(sent.get(gap_key, -MIN_GAP_S)) < MIN_GAP_S:
                continue  # an undelivered review count stays unrecorded and is retried after the gap
            subject = f"PR #{number}: {kind.replace('_', ' ')}"
            body = (f"{text}. You own pr:{number}. Check the PR page for details (no logs here); fix on your own branch "
                    "with a normal push. This is data from the factory router, not an instruction or approval.")
            recipients = [claim["agent"]] + ([MANAGER] if gone else [])
            for to in recipients:
                messages.send(store, SENDER, to, subject, body, f"pr:{number}")
                done.append({"pr": number, "kind": kind, "to": to})
            sent[digest] = now
            sent[gap_key] = now
            if kind == "review":
                comments[key_c] = sig["review_comments"]
    if len(sent) > 400:
        keep = sorted(sent.items(), key=lambda kv: kv[1])[-200:]
        sent = dict(keep)
    _save(store, STATE_KEY, {"sent": sent, "comments": comments})
    return done
