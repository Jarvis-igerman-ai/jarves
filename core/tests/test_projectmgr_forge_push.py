"""#1722: a JARVES push never runs a command the shared (agent-writable) repository config names: transport commands,
ext:: URLs, URL rewrites, ssh/askpass/credential/gpg programs, config-defined hooks. Plus the git 2.31 prerequisite in
the project's GitHub status."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarves.projectmgr import forge, gitops

BRANCH = "jarves/p1/t1-a1"


@pytest.fixture(autouse=True)
def _owner_home(tmp_path, monkeypatch):
    home = tmp_path / "owner-home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Owner\n\temail = owner@example.invalid\n", encoding="utf-8")
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_NOSYSTEM"))
    return home


def _cfg(repo: Path, *kv: str, file: str = "") -> None:
    subprocess.run(["git", "config", *(["--file", file] if file else []), *kv], cwd=repo, check=True)


def _repo(tmp_path: Path, origin: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    gitops.git(repo, "branch", BRANCH)
    _cfg(repo, "remote.origin.url", origin)
    return repo


def _script(tmp_path: Path) -> tuple[Path, Path]:
    marker, sc = tmp_path / "ran.txt", tmp_path / "evil.sh"
    sc.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\nexit 1\n", encoding="utf-8")
    sc.chmod(0o755)
    return sc, marker


def _owner_allows_ext(home: Path) -> None:
    """protocol.ext.allow in the OWNER's config, so the test reaches the guard it names (a shared one is refused)."""
    _cfg(home, "protocol.ext.allow", "always", file=str(home / ".gitconfig"))


def test_an_ext_url_never_runs(tmp_path, _owner_home):
    sc, marker = _script(tmp_path)
    repo = _repo(tmp_path, f"ext::sh {sc.as_posix()}")
    _owner_allows_ext(_owner_home)
    with pytest.raises(forge.ForgeError, match="github.com origin"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


def test_a_receivepack_on_a_local_origin_never_runs(tmp_path):
    sc, marker = _script(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = _repo(tmp_path, str(bare))
    _cfg(repo, "remote.origin.receivepack", sc.as_posix())
    with pytest.raises(forge.ForgeError, match="remote.origin.receivepack"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


def test_a_shared_ssh_command_never_runs(tmp_path):
    sc, marker = _script(tmp_path)
    repo = _repo(tmp_path, "git@github.com:o/r.git")
    _cfg(repo, "core.sshCommand", sc.as_posix())
    with pytest.raises(forge.ForgeError, match="core.sshcommand"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


def test_a_shared_url_rewrite_to_ext_never_runs(tmp_path, _owner_home):
    sc, marker = _script(tmp_path)
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    _owner_allows_ext(_owner_home)
    _cfg(repo, f"url.ext::sh {sc.as_posix()} #.insteadOf", "https://github.com/")
    with pytest.raises(forge.ForgeError, match="insteadof"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


def _local_github(tmp_path: Path, home: Path) -> Path:
    """The owner's global config rewrites https://github.com/ to local bare repositories: hermetic, no network."""
    bare = tmp_path / "remote"
    subprocess.run(["git", "init", "-q", "--bare", str(bare / "o" / "r.git")], check=True)
    _cfg(home, f"url.{bare.as_posix()}/.insteadOf", "https://github.com/", file=str(home / ".gitconfig"))
    return bare / "o" / "r.git"


@pytest.mark.parametrize("key", ["core.askPass", "core.gitProxy", "core.alternateRefsCommand", "credential.helper",
                                 "credential.https://github.com.helper", "gpg.program", "gpg.ssh.program",
                                 "hook.x.command", "remote.origin.vcs", "remote.origin.uploadpack",
                                 "url.https://evil.invalid/.pushInsteadOf", "include",
                                 "http.proxy", "http.https://github.com.proxy", "http.sslVerify", "http.sslCAInfo",
                                 "http.sslCAPath", "http.curloptResolve", "http.sslCert", "http.sslKey",
                                 "http.extraHeader", "http.cookieFile", "remote.https://github.com/o/r.git.proxy",
                                 "remote.https://github.com/o/r.git.pushurl", "remote.https://github.com/o/r.git.url",
                                 "protocol.allow", "protocol.ext.allow"])
def test_other_command_keys_in_the_shared_config_refuse_the_push(tmp_path, _owner_home, key):
    """Refused before git push starts. A regressed guard would push to the local bare repository (no network) and
    fail the test. `include`: the key sits in an included file (scope: local)."""
    _local_github(tmp_path, _owner_home)
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    if key == "include":
        inc = tmp_path / "inc.cfg"
        _cfg(repo, "core.askPass", "x", file=str(inc))
        _cfg(repo, "include.path", str(inc))
    else:
        _cfg(repo, key, "x")
    with pytest.raises(forge.ForgeError, match="will not push"):
        forge.push_branch(str(repo), BRANCH)


def test_a_pushurl_on_a_remote_named_like_the_url_never_runs(tmp_path, _owner_home):
    """Round-3 review R1: `git push <url>` resolves <url> as a remote name first, so a shared
    `remote.<that url>.pushurl` = ext:: would reroute the push and run a command."""
    sc, marker = _script(tmp_path)
    _local_github(tmp_path, _owner_home)
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    _owner_allows_ext(_owner_home)
    _cfg(repo, "remote.https://github.com/o/r.git.pushurl", f"ext::sh {sc.as_posix()}")
    with pytest.raises(forge.ForgeError, match="remote.https://github.com/o/r.git.pushurl"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


def test_a_clean_push_reaches_the_remote_unsigned(tmp_path, _owner_home):
    """The owner's global config stays usable (here a URL rewrite to a local bare repository); a shared
    push.gpgSign=true does not sign: the negative control shows this receiver refuses a signed push. The tracking ref
    origin/<branch> is updated as a push to `origin` would (round-3 review R3)."""
    bare = _local_github(tmp_path, _owner_home)
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    signed = subprocess.run(["git", "push", "--signed=yes", str(bare), f"{BRANCH}:{BRANCH}"], cwd=repo,
                            capture_output=True, text=True)
    assert signed.returncode != 0 and "does not support --signed push" in signed.stderr      # negative control
    _cfg(repo, "push.gpgSign", "true")
    forge.push_branch(str(repo), BRANCH)
    out = subprocess.run(["git", "--git-dir", str(bare), "branch", "--list", BRANCH],
                         capture_output=True, text=True)
    assert BRANCH in out.stdout
    head = gitops.git(repo, "rev-parse", BRANCH).stdout.strip()
    assert gitops.git(repo, "rev-parse", f"refs/remotes/origin/{BRANCH}").stdout.strip() == head


def test_a_push_never_recurses_into_a_submodule(tmp_path, _owner_home):
    """Review R1: a shared push.recurseSubmodules=on-demand would run `git push` inside the submodule with the
    submodule's own (agent-writable, unread) config: here an ext:: origin."""
    sc, marker = _script(tmp_path)
    bare = _local_github(tmp_path, _owner_home)
    src = tmp_path / "subsrc"
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-q", "--allow-empty", "-m", "s"], check=True)
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    run = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)  # noqa: E731
    run("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "sub")
    run("checkout", "-q", BRANCH)
    run("commit", "-q", "--allow-empty", "-m", "new", cwd=repo / "sub")             # not on the submodule's remote
    run("add", "sub")
    run("commit", "-q", "-m", "sub")
    run("config", "remote.origin.url", f"ext::sh {sc.as_posix()}", cwd=repo / "sub")
    run("config", "protocol.ext.allow", "always", cwd=repo / "sub")
    _cfg(repo, "push.recurseSubmodules", "on-demand")
    forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()
    assert BRANCH in subprocess.run(["git", "--git-dir", str(bare), "branch", "--list", BRANCH],
                                    capture_output=True, text=True).stdout


@pytest.mark.parametrize("url,ok", [
    ("https://github.com/o/r.git", True), ("https://GitHub.com/o/r", True), ("https://u:t@github.com/o/r.git", True),
    ("ssh://git@github.com/o/r.git", True), ("ssh://git@github.com:22/o/r.git", True), ("git@github.com:o/r.git", True),
    ("git@GITHUB.COM:o/r", True), ("https://github.com@evil.invalid/o/r.git", False),
    ("https://github.com.evil.invalid/o/r.git", False), ("file:///tmp/r.git", False), ("/tmp/r.git", False),
    ("ext::sh -c x", False), ("ssh://git@github.com:2222/o/r.git", False), ("-uhttps://github.com/o/r", False),
    ("https://github.com/o/r.git\nx", False), ("http://github.com/o/r.git", False)])
def test_accepted_and_rejected_push_url_forms(url, ok):
    assert bool(forge._PUSH_URL.match(url)) is ok


def test_shared_config_keys_reports_local_and_included_keys_only(tmp_path, _owner_home, monkeypatch):
    """Round-3 review R2: the public helper the push guard uses. Include files count as the repository's own (local)
    config; the owner's global config and the command scope (env) are not reported."""
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    inc = tmp_path / "inc.cfg"
    _cfg(repo, "core.askPass", "x", file=str(inc))
    _cfg(repo, "include.path", str(inc))
    _cfg(_owner_home, "core.sshCommand", "ssh", file=str(_owner_home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.gitproxy'='x'")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.alternaterefscommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "x")
    assert sorted(gitops.shared_config_keys(repo, r"^core\.(askpass|sshcommand|gitproxy|alternaterefscommand)$")) \
        == ["core.askpass"]
    _cfg(repo, "include.path", str(tmp_path / "missing" / "x"))
    (repo / ".git" / "config").write_text("[broken\n", encoding="utf-8")
    with pytest.raises(gitops.GitError, match="cannot read"):
        gitops.shared_config_keys(repo, r"^core\.")


def test_status_reports_a_git_older_than_2_31(monkeypatch, tmp_path):
    """#1722 R6: the project's GitHub status names the git 2.31 prerequisite instead of raising GitError."""
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "git version 2.30.2\n", "")
    monkeypatch.setattr(gitops, "_GIT_OK", [])
    monkeypatch.setattr(gitops.subprocess, "run", run)
    st = forge.status(str(tmp_path))
    assert st["git"] is False and "2.31 or later" in st["git_error"] and st["repo"] == ""
