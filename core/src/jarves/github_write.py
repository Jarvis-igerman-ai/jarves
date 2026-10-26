"""GitHub WRITE FRAMEWORK (V7 slice G2, issue #409; umbrella #50). NOTHING IS WRITTEN HERE: no kind is implemented yet
(G3 issues/comments/labels, G4 pull requests/CI, G5 merge queue add handlers to HANDLERS). Every kind that passes the
gates returns GITHUB_WRITE_NOT_IMPLEMENTED, so the framework is testable without touching GitHub.

`execute(action)` is the ONLY path. In this order it refuses on:
  1. a FORBIDDEN kind (forever): delete repo, visibility/settings/secrets/org/permissions/branch protection/rulesets,
     force push, direct merge (merge only through the merge queue, G5)        -> GITHUB_WRITE_FORBIDDEN
  2. the switch `github_write` off (default OFF, high risk, beta gate, needs `github`)   -> GITHUB_WRITE_OFF
  3. an untrusted turn (text written by others was read this turn)                       -> GITHUB_WRITE_UNTRUSTED
  4. a repo not in `github_repos` of config.toml (default empty = nothing)               -> GITHUB_WRITE_REPO
  5. a token without write rights, probed with READ calls; fail closed                    -> GITHUB_WRITE_SCOPE
  6. the rate limit (MAX_WRITES per WINDOW_S)                                             -> GITHUB_WRITE_RATE_LIMIT
  7. the owner's approval of the EXACT action (ApprovalService; answered only by the owner's channels, never by the
     model or text), bound to the action hash: an approval for A never covers B          -> GITHUB_WRITE_DENIED/TIMEOUT
  then the handler, or GITHUB_WRITE_NOT_IMPLEMENTED. Every outcome is audited (kind, repo, target, hash, result code:
  never payload text or a token). The token never reaches this module's logs.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from . import clilang, logs
from . import features as feat
from . import github_client as gc
from .approvals import append_audit
from .channels_common import targets

FEATURE = "github_write"
MAX_WRITES, WINDOW_S = 10, 600.0
GRANT_TTL_S = 300.0
# kind -> token permission class the owner must grant (classic tokens: scope `repo`). Reserved names for G3-G5.
KIND_PERMS: dict[str, str] = {
    "issue.create": "Issues: write", "issue.edit": "Issues: write", "issue.comment": "Issues: write",
    "issue.labels": "Issues: write", "pr.open": "Pull requests: write", "pr.update": "Pull requests: write",
    "pr.comment": "Pull requests: write", "pr.review": "Pull requests: write", "ci.rerun": "Actions: write",
    "merge.enqueue": "Pull requests: write"}
_FORBIDDEN_TOKENS = frozenset({"secret", "secrets", "org", "orgs", "organization", "permission", "permissions", "protection",
                               "ruleset", "rulesets", "visibility", "settings", "force", "admin", "bypass", "transfer",
                               "webhook", "webhooks", "collaborator", "collaborators", "deploykey"})
_FORBIDDEN_KINDS = frozenset({"repo.delete", "delete.repo", "repo.archive", "pr.merge", "merge", "merge.direct",
                              "merge.squash", "push.force", "branch.delete", "release.delete"})
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
HANDLERS: dict[str, Callable[["WriteAction", gc.GitHubClient], Awaitable[dict]]] = {}     # filled by G3+


class WriteAction(BaseModel):
    """One exact write. `preview` is human-readable; the approval ALSO shows the exact payload, so a misleading preview
    cannot hide what is sent."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str = Field(max_length=60)
    repo: str = Field(max_length=201)                 # "owner/repo"
    target: str = Field("", max_length=100)           # issue / PR number, ref or "" (a new object)
    payload: dict = Field(default_factory=dict)
    preview: str = Field(max_length=600)

    def hash(self) -> str:
        blob = json.dumps([self.kind.strip().lower(), self.repo.strip().lower(), self.target, self.payload, self.preview],
                          sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def approval_text(self) -> str:
        body = json.dumps(self.payload, ensure_ascii=False, sort_keys=True, default=str)
        return (f"GitHub write: {self.kind} on {self.repo}" + (f" ({self.target})" if self.target else "")
                + f"\n{self.preview}\nExact payload: {body[:1200]}\nRef {self.hash()[:10]}")


class WriteResult(BaseModel):
    ok: bool
    code: str = ""
    message: str = ""
    data: dict = Field(default_factory=dict)


def forbidden(kind: str) -> bool:
    k = re.sub(r"\s+", "", str(kind or "")).lower()
    toks = set(re.split(r"[._:/-]+", k))
    return (not k or k in _FORBIDDEN_KINDS or bool(toks & _FORBIDDEN_TOKENS) or "force" in k
            or ("delete" in toks and "repo" in toks) or ("merge" in toks and k != "merge.enqueue"))


class GitHubWriter:
    """`feats` has `.active(id)`; `approvals` is an ApprovalService; `client` builds a GitHubClient (tests: FakeGitHub)."""

    def __init__(self, home: Path, feats, approvals, *, repos: list[str] | None = None,
                 client: Callable[[], gc.GitHubClient] | None = None, clock: Callable[[], float] = time.time,
                 limit: tuple[int, float] = (MAX_WRITES, WINDOW_S)) -> None:
        self.home, self.feats, self.approvals = Path(home), feats, approvals
        self.repos = [r.strip().lower() for r in (repos or []) if isinstance(r, str) and _REPO.match(r.strip())]
        self.client = client or (lambda: gc.GitHubClient(self.home))
        self.clock, self.limit = clock, limit
        self._recent: list[float] = []
        self._grants: dict[str, float] = {}          # action hash -> expiry, single use (batch approvals)

    def _refuse(self, a: WriteAction, code: str, msg: str, origin: str) -> WriteResult:
        self._audit(a, code, origin)
        return WriteResult(ok=False, code=code, message=msg)

    def _audit(self, a: WriteAction, code: str, origin: str) -> None:
        append_audit(self.home / "github" / "write_audit.jsonl", {
            "ts": round(self.clock(), 3), "kind": str(a.kind)[:60], "repo": str(a.repo)[:201], "target": a.target[:100],
            "hash": a.hash()[:16], "origin": origin, "result": code})
        logs.info("github", "GITHUB_WRITE", "github write attempt", kind=a.kind[:60], result=code, origin=origin)

    def _scope_problem(self, a: WriteAction) -> str:
        """"" = the token can write; else why not. READ calls only; anything unknown is refused (fail closed)."""
        owner, _, repo = a.repo.partition("/")
        try:
            p = self.client().write_probe(owner, repo)
        except gc.GitHubError as e:
            return f"could not verify the token's write permission ({e.code})"
        perm = KIND_PERMS.get(a.kind.strip().lower(), "write access")
        if p["scopes"] is not None:                                    # classic token: X-OAuth-Scopes
            return "" if {"repo", "public_repo"} & set(p["scopes"]) else f"the token lacks the 'repo' scope ({perm})"
        return "" if p["push"] is True else f"the token has no write permission on the repo ({perm})"

    async def _gates(self, a: WriteAction, origin: str, untrusted: bool, rate: bool = True) -> WriteResult | None:
        if forbidden(a.kind):
            return self._refuse(a, "GITHUB_WRITE_FORBIDDEN", "this kind of change is never allowed", origin)
        if not self.feats.active(FEATURE):
            return self._refuse(a, "GITHUB_WRITE_OFF", "GitHub writes are off (owner: jarvis github write on)", origin)
        if untrusted or origin not in ("user", "model"):
            return self._refuse(a, "GITHUB_WRITE_UNTRUSTED", "untrusted text was read this turn; no write now", origin)
        if a.repo.strip().lower() not in self.repos:
            return self._refuse(a, "GITHUB_WRITE_REPO", "this repo is not in the owner's github_repos list", origin)
        why = self._scope_problem(a)
        if why:
            return self._refuse(a, "GITHUB_WRITE_SCOPE", why, origin)
        if not rate:                                   # a batch action was counted when the batch was approved
            return None
        now, (n, window) = self.clock(), self.limit
        self._recent = [t for t in self._recent if now - t < window]
        if len(self._recent) >= n:
            return self._refuse(a, "GITHUB_WRITE_RATE_LIMIT", f"more than {n} writes in {int(window)} s", origin)
        self._recent.append(now)
        return None

    async def _ask(self, actions: list[WriteAction], origin: str) -> str:
        """"" = approved, else "denied" / "timeout". The prompt shows every exact action; details carry the hashes."""
        a = actions[0]
        title = f"GitHub: {a.kind} on {a.repo}" if len(actions) == 1 else f"GitHub: {len(actions)} actions on {a.repo}"
        ok = await self.approvals.ask(
            "\n---\n".join(x.approval_text() for x in actions), title=title[:120], risk="high", kind="tool",
            why="a change on GitHub; the exact action is shown", source=origin,
            details={"action_hashes": ",".join(x.hash()[:16] for x in actions), "repo": a.repo, "kind": a.kind})
        return "" if ok else ("timeout" if getattr(self.approvals, "last_resolution", "") == "timeout" else "denied")

    async def approve_batch(self, actions: list[WriteAction], *, origin: str = "user", untrusted: bool = False) -> WriteResult:
        """ONE approval listing every action of a task (1 to 10, one repo). Each action hash is then spendable ONCE by
        execute(); an action that was not listed still asks again."""
        if not 1 <= len(actions) <= 10 or len({x.repo.lower() for x in actions}) != 1:
            return WriteResult(ok=False, code="GITHUB_WRITE_BAD_ARGUMENT", message="a batch is 1 to 10 actions on one repo")
        for a in actions:                     # the same gates as execute(), BEFORE the owner is asked
            r = await self._gates(a, origin, untrusted)
            if r is not None:
                return r
        why = await self._ask(actions, origin)
        if why:
            return self._refuse(actions[0], "GITHUB_WRITE_TIMEOUT" if why == "timeout" else "GITHUB_WRITE_DENIED",
                                "the owner did not approve the batch", origin)
        for a in actions:
            self._grants[a.hash()] = self.clock() + GRANT_TTL_S
        return WriteResult(ok=True, code="GITHUB_WRITE_APPROVED", message=f"{len(actions)} actions approved")

    async def execute(self, a: WriteAction, *, origin: str = "model", untrusted: bool = False) -> WriteResult:
        """The ONE write path. `untrusted` = text written by others was read in this turn."""
        now, h = self.clock(), a.hash()
        self._grants = {k: v for k, v in self._grants.items() if v > now}
        granted = h in self._grants                    # a batch approval of exactly this action
        if granted:                                    # (the gates still run: switch, repo, scope, rate)
            self._grants.pop(h)
        r = await self._gates(a, origin, untrusted, rate=not granted)
        if r is not None:
            return r
        if not granted:
            why = await self._ask([a], origin)
            if why:
                return self._refuse(a, "GITHUB_WRITE_TIMEOUT" if why == "timeout" else "GITHUB_WRITE_DENIED",
                                    "the owner did not approve this exact action", origin)
        handler = HANDLERS.get(a.kind.strip().lower())
        if handler is None:
            return self._refuse(a, "GITHUB_WRITE_NOT_IMPLEMENTED", "this kind of write is not implemented yet", origin)
        try:
            data = await handler(a, self.client())
        except gc.GitHubError as e:
            return self._refuse(a, e.code, e.message, origin)
        self._audit(a, "OK", origin)
        return WriteResult(ok=True, code="OK", data=data)

    def status(self) -> dict:
        return {"switch_on": bool(self.feats.active(FEATURE)), "allowed_repos": list(self.repos),
                "implemented_kinds": sorted(HANDLERS), "reserved_kinds": sorted(KIND_PERMS),
                "token_permissions_per_class": sorted(set(KIND_PERMS.values())),
                "limit": f"{self.limit[0]} per {int(self.limit[1])} s"}


# ------------------------------------------------------------------------------------------ feature + CLI
def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    if not gc.connected(c.home):
        return False, f"GitHub not connected; tell the user: \"תריץ במחשב {gc.START_COMMAND}, ואני אדריך אותך\""
    return True, ""


feat.register(FEATURE, "GitHub: כתיבה באישור", "GitHub: writes with approval", False, probe=_probe, needs=["github"],
              risk="high",
              about="framework for changing GitHub (issues, pull requests, CI) only after you approve the exact action, "
                    "only on repos you list, never merge directly (nothing is written yet)",
              targets=targets("UNIT test_github_write.py (fake GitHub API and approvals, no network)",
                              "no write kind exists yet and it never touched the real GitHub API; the owner sets the token permissions"),
              permissions=["writes approved each time"], settings="jarvis github write status")


def cmd_github_write(a, cfg, home) -> int:
    """jarvis github write status|on|off: the switch and what is allowed (the allow-list is `github_repos` in config.toml)."""
    from .cli import _cli_features
    f = _cli_features(cfg, home)
    mode = getattr(a, "write_action", None) or "status"
    if mode in ("on", "off"):
        try:
            f.set(FEATURE, mode == "on", by="cli")
        except feat.BetaLocked as e:
            print(f"{FEATURE}: {e} (jarvis features beta on)")
            return 1
        print(clilang.bi("כתיבה ל-GitHub מופעלת, כל כתיבה באישור שלך. / GitHub writes are ON, each needs your approval."
                         if mode == "on" else "כתיבה ל-GitHub כבויה. / GitHub writes are OFF."))
    print(json.dumps(GitHubWriter(home, f, None, repos=list(getattr(cfg, "github_repos", []) or [])).status(),
                     ensure_ascii=False))
    return 0
