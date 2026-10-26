"""GitHub client (V7 G1 #408 reads; G3 #410 adds ONE write door, `write()`, used only by github_write handlers; umbrella #50).

- OFF by default (feature `github`, high risk). Official GitHub REST API v3 + GraphQL (merge queue), nothing else.
- Token: a fine-grained personal access token the owner creates. Stored ONLY on this PC at <state_dir>/github/token.json
  (owner-only file, atomic write: the same private token store the other integrations use). Never logged, never in a
  report, never in an exception message; every text that leaves this module goes through `redact`.
- Minimal fine-grained scopes (repository permissions, all "Read-only"): Metadata, Issues, Pull requests, Actions,
  Checks, Commit statuses. Contents is NOT needed. The merge-queue read needs Pull requests: read.
- Errors map to codes: GITHUB_NOT_CONNECTED, GITHUB_AUTH (401), GITHUB_FORBIDDEN (403 without a limit), GITHUB_RATE_LIMIT
  (403/429 with the limit used up), GITHUB_NOT_FOUND, GITHUB_OFFLINE, GITHUB_PROVIDER_ERROR, GITHUB_BAD_ARGUMENT.
- Pagination is capped (MAX_PAGES x PER_PAGE); every call has a timeout.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from . import clilang
from . import features as feat
from .channels_common import read_json, targets, write_private_json

API = "https://api.github.com"
START_COMMAND = "jarvis github token"
API_VERSION = "2022-11-28"
TIMEOUT = 15.0
PER_PAGE, MAX_PAGES = 30, 3
_NAME = re.compile(r"^(?!\.)[A-Za-z0-9_.-]{1,100}$")
_REF = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//)(?!.*/$)[A-Za-z0-9_./-]{1,200}$")
_TOKEN = re.compile(r"(github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|(?i:bearer|token|basic)\s+[A-Za-z0-9._~+/=-]{8,}|\b[0-9a-f]{40}\b)")
READ_SCOPES = ["Metadata", "Issues", "Pull requests", "Actions", "Checks", "Commit statuses"]   # all read-only


def redact(text: object) -> str:
    return _TOKEN.sub("[REDACTED]", str(text))


class GitHubError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(redact(message))
        self.code, self.message = code, redact(message)


def _write_status(r: httpx.Response) -> None:
    """Map a write answer to a clear code (the provider's own text is never passed on)."""
    c, h = r.status_code, r.headers
    if c < 300:
        return
    limited = h.get("x-ratelimit-remaining") == "0" or h.get("retry-after") or c == 429 or "rate limit" in r.text.lower()
    if c in (403, 429) and limited:
        reset = "".join(ch for ch in str(h.get("x-ratelimit-reset", ""))[:12] if ch.isdigit()) or "?"
        raise GitHubError("GITHUB_RATE_LIMIT", f"GitHub limit (or abuse protection) hit; wait, resets at {reset}")
    table = {401: ("GITHUB_AUTH", "GitHub rejected the token (expired or revoked)"),
             403: ("GITHUB_WRITE_PERMISSION", "GitHub refused: the token has no write permission for this"),
             404: ("GITHUB_NOT_FOUND", "not found (wrong number, or the token cannot see it)"),
             410: ("GITHUB_NOT_FOUND", "gone (issues are disabled or the item was deleted)"),
             422: ("GITHUB_WRITE_INVALID", "GitHub rejected the content (validation failed)")}
    if c >= 500:                    # a server error after a write may still have executed: never retry blindly
        raise GitHubError("GITHUB_WRITE_UNKNOWN", f"GitHub answered {c}; the change may have happened, check on GitHub first")
    if c in (301, 302, 307, 308):
        raise GitHubError("GITHUB_WRITE_REDIRECT", "the repo was renamed or moved; update github_repos")
    code, msg = table.get(c, ("GITHUB_PROVIDER_ERROR", f"GitHub answered {c}"))
    raise GitHubError(code, msg)


def broad_scopes(scopes: list[str] | None) -> list[str]:
    """Classic-token scopes that give far more than issue/PR work (repo is needed for private repos but is broad)."""
    return sorted(s for s in scopes or [] if s in ("repo", "delete_repo", "workflow", "write:packages", "delete:packages")
                  or s.startswith("admin:"))


_SEG = r"(?!\.\.?/)[A-Za-z0-9_.-]{1,100}"            # owner / repo; never a "." or ".." segment
_WRITE_PATHS = {                                       # each verb only on its own endpoint templates (issues family)
    "POST": re.compile(rf"^/repos/{_SEG}/{_SEG}/issues(/[1-9]\d{{0,8}}/(comments|labels))?$"),
    "PATCH": re.compile(rf"^/repos/{_SEG}/{_SEG}/issues/[1-9]\d{{0,8}}$"),
    "DELETE": re.compile(rf"^/repos/{_SEG}/{_SEG}/issues/[1-9]\d{{0,8}}/labels/(?!\.\.?$)[^/?#]{{1,300}}$"),
}
_WRITE_VERBS = tuple(_WRITE_PATHS)


def _write_allowed(method: str, path: str) -> bool:
    """Verb bound to its endpoint template; no dot segment (httpx would collapse `labels/.` to `labels` = remove ALL)."""
    rx = _WRITE_PATHS.get(method)
    return bool(rx and rx.match(path) and "%2e" not in path.lower())


class TokenStore:
    def __init__(self, home: Path) -> None:
        self.file = Path(home) / "github" / "token.json"

    def save(self, token: str) -> None:
        token = token.strip()
        if not token or any(c.isspace() for c in token):
            raise GitHubError("GITHUB_BAD_ARGUMENT", "empty or malformed token")
        write_private_json(self.file, {"token": token})

    def token(self) -> str:
        t = read_json(self.file).get("token")
        return t if isinstance(t, str) else ""

    def clear(self) -> None:
        self.file.unlink(missing_ok=True)

    def status(self) -> dict:
        """Safe summary: never the token itself."""
        return {"connected": bool(self.token()), "scopes_needed": READ_SCOPES, "read_only": True}


def connected(home: Path | None) -> bool:
    return home is not None and TokenStore(home).status()["connected"]


def _name(v: str, what: str) -> str:
    if not isinstance(v, str) or not _NAME.match(v):
        raise GitHubError("GITHUB_BAD_ARGUMENT", f"bad {what}")
    return v


def _state(s: str) -> str:
    if s not in ("open", "closed", "all"):
        raise GitHubError("GITHUB_BAD_ARGUMENT", "state must be open, closed or all")
    return s


class GitHubClient:
    """Read-only. `transport` lets tests plug in FakeGitHub (an httpx.MockTransport); `token` overrides the store."""

    def __init__(self, home: Path | None = None, *, token: str = "", transport: httpx.BaseTransport | None = None,
                 base: str = API) -> None:
        self._token = token or (TokenStore(home).token() if home is not None else "")
        if transport is None and base.rstrip("/") != API:        # never send the token to another host
            raise GitHubError("GITHUB_BAD_ARGUMENT", "only api.github.com is allowed")
        self.transport, self.base = transport, base.rstrip("/")
        self.rate: dict = {"remaining": None, "reset": None}

    def _request(self, method: str, path: str, *, write: bool = False, **kw) -> httpx.Response:
        if not self._token:
            raise GitHubError("GITHUB_NOT_CONNECTED", f"GitHub is not connected; run {START_COMMAND}")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": API_VERSION, "User-Agent": "JARVES"}
        try:
            with httpx.Client(timeout=TIMEOUT, transport=self.transport, follow_redirects=False) as c:
                r = c.request(method, self.base + path, headers=headers, **kw)
        except httpx.HTTPError as e:
            if write:       # the request may have been executed: never retry blindly
                raise GitHubError("GITHUB_WRITE_UNKNOWN", f"no answer ({type(e).__name__}); check on GitHub before retrying") from None
            raise GitHubError("GITHUB_OFFLINE", f"cannot reach GitHub ({type(e).__name__})") from None
        h = r.headers
        self.rate = {"remaining": h.get("x-ratelimit-remaining"), "reset": h.get("x-ratelimit-reset")}
        if write:
            _write_status(r)
        if r.status_code == 401:
            raise GitHubError("GITHUB_AUTH", "GitHub rejected the token (expired or revoked)")
        if r.status_code in (403, 429):
            if h.get("x-ratelimit-remaining") == "0" or h.get("retry-after") or r.status_code == 429:
                raise GitHubError("GITHUB_RATE_LIMIT",
                                  f"GitHub rate limit used up; resets at {h.get('x-ratelimit-reset', '?')}")
            raise GitHubError("GITHUB_FORBIDDEN", "the token lacks a read permission for this")
        if r.status_code == 404:
            raise GitHubError("GITHUB_NOT_FOUND", "not found (or the token cannot see it)")
        if r.status_code >= 300:
            raise GitHubError("GITHUB_PROVIDER_ERROR", f"GitHub answered {r.status_code}")
        return r

    def __repr__(self) -> str:
        return "GitHubClient(token=[REDACTED])"

    def _json(self, method: str, path: str, **kw):
        try:
            return self._request(method, path, **kw).json()
        except ValueError:
            raise GitHubError("GITHUB_PROVIDER_ERROR", "GitHub sent an unreadable answer") from None

    def _pages(self, path: str, key: str | None = None, **params) -> list:
        out: list = []
        for page in range(1, MAX_PAGES + 1):
            j = self._json("GET", path, params={**params, "per_page": PER_PAGE, "page": page})
            items = j.get(key, []) if key and isinstance(j, dict) else j
            if not isinstance(items, list):
                raise GitHubError("GITHUB_PROVIDER_ERROR", "unexpected answer shape")
            out += items
            if len(items) < PER_PAGE:
                break
        return out

    # ---- reads (the only write door is write() below; the framework is github_write.py)
    def write_probe(self, owner: str, repo: str) -> dict:
        """What this token may do on one repo, from read calls only: `scopes` = the classic token's X-OAuth-Scopes
        (None for a fine-grained token: GitHub sends none) and `push` = the repo's permissions.push for this token."""
        _name(owner, "owner"), _name(repo, "repo")
        r = self._request("GET", f"/repos/{owner}/{repo}")
        raw = r.headers.get("x-oauth-scopes")
        try:
            perms = (r.json() or {}).get("permissions") or {}
        except ValueError:
            perms = {}
        try:
            full = str((r.json() or {}).get("full_name") or "")
        except ValueError:
            full = ""
        return {"scopes": None if raw is None else [x.strip() for x in raw.split(",") if x.strip()], "full_name": full,
                "push": bool(perms.get("push")) if isinstance(perms, dict) and "push" in perms else None}

    def family_probe(self, owner: str, repo: str) -> None:
        """A harmless authenticated GET on the issues endpoint family; raises if the token cannot even read it."""
        _name(owner, "owner"), _name(repo, "repo")
        self._request("GET", f"/repos/{owner}/{repo}/issues", params={"per_page": 1})

    def repo_labels(self, owner: str, repo: str) -> list[str]:
        _name(owner, "owner"), _name(repo, "repo")
        return [str(x.get("name")) for x in self._pages(f"/repos/{owner}/{repo}/labels") if isinstance(x, dict)]

    def write(self, method: str, path: str, body: dict | None = None):
        """THE write door (G3): POST/PATCH/DELETE on the issues family of ONE repo, api.github.com only, no redirects.
        Callers pass a repo already checked against the owner's allow-list by github_write.execute()."""
        if not _write_allowed(method, path):
            raise GitHubError("GITHUB_BAD_ARGUMENT", "this endpoint is not an allowed write")
        r = self._request(method, path, write=True, **({"json": body} if body is not None else {}))
        try:
            return r.json() if r.content else {}
        except ValueError:
            return {}

    def me(self) -> dict:
        return {"login": self._json("GET", "/user").get("login")}

    def repos(self) -> list[dict]:
        return [{"full_name": r.get("full_name"), "private": r.get("private"), "default_branch": r.get("default_branch")}
                for r in self._pages("/user/repos", sort="updated")]

    def issues(self, owner: str, repo: str, state: str = "open") -> list[dict]:
        _name(owner, "owner"), _name(repo, "repo")
        rows = self._pages(f"/repos/{owner}/{repo}/issues", state=_state(state))
        return [{"number": i.get("number"), "title": i.get("title"), "state": i.get("state"),
                 "labels": [x.get("name") for x in i.get("labels", [])]} for i in rows if "pull_request" not in i]

    def pulls(self, owner: str, repo: str, state: str = "open") -> list[dict]:
        _name(owner, "owner"), _name(repo, "repo")
        return [{"number": p.get("number"), "title": p.get("title"), "draft": p.get("draft"),
                 "head": (p.get("head") or {}).get("sha")}
                for p in self._pages(f"/repos/{owner}/{repo}/pulls", state=_state(state))]

    def check_runs(self, owner: str, repo: str, ref: str) -> list[dict]:
        _name(owner, "owner"), _name(repo, "repo")
        if not _REF.match(ref or ""):
            raise GitHubError("GITHUB_BAD_ARGUMENT", "bad ref")
        return [{"name": c.get("name"), "status": c.get("status"), "conclusion": c.get("conclusion")}
                for c in self._pages(f"/repos/{owner}/{repo}/commits/{ref}/check-runs", "check_runs")]

    def workflow_runs(self, owner: str, repo: str, branch: str = "") -> list[dict]:
        _name(owner, "owner"), _name(repo, "repo")
        params = {"branch": branch} if branch else {}
        return [{"id": w.get("id"), "name": w.get("name"), "status": w.get("status"), "conclusion": w.get("conclusion")}
                for w in self._pages(f"/repos/{owner}/{repo}/actions/runs", "workflow_runs", **params)]

    def merge_queue(self, owner: str, repo: str, branch: str = "main") -> list[dict]:
        """Pull requests waiting in the merge queue (a GraphQL query; a query never changes anything)."""
        _name(owner, "owner"), _name(repo, "repo")
        q = ("query($o:String!,$r:String!,$b:String!){repository(owner:$o,name:$r){mergeQueue(branch:$b){entries(first:20)"
             "{nodes{position state pullRequest{number title}}}}}}")
        j = self._json("POST", "/graphql", json={"query": q, "variables": {"o": owner, "r": repo, "b": branch}})
        if j.get("errors"):
            raise GitHubError("GITHUB_PROVIDER_ERROR", "GraphQL error")
        mq = ((j.get("data") or {}).get("repository") or {}).get("mergeQueue") or {}
        return [{"position": n.get("position"), "state": n.get("state"), "number": n["pullRequest"]["number"]}
                for n in (mq.get("entries") or {}).get("nodes", [])]


# ------------------------------------------------------------------------------------------ fake for tests
class FakeGitHub:
    """Recorded, REDACTED response shapes from the REST/GraphQL docs (docs.github.com/rest, 26 Oct 2026). `.transport` is
    an httpx.MockTransport; `.calls` lists (method, path); `.fail` forces a (status, headers) answer."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.auth: list[str] = []
        self.fail: tuple[int, dict] | None = None
        self.scopes: str | None = "repo"        # X-OAuth-Scopes of a classic token; None = a fine-grained token
        self.push: bool | None = True           # permissions.push the repo reports for this token
        self.rename = ""                        # full_name the repo API reports (a renamed/transferred repo); "" = as asked
        self.write_fail: tuple[int, dict] | None = None   # forces this answer for POST/PATCH/DELETE only (reads still pass)
        self.bodies: list = []                  # JSON bodies of the writes, in order
        self.labels = ["bug", "P1"]             # labels that exist on the repo
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        self.auth.append(req.headers.get("authorization", ""))
        if self.fail:
            return httpx.Response(self.fail[0], headers=self.fail[1], json={"message": "x"})
        p, page = req.url.path, int(req.url.params.get("page", "1"))
        hdr = {"x-ratelimit-remaining": "4999", "x-ratelimit-reset": "1790000000"}
        if req.method != "GET" and p != "/graphql":
            return self._write(req, p, hdr)
        if p.endswith("/labels"):
            return httpx.Response(200, headers=hdr, json=[{"name": n} for n in self.labels])
        if p == "/user":
            return httpx.Response(200, headers=hdr, json={"login": "octo"})
        if re.fullmatch(r"/repos/[^/]+/[^/]+", p):        # the repo itself: scopes header + permissions (write_probe)
            h = dict(hdr)
            if self.scopes is not None:
                h["x-oauth-scopes"] = self.scopes
            j = {"full_name": self.rename or p[len("/repos/"):]}
            if self.push is not None:
                j["permissions"] = {"push": self.push}
            return httpx.Response(200, headers=h, json=j)
        if p == "/user/repos":
            return httpx.Response(200, headers=hdr, json=[{"full_name": f"octo/r{page}-{i}", "private": True,
                                                           "default_branch": "main"} for i in range(PER_PAGE)])
        if p.endswith("/issues"):
            return httpx.Response(200, headers=hdr, json=[
                {"number": 1, "title": "bug", "state": "open", "labels": [{"name": "P1"}]},
                {"number": 2, "title": "a pr", "state": "open", "labels": [], "pull_request": {}}])
        if p.endswith("/pulls"):
            return httpx.Response(200, headers=hdr, json=[{"number": 2, "title": "a pr", "draft": False,
                                                           "head": {"sha": "abc123"}}])
        if p.endswith("/check-runs"):
            return httpx.Response(200, headers=hdr, json={"total_count": 1, "check_runs": [
                {"name": "ci", "status": "completed", "conclusion": "success"}]})
        if p.endswith("/actions/runs"):
            return httpx.Response(200, headers=hdr, json={"workflow_runs": [
                {"id": 9, "name": "CI", "status": "completed", "conclusion": "success"}]})
        if p == "/graphql":
            return httpx.Response(200, headers=hdr, json={"data": {"repository": {"mergeQueue": {"entries": {"nodes": [
                {"position": 1, "state": "QUEUED", "pullRequest": {"number": 2, "title": "a pr"}}]}}}}})
        return httpx.Response(404, json={"message": "Not Found"})


    def _write(self, req: httpx.Request, p: str, hdr: dict) -> httpx.Response:
        if self.write_fail:
            return httpx.Response(self.write_fail[0], headers={**hdr, **self.write_fail[1]}, json={"message": "x"})
        self.bodies.append(json.loads(req.content) if req.content else None)
        if req.method == "POST" and p.endswith("/issues"):
            return httpx.Response(201, headers=hdr, json={"number": 7, "html_url": "https://github.com/octo/r/issues/7"})
        if req.method == "POST" and p.endswith("/comments"):
            return httpx.Response(201, headers=hdr, json={"id": 55, "html_url": "https://github.com/octo/r/issues/7#c55"})
        if req.method == "PATCH":
            return httpx.Response(200, headers=hdr, json={"number": int(p.rsplit("/", 1)[1]), "state": "open"})
        return httpx.Response(200, headers=hdr, json=[{"name": n} for n in self.labels])


# ------------------------------------------------------------------------------------------ feature + CLI
def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    if not connected(c.home):
        return False, f"GitHub not connected; tell the user: \"תריץ במחשב {START_COMMAND}, ואני אדריך אותך\""
    return True, ""


feat.register("github", "GitHub (קריאה בלבד)", "GitHub (read only)", False, probe=_probe, needs=["github_token"],
              risk="high",
              about="read your GitHub repos, issues, pull requests, checks and merge queue with a read-only token "
                    "kept on this computer only (no writes)",
              targets=targets("UNIT test_github_client.py (fake GitHub API, no network)",
                              "never connected to the real GitHub API; the owner creates the read-only token"),
              permissions=["read"], settings=START_COMMAND)


def add_cli(sub) -> None:
    h = sub.add_parser("github", help="GitHub (read only): `status` shows the connection; `token` saves a read-only "
                                       "token (asked privately); `off` deletes it. Switch on: `jarvis features on github`")
    h.add_argument("action", choices=["status", "token", "off", "write", "issue"])
    h.add_argument("write_action", nargs="?", default=None,
                   choices=["status", "on", "off", "create", "edit", "comment", "labels-add", "labels-remove"],
                   help="with `write`: status|on|off (the GitHub write switch); with `issue`: create|edit|comment|"
                        "labels-add|labels-remove (each asks your approval, shows the exact text)")
    h.add_argument("number", nargs="?", default=None, help="with `issue`: the issue or pull request number")
    h.add_argument("--repo", default="", help="owner/repo from github_repos (default: the only one listed)")
    h.add_argument("--title", default=None)
    h.add_argument("--body", default=None)
    h.add_argument("--state", default=None, choices=["open", "closed"])
    h.add_argument("--label", action="append", default=None, help="an existing label (repeatable)")
    h.add_argument("--assignee", action="append", default=None, help="a GitHub login (repeatable)")


def cmd_github(a, cfg, home) -> int:
    if a.action == "issue":
        from .github_issues import cmd_github_issue
        return cmd_github_issue(a, cfg, home)
    if a.action == "write":
        from .github_write import cmd_github_write
        return cmd_github_write(a, cfg, home)
    store = TokenStore(home)
    if a.action == "status":
        st = store.status()
        print(json.dumps({**st, "feature_on": None}, ensure_ascii=False))
        return 0 if st["connected"] else 1
    if a.action == "off":
        had = store.token() != ""
        store.clear()
        print(clilang.bi("GitHub נותק: האסימון נמחק מהמחשב. / GitHub disconnected: the token was deleted from this computer.")
              if had else clilang.bi("GitHub לא היה מחובר. / GitHub was not connected."))
        return 0
    import getpass
    try:
        store.save(getpass.getpass("GitHub read-only token (hidden): "))
    except GitHubError as e:
        print(f"{e.code}: {e.message}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("GITHUB_BAD_ARGUMENT: no token entered")
        return 1
    print(clilang.bi("האסימון נשמר במחשב הזה בלבד. הפעלה: jarvis features on github. / Token saved on this computer only. "
                     "Switch on: jarvis features on github."))
    return 0
