"""Project manager git helper: tasks share one repository, so worktree work takes turns and a held lock file is retried.
No network; a throw-away local repository."""
from __future__ import annotations

import os
import subprocess
import threading
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


def _fake_run(outcomes, calls):
    def run(argv, **kw):
        if "--get-regexp" in argv:                  # the shared-config read before each command (#1390): none set
            return subprocess.CompletedProcess(argv, 1, "", "")
        calls.append(argv)
        rc, err = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        return subprocess.CompletedProcess(argv, rc, "", err)
    return run


LOCK = "fatal: Unable to create 'C:/r/.git/index.lock': File exists."


def test_a_held_lock_file_is_retried_then_succeeds(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(128, LOCK), (128, LOCK), (0, "")], calls))
    monkeypatch.setattr(gitops.time, "sleep", lambda s: None)
    assert gitops.git(tmp_path, "status").returncode == 0 and len(calls) == 3


def test_a_lock_that_never_clears_fails_after_the_retries(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(128, LOCK)], calls))
    monkeypatch.setattr(gitops.time, "sleep", lambda s: None)
    with pytest.raises(gitops.GitError):
        gitops.git(tmp_path, "status")
    assert len(calls) == gitops.LOCK_RETRIES + 1


def test_other_failures_are_never_retried(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(128, "fatal: not a git repository")], calls))
    with pytest.raises(gitops.GitError):
        gitops.git(tmp_path, "status")
    assert len(calls) == 1


def test_parallel_tasks_can_all_create_and_replace_their_worktrees(tmp_path):
    repo, home = _repo(tmp_path), tmp_path / "home"
    gitops.ensure_integration(home, str(repo), 1)
    errors: list = []

    def work(seq: int) -> None:
        for attempt in (1, 2, 3, 1, 2):                      # the repeats replace an earlier worktree, like a retry does
            try:
                wt, _ = gitops.new_task_worktree(home, str(repo), 1, seq, attempt)
                assert (wt / ".git").exists()
            except Exception as e:                           # noqa: BLE001 - collected and asserted below
                errors.append((seq, attempt, str(e)[:200]))

    threads = [threading.Thread(target=work, args=(s,)) for s in range(1, 5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml", ".GitHub/workflows/ci.yml", ".GITHUB/dependabot.yml",
    ".Claude/settings.json", ".CLAUDE/agents/x.md", ".Codex/config.toml", ".OpenCode/x", ".Git/config",
    ".GitHooks/pre-commit", "OpenCode.json", "sub/.MCP.json", ".GitModules", r".GitHub\workflows\ci.yml"])
def test_protected_paths_are_refused_in_any_letter_case(path):
    # JAR-317: Windows/macOS ignore case, so `.GitHub/...` is the same CI file as `.github/...`
    prot, outside = gitops.violations([path], ["src/**"])
    assert prot == [path] and outside == []


def test_changed_files_shows_non_ascii_and_renamed_protected_paths(tmp_path):
    # JAR-574: plain `git diff --name-only` quotes a non-ASCII name (`".github/workflows/\303\251.yml"`), which no
    # PROTECTED prefix matches, and names only the target of a rename, hiding a file moved out of `.github/`
    repo = _repo(tmp_path)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "ci", "--no-verify")
    base = gitops.git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / ".GitHub" / "workflows").mkdir(parents=True, exist_ok=True)  # a case-sensitive FS needs its own dir
    (repo / ".GitHub" / "workflows" / "é.yml").write_text("on: push\n", encoding="utf-8")
    (repo / "src").mkdir()
    gitops.git(repo, "mv", ".github/workflows/ci.yml", "src/ci.yml")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "sneak", "--no-verify")
    files = gitops.changed_files(repo, base)
    prot, _ = gitops.violations(files, ["src/**"])
    assert ".github/workflows/ci.yml" in prot
    # exact name: gitops.git() decodes stdout as UTF-8 (encoding="utf-8"), never the Windows locale codec
    assert any(f.casefold() == ".github/workflows/é.yml" for f in prot), files


@pytest.mark.parametrize("path", ["CLAUDE~1/settings.local.json", "GITHUB~1/workflows/ci.yml", "github~2/x.yml",
                                  "src/OPENCO~1.JSO", r"GITHUB~1\workflows\ci.yml"])
def test_windows_short_names_are_refused(path):
    # JAR-574: a Windows checkout writes `CLAUDE~1/x` into the existing `.claude/` folder
    assert gitops.is_protected(path)


@pytest.mark.parametrize("path", ["src/github/x.py", "docs/.github.md", "src/claude/x.py", "my.mcp.json.bak",
                                  "src/backup~/x.py", "notes~old.md", "src/version~12345678/x.py"])
def test_ordinary_paths_with_similar_names_are_not_protected(path):
    assert not gitops.is_protected(path)


def test_agent_tool_rules_deny_edits_to_github_workflows():
    from jarves.projectmgr import runners
    assert "Edit(.github/**)" in runners.DENY and "Write(.github/**)" in runners.DENY


def _hook(folder: Path, name: str, marker: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    h = folder / name
    h.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\n", encoding="utf-8")
    h.chmod(0o755)


# every hook JARVES's own commands could trigger (#1381): index writes run post-index-change, ref writes run
# reference-transaction, merges, commits and checkouts run the rest
HOOKS = ("post-merge", "pre-merge-commit", "commit-msg", "prepare-commit-msg", "pre-commit", "post-commit",
         "post-checkout", "post-index-change", "reference-transaction", "post-rewrite")


def test_merges_never_run_hooks_from_the_shared_git_folder_or_config(tmp_path):
    """#1381 item 1: an agent that writes `.git/hooks` or sets `core.hooksPath` in the shared config must not get code
    run by JARVES's merges (merge_in, its abort, fast_forward; ship uses merge_in with the owner's identity)."""
    repo, marker = _repo(tmp_path), tmp_path / "hook-ran.txt"
    gitops.git(repo, "config", "user.name", "Owner")                              # ident=False: the owner's identity
    gitops.git(repo, "config", "user.email", "owner@example.invalid")
    for br in ("side", "side2", "main"):                                          # three divergent tips
        gitops.git(repo, "checkout", "-q", *(["main"] if br == "main" else ["-b", br, "main"]))
        (repo / f"{br}.txt").write_text(br, encoding="utf-8")
        gitops.git(repo, "add", "-A")
        gitops.git(repo, "commit", "-qm", br, "--no-verify")
    for name in HOOKS:
        _hook(repo / ".git" / "hooks", name, marker)
    assert gitops.merge_in(repo, "side") == [] and (repo / "side.txt").exists()   # a real merge commit
    assert gitops.merge_in(repo, "side2", ident=False) == [] and (repo / "side2.txt").exists()   # the ship path
    assert gitops.git(repo, "log", "-1", "--format=%an").stdout.strip() == "Owner"
    assert gitops.merge_in(repo, "main~1") == []                                  # already merged: nothing to do
    gitops.git(repo, "checkout", "-qb", "ff", "HEAD~1")
    gitops.fast_forward(repo, "main")
    assert not marker.exists()


def _evil_config(repo: Path, tmp_path: Path, marker: Path) -> None:
    """What an agent that can write the shared `.git/config` sets: a hooks folder with every hook, and an fsmonitor
    command, which git runs on every index refresh (add, status, diff, merge) whatever `core.hooksPath` says."""
    for name in HOOKS:
        _hook(tmp_path / "evil-hooks", name, marker)
    _hook(tmp_path / "evil-bin", "fsmonitor", marker)
    subprocess.run(["git", "config", "core.hooksPath", str(tmp_path / "evil-hooks")], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.fsmonitor", (tmp_path / "evil-bin" / "fsmonitor").as_posix()], cwd=repo,
                   check=True)


def test_hooks_and_fsmonitor_set_in_the_shared_config_never_run(tmp_path):
    """#1381 review R1 and R5: a merge commit, a fast-forward, worktree add and a commit with `core.hooksPath` and
    `core.fsmonitor` set in the shared config run neither."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "hook-ran.txt"
    gitops.git(repo, "config", "user.name", "Owner")                    # ident=False needs the owner's identity
    gitops.git(repo, "config", "user.email", "owner@example.invalid")
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "side.txt").write_text("s", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "side", "--no-verify")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "main.txt").write_text("m", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "main", "--no-verify")
    _evil_config(repo, tmp_path, marker)
    subprocess.run(["git", "status"], cwd=repo, capture_output=True, check=True)     # the setup itself works
    assert marker.exists()
    marker.unlink()
    assert gitops.merge_in(repo, "side", ident=False) == [] and (repo / "side.txt").exists()     # merge commit
    gitops.git(repo, "checkout", "-qb", "ff", "side")
    gitops.fast_forward(repo, "main")
    gitops.ensure_integration(home, str(repo), 1)
    wt, _ = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt, "a") and gitops.changed_files(wt, "main") == ["a.py"]
    assert not marker.exists()


def test_symlinks_and_gitlinks_in_scope_count_as_protected(tmp_path):
    """#1381 item 2: `is_protected` only reads path text, so a symlink in scope pointing into `.github/` or a gitlink
    (submodule entry) in scope passed the scope check. link_files reports both; the engine treats them as protected."""
    repo = _repo(tmp_path)
    base = gitops.git(repo, "rev-parse", "HEAD").stdout.strip()
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="../.github/workflows",
                          capture_output=True, text=True, check=True).stdout.strip()
    gitops.git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},src/link")
    gitops.git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},src/sub")
    (repo / "src").mkdir()
    (repo / "src" / "ok.py").write_text("1", encoding="utf-8")
    gitops.git(repo, "add", "src/ok.py")
    gitops.git(repo, "commit", "-qm", "links", "--no-verify")
    files = gitops.changed_files(repo, base)
    assert gitops.violations(files, ["src/"]) == ([], [])                       # the path text alone looks harmless
    assert sorted(gitops.link_files(repo, base)) == ["src/link", "src/sub"]


def test_worktree_creation_and_commits_never_run_shared_hooks(tmp_path):
    """#1381 review: worktree add runs `post-checkout`, `commit --no-verify` still runs `prepare-commit-msg` and
    `post-commit`, `add -A` runs `post-index-change`, branch create/delete runs `reference-transaction`. JARVES's own
    git commands run no shared hook at all."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "hook-ran.txt"
    for name in HOOKS:
        _hook(repo / ".git" / "hooks", name, marker)
    gitops.ensure_integration(home, str(repo), 1)
    wt, _ = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt, "a")
    gitops.detached_worktree(home, str(repo), 1, gitops.git(wt, "rev-parse", "HEAD").stdout.strip())
    gitops.remove_worktree(str(repo), wt, gitops.task_branch(1, 1, 1))           # branch -D: a ref write
    assert gitops.git(repo, "branch", "--list", gitops.task_branch(1, 1, 1)).stdout.strip() == ""
    assert not marker.exists()


def test_the_hooks_path_can_never_become_a_folder_an_agent_fills():
    """#1381 review R1: a shared temp folder could be filled by a racing agent between a check and git's hook lookup.
    The hooks path sits below an existing file, so nobody can create it as a folder, and every git call uses it."""
    assert not os.path.isdir(gitops.NO_HOOKS)
    with pytest.raises(OSError):
        os.makedirs(gitops.NO_HOOKS)
    with pytest.raises(OSError):
        (Path(gitops.NO_HOOKS) / "post-merge").write_text("#!/bin/sh\n", encoding="utf-8")


def test_every_git_call_disables_hooks_and_fsmonitor(monkeypatch, tmp_path):
    """#1381 review R3: the one `git()` helper puts both overrides in front of every command, with either identity."""
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls))
    gitops.git(tmp_path, "merge", "x")
    gitops.git(tmp_path, "merge", "x", ident=False)
    for argv in calls:
        pairs = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-c"]
        assert f"core.hooksPath={gitops.NO_HOOKS}" in pairs and "core.fsmonitor=false" in pairs
        assert argv.index("merge") > max(i for i, a in enumerate(argv) if a == "-c")


def test_commands_named_in_the_shared_config_never_run(tmp_path):
    """#1390 / #1407 item 3: besides hooks and fsmonitor, the shared (agent-writable) repository config can name
    filter drivers, a merge driver, diff commands and a signing program. With a `.gitattributes` naming them, add,
    worktree add, commit, diff and merge (also the ship path with the owner's identity) must run none of them."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "cmd-ran.txt"
    gitops.git(repo, "config", "user.name", "Owner")
    gitops.git(repo, "config", "user.email", "owner@example.invalid")
    _hook(tmp_path / "evil-bin", "cmd", marker)
    cmd = (tmp_path / "evil-bin" / "cmd").as_posix()
    (repo / ".gitattributes").write_text("* filter=x merge=m diff=d\n*.p filter=p\n", encoding="utf-8")
    (repo / "q.p").write_text("p", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "attrs", "--no-verify")
    for key in ("filter.x.clean", "filter.x.smudge", "merge.m.driver", "diff.d.command", "diff.external",
                "gpg.program"):
        subprocess.run(["git", "config", key, cmd], cwd=repo, check=True)
    (repo / "f.txt").write_text("main side", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, capture_output=True, check=True)
    assert marker.exists()                          # the setup works: without the fix git runs the clean filter
    marker.unlink()
    subprocess.run(["git", "config", "filter.p.process", cmd], cwd=repo, check=True)
    gitops.git(repo, "commit", "-qm", "main side", "--no-verify")
    gitops.ensure_integration(home, str(repo), 1, base="HEAD~1")    # worktree add: smudge and process filters
    wt, _ = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    (wt / "f.txt").write_text("task side", encoding="utf-8")
    assert gitops.commit_all(wt, "task") and gitops.changed_files(wt, "HEAD~1") == ["f.txt"]
    gitops.diffstat(wt, "HEAD~1")
    gitops.git(wt, "diff", "HEAD~1", check=False)                   # a patch diff: diff.<x>.command / diff.external
    assert gitops.merge_in(wt, "main") == ["f.txt"]                  # the merge driver would run on this conflict
    wt2, br2 = gitops.new_task_worktree(home, str(repo), 1, 2, 1)
    (wt2 / "new.txt").write_text("n", encoding="utf-8")
    assert gitops.commit_all(wt2, "new")
    for key, value in (("commit.gpgsign", "true"), ("gpg.format", "openpgp")):
        subprocess.run(["git", "config", key, value], cwd=repo, check=True)
    gitops.merge_in(repo, br2, ident=False)                         # the ship path: the owner's own signing settings
    assert not marker.exists()


def test_only_repository_scope_command_settings_are_overridden(monkeypatch, tmp_path):
    """#1390: every command key set in the repository's own config is overridden (signing programs fall back to git's
    default program, the rest to empty); the owner's global settings (e.g. a git-lfs filter) are left alone."""
    home = tmp_path / "owner-home"
    home.mkdir()
    (home / ".gitconfig").write_text("[filter \"lfs\"]\n\tclean = git-lfs clean -- %f\n"
                                     "[includeIf \"gitdir:/nowhere/\"]\n\tpath = owner.cfg\n",   # the owner's own: allowed
                                     encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    repo = _repo(tmp_path)
    keys = ("core.sshCommand", "core.askPass", "core.pager", "core.editor", "sequence.editor", "credential.helper",
            "gpg.program", "gpg.ssh.program", "gpg.x509.program", "gpg.ssh.defaultKeyCommand", "filter.Lfs2.clean",
            "credential.https://example.invalid.helper", "core.gitProxy", "core.alternateRefsCommand",
            "remote.origin.uploadpack", "remote.origin.receivepack", "remote.origin.vcs",       # review R2
            "trailer.sign.command", "trailer.sign.cmd")                                         # #1468 R3
    for key in keys:
        subprocess.run(["git", "config", key, "evil"], cwd=repo, check=True)
    over = gitops._config_overrides(repo)
    pairs = dict(x.split("=", 1) for x in over[1::2])
    assert "filter.lfs.clean" not in pairs and pairs.pop("gpg.openpgp.program") == "gpg"    # #1468 R1: always set
    assert pairs.pop("gpg.program") == "gpg" and pairs.pop("gpg.ssh.program") == "ssh-keygen"
    assert pairs.pop("gpg.x509.program") == "gpgsm" and pairs.pop("filter.Lfs2.clean") == ""
    assert {k.lower() for k in pairs} == {k.lower() for k in keys} - {"gpg.program", "gpg.ssh.program",
                                                                      "gpg.x509.program", "filter.lfs2.clean"}
    assert set(pairs.values()) == {""}


def test_signing_programs_are_set_on_every_call_from_the_owner_scope_only(monkeypatch, tmp_path):
    """PR #1468 R1: the signing programs and `gpg.ssh.defaultKeyCommand` have fixed names, so they are set on every
    call (the owner's global value, else git's default) and never depend on the repository-scope read, which an agent
    can race."""
    home = tmp_path / "owner-home"
    home.mkdir()
    (home / ".gitconfig").write_text("[gpg]\n\tprogram = owner-gpg\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    pairs = dict(x.split("=", 1) for x in gitops._config_overrides(_repo(tmp_path))[1::2])
    assert pairs == {"gpg.program": "owner-gpg", "gpg.openpgp.program": "gpg", "gpg.x509.program": "gpgsm",
                     "gpg.ssh.program": "ssh-keygen", "gpg.ssh.defaultkeycommand": ""}
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls))
    gitops.git(tmp_path, "commit", "-m", "x")                       # the read saw nothing: still set
    assert "gpg.ssh.defaultkeycommand=" in calls[0] and "gpg.program=gpg" in calls[0]


@pytest.mark.parametrize("cmd", ["diff", "show", "log"])
def test_patch_output_never_runs_an_external_diff(monkeypatch, tmp_path, cmd):
    """PR #1468 R2: `diff.external` has a fixed name, so a patch diff must not depend on the config read: every
    diff/show/log call passes --no-ext-diff."""
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls))
    gitops.git(tmp_path, cmd, "HEAD~1")
    assert calls[0][calls[0].index(cmd):] == [cmd, "--no-ext-diff", "HEAD~1"]


def test_an_unreadable_shared_config_fails_closed(tmp_path):
    """#1390: when the overrides cannot be read, the git command does not run (an error, or rc 128 with check=False)."""
    repo = _repo(tmp_path)
    (repo / ".git" / "config").write_text("[core\n broken", encoding="utf-8")
    with pytest.raises(gitops.GitError, match="could not be read"):
        gitops.git(repo, "status")
    assert gitops.git(repo, "status", check=False).returncode == 128 and not gitops.is_repo(str(repo))
    (repo / ".git" / "config").write_text('[filter "a=b"]\n\tclean = evil\n', encoding="utf-8")    # "-c" cannot name it
    with pytest.raises(gitops.GitError, match="could not be read"):
        gitops.git(repo, "add", "-A")


def test_auto_maintenance_never_runs_from_a_jarves_command(tmp_path):
    """PR #1468 review R1: `commit` and `merge` start `maintenance run --auto` / `gc --auto`, and auto-gc runs commands
    the shared config can name (`gc.recentObjectsHook`, a multi-valued key one `-c` cannot remove). JARVES turns
    auto-maintenance off on every call: with `gc.auto=1`/`gc.autoPackLimit=1` set, a commit repacks nothing."""
    repo, marker = _repo(tmp_path), tmp_path / "gc-hook-ran.txt"
    _hook(tmp_path / "evil-bin", "gc-hook", marker)
    for key, value in (("gc.auto", "1"), ("gc.autoPackLimit", "1"), ("gc.autoDetach", "false"),
                       ("maintenance.autoDetach", "false"), ("gc.recentObjectsHook",
                                                             (tmp_path / "evil-bin" / "gc-hook").as_posix())):
        subprocess.run(["git", "config", key, value], cwd=repo, check=True)
    for i in range(2):                                              # two packs: over gc.autoPackLimit
        (repo / f"p{i}.txt").write_text(str(i), encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", *gitops.IDENT, "-c", "gc.auto=0", "commit", "-qm", f"p{i}", "--no-verify"], cwd=repo,
                       check=True)                                  # CI has no git identity of its own
        subprocess.run(["git", "repack", "-q"], cwd=repo, check=True)
    packs = sorted((repo / ".git" / "objects" / "pack").glob("*.pack"))
    assert len(packs) == 2
    (repo / "new.txt").write_text("n", encoding="utf-8")
    assert gitops.commit_all(repo, "new")
    assert sorted((repo / ".git" / "objects" / "pack").glob("*.pack")) == packs and not marker.exists()


def test_a_config_hook_in_the_shared_config_fails_closed(tmp_path):
    """#1468 review R3 (round 1 R4): newer git (2.56 in CI) runs `hook.<name>.command` for `hook.<name>.event`, even on
    a commit with verification. An empty command is no safe override there (git fails "cannot run"), so a
    repository-scope config hook stops every JARVES git command, whatever the git version."""
    repo, marker = _repo(tmp_path), tmp_path / "cfg-hook-ran.txt"
    for key, value in (("hook.x.command", f"echo ran > '{marker.as_posix()}'"), ("hook.x.event", "pre-commit")):
        subprocess.run(["git", "config", key, value], cwd=repo, check=True)
    (repo / "v.txt").write_text("v", encoding="utf-8")
    with pytest.raises(gitops.GitError, match="config hook"):
        gitops.git(repo, "add", "-A")
    with pytest.raises(gitops.GitError, match="config hook"):
        gitops.git(repo, "commit", "-qm", "verified")
    assert not marker.exists()


@pytest.mark.parametrize("condition", ["onbranch:jarves/**", "gitdir:**/worktrees/**"])
def test_a_conditional_include_in_the_shared_config_fails_closed(tmp_path, condition):
    """PR #1468 review round 2 R1: the overrides come from one config read in JARVES's own context, but `worktree add`
    checks out in a child where HEAD is the new branch and the gitdir is `.git/worktrees/<id>`. A repository-scope
    `includeIf` matching only there pulled in a smudge filter the read never saw. Such a config now stops the command."""
    repo, marker = _repo(tmp_path), tmp_path / "include-ran.txt"
    _hook(tmp_path / "evil-bin", "cmd", marker)
    (repo / ".gitattributes").write_text("* filter=x\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "attrs", "--no-verify")
    evil = tmp_path / "evil.cfg"
    evil.write_text(f"[filter \"x\"]\n\tsmudge = {(tmp_path / 'evil-bin' / 'cmd').as_posix()}\n", encoding="utf-8")
    subprocess.run(["git", "config", f"includeIf.{condition}.path", evil.as_posix()], cwd=repo, check=True)
    with pytest.raises(gitops.GitError, match="conditional include"):
        gitops.ensure_integration(tmp_path / "home", str(repo), 1)
    assert not marker.exists()


def test_fixed_name_command_keys_are_overridden_on_every_call(monkeypatch, tmp_path):
    """Review round 2 R2: keys with a fixed name are overridden whatever the config read saw, so an agent toggling one
    between JARVES's read and git's own read gains nothing. Only `<section>.<name>.*` keys depend on the read."""
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls))
    gitops.git(tmp_path, "status")
    pairs = [calls[0][i + 1] for i, a in enumerate(calls[0][:-1]) if a == "-c"]
    for key in ("core.sshCommand", "core.askPass", "core.pager", "core.editor", "sequence.editor", "core.gitProxy",
                "core.alternateRefsCommand", "credential.helper"):
        assert f"{key}=" in pairs


def test_a_submodule_filter_never_runs_from_a_jarves_command(tmp_path):
    """PR #1468 review round 3 R2: a submodule's own config (`.git/modules/<name>/config`) is in the agent-writable
    shared `.git`, but JARVES never reads it. With `submodule.recurse=true` at repository scope a checkout recursed into
    the submodule and ran a smudge filter named there. JARVES turns submodule recursion off on every call."""
    marker, g = tmp_path / "sub-filter-ran.txt", ["git", *gitops.IDENT, "-c", "protocol.file.allow=always"]
    _hook(tmp_path / "evil-bin", "cmd", marker)
    src = tmp_path / "subsrc"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    for name in ("a", "b"):                                         # the second submodule commit adds a filtered file
        (src / ".gitattributes").write_text("* filter=x\n", encoding="utf-8")
        (src / name).write_text(name, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=src, check=True)
        subprocess.run([*g, "commit", "-qm", name, "--no-verify"], cwd=src, check=True)
    repo = _repo(tmp_path)
    subprocess.run([*g, "submodule", "-q", "add", src.as_posix(), "sub"], cwd=repo, check=True, capture_output=True)
    subprocess.run([*g, "commit", "-qm", "sub at b", "--no-verify"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "HEAD~1"], cwd=repo / "sub", check=True, capture_output=True)
    subprocess.run([*g, "commit", "-qam", "sub at a", "--no-verify"], cwd=repo, check=True)
    subprocess.run(["git", "config", "-f", ".git/modules/sub/config", "filter.x.smudge",
                    (tmp_path / "evil-bin" / "cmd").as_posix()], cwd=repo, check=True)
    subprocess.run(["git", "config", "submodule.recurse", "true"], cwd=repo, check=True)
    gitops.git(repo, "checkout", "-q", "HEAD~1")                    # recursing would check out `b` in the submodule
    assert not marker.exists()
