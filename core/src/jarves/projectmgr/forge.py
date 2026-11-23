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
    `http.sslBackend` (schannel: the Windows certificate store; openssl: its bundled `http.sslCAInfo`). Only that value
    is carried over, and only when it is schannel or openssl; neither can switch verification off. With openssl the
    system `http.sslCAInfo` comes back too (review R2), but only an existing file under the grandparent of
    `git --exec-path` (Git for Windows: `<Git>/mingw64`; Linux: `/usr`, a root-owned tree like the system config it
    comes from); any other CA path is dropped and verification fails closed.
    No value (MSYS2, Cygwin, most Linux git): no pin."""
    def system(key: str) -> str:
        return gitops.git(path, "config", "--system", "--get", key, check=False).stdout.strip()
    v = system("http.sslBackend").lower()
    if v not in ("schannel", "openssl"):
        return []
    pins = ["-c", f"http.sslBackend={v}"]
    ca = system("http.sslCAInfo") if v == "openssl" else ""
    if ca:
        root = Path(gitops.git(path, "--exec-path", check=False).stdout.strip() or "/nonexistent").resolve().parent.parent
        f = Path(ca).resolve()
        if root != Path(root.anchor) and f.is_file() and f.is_relative_to(root):
            pins += ["-c", f"http.sslCAInfo={f}"]
    return pins


MIN_GIT = (2, 32)          # first git that honours GIT_CONFIG_GLOBAL; older ones read ~/.gitconfig silently (R1)


def _git_version(path: str) -> tuple[int, int]:
    """(major, minor) of the git on PATH, (0, 0) when unreadable: `git version 2.45.1.windows.1` -> (2, 45)."""
    m = re.match(r"git version (\d+)\.(\d+)", gitops.git(path, "--version", check=False).stdout.strip())
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _gh_helper(cmd: list[str]) -> str:
    """git's `!` credential helper running the owner's gh: one program, single-quoted for git's shell (spaces, `'`)."""
    if len(cmd) != 1:                                                   # review R3: never drop a wrapper's arguments
        raise ForgeError("the gh credential helper must be one program")
    return "!'" + cmd[0].replace("'", "'\\''") + "' auth git-credential"


# Exactly the keys `git init --bare` writes (review R1 of 86f15520): core.askPass, core.sshCommand, core.gitProxy or
# any other key in the temp repo's config means a same-user process wrote it, and the push is refused.
_INIT_KEYS = frozenset({"core.repositoryformatversion", "core.filemode", "core.bare", "core.symlinks",
                        "core.ignorecase", "core.precomposeunicode", "extensions.objectformat"})
_INIT_ENTRIES = frozenset({"HEAD", "branches", "config", "description", "hooks", "info", "objects", "refs"})


def push_branch(path: str, branch: str, slug: str) -> None:
    """A normal push of one branch (never --force, never a tag, never the default branch) to the pinned
    `https://github.com/<slug>.git` (#1595). `slug` must come from the caller's own trusted record, never from the
    agent-writable `remote.origin.url`; this function has no caller yet (GitHub write mode does not push today).
    It runs from a throw-away repository borrowing the project's objects, with GIT_CONFIG_GLOBAL = an empty file and
    GIT_CONFIG_NOSYSTEM=1, so no repository, global, XDG or system `url.*`, `http.*`, credential or remote key
    applies; only JARVES's gh credential helper and `http.sslVerify=true` (plus _tls_pins). git older than 2.32
    ignores GIT_CONFIG_GLOBAL, so it is refused (review R1). gitops.child_env is an allow-list: GIT_SSL_*, GIT_TRACE*,
    GIT_PROXY_*, GIT_CONFIG_PARAMETERS, SSL_CERT_DIR and CURL_CA_BUNDLE never reach git; the owner's own SSL_CERT_FILE and
    HTTPS_PROXY do (corporate TLS proxies). rev-parse runs in the project repo (no filter/transport)."""
    if not re.match(r"^jarves/p\d+/[\w.\-/]+$", branch) or ".." in branch or branch.startswith("/") or branch.endswith("/"):
        raise ForgeError("JARVES pushes only its own jarves/p<N>/... branches")
    if not _SLUG_OK.match(slug or "") or ".." in slug or slug.lower().endswith(".git"):
        raise ForgeError("push target must be owner/repo (without .git)")
    cmd = gh_cmd()
    if not cmd:
        raise ForgeError("the GitHub CLI (gh) is not installed: install it and run gh auth login")
    helper = _gh_helper(cmd)
    if _git_version(path) < MIN_GIT:
        raise ForgeError("git 2.32 or newer is required for a pinned GitHub push: update git")
    sha = gitops.git(path, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").stdout.strip()
    fmt = gitops.git(path, "rev-parse", "--show-object-format").stdout.strip() or "sha1"     # review R4
    if fmt not in ("sha1", "sha256"):
        raise ForgeError(f"unsupported git object format: {fmt[:20]}")
    objects = (Path(path) / gitops.git(path, "rev-parse", "--git-path", "objects").stdout.strip()).resolve()
    tls = _tls_pins(path)
    with tempfile.TemporaryDirectory(prefix="jarves-push-") as d:
        repo, empty = Path(d) / "r.git", Path(d) / "empty.gitconfig"
        empty.write_bytes(b"")
        # gitops.child_env's ENV_KEEP does not list the gh variables, so this call alone adds them: the gh credential
        # helper needs the owner's token/host/config; no other git or agent call receives them.
        env = {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1",
               **{k: os.environ[k] for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_CONFIG_DIR") if os.environ.get(k)}}
        gitops.git(d, "init", "-q", "--bare", f"--object-format={fmt}", str(repo), ident=False, env=env)
        (repo / "objects" / "info" / "alternates").write_bytes(str(objects).encode() + b"\n")   # no CRLF on Windows
        # Any same-user process can write the temp folder. GIT_DIR pins the repository (no discovery of a planted
        # r.git/.git), only the entries `git init --bare` makes may exist (no commondir, gitdir, config.worktree), and
        # the check reads config exactly as the push will: same -c options and env (review R1 of 59b11146). Every
        # value not from the command line must be one of _INIT_KEYS. A window between check and push remains.
        env["GIT_DIR"] = str(repo)
        pre = ["-c", "credential.helper=", "-c", f"credential.helper={helper}", "-c", "http.sslVerify=true", *tls]
        out = gitops.git(d, *pre, "config", "--list", "--show-origin", "--name-only", "--null", ident=False, env=env)
        f = out.stdout.split("\0")
        if ({x.name for x in repo.iterdir()} - _INIT_ENTRIES
                or any(o != "command line:" and k.lower() not in _INIT_KEYS for o, k in zip(f[0::2], f[1::2]))):
            raise ForgeError("the temporary push repository was changed; push refused")
        gitops.git(d, *pre, "push", "--no-verify", f"{PUSH_BASE}{slug}.git", f"{sha}:refs/heads/{branch}", timeout=300,
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
