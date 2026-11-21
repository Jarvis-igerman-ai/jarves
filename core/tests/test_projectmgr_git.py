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


def _fake_run(outcomes, calls, config=""):
    def run(argv, **kw):
        if "config" in argv and "--show-scope" in argv:               # the #1407 config read: not counted
            return subprocess.CompletedProcess(argv, 0, config, "")
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


def test_commands_named_in_the_repository_config_never_run(tmp_path, monkeypatch):
    """#1407 item 3: filter and merge drivers (picked by a `.gitattributes` in the worktree) and signing programs set in
    the shared `.git/config` run no code; the owner's own global signing program is still the one used."""
    owner_home, marker, owner_marker = tmp_path / "owner", tmp_path / "ran.txt", tmp_path / "owner-gpg.txt"
    _hook(tmp_path / "bin", "evil", marker)
    _hook(tmp_path / "bin", "owner-gpg", owner_marker)
    owner_home.mkdir()
    (owner_home / ".gitconfig").write_text(f"[gpg]\n\tprogram = {(tmp_path / 'bin' / 'owner-gpg').as_posix()}\n",
                                           encoding="utf-8")
    monkeypatch.setenv("HOME", str(owner_home))
    monkeypatch.setenv("USERPROFILE", str(owner_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    repo, evil = _repo(tmp_path), (tmp_path / "bin" / "evil").as_posix()
    for k, v in (("user.name", "Owner"), ("user.email", "o@example.invalid"), ("merge.x.driver", evil),
                 ("gpg.openpgp.program", evil)):
        subprocess.run(["git", "config", k, v], cwd=repo, check=True)
    (repo / ".gitattributes").write_text("*.txt merge=x\n", encoding="utf-8")
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "f.txt").write_text("side", encoding="utf-8")
    assert gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("main", encoding="utf-8")
    assert gitops.commit_all(repo, "main")
    assert gitops.merge_in(repo, "side") == ["f.txt"]                        # the driver never decides: a conflict
    assert not marker.exists()
    subprocess.run(["git", "config", "commit.gpgsign", "true"], cwd=repo, check=True)
    (repo / "g.md").write_text("g", encoding="utf-8")
    gitops.git(repo, "checkout", "-qb", "side2")
    gitops.commit_all(repo, "g")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "h.md").write_text("h", encoding="utf-8")
    gitops.commit_all(repo, "h")
    gitops.merge_in(repo, "side2", ident=False)                              # the ship path signs as the owner
    assert owner_marker.exists() and not marker.exists()


def test_config_hooks_are_neutralised_and_an_unreadable_config_fails_closed(monkeypatch, tmp_path):
    """#1407 item 3: config-defined hooks (`hook.<name>.command`, newer git than CI's 2.43) get a no-op command, diff
    commands never run an external diff or textconv, and a config git cannot read stops the command."""
    calls: list = []
    conf = ("local\0hook.x.command\nevil\0local\0merge.y.driver\nevil\0global\0filter.lfs.clean\ngit-lfs\0"
            "global\0filter.lfs.smudge\ngit-lfs s\0global\0filter.lfs.required\ntrue\0local\0filter.lfs.smudge\nevil\0"
            "local\0filter.lfs.required\nfalse\0local\0filter.enc.process\nevil\0global\0hook.own.command\nmine\0"
            "local\0hook.own.event\npre-commit\0")
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls, conf))
    gitops.git(tmp_path, "diff", "--shortstat", "a...b")
    pairs = [calls[0][i + 1] for i, a in enumerate(calls[0][:-1]) if a == "-c"]
    assert "hook.x.command=exit 0" in pairs and "merge.y.driver=exit 1" in pairs
    assert not any(p.startswith("filter.lfs.clean") for p in pairs)            # the owner's own filter is kept
    assert "filter.lfs.smudge=git-lfs s" in pairs                              # a repository copy: the owner's
    assert "filter.lfs.required=true" in pairs                                 # review R3: owner's flag kept
    assert "filter.enc.process=exit 1" in pairs and "filter.enc.required=true" in pairs   # round 3 R1: fails closed
    assert "hook.own.command=exit 0" in pairs           # round 3 R3: a repository `.event` never runs the owner's hook
    assert calls[0][calls[0].index("diff") + 1:][:2] == ["--no-ext-diff", "--no-textconv"]
    calls.clear()
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls, "global\0user.signingkey\nOWNER\0"
                                                            "local\0user.signingkey\nAGENT\0local\0gpg.format\nssh\0"))
    gitops.git(tmp_path, "merge", "x", ident=False)                # round 2 R4: format and key are the owner's
    pairs = [calls[0][i + 1] for i, a in enumerate(calls[0][:-1]) if a == "-c"]
    assert "user.signingkey=OWNER" in pairs and "gpg.format=openpgp" in pairs
    monkeypatch.undo()
    repo = _repo(tmp_path)
    with open(repo / ".git" / "config", "a", encoding="utf-8") as fh:
        fh.write("[broken\n")
    with pytest.raises(gitops.GitError):
        gitops.git(repo, "status")
    assert not gitops.is_repo(str(repo))


def test_a_git_without_show_scope_names_the_minimum_version(monkeypatch, tmp_path):
    """#1407 review R4: git older than 2.26 rejects `--show-scope` (exit 129); the error names the version needed."""
    monkeypatch.setattr(gitops.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 129, "", "usage"))
    with pytest.raises(gitops.GitError, match="git 2.26 or newer"):
        gitops.git(tmp_path, "status")


def test_a_filter_set_only_in_the_repository_config_stops_the_commit(tmp_path):
    """#1407 review round 3 R1: a repository-only filter (git-crypt writes `.git/config` only) must not be skipped
    silently: the file would be committed unconverted (plaintext). The command stops, naming the cause."""
    repo = _repo(tmp_path)
    for k, v in (("filter.x.clean", "tr a-z A-Z"), ("filter.x.smudge", "cat"), ("filter.x.required", "false")):
        subprocess.run(["git", "config", k, v], cwd=repo, check=True)
    (repo / ".gitattributes").write_text("*.txt filter=x\n", encoding="utf-8")
    before = gitops.commit_of(repo, "HEAD")
    (repo / "s.txt").write_text("secret\n", encoding="utf-8")
    with pytest.raises(gitops.GitError, match="global config"):
        gitops.commit_all(repo, "f")
    assert gitops.commit_of(repo, "HEAD") == before
    staged = subprocess.run(["git", "ls-files", "-s", "s.txt"], cwd=repo, capture_output=True, text=True).stdout
    assert not staged                                                        # never stored raw


def test_signing_without_an_owner_key_uses_the_committer_email(monkeypatch, tmp_path):
    """#1407 review round 3 R2: a repository signing key with no owner key gave `user.signingkey=` (empty), which git
    uses as is; git's own default is the committer identity."""
    calls: list = []
    conf = "global\0user.email\no@example.invalid\0local\0user.signingkey\nREPO\0"
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls, conf))
    gitops.git(tmp_path, "merge", "x", ident=False)
    pairs = [calls[0][i + 1] for i, a in enumerate(calls[0][:-1]) if a == "-c"]
    assert "user.signingkey=o@example.invalid" in pairs
    assert "signing settings" in gitops._hint("error: gpg failed to sign the data")


def test_fallback_signing_key_never_comes_from_the_repository_email(monkeypatch, tmp_path):
    """#1407 restart R4: the fallback signing key is the owner's email, never a `user.email` from `.git/config`."""
    calls: list = []
    conf = "global\0user.email\no@example.invalid\0local\0user.email\nagent@example.invalid\0" \
           "local\0user.signingkey\nREPO\0"
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls, conf))
    gitops.git(tmp_path, "merge", "x", ident=False)
    pairs = [calls[0][i + 1] for i, a in enumerate(calls[0][:-1]) if a == "-c"]
    assert "user.signingkey=o@example.invalid" in pairs, pairs


def test_read_only_ref_lookups_skip_the_config_read(monkeypatch, tmp_path):
    """#1407 restart R5: rev-parse/merge-base run no config-named command, so the hot paths spawn one process."""
    argvs: list = []

    def run(argv, **kw):
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(gitops.subprocess, "run", run)
    gitops.git(tmp_path, "rev-parse", "HEAD")
    gitops.git(tmp_path, "merge-base", "--is-ancestor", "a", "b")
    assert len(argvs) == 2 and not any("--show-scope" in a for a in argvs)
    gitops.git(tmp_path, "status")
    assert len(argvs) == 4 and "--show-scope" in argvs[2]
