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
  6. the rate limit (MAX_WRITES per WINDOW_S): the slot is RESERVED under a file lock before the owner is asked
                                                                                          -> GITHUB_WRITE_RATE_LIMIT
  0. (first of all) the audit file is a hash chain; a broken chain or a truncation refuses every write until the
     owner resets it (`jarvis github write reset-audit`)                                  -> GITHUB_WRITE_AUDIT_TAMPERED
  7. the owner's approval of the EXACT action (ApprovalService; answered only by the owner's channels, never by the
     model or text), bound to the action hash: an approval for A never covers B          -> GITHUB_WRITE_DENIED/TIMEOUT
  then the handler, or GITHUB_WRITE_NOT_IMPLEMENTED. Every outcome is audited (kind, repo, target, hash, result code:
  never payload text or a token). The token never reaches this module's logs.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from . import clilang, logs
from . import features as feat
from . import github_client as gc
from .channels_common import targets

FEATURE = "github_write"
MERGE_FEATURE = "github_merge_queue"            # G5: a SEPARATE owner switch for turning on auto-merge / queue entry: default OFF, beta
APPROVE_FEATURE = "github_approve_reviews"      # a SEPARATE owner switch for review event APPROVE (#578): default OFF, beta
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
                           "GITHUB_WRITE_DENIED", "GITHUB_WRITE_TIMEOUT", "GITHUB_WRITE_UNREADABLE", "GITHUB_WRITE_AUDIT_TAMPERED",
                           "GITHUB_WRITE_AGENT_SESSION", "GITHUB_WRITE_APPROVE_OFF", "GITHUB_WRITE_MERGE_OFF"})   # every other audited result = the owner approved it
RESERVED = "GITHUB_WRITE_RESERVED"          # a slot taken before the approval; the final row with the same rid replaces it
DUP_WINDOW_S = 600.0                       # the same approved action is not executed twice within this time
_TOGGLES = frozenset({"issue.edit", "labels.add", "labels.remove", "pr.update"})   # a state: only the latest write on the target counts
_XREF = re.compile(r"(?<![\w/.-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+")
_LINK = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|org|net|io|dev)\b", re.I)


# ------------------------------------------------------------------------------------------ audit: lock + hash chain
@contextlib.contextmanager
def _locked(path: Path):
    """An exclusive cross-process lock (a sidecar file): the rate-limit check and the slot reservation are one step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "ab") as f:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)          # retries ~10 s, then OSError (the caller fails closed)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _link(prev: str, rec: dict) -> str:
    return hashlib.sha256((prev + json.dumps(rec, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()


def _lines(path: Path) -> list[str]:
    """Audit lines split on "\n" ONLY: str.splitlines() also splits on U+2028/U+0085, which a model-chosen kind or
    target could carry into a row and so fake a tamper alarm that stops every write."""
    return [x for x in path.read_text(encoding="utf-8").split("\n") if x.strip()] if path.exists() else []


def _scan(path: Path) -> tuple[list[dict], str, list[str]]:
    head_p = path.with_suffix(".head")
    try:
        lines = _lines(path)
    except (OSError, ValueError):
        return [], "the audit file cannot be read", []
    rows: list[dict] = []
    hashes: list[str] = []
    for i, x in enumerate(lines, 1):
        try:
            r = json.loads(x)
            if not isinstance(r, dict):
                raise ValueError
        except (ValueError, TypeError):
            return [], f"audit line {i} is not valid", []
        prev = hashes[-1] if hashes else ""
        if "h" in r:
            h = r.pop("h")
            if r.get("p") != prev or _link(prev, r) != h:
                return [], f"the audit chain is broken at line {i}", []
            hashes.append(h)
        elif hashes:
            return [], f"audit line {i} is not chained", []
        r["ts"] = float(r["ts"]) if isinstance(r.get("ts"), (int, float)) else 0.0
        rows.append(r)
    try:
        head = json.loads(head_p.read_text(encoding="utf-8")) if head_p.exists() else None
    except (OSError, ValueError):
        return [], "the audit head file is not valid", []
    n = len(hashes)
    # the head may LAG by exactly one row (a crash, or on Windows a reader holding the head open, between the fsynced
    # append and the head replace): appending a row is never hidden by this, a removed row still is detected
    lag_ok = (head is None and n == 1) or (isinstance(head, dict) and n >= 2 and head.get("n") == n - 1
                                           and head.get("h") == hashes[-2])
    if head is None:
        return (rows, "", hashes) if not n or lag_ok else ([], "the audit head file is missing", [])
    if lag_ok:
        return rows, "", hashes
    if not isinstance(head, dict) or head.get("n") != n or head.get("h") != (hashes[-1] if hashes else ""):
        return [], "the audit file was shortened or replaced", []
    return rows, "", hashes


def read_chain(path: Path) -> tuple[list[dict], str]:
    """(rows, problem). Every row after a legacy (un-chained) prefix carries `p` (the previous hash) and `h` =
    sha256(p + the row without h). A wrong link, an un-chained or torn line, or fewer rows than the head file recorded
    (truncation) = a problem text; the caller refuses writes. HONEST LIMIT: a program running as the same user that
    rewrites BOTH the audit and its head file consistently (or deletes both) is not detected."""
    rows, bad, _ = _scan(path)
    return rows, bad


def _append_chained(path: Path, rec: dict) -> None:
    """Caller holds the lock. Verifies the chain first: a tampered file is never extended."""
    _, bad, hashes = _scan(path)
    if bad:
        logs.warn("github", "GITHUB_AUDIT_TAMPERED", "audit chain broken; not appended", why=bad[:80])
        return
    prev, n = (hashes[-1] if hashes else ""), len(hashes)
    rec = {**logs.redact_deep(rec), "p": prev}
    h = _link(prev, rec)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({**rec, "h": h}, ensure_ascii=True) + "\n")     # ASCII: no character can split the line
        f.flush()
        os.fsync(f.fileno())
    tmp = path.with_suffix(".head.tmp")
    tmp.write_text(json.dumps({"n": n + 1, "h": h}), encoding="utf-8")
    os.replace(tmp, path.with_suffix(".head"))


def reset_audit(home: Path) -> str:
    """Owner action after a tamper alarm: the old file is kept as evidence (renamed) and a new chain starts. Returns the
    name of the kept file ("" when there was nothing)."""
    path = Path(home) / "github" / "write_audit.jsonl"
    with _locked(path):
        kept = ""
        if path.exists():
            kept = f"write_audit.tampered-{uuid.uuid4().hex[:8]}.jsonl"
            os.replace(path, path.with_name(kept))
        path.with_suffix(".head").unlink(missing_ok=True)
    return kept


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
                 limit: tuple[int, float] = (MAX_WRITES, WINDOW_S)) -> None:
        self.home, self.feats, self.approvals = Path(home), feats, approvals
        self.repos = [r.strip().lower() for r in (repos or []) if isinstance(r, str) and _REPO.match(r.strip())]
        self.client = client or (lambda: gc.GitHubClient(self.home))
        self.clock, self.limit = clock, limit
        self._token_warn = ""
        self._grants: dict[str, tuple[float, str]] = {}   # action hash -> (expiry, reserved slot id), single use (batch)

    def _refuse(self, a: WriteAction, code: str, msg: str, origin: str, rid: str = "") -> WriteResult:
        self._audit(a, code, origin, rid)
        return WriteResult(ok=False, code=code, message=msg)

    @property
    def _path(self) -> Path:
        return self.home / "github" / "write_audit.jsonl"

    def _rec(self, a: WriteAction, code: str, origin: str, rid: str = "") -> dict:
        return {"ts": round(self.clock(), 3), "kind": str(a.kind)[:60], "repo": str(a.repo)[:201], "target": a.target[:100],
                "hash": a.hash()[:16], "tkey": a.target_key(), "origin": origin, "result": code,
                **({"rid": rid} if rid else {})}

    def _audit(self, a: WriteAction, code: str, origin: str, rid: str = "") -> None:
        try:
            with _locked(self._path):
                _append_chained(self._path, self._rec(a, code, origin, rid))
        except OSError:
            logs.warn("github", "AUDIT_WRITE_FAILED", "could not append to the write audit file")
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

    def _reserve(self, actions: list[WriteAction], origin: str, *, dup_check: bool = False) -> list[str] | str | None:
        """Check the rate limit AND take the slots in ONE locked step (another call or process cannot slip in between).
        Returns the slot ids, or None (limit reached, audit tampered or lock unavailable: fail closed)."""
        try:
            with _locked(self._path):
                if self._tampered() or self._used() + len(actions) > self.limit[0]:
                    return None
                if dup_check:                          # #497: under the lock, so two identical calls cannot both pass
                    for a in actions:
                        dup = self._duplicate(a) or self._in_flight(a)
                        if dup:
                            return dup
                rids = [uuid.uuid4().hex[:12] for _ in actions]
                for a, rid in zip(actions, rids):
                    _append_chained(self._path, self._rec(a, RESERVED, origin, rid))
                return rids
        except OSError:
            return None

    def _in_flight(self, a: WriteAction) -> str:
        """An identical action whose slot is still open (the owner is being asked, or the write is running)."""
        rows = self._rows()
        closed = {r.get("rid") for r in rows if r.get("rid") and r.get("result") != RESERVED}
        now, h = self.clock(), a.hash()[:16]
        if any(r.get("result") == RESERVED and r.get("hash") == h and r.get("rid") not in closed
               and r.get("rid") not in {g[1] for g in self._grants.values()}      # a batch grant is spent by execute()
               and now - float(r.get("ts") or 0) < GRANT_TTL_S for r in rows):
            return "this exact action is already waiting for approval or running; wait for it to finish"
        return ""

    def _rate_refusal(self, a: WriteAction, origin: str) -> WriteResult:
        n, window = self.limit
        return self._refuse(a, "GITHUB_WRITE_RATE_LIMIT", f"more than {n} writes in {int(window)} s", origin)

    async def _gates(self, a: WriteAction, origin: str, untrusted: bool, rid: str = "") -> WriteResult | None:
        """`rid` = the slot a batch approval already reserved: every refusal here closes it (#497)."""
        if self._tampered():
            return self._refuse(a, "GITHUB_WRITE_AUDIT_TAMPERED", "the write audit file was changed or shortened: all GitHub "
                                "writes are stopped until the owner runs: jarvis github write reset-audit", origin, rid)
        if forbidden(a.kind):
            return self._refuse(a, "GITHUB_WRITE_FORBIDDEN", "this kind of change is never allowed", origin, rid)
        if a.kind.strip().lower() not in KIND_PERMS:          # fail closed: an unknown kind is refused, not allowed
            return self._refuse(a, "GITHUB_WRITE_FORBIDDEN", "unknown kind of change", origin, rid)
        if len(json.dumps(a.payload, ensure_ascii=False, sort_keys=True, default=str)) > MAX_PAYLOAD_CHARS:
            return self._refuse(a, "GITHUB_WRITE_BAD_ARGUMENT", "payload too large to show in full to the owner", origin, rid)
        bad = (VALIDATORS.get(a.kind.strip().lower()) or (lambda _a: ""))(a)
        if bad:
            return self._refuse(a, "GITHUB_WRITE_BAD_ARGUMENT", bad, origin, rid)
        if len(a.approval_text()) > MAX_APPROVAL_CHARS:
            return self._refuse(a, "GITHUB_WRITE_BAD_ARGUMENT", "action too large to show in full to the owner", origin, rid)
        if not self.feats.active(FEATURE):
            return self._refuse(a, "GITHUB_WRITE_OFF", "GitHub writes are off (owner: jarvis github write on)", origin, rid)
        kind = a.kind.strip().lower()
        if (kind.startswith("pr.") or kind.startswith("ci.") or kind.startswith("merge.")) and origin != "user":     # CLI only: held HERE, not by a source scan
            return self._refuse(a, "GITHUB_WRITE_FORBIDDEN", "pull request and CI actions are command line only: no model or other route",
                                origin, rid)
        if kind == "pr.review" and str(a.payload.get("event")) == "APPROVE" and not self.feats.active(APPROVE_FEATURE):
            return self._refuse(a, "GITHUB_WRITE_APPROVE_OFF", "approving a pull request needs its own owner switch "
                                f"(jarvis features on {APPROVE_FEATURE}); comments and change requests need only the normal approval",
                                origin, rid)
        if kind == "merge.enqueue" and not self.feats.active(MERGE_FEATURE):
            return self._refuse(a, "GITHUB_WRITE_MERGE_OFF", "turning on auto-merge needs its own owner switch "
                                f"(jarvis features on {MERGE_FEATURE}); it only ever uses the merge queue", origin, rid)
        if untrusted or origin not in ("user", "model"):
            return self._refuse(a, "GITHUB_WRITE_UNTRUSTED", "untrusted text was read this turn; no write now", origin, rid)
        if a.repo.strip().lower() not in self.repos:
            return self._refuse(a, "GITHUB_WRITE_REPO", "this repo is not in the owner's github_repos list", origin, rid)
        why = self._scope_problem(a)
        if why:
            return self._refuse(a, "GITHUB_WRITE_SCOPE", why, origin, rid)
        return None

    def _tampered(self) -> str:
        return read_chain(self._path)[1]

    def _rows(self) -> list[dict]:
        return read_chain(self._path)[0][-500:]       # a broken chain gives no rows; _gates refuses before that matters

    def _used(self) -> int:
        """Slots in the window, counted from the AUDIT file (a new CLI process cannot reset it): successes (and lost
        answers) and still-open reservations. A reservation with a final row (same rid) counts once, by that row; a
        denied one is released."""
        now, window = self.clock(), self.limit[1]
        rows = self._rows()
        closed = {r.get("rid") for r in rows if r.get("rid") and r.get("result") != RESERVED}
        return sum(1 for r in rows if now - float(r.get("ts") or 0) < window and (
            (r.get("result") == RESERVED and r.get("rid") not in closed)
            or (r.get("result") != RESERVED and r.get("result") not in _NOT_APPROVED)))

    async def _ask(self, actions: list[WriteAction], origin: str, extras: list[str] | None = None) -> str:
        """"" = approved, else "denied" / "timeout". The prompt shows every exact action; details carry the hashes.
        local_only: only the JARVES popup on the computer can approve (not `jarvis approvals approve`, phone, dashboard,
        a typed code). The CLI uses the same popup (cmd_github_issue attaches a PopupChannel)."""
        a = actions[0]
        title = f"GitHub: {a.kind} on {a.repo}" if len(actions) == 1 else f"GitHub: {len(actions)} actions on {a.repo}"
        ex = list(extras or []) + [""] * len(actions)
        texts = [x.approval_text(" ".join(filter(None, [self._token_warn, ex[i]]))) for i, x in enumerate(actions)]
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
        if len({x.hash() for x in actions}) != len(actions):      # #569: the grants are keyed by hash: two equal actions would share one
            return WriteResult(ok=False, code="GITHUB_WRITE_BAD_ARGUMENT", message="a batch cannot contain the same action twice")
        for a in actions:                     # the same gates as execute(), BEFORE the owner is asked
            r = await self._gates(a, origin, untrusted)
            if r is not None:
                return r
        extras: list[str] = []                 # #580: the same READ preview as a single action (APPROVE, fork, what an edit replaces)
        for a in actions:
            ex = await self._extra(a)
            if ex is None:                     # fail closed, never a partial preview
                return self._refuse(a, "GITHUB_WRITE_UNREADABLE", self._unreadable_text(a), origin)
            extras.append(ex)
        if sum(len(x.approval_text(" ".join(filter(None, [self._token_warn, extras[i]])))) + 5
               for i, x in enumerate(actions)) > MAX_APPROVAL_CHARS:
            return WriteResult(ok=False, code="GITHUB_WRITE_BAD_ARGUMENT", message="the batch is too long to show in full")
        rids = self._reserve(actions, origin)          # atomic: check + take one slot per action
        if rids is None:
            return self._rate_refusal(actions[0], origin)
        why = await self._ask(actions, origin, extras)
        if why:
            code = "GITHUB_WRITE_TIMEOUT" if why == "timeout" else "GITHUB_WRITE_DENIED"
            for a, rid in zip(actions[1:], rids[1:]):                  # release every slot (a final row with the same rid)
                self._audit(a, code, origin, rid)
            return self._refuse(actions[0], code, "the owner did not approve the batch", origin, rids[0])
        for a, rid in zip(actions, rids):
            self._grants[a.hash()] = (self.clock() + GRANT_TTL_S, rid)
        return WriteResult(ok=True, code="GITHUB_WRITE_APPROVED", message=f"{len(actions)} actions approved")

    async def execute(self, a: WriteAction, *, origin: str = "model", untrusted: bool = False) -> WriteResult:
        """The ONE write path. `untrusted` = text written by others was read in this turn."""
        now, h = self.clock(), a.hash()
        self._grants = {k: v for k, v in self._grants.items() if v[0] > now}
        granted = h in self._grants                    # a batch approval of exactly this action
        rid = self._grants.pop(h)[1] if granted else ""   # (the gates still run: switch, repo, scope; the slot is reserved)
        dup = self._duplicate(a)
        if dup:
            return self._refuse(a, "GITHUB_WRITE_DUPLICATE", dup, origin, rid)
        r = await self._gates(a, origin, untrusted, rid)
        if r is not None:
            return r
        if not granted:
            extra = await self._extra(a)
            if extra is None:                          # never a partial preview of what an edit replaces
                return self._refuse(a, "GITHUB_WRITE_UNREADABLE", self._unreadable_text(a), origin)
            if len(a.approval_text(extra)) > MAX_APPROVAL_CHARS:
                return self._refuse(a, "GITHUB_WRITE_BAD_ARGUMENT", "action too large to show in full to the owner", origin)
            slots = self._reserve([a], origin, dup_check=True)   # atomic check + duplicate + reserve BEFORE the owner is asked
            if isinstance(slots, str):
                return self._refuse(a, "GITHUB_WRITE_DUPLICATE", slots, origin)
            if slots is None:
                return self._rate_refusal(a, origin)
            rid = slots[0]
            why = await self._ask([a], origin, [extra])
            if why:
                return self._refuse(a, "GITHUB_WRITE_TIMEOUT" if why == "timeout" else "GITHUB_WRITE_DENIED",
                                    "the owner did not approve this exact action", origin, rid)
        if a.hash() != h:                              # the payload changed while the owner was asked (TOCTOU)
            return self._refuse(a, "GITHUB_WRITE_DENIED", "the action changed after approval", origin, rid)
        handler = HANDLERS.get(a.kind.strip().lower())
        if handler is None:
            return self._refuse(a, "GITHUB_WRITE_NOT_IMPLEMENTED", "this kind of write is not implemented yet", origin, rid)
        try:
            data = await handler(a, self.client())
        except gc.GitHubError as e:
            msg = e.message
            if e.code == "GITHUB_WRITE_PERMISSION":        # the answer after approval is authoritative
                msg += f" (needs {KIND_PERMS.get(a.kind.strip().lower(), 'write access')} on this repo)"
            return self._refuse(a, e.code, msg, origin, rid)
        except Exception as e:                         # noqa: BLE001 - e.g. httpx.InvalidURL / a transport error after approval:
            # the slot gets a final row (the answer is unknown, so it counts and the duplicate guard applies); no traceback (#578)
            return self._refuse(a, "GITHUB_WRITE_UNKNOWN", f"the write could not be confirmed ({type(e).__name__}); "
                                "check GitHub before trying again", origin, rid)
        self._audit(a, "OK", origin, rid)
        return WriteResult(ok=True, code="OK", data=data)

    @staticmethod
    def _unreadable_text(a: WriteAction) -> str:
        if a.kind.strip().lower() == "ci.rerun":
            return "could not read the workflow run on GitHub to show what it is; try again"
        return "could not read the issue on GitHub to show what the edit replaces; try again"

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
                "limit": f"{self.limit[0]} per {int(self.limit[1])} s", "token_warnings": self.token_warnings(),
                "audit": self._tampered() or "ok"}


# ------------------------------------------------------------------------------------------ feature + CLI
# Environment markers set by coding agents / their shells. A pty (ConPTY, winpty, script) makes isatty() true for a program
# that controls it, so a terminal is NOT proof of a person: the popup is the real protection; this refuses early and clearly.
_AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "CODEX_HOME", "CODEX_SANDBOX", "CODEX_CI",
              "AIDER_MODEL", "CURSOR_TRACE_ID", "CURSOR_AGENT", "GEMINI_CLI", "OPENCODE", "CLINE_TASK", "GOOSE_SESSION",
              "AGENT_SESSION", "JARVES_AGENT_SESSION", "CI", "GITHUB_ACTIONS")


def agent_session_reason(env=None, tty: bool | None = None) -> str:
    """"" = looks like a person at a real terminal; else why a GitHub write from the CLI is refused (conservative:
    any doubt refuses). The owner's own terminal sets none of these variables."""
    env = os.environ if env is None else env
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()) if tty is None else tty
    if not interactive:
        return "this is not an interactive terminal (a pipe, script or agent shell)"
    hit = [k for k in _AGENT_ENV if str(env.get(k, "")).strip()]
    if hit:
        return "an agent or automation session is running this command (" + ", ".join(hit[:3]) + ")"
    return ""


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    if not gc.connected(c.home):
        return False, f"GitHub not connected; tell the user: \"תריץ במחשב {gc.START_COMMAND}, ואני אדריך אותך\""
    return True, ""


feat.register(FEATURE, "GitHub: כתיבה באישור", "GitHub: writes with approval", False, probe=_probe, needs=["github"],
              risk="high",
              about="change GitHub (issues, comments, labels, pull requests, reviews, CI re-run) only after you approve the exact text, only on repos you list, never merge directly",
              targets=targets("UNIT test_github_write.py, test_github_issues.py, test_github_prs.py (fake GitHub API and approvals, no network)",
                              "never touched the real GitHub API; the owner creates the token with Issues, Pull requests and Actions write for the chosen repos"),
              permissions=["writes approved each time"], settings="jarvis github write status")


def _confirm_reset(cfg, home) -> bool:
    """#497: the reset restarts the rate limit and the duplicate guard, so it needs the owner's click in the JARVES popup on
    this computer (local_only), like every write; an isatty/env check alone can be faked by a pty."""
    import asyncio
    from .approvals import ApprovalService, PopupChannel
    svc = ApprovalService(home)
    svc.channels.append(PopupChannel(svc, enabled=True, lang=getattr(getattr(cfg, "ui", None), "popup_language", "en")))
    problem = read_chain(Path(home) / "github" / "write_audit.jsonl")[1]        # #569: the owner sees why he is asked
    state = (f"Audit state: TAMPERED ({problem[:80]}). / מצב היומן: נפגע ({problem[:80]}). " if problem
             else "Audit state: OK (no tamper alarm; this only resets the limits). / מצב היומן: תקין (אין התראה; האיפוס רק מאפס את המגבלות). ")
    return bool(asyncio.run(svc.ask(
        state + "Reset the GitHub write audit? The old file is kept as evidence; the rate limit and the duplicate guard start from zero. / "
        "לאפס את יומן הביקורת של כתיבה ל-GitHub? הקובץ הישן נשמר כראיה; מגבלת הקצב ובדיקת הכפילות מתחילות מאפס.",
        title="GitHub: reset write audit", risk="high", kind="tool", local_only=True,
        why="the audit chain is reset by the owner only", source="user")))


def cmd_github_write(a, cfg, home) -> int:
    """jarvis github write status|on|off: the switch and what is allowed (the allow-list is `github_repos` in config.toml)."""
    from .cli import _cli_features
    f = _cli_features(cfg, home)
    mode = getattr(a, "write_action", None) or "status"
    if mode == "reset-audit":
        why = agent_session_reason()
        if why:
            print(f"GITHUB_WRITE_AGENT_SESSION: refused: {why}; the owner resets the audit in his own terminal")
            return 1
        if not _confirm_reset(cfg, home):
            print(clilang.bi("איפוס יומן הביקורת לא אושר בחלון JARVES. / The audit reset was not approved in the JARVES window."))
            return 1
        kept = reset_audit(home)
        print(clilang.bi("יומן הביקורת אופס; הקובץ הישן נשמר כראיה. / The audit was reset; the old file is kept as evidence.")
              + (f" ({kept})" if kept else ""))
        mode = "status"
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
from . import github_prs  # noqa: E402,F401  (registers the G4 kinds)
from . import github_merge  # noqa: E402,F401  (registers the G5 kind)
