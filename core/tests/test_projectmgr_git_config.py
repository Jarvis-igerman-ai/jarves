"""#1390: commands named by the shared git config (filters, merge drivers, diff commands, config hooks, helpers)
never run under JARVES's own git calls. An agent can write the shared `.git/config`; each test first proves that
plain git runs the planted command, then that `gitops.git()` does not."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from jarves.projectmgr import gitops


@pytest.fixture(autouse=True)
def _own_git_config(monkeypatch, tmp_path):
    """R5 (#1615): the machine's own global/system git config never reaches these tests, in plain git or gitops."""
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.cfg"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(gitops, "_LAZY_OFF_OK", [])      # #1632 round 3 R2: no git-version cache leaks between tests


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
    # a shared helper empties the whole helper list, then the owner's helper comes back under its own key (R2)
    gen = "credential.helper"
    url = "credential.https://h.example.helper"
    assert pairs.index((gen, "manager")) > pairs.index((gen, "")) > pairs.index((url, ""))
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
    argv, env = [c for c in seen if "--show-scope" not in c[0]][-1]
    assert argv[1] == "--no-pager" and "protocol.ext.allow=never" in argv
    assert env["GIT_EDITOR"] == ":" and env["GIT_SEQUENCE_EDITOR"] == ":"
    _shared_config(repo, '[protocol "ext"]\n\tallow = always\n')
    gitops.git(repo, "remote", "add", "evil", f"ext::sh -c touch% {marker.as_posix()}")
    assert gitops.git(repo, "push", "evil", "main", check=False).returncode != 0 and not marker.exists()


def test_an_unreadable_config_stops_the_command(monkeypatch, tmp_path):
    """Fail closed: when the config cannot be read, the overrides are unknown, so the command is never spawned
    (R5: `status` itself would succeed here, only the read fails)."""
    repo, spawned = _repo(tmp_path), []
    real = subprocess.run
    monkeypatch.setattr(gitops.subprocess, "run", lambda argv, **kw: spawned.append(argv) or real(argv, **kw))
    monkeypatch.setattr(gitops, "_config_entries",
                        lambda cwd, timeout: subprocess.CompletedProcess(["git"], 1, "", "cannot read config"))
    with pytest.raises(gitops.GitError, match="cannot read config"):
        gitops.git(repo, "status")
    assert gitops.git(repo, "status", check=False).returncode == 1 and spawned == []


@pytest.mark.parametrize("cond", ["onbranch:jarves/p1/t*", "gitdir:**/worktrees/**"])
def test_a_conditional_include_in_the_shared_config_stops_the_command(tmp_path, cond):
    """R1 (#1390): an include that matches only in a child git (the task branch, the new worktree) is never seen by the
    read in `cwd`, so its filter would run on `worktree add`. Such a condition in the shared config fails closed."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "ran.txt"
    _diverged(repo, "*.txt filter=ev\n")
    gitops.ensure_integration(home, str(repo), 1)
    (repo / ".git" / "evil.cfg").write_text(f'[filter "ev"]\n\tsmudge = "{_runs(marker)}"\n', encoding="utf-8")
    subprocess.run(["git", "config", f"includeIf.{cond}.path", "evil.cfg"], cwd=repo, check=True)
    _plain(repo, "worktree", "add", "-b", "jarves/p1/t9-a1", str(tmp_path / "plain"), "main")
    assert marker.exists()
    marker.unlink()
    with pytest.raises(gitops.GitError, match="conditional include"):
        gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    assert gitops.git(repo, "status", check=False).returncode != 0 and not marker.exists()


def test_submodules_never_recurse(tmp_path):
    """R1 (#1390): `submodule.recurse=true` would make reset/checkout/merge run git in `.git/modules/<n>`, whose config
    (writable by agents) is never read; its filter would run there."""
    sub, marker = tmp_path / "sub", tmp_path / "ran.txt"
    sub.mkdir()
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "1"]):
        (sub / ".gitattributes").write_text("*.txt filter=ev\n", encoding="utf-8")
        (sub / "a.txt").write_text("1\n", encoding="utf-8")
        _plain(sub, *args)
    repo = _repo(tmp_path)
    _plain(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "s")
    _plain(repo, "commit", "-qm", "sub")
    with open(repo / ".git" / "modules" / "s" / "config", "a", encoding="utf-8") as f:
        f.write(f'[filter "ev"]\n\tsmudge = "{_runs(marker)}"\n')
    subprocess.run(["git", "config", "submodule.recurse", "true"], cwd=repo, check=True)
    (repo / "s" / "a.txt").unlink()
    _plain(repo, "reset", "-q", "--hard")
    assert marker.exists()
    marker.unlink()
    (repo / "s" / "a.txt").unlink()
    gitops.git(repo, "reset", "-q", "--hard")
    assert not marker.exists()


def test_worktrees_are_checked_out_by_a_git_that_reads_their_own_config(monkeypatch, tmp_path):
    """R1 (#1390): `worktree add` checks out in a child git whose config was never read; JARVES adds the worktree
    with --no-checkout and checks it out in a separate call from inside the new worktree."""
    repo, home, seen = _repo(tmp_path), tmp_path / "home", []
    real = subprocess.run
    monkeypatch.setattr(gitops.subprocess, "run",
                        lambda argv, **kw: seen.append((argv, kw.get("cwd"))) or real(argv, **kw))
    gitops.ensure_integration(home, str(repo), 1)
    wt, _ = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    rel = gitops.detached_worktree(home, str(repo), 1, gitops.git(repo, "rev-parse", "HEAD").stdout.strip())
    adds = [a for a, _ in seen if "worktree" in a and "add" in a]
    assert len(adds) == 3 and all("--no-checkout" in a for a in adds)
    for w in (home / "projects" / "1" / "integration", wt, rel):
        assert any(c == str(w) and "reset" in a for a, c in seen) and (w / "f.txt").exists()


def _helpers_run(repo: Path, marker: Path, url: str) -> list[str]:
    """Which helpers a real `git credential fill` runs for `url` under JARVES's overrides for this repo's config."""
    entries = gitops._config_entries(repo, 30)
    env = gitops.child_env({**gitops.FIXED_ENV, **gitops._override_env(gitops.config_overrides(entries))})
    subprocess.run(["git", "credential", "fill"], cwd=repo, input=f"url={url}\n\n", env=env, capture_output=True,
                   text=True, timeout=30)
    return marker.read_text(encoding="utf-8").split() if marker.exists() else []


def test_a_url_credential_reset_keeps_every_matching_owner_helper(tmp_path):
    """R2 (#1615): the owner's helpers under the generic key, a host key and a wildcard key all match the request; a
    shared-config helper under yet another matching key is undone and each owner helper runs exactly once."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"

    def helper(name: str) -> str:
        return f"!f() {{ echo {name} >> '{marker.as_posix()}'; }}; f"
    for key, name in (("credential.helper", "generic"), ("credential.https://h.example.helper", "host"),
                      ("credential.https://*.example.helper", "wild")):
        subprocess.run(["git", "config", "--global", "--add", key, helper(name)], cwd=repo, check=True)
    subprocess.run(["git", "config", "--add", "credential.https://h.example/.helper", helper("evil")], cwd=repo,
                   check=True)
    assert sorted(_helpers_run(repo, marker, "https://h.example/r")) == ["generic", "host", "wild"]
    marker.unlink()
    assert _helpers_run(repo, marker, "https://o.other/r") == ["generic"]            # no owner helper runs twice


def test_trailer_commands_set_in_the_shared_config_never_run(tmp_path):
    """R1 (#1615): `git interpret-trailers` and `git commit --trailer` run `trailer.<token>.cmd`/`.command`."""
    repo, marker, msg = _repo(tmp_path), tmp_path / "ran.txt", tmp_path / "msg.txt"
    msg.write_text("subject\n", encoding="utf-8")
    _shared_config(repo, f'[trailer "ev"]\n\tcmd = "{_runs(marker, "true")}"\n'
                         f'\tcommand = "{_runs(marker, "true")}"\n')
    _plain(repo, "interpret-trailers", "--trailer", "ev=x", str(msg))
    assert marker.exists()
    marker.unlink()
    gitops.git(repo, "interpret-trailers", "--trailer", "ev=x", str(msg))
    gitops.git(repo, "commit", "-q", "--allow-empty", "-m", "t", "--trailer", "ev=x")
    assert not marker.exists()


def test_config_hook_events_are_undone_on_every_git(monkeypatch, tmp_path):
    """R5 (#1615): runs on any git, also one without config hooks: the command git() spawns carries an empty
    `hook.<name>.event` for every hook the config defines, so no event can trigger it."""
    repo, seen = _repo(tmp_path), []
    _shared_config(repo, '[hook "ev"]\n\tcommand = "touch x"\n\tevent = pre-commit\n')
    real = subprocess.run
    monkeypatch.setattr(gitops.subprocess, "run",
                        lambda argv, **kw: seen.append((argv, kw.get("env"))) or real(argv, **kw))
    gitops.git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    env = next(e for a, e in seen if "commit" in a)
    pairs = [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(int(env["GIT_CONFIG_COUNT"]))]
    assert ("hook.ev.event", "") in pairs
    assert ("hook.ev.command", ":") in pairs        # round 3 R3: a no-op even if an empty event does not clear the list


def test_inert_git_calls_spawn_one_process(monkeypatch, tmp_path):
    """R3 (#1615): rev-parse/rev-list/merge-base run no config-named command, so where GIT_NO_LAZY_FETCH works they
    skip both config reads (most projectmgr calls are rev-parse); a failed one, an older git and anything else read
    the config before and after (#1632 R1)."""
    repo, seen = _repo(tmp_path), []
    real = subprocess.run
    monkeypatch.setattr(gitops.subprocess, "run",
                        lambda argv, **kw: seen.append((argv, kw.get("env"))) or real(argv, **kw))
    monkeypatch.setattr(gitops, "_LAZY_OFF_OK", [True])
    for args in (("rev-parse", "HEAD"), ("rev-list", "--count", "HEAD"), ("merge-base", "HEAD", "HEAD")):
        seen.clear()
        gitops.git(repo, *args)
        assert len(seen) == 1 and "protocol.ext.allow=never" in seen[0][0]
        assert seen[0][1]["GIT_NO_LAZY_FETCH"] == "1"
    for lazy_ok, args, spawned in ((True, ("rev-parse", "--verify", "-q", "nope"), 4),
                                   (False, ("rev-parse", "HEAD"), 3), (True, ("status", "--porcelain"), 3),
                                   (True, ("rev-list", "--alternate-refs", "HEAD"), 3),       # round 3 R4
                                   (True, ("rev-parse", "--show-toplevel", "--parseopt"), 3)):
        monkeypatch.setattr(gitops, "_LAZY_OFF_OK", [lazy_ok])
        seen.clear()
        gitops.git(repo, *args, check=False)
        assert len(seen) == spawned


@pytest.mark.parametrize("version,ok", [("git version 2.45.1", True), ("git version 2.43.4.windows.1", True),
                                        ("git version 2.43.0", False), ("git version 2.44.0", False), ("junk", False)])
def test_the_fast_path_needs_a_git_with_the_lazy_fetch_switch(monkeypatch, version, ok):
    """#1632 R1: GIT_NO_LAZY_FETCH exists from 2.45.1 and its maintenance releases; an older git ignores it."""
    monkeypatch.setattr(gitops, "_LAZY_OFF_OK", [])
    monkeypatch.setattr(gitops.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, version, ""))
    assert gitops._lazy_fetch_switch() is ok
    assert gitops._LAZY_OFF_OK == ([ok] if version != "junk" else [])



def test_a_key_that_is_not_utf8_stops_the_command(tmp_path):
    """R1 (round 2): a reset can only name a key that decoded losslessly; a `\\xff` subsection would be reset under
    other bytes and git would still run the agent's filter, so such a key fails closed."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    _diverged(repo, "*.txt filter=ev\n")
    (repo / ".gitattributes").write_bytes(b"*.txt filter=\xff\n")
    (repo / ".git" / "agent.cfg").write_bytes(b'[filter "\xff"]\n\tsmudge = "' + _runs(marker).encode() + b'"\n')
    subprocess.run(["git", "config", "--add", "include.path", "agent.cfg"], cwd=repo, check=True)
    (repo / "a.txt").unlink()
    _plain(repo, "checkout", "--", "a.txt")
    assert marker.exists()
    marker.unlink()
    (repo / "a.txt").unlink()
    with pytest.raises(gitops.GitError, match="not valid UTF-8"):
        gitops.git(repo, "checkout", "--", "a.txt")
    assert not marker.exists()


def test_a_push_never_reaches_a_local_repository(tmp_path):
    """R2 (round 2): a local remote runs receive-pack and the target's own hooks in a git that gets none of the
    overrides; the shared config can aim `origin` there, so the file transport is off for JARVES's calls."""
    repo, target, marker = _repo(tmp_path), tmp_path / "t.git", tmp_path / "ran.txt"
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    hook = target / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(["git", "remote", "add", "origin", str(target)], cwd=repo, check=True)
    _plain(repo, "push", "-q", "origin", "main:p")
    if not marker.exists():
        pytest.skip("this platform runs no shell hook")
    marker.unlink()
    assert gitops.git(repo, "push", "origin", "main:q", check=False).returncode != 0 and not marker.exists()


def test_every_git_call_turns_off_submodule_fetch_and_push_recursion(monkeypatch, tmp_path):
    """R2 (round 2): `fetch.recurseSubmodules` / `push.recurseSubmodules` win over `submodule.recurse` and would run
    a fetch or push in `.git/modules/<n>` with that repository's unread config."""
    seen: list = []
    real = subprocess.run
    monkeypatch.setattr(gitops.subprocess, "run", lambda argv, **kw: seen.append(argv) or real(argv, **kw))
    _repo(tmp_path)
    argv = [a for a in seen if "init" in a][-1]
    for flag in ("fetch.recurseSubmodules=false", "push.recurseSubmodules=no", "protocol.file.allow=never"):
        assert flag in argv


def test_a_command_key_added_during_the_call_is_reported(monkeypatch, tmp_path):
    """R3 (round 2): the config is read again after the command; a command key or conditional include that appeared
    in between fails the call with its name instead of passing silently."""
    repo = _repo(tmp_path)
    real, reads = gitops._config_entries, []

    def read(cwd, timeout):
        out = real(cwd, timeout)
        reads.append(1)
        return out + ([("local", "filter.ev.smudge", "evil")] if len(reads) % 2 == 0 else [])
    monkeypatch.setattr(gitops, "_config_entries", read)
    with pytest.raises(gitops.GitError, match="changed while.*filter.ev.smudge"):
        gitops.git(repo, "status")
    assert gitops.git(repo, "status", check=False).returncode != 0


def test_a_key_toggled_between_the_two_reads_is_a_known_residual(monkeypatch, tmp_path):
    """R5 (round 3): documents the limit stated in git(): a key added after the first read and removed before the
    second read runs and is not reported. Detection, not prevention; if this test starts failing, the residual is
    closed and the docstring and changelog should say so."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    (repo / "f.txt").write_text("changed", encoding="utf-8")
    real, cfg = subprocess.run, repo / ".git" / "config"

    def run(argv, **kw):
        if "diff" in argv and "config" not in argv:            # the command itself, between the two reads
            clean = cfg.read_text(encoding="utf-8")
            cfg.write_text(clean + f'[diff]\n\texternal = "{_runs(marker, "true")}"\n', encoding="utf-8")
            try:
                return real(argv, **kw)
            finally:
                cfg.write_text(clean, encoding="utf-8")
        return real(argv, **kw)
    monkeypatch.setattr(gitops.subprocess, "run", run)
    assert gitops.git(repo, "diff", check=False).returncode == 0
    assert marker.exists()


@pytest.mark.parametrize("fast_path", [False, True])
def test_a_lazy_fetch_in_a_partial_clone_never_runs_the_shared_ssh_command(monkeypatch, tmp_path, fast_path):
    """R1 (#1632): in a partial clone, rev-parse/rev-list/merge-base start a lazy `git fetch` for a missing object; the
    fast path must never let that fetch run the shared `core.sshCommand` (or helper, upload-pack) un-overridden."""
    server, marker = _repo(tmp_path), tmp_path / "ran.txt"
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=server, check=True)
    clone = tmp_path / "clone"
    _plain(tmp_path, "-c", "protocol.file.allow=always", "clone", "-q", "--filter=tree:0", "--no-checkout",
           server.as_uri(), str(clone))
    subprocess.run(["git", "remote", "set-url", "origin", "ssh://127.0.0.1:9/x"], cwd=clone, check=True)
    # round 3 R5: the owner's own ssh command (restored by the overrides) fails at once, so no real ssh ever starts
    subprocess.run(["git", "config", "--global", "core.sshCommand", "false"], cwd=clone, check=True)
    _shared_config(clone, f'[core]\n\tsshCommand = "{_runs(marker, "exit 1")}"\n')
    _plain(clone, "rev-parse", "HEAD:f.txt")                  # the tree of HEAD is missing: lazy fetch over ssh
    assert marker.exists()
    marker.unlink()
    subprocess.run(["git", "rev-parse", "HEAD:f.txt"], cwd=clone, capture_output=True, env={**os.environ,
                   "GIT_NO_LAZY_FETCH": "1"})
    if fast_path and marker.exists():
        pytest.skip("this git ignores GIT_NO_LAZY_FETCH")
    monkeypatch.setattr(gitops, "_LAZY_OFF_OK", [fast_path])
    for args in (("rev-parse", "HEAD:f.txt"), ("rev-list", "--objects", "HEAD"), ("merge-base", "HEAD", "HEAD")):
        gitops.git(clone, *args, check=False)
    assert not marker.exists()


def test_a_worktree_whose_checkout_fails_is_removed_again(monkeypatch, tmp_path):
    """#1632 round 3 R1: `worktree add --no-checkout` then `reset --hard`; if the reset fails, no half worktree (empty
    index: "every file deleted") may stay registered, or ensure_integration would return it as-is next time."""
    repo, home = _repo(tmp_path), tmp_path / "home"
    real = gitops.git

    def failing(cwd, *args, **kw):
        if args[:1] == ("reset",):
            raise gitops.GitError("the shared git config changed while git ran: filter.ev.smudge")
        return real(cwd, *args, **kw)
    monkeypatch.setattr(gitops, "git", failing)
    for make in (lambda: gitops.ensure_integration(home, str(repo), 1),
                 lambda: gitops.detached_worktree(home, str(repo), 1, real(repo, "rev-parse", "HEAD").stdout.strip())):
        with pytest.raises(gitops.GitError, match="changed while"):
            make()
    listed = real(repo, "worktree", "list", "--porcelain").stdout
    assert listed.count("worktree ") == 1 and not (home / "projects" / "1" / "integration").exists()
    monkeypatch.setattr(gitops, "git", real)
    wt = gitops.ensure_integration(home, str(repo), 1)
    assert real(wt, "status", "--porcelain").stdout == "" and (wt / "f.txt").exists()
