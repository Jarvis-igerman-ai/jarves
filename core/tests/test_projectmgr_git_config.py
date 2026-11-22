"""#1390: commands named by the shared git config (filters, merge drivers, diff commands, config hooks, helpers)
never run under JARVES's own git calls. An agent can write the shared `.git/config`; each test first proves that
plain git runs the planted command, then that `gitops.git()` does not."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarves.projectmgr import gitops


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    return repo


def _runs(marker: Path, then: str = "cat") -> str:
    """A config value that leaves a mark, then behaves like a harmless filter/driver."""
    return f"echo ran >> '{marker.as_posix()}'; {then}"


def _shared_config(repo: Path, text: str, name: str = "agent.cfg") -> None:
    """What an agent writes into the shared `.git/config`, here through an include (git follows it)."""
    (repo / ".git" / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "config", "--add", "include.path", name], cwd=repo, check=True)


def _plain(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@b", *args], cwd=repo, capture_output=True)


def _diverged(repo: Path, attrs: str) -> None:
    """main and side both change a.txt, which `.gitattributes` routes through the agent's filter/driver."""
    (repo / ".gitattributes").write_text(attrs, encoding="utf-8")
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "base", "--no-verify")
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "a.txt").write_text("side\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "side", "--no-verify")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "a.txt").write_text("main\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "main", "--no-verify")


def test_filters_set_in_the_shared_config_never_run(tmp_path):
    """clean/smudge/process run on add, status, reset, worktree add and merge whenever `.gitattributes` names them."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "ran.txt"
    _diverged(repo, "*.txt filter=ev\n")
    _shared_config(repo, f'[filter "ev"]\n\tclean = "{_runs(marker)}"\n\tsmudge = "{_runs(marker)}"\n')
    (repo / "a.txt").write_text("dirty\n", encoding="utf-8")
    _plain(repo, "add", "-A")
    assert marker.exists()
    marker.unlink()
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "status", "--porcelain")
    gitops.git(repo, "reset", "-q", "--hard")
    gitops.ensure_integration(home, str(repo), 1)
    wt, _ = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    (wt / "b.txt").write_text("b\n", encoding="utf-8")
    assert gitops.commit_all(wt, "b") and gitops.changed_files(wt, "main") == ["b.txt"]
    assert gitops.merge_in(repo, "side") == ["a.txt"]                       # a real conflict, aborted
    assert not marker.exists()


def test_a_long_running_filter_process_never_starts(tmp_path):
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    _diverged(repo, "*.txt filter=ev\n")
    _shared_config(repo, f'[filter "ev"]\n\tprocess = "{_runs(marker, "exit 1")}"\n')
    (repo / "a.txt").write_text("dirty\n", encoding="utf-8")
    _plain(repo, "add", "-A")
    assert marker.exists()
    marker.unlink()
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "reset", "-q", "--hard")
    assert not marker.exists()


def test_merge_drivers_set_in_the_shared_config_never_run(tmp_path):
    """`merge.<x>.driver` runs on a merge when `.gitattributes` names it, the ship path (ident=False) included."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    gitops.git(repo, "config", "user.name", "Owner")
    gitops.git(repo, "config", "user.email", "owner@example.invalid")
    _diverged(repo, "*.txt merge=ev\n")
    _shared_config(repo, f'[merge "ev"]\n\tdriver = "{_runs(marker, "exit 1")}"\n')
    _plain(repo, "merge", "--no-edit", "side")
    _plain(repo, "merge", "--abort")
    assert marker.exists()
    marker.unlink()
    assert gitops.merge_in(repo, "side") == ["a.txt"]
    assert gitops.merge_in(repo, "side", ident=False) == ["a.txt"]
    assert not marker.exists()


def test_diff_commands_set_in_the_shared_config_never_run(tmp_path):
    """`diff.<x>.command`, `diff.<x>.textconv` and `diff.external` are commands a diff runs."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    (repo / ".gitattributes").write_text("*.txt diff=ev\n", encoding="utf-8")
    (repo / "a.txt").write_text("1\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "a", "--no-verify")
    base = gitops.git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "b", "--no-verify")
    _shared_config(repo, f'[diff "ev"]\n\tcommand = "{_runs(marker, "true")}"\n\ttextconv = "{_runs(marker)}"\n')
    _plain(repo, "diff", base)
    assert marker.exists()
    marker.unlink()
    gitops.git(repo, "diff", base, check=False)          # a full patch fails instead of running it (JARVES uses none)
    gitops.git(repo, "diff", "--stat", base)
    assert gitops.diffstat(repo, base) != "no changes" and gitops.changed_files(repo, base) == ["a.txt"]
    _shared_config(repo, f'[diff]\n\texternal = "{_runs(marker, "true")}"\n', "agent2.cfg")
    gitops.git(repo, "diff", base, check=False)
    assert gitops.changed_files(repo, base) == ["a.txt"]
    assert not marker.exists()


def test_config_defined_hooks_never_run(tmp_path):
    """`hook.<name>.command` (newer git) runs even with `core.hooksPath` pointing nowhere."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    _shared_config(repo, f'[hook "ev"]\n\tcommand = "{_runs(marker, "true")}"\n\tevent = post-commit\n'
                         "\tevent = pre-commit\n\tevent = reference-transaction\n")
    _plain(repo, "-c", f"core.hooksPath={gitops.NO_HOOKS}", "commit", "-q", "--allow-empty", "-m", "p")
    if not marker.exists():
        pytest.skip("this git has no config-defined hooks")
    marker.unlink()
    gitops.git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    gitops.git(repo, "branch", "b")
    assert not marker.exists()


def test_owner_values_survive_and_shared_ones_are_undone():
    """A key the shared config sets is reset, then the owner's own system/global values return (LFS, credential
    manager, signing); a gpg/ssh/pack program with no owner value gets git's default name."""
    entries = [("system", "credential.helper", "manager"), ("global", "filter.lfs.smudge", "git-lfs smudge -- %f"),
               ("local", "filter.lfs.smudge", "evil"), ("local", "filter.Ev.x=y.clean", "evil"),
               ("local", "credential.https://h.example.helper", "!evil"), ("local", "gpg.program", "evil"),
               ("worktree", "core.sshcommand", "evil"), ("local", "remote.origin.receivepack", "evil"),
               ("local", "remote.origin.url", "../x"), ("global", "hook.lint.command", "lint"),
               ("local", "hook.ev.event", "post-commit"), ("local", "user.name", "Owner"),
               ("global", "diff.tool", "x")]
    pairs = gitops.config_overrides(entries)
    resets = {k for k, v in pairs if v == ""}
    assert {"filter.lfs.smudge", "filter.Ev.x=y.clean", "gpg.program", "core.sshcommand", "remote.origin.receivepack",
            "credential.https://h.example.helper", "hook.ev.event", "hook.lint.event"} <= resets
    last = dict(pairs)
    assert last["filter.lfs.smudge"] == "git-lfs smudge -- %f" and last["filter.Ev.x=y.clean"] == ""
    # the URL helper reset empties the whole helper list, so the owner's helper comes back after it
    assert pairs.index(("credential.helper", "manager")) > pairs.index(("credential.https://h.example.helper", ""))
    assert last["gpg.program"] == "gpg" and last["core.sshcommand"] == "ssh"
    assert last["remote.origin.receivepack"] == "git-receive-pack"
    assert not {"remote.origin.url", "user.name", "diff.tool"} & set(last)
    assert gitops.config_overrides([("global", "filter.lfs.smudge", "git-lfs")]) == []     # owner-only: untouched


def test_every_git_call_fixes_editors_pagers_and_ext_remotes(monkeypatch, tmp_path):
    """Editors and pagers named in the shared config never start; an `ext::` remote never runs its command."""
    seen: list = []
    real = subprocess.run

    def run(argv, **kw):
        seen.append((argv, kw.get("env") or {}))
        return real(argv, **kw)
    monkeypatch.setattr(gitops.subprocess, "run", run)
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    argv, env = seen[-1]
    assert argv[1] == "--no-pager" and "protocol.ext.allow=never" in argv
    assert env["GIT_EDITOR"] == ":" and env["GIT_SEQUENCE_EDITOR"] == ":"
    _shared_config(repo, '[protocol "ext"]\n\tallow = always\n')
    gitops.git(repo, "remote", "add", "evil", f"ext::sh -c touch% {marker.as_posix()}")
    assert gitops.git(repo, "push", "evil", "main", check=False).returncode != 0 and not marker.exists()


def test_an_unreadable_config_stops_the_command(tmp_path):
    """Fail closed: when the config cannot be read, the overrides are unknown, so the command never runs."""
    repo = _repo(tmp_path)
    with open(repo / ".git" / "config", "a", encoding="utf-8") as f:
        f.write("[broken\n")
    with pytest.raises(gitops.GitError):
        gitops.git(repo, "status")
    assert gitops.git(repo, "status", check=False).returncode != 0
