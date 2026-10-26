"""GitHub WRITE FRAMEWORK (V7 G2 #409; umbrella #50). G3 (github_issues.py) implements issues/comments/labels; G4 pull
requests/CI and G5 merge queue add handlers to HANDLERS. A kind without a handler returns GITHUB_WRITE_NOT_IMPLEMENTED.

`execute(action)` is the ONLY path. In this order it refuses on:
  1. a FORBIDDEN kind (forever): delete repo, visibility/settings/secrets/org/permissions/branch protection/rulesets,
     force push, direct merge (merge only through the merge queue, G5)        -> GITHUB_WRITE_FORBIDDEN
  2. the switch `github_write` off (default OFF, high risk, beta gate, needs `github`)   -> GITHUB_WRITE_OFF
  3. an untrusted turn (text written by others was read this turn)                       -> GITHUB_WRITE_UNTRUSTED
  4. a repo not in `github_repos` of config.toml (default empty = nothing)               -> GITHUB_WRITE_REPO
  5. a token without write rights, probed with READ calls (canonical repo name, issues-family GET); fail closed
                                                                                          -> GITHUB_WRITE_SCOPE
     (a retry of the same action within 10 minutes -> GITHUB_WRITE_DUPLICATE; a 403 after approval is authoritative)
  6. the rate limit (MAX_WRITES per WINDOW_S)                                             -> GITHUB_WRITE_RATE_LIMIT
  7. the owner's approval of the EXACT action (ApprovalService; answered only by the owner's channels, never by the
     model or text), bound to the action hash: an approval for A never covers B          -> GITHUB_WRITE_DENIED/TIMEOUT
  then the handler, or GITHUB_WRITE_NOT_IMPLEMENTED. Every outcome is audited (kind, repo, target, hash, result code:
  never payload text or a token). The token never reaches this module's logs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
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
    "labels.add": "Issues: write", "labels.remove": "Issues: write", "pr.open": "Pull requests: write", "pr.update": "Pull requests: write",
    "pr.comment": "Pull requests: write", "pr.review": "Pull requests: write", "ci.rerun": "Actions: write",
    "merge.enqueue": "Pull requests: write"}
_FORBIDDEN_TOKENS = frozenset({"secret", "secrets", "org", "orgs", "organization", "permission", "permissions", "protection",
                               "ruleset", "rulesets", "visibility", "settings", "force", "admin", "bypass", "transfer",
                               "webhook", "webhooks", "collaborator", "collaborators", "deploykey"})
_FORBIDDEN_KINDS = frozenset({"repo.delete", "delete.repo", "repo.archive", "pr.merge", "merge", "merge.direct",
                              "merge.squash", "push.force", "branch.delete", "release.delete"})
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
HANDLERS: dict[str, Callable[["WriteAction", gc.GitHubClient], Awaitable[dict]]] = {}     # filled by G3+
EXTRAS: dict[str, Callable[["WriteAction", gc.GitHubClient], str]] = {}    # kind -> approval lines from a READ (G3b)
VALIDATORS: dict[str, Callable[["WriteAction"], str]] = {}      # kind -> "" or why the payload is refused (G3+)
MAX_APPROVAL_CHARS = 1700                  # ApprovalService.ask cuts a prompt at 2000: never cut what the owner must see
_NOT_APPROVED = frozenset({"GITHUB_WRITE_FORBIDDEN", "GITHUB_WRITE_OFF", "GITHUB_WRITE_UNTRUSTED", "GITHUB_WRITE_REPO",
                           "GITHUB_WRITE_SCOPE", "GITHUB_WRITE_RATE_LIMIT", "GITHUB_WRITE_BAD_ARGUMENT", "GITHUB_WRITE_DUPLICATE",
                           "GITHUB_WRITE_DENIED", "GITHUB_WRITE_TIMEOUT", "GITHUB_WRITE_UNREADABLE"})   # every other audited result = the owner approved it
DUP_WINDOW_S = 600.0                       # the same approved action is not executed twice within this time
_TOGGLES = frozenset({"issue.edit", "labels.add", "labels.remove"})   # a state: only the latest write on the target counts
_XREF = re.compile(r"(?<![\w/.-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+")
_LINK = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|org|net|io|dev)\b", re.I)


class WriteAction(BaseModel):
    """One exact write. `preview` is human-readable; the approval ALSO shows the exact payload, so a misleading preview
    cannot hide what is sent."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str = Field(max_length=60)
    repo: str = Field(max_length=201)                 # "owner/repo"
    target: str = Field("", max_length=100)           # issue / PR number, ref or "" (a new object)
    payload: dict = Field(default_factory=dict)
    preview: str = Field(max_length=600)

    def normalised(self) -> dict:
        """The payload with label / assignee sets sorted and lower-cased (order and case have no meaning on GitHub). Text
        is NOT trimmed: the hash binds batch grants, so it must cover exactly the text that is sent."""
        out: dict = {}
        for k, v in self.payload.items():
            if isinstance(v, list) and k in ("labels", "assignees"):
                v = sorted(str(x).strip().lower() for x in v)
            out[k] = v
        return out

    def hash(self) -> str:
        """Over kind, repo, target and the normalised payload (the target state included) but NOT the preview, so
        rewording the preview cannot dodge the duplicate guard."""
        blob = json.dumps([self.kind.strip().lower(), self.repo.strip().lower(), self.target, self.normalised()],
                          sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def target_key(self) -> str:
        """The thing this action changes (kind + repo + target): the duplicate guard compares only with the LATEST write
        on the same thing, so open -> closed -> open is a legitimate toggle."""
        return hashlib.sha256(f"{self.kind.strip().lower()}|{self.repo.strip().lower()}|{self.target}".encode()).hexdigest()[:16]

    def warnings(self) -> list[str]:
        """Mentions and links in the text notify or lead real people: say so, at the top of the approval."""
        text = json.dumps(self.payload, ensure_ascii=False, default=str)
        out = []
        if "@" in text:
            out.append("WARNING: the text contains @ : a mention notifies that person or team on GitHub")
        if _LINK.search(text):
            out.append("WARNING: the text contains a link or address")
        if _XREF.search(text):
            out.append("WARNING: the text refers to an issue in a repo (owner/repo#N): GitHub links it and notes it on that issue")
        who = self.payload.get("assignees")
        if isinstance(who, list) and who:
            out.append("WARNING: assigns " + ", ".join(str(x) for x in who) + ": GitHub notifies each assignee")
        return out

    def approval_text(self, extra: str = "") -> str:
        body = json.dumps(self.payload, ensure_ascii=False, sort_keys=True, default=str)
        warn = "".join(w + "\n" for w in [*self.warnings(), *([extra] if extra else [])])
        return (f"GitHub write: {self.kind} on {self.repo}" + (f" ({self.target})" if self.target else "")
                + f"\n{warn}{' '.join(self.preview.split())}\nExact payload: {body[:1200]}\nRef {self.hash()[:10]}")


class WriteResult(BaseModel):
    ok: bool
    code: str = ""
    message: str = ""
    data: dict = Field(default_factory=dict)


MAX_PAYLOAD_CHARS = 1200      # the approval shows the WHOLE payload; a bigger one is refused, never truncated on screen


def forbidden(kind: str) -> bool:
    k = re.sub(r"\s+", "", str(kind or "")).lower()
    toks = set(re.split(r"[._:/-]+", k))
    return (not k or k in _FORBIDDEN_KINDS or bool(toks & _FORBIDDEN_TOKENS) or "force" in k
            or ("delete" in toks and "repo" in toks) or ("merge" in toks and k != "merge.enqueue"))


class GitHubWriter:
    """`feats` has `.active(id)`; `approvals` is an ApprovalService; `client` builds a GitHubClient (tests: FakeGitHub)."""

    def __init__(self, home: Path, feats, approvals, *, repos: list[str] | None = None,
                 client: Callable[[], gc.GitHubClient] | None = None, clock: Callable[[], float] = time.time,
                 limit: tuple[int, float] = (MAX_WRITES, WINDOW_S),
                 terminal_confirm: Callable[[list[str], list[str]], bool] | None = None) -> None:
        self.home, self.feats, self.approvals = Path(home), feats, approvals
        self.repos = [r.strip().lower() for r in (repos or []) if isinstance(r, str) and _REPO.match(r.strip())]
        self.client = client or (lambda: gc.GitHubClient(self.home))
        self.clock, self.limit = clock, limit
        # CLI path: the owner's typed confirmation in THIS terminal replaces the popup (see terminal_confirm()).
        self.terminal_confirm = terminal_confirm
        self._token_warn = ""
        self._grants: dict[str, float] = {}          # action hash -> expiry, single use (batch approvals)

    def _refuse(self, a: WriteAction, code: str, msg: str, origin: str) -> WriteResult:
        self._audit(a, code, origin)
        return WriteResult(ok=False, code=code, message=msg)

    def _audit(self, a: WriteAction, code: str, origin: str) -> None:
        append_audit(self.home / "github" / "write_audit.jsonl", {
            "ts": round(self.clock(), 3), "kind": str(a.kind)[:60], "repo": str(a.repo)[:201], "target": a.target[:100],
            "hash": a.hash()[:16], "tkey": a.target_key(), "origin": origin, "result": code})
        logs.info("github", "GITHUB_WRITE", "github write attempt", kind=a.kind[:60], result=code, origin=origin)

    def _scope_problem(self, a: WriteAction) -> str:
        """"" = the token can write; else why not. READ calls only; anything unknown is refused (fail closed)."""
        owner, _, repo = a.repo.partition("/")
        try:
            p = self.client().write_probe(owner, repo)
        except gc.GitHubError as e:
            return f"could not verify the token's write permission ({e.code})"
        perm = KIND_PERMS.get(a.kind.strip().lower(), "write access")
        if (p.get("full_name") or "").lower() != a.repo.strip().lower():   # renamed / transferred / forged answer
            return "the repo's real name on GitHub differs from the github_repos entry (renamed or moved); update the list"
        self._token_warn = ""
        broad = gc.broad_scopes(p["scopes"])
        if broad:
            self._token_warn = ("WARNING: this classic token has broad scopes (" + ", ".join(broad)
                                + "); a fine-grained token limited to the chosen repos is safer")
        try:                                           # a harmless authenticated read of the endpoint family
            self.client().family_probe(owner, repo)
        except gc.GitHubError as e:
            return f"the token cannot read the issues of this repo ({e.code})"
        if p["scopes"] is not None:                                    # classic token: X-OAuth-Scopes
            if p["push"] is False:                                    # a scope does not give write on THIS repo
                return f"the token has no write permission on the repo ({perm})"
            return "" if {"repo", "public_repo"} & set(p["scopes"]) else f"the token lacks the 'repo' scope ({perm})"
        return "" if p["push"] is True else f"the token has no write permission on the repo ({perm})"

    async def _gates(self, a: WriteAction, origin: str, untrusted: bool, rate: bool = True, reserve: int = 1) -> WriteResult | None:
        if forbidden(a.kind):
            return self._refuse(a, "GITHUB_WRITE_FORBIDDEN", "this kind of change is never allowed", origin)
        if a.kind.strip().lower() not in KIND_PERMS:          # fail closed: an unknown kind is refused, not allowed
            return self._refuse(a, "GITHUB_WRITE_FORBIDDEN", "unknown kind of change", origin)
        if len(json.dumps(a.payload, ensure_ascii=False, sort_keys=True, default=str)) > MAX_PAYLOAD_CHARS:
            return self._refuse(a, "GITHUB_WRITE_BAD_ARGUMENT", "payload too large to show in full to the owner", origin)
        bad = (VALIDATORS.get(a.kind.strip().lower()) or (lambda _a: ""))(a)
        if bad:
            return self._refuse(a, "GITHUB_WRITE_BAD_ARGUMENT", bad, origin)
        if len(a.approval_text()) > MAX_APPROVAL_CHARS:
            return self._refuse(a, "GITHUB_WRITE_BAD_ARGUMENT", "action too large to show in full to the owner", origin)
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
        n, window = self.limit
        if self._used() + reserve > n:                 # counted from the AUDIT file: a new CLI process cannot reset it
            return self._refuse(a, "GITHUB_WRITE_RATE_LIMIT", f"more than {n} writes in {int(window)} s", origin)
        return None

    def _rows(self) -> list[dict]:
        try:
            lines = (self.home / "github" / "write_audit.jsonl").read_text(encoding="utf-8").splitlines()[-500:]
        except (OSError, ValueError):
            return []
        out = []
        for x in lines:                          # one torn line must not switch off the rate limit and duplicate guard
            try:
                r = json.loads(x)
                if isinstance(r, dict):
                    r["ts"] = float(r.get("ts") or 0)
                    out.append(r)
            except (ValueError, TypeError):
                continue
        return out

    def _used(self) -> int:
        """Writes in the window: audited successes (and lost answers) plus approved-but-unspent batch grants."""
        now, window = self.clock(), self.limit[1]
        done = sum(1 for r in self._rows() if r.get("result") not in _NOT_APPROVED and now - float(r.get("ts") or 0) < window)
        return done + len(self._grants)

    async def _ask(self, actions: list[WriteAction], origin: str, extras: list[str] | None = None) -> str:
        """"" = approved, else "denied" / "timeout". The prompt shows every exact action; details carry the hashes.
        local_only: only the JARVES popup on the computer can approve (not `jarvis approvals approve`, phone, dashboard);
        the CLI path asks in its own terminal instead (terminal_confirm)."""
        a = actions[0]
        title = f"GitHub: {a.kind} on {a.repo}" if len(actions) == 1 else f"GitHub: {len(actions)} actions on {a.repo}"
        ex = list(extras or []) + [""] * len(actions)
        texts = [x.approval_text(" ".join(filter(None, [self._token_warn, ex[i]]))) for i, x in enumerate(actions)]
        if self.terminal_confirm is not None:
            ok = await asyncio.to_thread(self.terminal_confirm, texts, [x.hash() for x in actions])
            return "" if ok else "denied"
        ok = await self.approvals.ask(
            "\n---\n".join(texts)[:1990], title=title[:120], risk="high", kind="tool", local_only=True,
            why="a change on GitHub; the exact action is shown", source=origin,
            details={"action_hashes": ",".join(x.hash()[:16] for x in actions), "repo": a.repo, "kind": a.kind})
        return "" if ok else ("timeout" if getattr(self.approvals, "last_resolution", "") == "timeout" else "denied")

    async def approve_batch(self, actions: list[WriteAction], *, origin: str = "user", untrusted: bool = False) -> WriteResult:
        """ONE approval listing every action of a task (1 to 10, one repo). Each action hash is then spendable ONCE by
        execute(); an action that was not listed still asks again."""
        if not 1 <= len(actions) <= 10 or len({x.repo.lower() for x in actions}) != 1:
            return WriteResult(ok=False, code="GITHUB_WRITE_BAD_ARGUMENT", message="a batch is 1 to 10 actions on one repo")
        for a in actions:                     # the same gates as execute(), BEFORE the owner is asked
            r = await self._gates(a, origin, untrusted, reserve=len(actions))
            if r is not None:
                return r
        if sum(len(x.approval_text(self._token_warn)) + 5 for x in actions) > MAX_APPROVAL_CHARS:
            return WriteResult(ok=False, code="GITHUB_WRITE_BAD_ARGUMENT", message="the batch is too long to show in full")
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
        dup = self._duplicate(a)
        if dup:
            return self._refuse(a, "GITHUB_WRITE_DUPLICATE", dup, origin)
        r = await self._gates(a, origin, untrusted, rate=not granted)
        if r is not None:
            return r
        if not granted:
            extra = await self._extra(a)
            if extra is None:                          # never a partial preview of what an edit replaces
                return self._refuse(a, "GITHUB_WRITE_UNREADABLE", "could not read the issue on GitHub to show what the "
                                    "edit replaces; try again", origin)
            if len(a.approval_text(extra)) > MAX_APPROVAL_CHARS:
                return self._refuse(a, "GITHUB_WRITE_BAD_ARGUMENT", "action too large to show in full to the owner", origin)
            why = await self._ask([a], origin, [extra])
            if why:
                return self._refuse(a, "GITHUB_WRITE_TIMEOUT" if why == "timeout" else "GITHUB_WRITE_DENIED",
                                    "the owner did not approve this exact action", origin)
        if a.hash() != h:                              # the payload changed while the owner was asked (TOCTOU)
            return self._refuse(a, "GITHUB_WRITE_DENIED", "the action changed after approval", origin)
        handler = HANDLERS.get(a.kind.strip().lower())
        if handler is None:
            return self._refuse(a, "GITHUB_WRITE_NOT_IMPLEMENTED", "this kind of write is not implemented yet", origin)
        try:
            data = await handler(a, self.client())
        except gc.GitHubError as e:
            msg = e.message
            if e.code == "GITHUB_WRITE_PERMISSION":        # the answer after approval is authoritative
                msg += f" (needs {KIND_PERMS.get(a.kind.strip().lower(), 'write access')} on this repo)"
            return self._refuse(a, e.code, msg, origin)
        self._audit(a, "OK", origin)
        return WriteResult(ok=True, code="OK", data=data)

    async def _extra(self, a: WriteAction) -> str | None:
        """Lines the owner must see that need a READ of the current state (what an edit REPLACES, a PR). None = the read
        failed (the caller refuses). Never raises."""
        fn = EXTRAS.get(a.kind.strip().lower())
        if fn is None:
            return ""
        try:
            return await asyncio.to_thread(fn, a, self.client())
        except Exception:                          # noqa: BLE001
            return None

    def _duplicate(self, a: WriteAction) -> str:
        """Idempotency: the audit holds the action hash; a retry of an action that already succeeded (or whose answer was
        lost) within DUP_WINDOW_S is refused, so a retried approved action never posts twice."""
        now, h, tk = self.clock(), a.hash(), a.target_key()
        same = [r for r in self._rows() if r.get("tkey") == tk and r.get("result") in ("OK", "GITHUB_WRITE_UNKNOWN")]
        # an edit / label change is a state: only the LATEST write on it counts (open -> closed -> open is allowed). A
        # create or comment adds something each time: any copy in the window counts (a lost answer is never re-posted)
        for r in (same[-1:] if a.kind.strip().lower() in _TOGGLES else same):
            if r.get("hash") == h[:16] and now - r["ts"] < DUP_WINDOW_S:
                return ("this exact action was already done (or its answer was lost); check on GitHub, "
                        f"or change the text, or wait {int(DUP_WINDOW_S // 60)} minutes")
        return ""

    def token_warnings(self) -> list[str]:
        """Broad classic-token scopes, from one harmless read on the first allowed repo (never raises)."""
        if not self.repos or not gc.connected(self.home):
            return []
        o, _, r = self.repos[0].partition("/")
        try:
            return [f"classic token with broad scopes: {', '.join(b)}; prefer a fine-grained token for the chosen repos"
                    for b in [gc.broad_scopes(self.client().write_probe(o, r)["scopes"])] if b]
        except gc.GitHubError:
            return []

    def status(self) -> dict:
        return {"switch_on": bool(self.feats.active(FEATURE)), "allowed_repos": list(self.repos),
                "implemented_kinds": sorted(HANDLERS), "reserved_kinds": sorted(KIND_PERMS),
                "token_permissions_per_class": sorted(set(KIND_PERMS.values())),
                "limit": f"{self.limit[0]} per {int(self.limit[1])} s", "token_warnings": self.token_warnings()}


# ------------------------------------------------------------------------------------------ feature + CLI
def terminal_confirm(texts: list[str], hashes: list[str], *, ask=input, tty=None) -> bool:
    """The CLI's owner confirmation, in the terminal that runs the command: show the exact action and require the typed
    first 6 characters of its hash. Not a terminal (a pipe, a plain script) = refused. HONEST LIMIT: this stops a typo, a
    pipe and an agent shell without a terminal; it does NOT stop a program that runs this command in a pseudo-terminal
    it controls (the hash is shown in the output and can be computed from the action). Approvals of the RUNNING JARVES
    (model writes) are popup-only (local_only)."""
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()) if tty is None else tty
    if not interactive or len(hashes) != 1:
        print("GITHUB_WRITE_DENIED: a GitHub write needs your typed confirmation in an interactive terminal")
        return False
    print(texts[0])
    want = hashes[0][:6]
    try:
        got = ask(f"Type {want} to approve this exact action (anything else cancels): ")
    except (EOFError, KeyboardInterrupt):
        return False
    return str(got).strip().lower() == want


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    if not gc.connected(c.home):
        return False, f"GitHub not connected; tell the user: \"תריץ במחשב {gc.START_COMMAND}, ואני אדריך אותך\""
    return True, ""


feat.register(FEATURE, "GitHub: כתיבה באישור", "GitHub: writes with approval", False, probe=_probe, needs=["github"],
              risk="high",
              about="change GitHub (create or edit issues, comment, add or remove labels) only after you approve the exact text, only on repos you list, never merge directly",
              targets=targets("UNIT test_github_write.py, test_github_issues.py (fake GitHub API and approvals, no network)",
                              "never touched the real GitHub API; the owner creates the token with Issues write for the chosen repos"),
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


from . import github_issues  # noqa: E402,F401  (registers the G3 kinds: HANDLERS and VALIDATORS)
