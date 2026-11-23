"""#1390: the shared repository config (`.git/config`, writable by every agent through the shared `.git` folder) can name
commands git runs: filter and merge drivers, diff commands, editors, ssh, gpg. JARVES's own git calls refuse to run while
the repository's own config (or a file it includes) names one; the owner's global config still applies.
No network; throw-away local repositories with an empty global config."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from jarves.projectmgr import gitops


@pytest.fixture(autouse=True)
def _empty_global(monkeypatch, tmp_path):
    """The host's own ~/.gitconfig must not shape the result."""
    (tmp_path / "userhome").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "userhome" / ".config"))


def _cmd(marker: Path) -> str:
    """A shell command that leaves a trace and passes its input through (a working filter or driver)."""
    return f"touch '{marker.as_posix()}'; cat"


def _setcfg(repo: Path, key: str, value: str) -> None:
    subprocess.run(["git", "config", key, value], cwd=repo, check=True)          # what an agent writes, outside JARVES


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    gitops.git(repo, "config", "user.name", "Owner")                          # ident=False: the owner's identity
    gitops.git(repo, "config", "user.email", "owner@example.invalid")
    (repo / ".gitattributes").write_text("*.txt filter=evil merge=evil diff=evil\n", encoding="utf-8")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    return repo


def test_a_filter_named_in_the_shared_config_never_runs(tmp_path):
    """#1390 filters: smudge runs on checkout (worktree add), clean on add (commit_all)."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "ran.txt"
    _setcfg(repo, "filter.evil.smudge", _cmd(marker))
    _setcfg(repo, "filter.evil.clean", _cmd(marker))
    with pytest.raises(gitops.GitError, match="filter.evil"):
        gitops.ensure_integration(home, str(repo), 1)
    (repo / "f.txt").write_text("y\n", encoding="utf-8")
    with pytest.raises(gitops.GitError):
        gitops.commit_all(repo, "change")
    assert not marker.exists()


def test_a_merge_driver_named_in_the_shared_config_never_runs(tmp_path):
    """#1390 merge drivers: a real three-way merge of f.txt (the ship path uses merge_in with the owner's identity)."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "f.txt").write_text("side\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "side", "--no-verify")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("main\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "main", "--no-verify")
    _setcfg(repo, "merge.evil.driver", f"touch '{marker.as_posix()}'; true")
    subprocess.run(["git", "merge", "-q", "--no-edit", "side"], cwd=repo, capture_output=True, check=True)
    assert marker.exists()                                                       # the setup itself runs the driver
    marker.unlink()
    subprocess.run(["git", "reset", "-q", "--hard", "HEAD~1"], cwd=repo, check=True)
    out = gitops.merge_in(repo, "side", ident=False)                             # check=False inside: not merged
    assert len(out) == 1 and out[0].startswith("(not merged: refused") and "merge.evil.driver" in out[0]
    assert not marker.exists()


@pytest.mark.parametrize("key", ["diff.external", "diff.evil.command", "diff.evil.textconv", "core.sshCommand",
                                 "core.editor", "sequence.editor", "core.askpass", "gpg.program",
                                 "gpg.ssh.program", "credential.helper", "hook.evil.command",
                                 "trailer.evil.cmd", "mergetool.evil.cmd", "remote.origin.uploadpack",
                                 "gpg.ssh.defaultKeyCommand", "core.alternateRefsCommand",
                                 "filter.evil.process", "core.gitProxy", "remote.origin.receivepack",
                                 "remote.origin.vcs", "difftool.evil.path", "credential.https://x.invalid.helper",
                                 "gpg.x509.program", "interactive.diffFilter",
                                 "browser.evil.cmd", "browser.evil.path", "web.browser", "man.evil.cmd",
                                 "help.browser", "instaweb.browser", "instaweb.httpd"])
def test_other_command_keys_in_the_shared_config_are_refused(tmp_path, key):
    """#1390 other command types: every git call refuses while the shared config names one."""
    repo = _repo(tmp_path)
    _setcfg(repo, key, "evil")
    with pytest.raises(gitops.GitError, match="repository config names a command"):
        gitops.git(repo, "status")


def test_an_included_file_and_conditional_includes_are_checked(tmp_path):
    """#1390 include.path / includeIf: an included file's keys count as the repository's own; a conditional include
    may match only inside a new worktree, so it is refused outright."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    (repo / ".git" / "more").write_text(f'[filter "evil"]\n\tsmudge = {_cmd(marker)}\n', encoding="utf-8")
    _setcfg(repo, "include.path", "more")
    with pytest.raises(gitops.GitError, match="filter.evil.smudge"):
        gitops.git(repo, "status")
    subprocess.run(["git", "config", "--unset", "include.path"], cwd=repo, check=True)
    _setcfg(repo, "includeIf.onbranch:jarves/**.path", "more")
    with pytest.raises(gitops.GitError, match="includeif"):
        gitops.git(repo, "status")
    assert not marker.exists()


def test_the_owners_global_config_and_neutralised_keys_still_work(tmp_path):
    """The owner's own global filter (git-lfs style) is not refused, nor are keys every call already overrides
    (core.hooksPath, core.fsmonitor)."""
    repo = _repo(tmp_path)
    (tmp_path / "userhome" / ".gitconfig").write_text('[filter "evil"]\n\tclean = cat\n\tsmudge = cat\n',
                                                      encoding="utf-8")
    _setcfg(repo, "core.hooksPath", str(tmp_path / "hooks"))
    _setcfg(repo, "core.fsmonitor", "false")
    scope = subprocess.run(["git", "config", "--show-scope", "--get", "filter.evil.clean"], cwd=repo, text=True,
                           capture_output=True, env=gitops.child_env(), check=True).stdout.split()
    assert scope == ["global", "cat"]                        # JARVES's own env really loads the planted global file
    assert gitops.git(repo, "status").returncode == 0
    assert gitops.is_repo(str(repo)) and gitops.repo_state(str(repo))["branch"] == "main"


def test_a_repository_that_names_a_command_is_not_a_usable_repo(tmp_path):
    """is_repo answers False and repo_state returns its empty defaults (instead of crashing) on a refused repo."""
    repo = _repo(tmp_path)
    _setcfg(repo, "core.editor", "evil")
    assert gitops.is_repo(str(repo)) is False
    assert gitops.repo_state(str(repo)) == {"branch": "", "commits": 0, "dirty": 0, "untracked": 0}


@pytest.mark.parametrize("key,value", [("submodule.lib.update", "checkout"), ("submodule.lib.update", "rebase"),
                                       ("submodule.lib.update", "none"), ("credential.helper", ""),
                                       ("core.pager", "less"), ("pager.log", "false")])
def test_values_that_name_no_command_are_accepted(tmp_path, key, value):
    """#1390 review R2: `git submodule init` copies update=checkout/rebase/merge/none, an empty credential.helper
    resets the list, and pagers never start without a terminal; none of these runs a command."""
    repo = _repo(tmp_path)
    _setcfg(repo, key, value)
    assert gitops.git(repo, "status").returncode == 0


def test_a_submodule_update_command_is_refused(tmp_path):
    repo = _repo(tmp_path)
    _setcfg(repo, "submodule.lib.update", "!evil")
    with pytest.raises(gitops.GitError, match="submodule.lib.update"):
        gitops.git(repo, "status")


def test_a_key_written_during_the_call_is_detected(tmp_path, monkeypatch):
    """#1390 review R1: a key that slips in between JARVES's read and git's own read fails the call afterwards."""
    repo, real = _repo(tmp_path), subprocess.run

    def racing(argv, **kw):
        if "status" in argv:                                                       # the agent wins the race
            real(["git", "config", "core.editor", "evil"], cwd=repo, check=True)
        return real(argv, **kw)
    monkeypatch.setattr(gitops.subprocess, "run", racing)
    with pytest.raises(gitops.GitError, match="changed during the call.*core.editor"):
        gitops.git(repo, "status")


def test_the_ext_transport_never_runs_a_command(tmp_path):
    """#1390 review R2: `protocol.ext.allow=always` plus an `ext::` remote runs a command on any transport call."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    _setcfg(repo, "protocol.ext.allow", "always")
    _setcfg(repo, "remote.evil.url", f"ext::sh -c touch% {marker.as_posix()}")
    subprocess.run(["git", "ls-remote", "evil"], cwd=repo, capture_output=True)
    assert marker.exists()                                                       # the setup itself runs the command
    marker.unlink()
    assert gitops.git(repo, "ls-remote", "evil", check=False).returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize("args", [("-C", "elsewhere", "status"), ("--git-dir=x", "status"), ("--work-tree", "x", "status"),
                                  ("-c", "user.name=Owner", "--git-dir=x", "status"),
                                  ("-c", "user.name=Owner", "-C", "x", "status"),
                                  ("--namespace", "n", "--work-tree=x", "status"), ("-Cx", "status")])
def test_a_call_that_points_git_at_another_folder_is_refused(tmp_path, args):
    """#1390 review R6: the config check reads `cwd`, so git must not be pointed at another repository."""
    with pytest.raises(gitops.GitError, match="another folder"):
        gitops.git(_repo(tmp_path), *args)


def _with_submodule(tmp_path: Path) -> tuple[Path, Path]:
    """A superproject with a populated submodule `lib`; its own config lives in the shared `.git/modules/lib`."""
    repo, lib = _repo(tmp_path), tmp_path / "lib"
    lib.mkdir()
    for argv in (["init", "-q", "-b", "main"], ["-c", "user.name=O", "-c", "user.email=o@x.invalid", "commit", "-q",
                                                "--allow-empty", "-m", "i"]):
        subprocess.run(["git", *argv], cwd=lib, check=True)
    (lib / ".gitattributes").write_text("*.txt filter=evil\n", encoding="utf-8")
    (lib / "g.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=lib, check=True)
    subprocess.run(["git", "-c", "user.name=O", "-c", "user.email=o@x.invalid", "commit", "-qm", "g"], cwd=lib, check=True)
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "-q", "add", str(lib), "lib"], cwd=repo,
                   check=True, capture_output=True)
    return repo, repo / ".git" / "modules" / "lib" / "config"


def _layout(tmp_path: Path, form: str) -> tuple[Path, Path]:
    """(the folder JARVES runs git in, the config file of the nested repository git recurses into)."""
    repo, cfg = _with_submodule(tmp_path)
    if form == "linked-worktree":                     # #1662 R1: a submodule initialised inside `worktree add`
        wt = tmp_path / "wt"
        subprocess.run(["git", "-c", "user.name=O", "-c", "user.email=o@x.invalid", "commit", "-qm", "sub"], cwd=repo,
                       check=True)
        subprocess.run(["git", "worktree", "add", "-q", str(wt)], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "-q", "update", "--init"], cwd=wt,
                       check=True, capture_output=True)
        return wt, repo / ".git" / "worktrees" / "wt" / "modules" / "lib" / "config"
    if form in ("embedded", "gitdir-file"):           # #1662 R2: a gitlink whose `.git` is not in .git/modules
        lib = repo / "lib"
        shutil.rmtree(lib / ".git") if (lib / ".git").is_dir() else (lib / ".git").unlink()
        shutil.copytree(tmp_path / "lib" / ".git", lib / ".git")
        if form == "gitdir-file":
            shutil.move(str(lib / ".git"), str(tmp_path / "elsewhere"))
            (lib / ".git").write_text(f"gitdir: {(tmp_path / 'elsewhere').as_posix()}\n", encoding="utf-8")
        subprocess.run(["git", "checkout", "-q", "--", "."], cwd=lib, check=True)
        return repo, (tmp_path / "elsewhere" if form == "gitdir-file" else lib / ".git") / "config"
    return repo, cfg


@pytest.mark.parametrize("form", ["modules", "linked-worktree", "embedded", "gitdir-file"])
def test_a_command_named_in_a_nested_repository_config_never_runs(tmp_path, form):
    """#1390 (#1650 R1, #1662 R1/R2): `git status` recurses into every populated gitlink, wherever its gitdir is;
    a filter planted in that repository's config must never run under JARVES."""
    (cwd, cfg), marker = _layout(tmp_path, form), tmp_path / "ran.txt"
    subprocess.run(["git", "config", "--file", str(cfg), "filter.evil.clean", _cmd(marker)], check=True)
    os.utime(cwd / "lib" / "g.txt", (1, 1))                        # same content, new stat: git must re-hash it
    subprocess.run(["git", "status"], cwd=cwd, capture_output=True, check=True)
    assert marker.exists()                                                       # the setup itself runs the filter
    marker.unlink()
    os.utime(cwd / "lib" / "g.txt", (2, 2))
    with pytest.raises(gitops.GitError, match="lib:filter.evil.clean"):
        gitops.git(cwd, "status")
    assert not marker.exists()


def test_a_failed_nested_repository_read_is_refused(tmp_path, monkeypatch):
    """#1662 R3: when git cannot list the gitlinks of a repository, the call is refused (fail closed)."""
    repo, real = _repo(tmp_path), subprocess.run
    monkeypatch.setattr(gitops.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"boom")
                        if "ls-files" in argv else real(argv, **kw))
    p = gitops.git(repo, "status", check=False)
    assert p.returncode == 128 and "unreadable" in p.stderr


def test_check_false_reports_a_refusal_as_a_failed_call(tmp_path):
    """#1390 review R2: callers of `check=False` never got GitError from git(); a refusal (before or after the call)
    is a failed result for them, never a raise and never a success."""
    repo = _repo(tmp_path)
    _setcfg(repo, "core.editor", "evil")
    p = gitops.git(repo, "status", check=False)
    assert p.returncode != 0 and "core.editor" in p.stderr
    assert gitops.git(repo, "-C", "x", "status", check=False).returncode != 0
