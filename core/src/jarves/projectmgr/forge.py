"""GitHub, optional and per project: off (default, local git only) | read | write. Through the `gh` CLI with the
owner's own login (JARVES stores no token). Never force-pushes, never merges on GitHub, never deletes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import gitops

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
MODES = ("off", "read", "write")
PUSH_BASE = "https://github.com/"        # the only push target (#1595); tests point it at a local folder
_SLUG_OK = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$")   # no leading `.`/`-` (review R3)
_SLUG = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class ForgeError(RuntimeError):
    pass


def gh_cmd() -> list[str]:
    p = shutil.which("gh") or shutil.which("gh.exe")
    return [p] if p else []


def repo_slug(path: str) -> str:
    """owner/repo of the project's `origin` remote on github.com, or ''."""
    url = gitops.git(path, "remote", "get-url", "origin", check=False).stdout.strip()
    m = _SLUG.search(url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def gh(path: str, *args: str, input_: str | None = None, timeout: float = 120) -> str:
    cmd = gh_cmd()
    if not cmd:
        raise ForgeError("the GitHub CLI (gh) is not installed: install it and run gh auth login")
    env = gitops.child_env({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"):        # the owner's gh login, or a token they set for gh
        if os.environ.get(k):
            env[k] = os.environ[k]
    p = subprocess.run([*cmd, *args], cwd=path, capture_output=True, text=True, timeout=timeout, env=env,
                       creationflags=_NOWIN, input=input_, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise ForgeError(" ".join((p.stderr or p.stdout or "gh failed").split())[:300])
    return p.stdout


def status(path: str) -> dict:
    """Read-only facts for `jarvis project github`: gh installed, signed in, the repo slug."""
    out = {"gh": bool(gh_cmd()), "signed_in": False, "repo": repo_slug(path)}
    if out["gh"]:
        try:
            gh(path, "auth", "status")
            out["signed_in"] = True
        except (ForgeError, OSError, subprocess.SubprocessError):
            pass
    return out


def issues(path: str, label: str = "", limit: int = 20) -> list[dict]:
    args = ["issue", "list", "--state", "open", "--limit", str(max(1, min(50, limit))), "--json",
            "number,title,body,labels,url"]
    if label:
        args += ["--label", label]
    rows = json.loads(gh(path, *args) or "[]")
    return [{"number": r["number"], "title": r["title"], "body": (r.get("body") or "")[:1200], "url": r.get("url", ""),
             "labels": [x.get("name") for x in r.get("labels") or []]} for r in rows]


def _tls_pins(path: str) -> list[str]:
    """The pinned push reads no system config (GIT_CONFIG_NOSYSTEM), which also drops Git for Windows' own
    `http.sslBackend=schannel` (the Windows certificate store). Only that one value is carried over, and only when it
    is schannel or openssl; neither can switch verification off. No value (MSYS2, Cygwin, most Linux git): no pin."""
    v = gitops.git(path, "config", "--system", "--get", "http.sslBackend", check=False).stdout.strip().lower()
    return ["-c", f"http.sslBackend={v}"] if v in ("schannel", "openssl") else []


def push_branch(path: str, branch: str, slug: str) -> None:
    """A normal push of one branch (never --force, never a tag, never the default branch) to the pinned
    `https://github.com/<slug>.git` (#1595). `slug` must come from the caller's own trusted record, never from the
    agent-writable `remote.origin.url`; this function has no caller yet (GitHub write mode does not push today).
    The push runs from a private throw-away repository that borrows the project's objects (alternates), with
    GIT_CONFIG_GLOBAL set to an empty file and GIT_CONFIG_NOSYSTEM=1: no repository, global or XDG config is read
    (agent-written code runs as the owner's user and can write all of them), so no `url.*.insteadOf`/`pushInsteadOf`,
    `http.proxy`, `http.sslVerify`, URL-specific `http.<url>.*`, credential helper or remote URL from there applies.
    Only JARVES's own settings do: the owner's gh login as the one credential helper and `http.sslVerify=true`.
    gitops.child_env is an allow-list, so git's GIT_SSL_*, GIT_TRACE*, GIT_CURL_VERBOSE, GIT_PROXY_* and
    GIT_CONFIG_PARAMETERS never reach it; only the owner's HTTPS_PROXY/HTTP_PROXY/SSL_CERT_FILE do. The `rev-parse` calls still run in the project
    repository with its config; rev-parse runs no filter, driver or transport."""
    if not re.match(r"^jarves/p\d+/[\w.\-/]+$", branch) or ".." in branch or branch.startswith("/") or branch.endswith("/"):
        raise ForgeError("JARVES pushes only its own jarves/p<N>/... branches")
    if not _SLUG_OK.match(slug or "") or ".." in slug or slug.lower().endswith(".git"):
        raise ForgeError("push target must be owner/repo (without .git)")
    cmd = gh_cmd()
    if not cmd:
        raise ForgeError("the GitHub CLI (gh) is not installed: install it and run gh auth login")
    if len(cmd) != 1:                                                   # review R3: never drop a wrapper's arguments
        raise ForgeError("the gh credential helper must be one program")
    helper = "!'" + cmd[0].replace("'", "'\\''") + "' auth git-credential"
    sha = gitops.git(path, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").stdout.strip()
    fmt = gitops.git(path, "rev-parse", "--show-object-format").stdout.strip() or "sha1"     # review R4
    if fmt not in ("sha1", "sha256"):
        raise ForgeError(f"unsupported git object format: {fmt[:20]}")
    objects = (Path(path) / gitops.git(path, "rev-parse", "--git-path", "objects").stdout.strip()).resolve()
    tls = _tls_pins(path)
    with tempfile.TemporaryDirectory(prefix="jarves-push-") as d:
        repo, empty = Path(d) / "r.git", Path(d) / "empty.gitconfig"
        empty.write_bytes(b"")
        env = {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1",
               **{k: os.environ[k] for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_CONFIG_DIR") if os.environ.get(k)}}
        gitops.git(d, "init", "-q", "--bare", f"--object-format={fmt}", str(repo), ident=False, env=env)
        (repo / "objects" / "info" / "alternates").write_bytes(str(objects).encode() + b"\n")   # no CRLF on Windows
        # review R1: the temp folder is the owner's, but any same-user process can write it. Right before the push
        # only git init's own keys (core.*, sha256's extensions.objectformat) may be in its config; a small window
        # between this check and the push remains
        keys = gitops.git(d, "config", "--file", str(repo / "config"), "--name-only", "--list", env=env).stdout.split()
        if any(not k.lower().startswith("core.") and k.lower() != "extensions.objectformat" for k in keys):
            raise ForgeError("the temporary push repository's config was changed; push refused")
        gitops.git(repo, "-c", "credential.helper=", "-c", f"credential.helper={helper}", "-c", "http.sslVerify=true",
                   *tls, "push", "--no-verify", f"{PUSH_BASE}{slug}.git", f"{sha}:refs/heads/{branch}", timeout=300,
                   ident=False, env=env)


def create_pr(path: str, head: str, base: str, title: str, body: str) -> str:
    out = gh(path, "pr", "create", "--head", head, "--base", base, "--title", title[:200], "--body", body[:6000])
    m = re.search(r"https://github\.com/\S+/pull/\d+", out)
    return m.group(0) if m else out.strip()[:200]


def pr_checks(path: str, ref: str) -> list[dict]:
    """CI of a PR (number, URL or branch): [{name, state, bucket}] (bucket: pass|fail|pending|skipping|cancel)."""
    try:
        out = gh(path, "pr", "checks", ref, "--json", "name,state,bucket")
    except ForgeError as e:
        if "no checks" in str(e).lower():
            return []
        raise
    return [{"name": r.get("name"), "state": r.get("state"), "bucket": r.get("bucket")} for r in json.loads(out or "[]")]
