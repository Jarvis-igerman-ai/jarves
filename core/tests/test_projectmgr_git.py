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
        calls.append(argv)
        rc, err = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        return subprocess.CompletedProcess(argv, rc, "", err)
    return run


LOCK = "fatal: Unable to create 'C:/r/.git/index.lock': File exists."


def test_a_held_lock_file_is_retried_then_succeeds(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops, "_config_overrides", lambda cwd: [])
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(128, LOCK), (128, LOCK), (0, "")], calls))
    monkeypatch.setattr(gitops.time, "sleep", lambda s: None)
    assert gitops.git(tmp_path, "status").returncode == 0 and len(calls) == 3


def test_a_lock_that_never_clears_fails_after_the_retries(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops, "_config_overrides", lambda cwd: [])
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(128, LOCK)], calls))
    monkeypatch.setattr(gitops.time, "sleep", lambda s: None)
    with pytest.raises(gitops.GitError):
        gitops.git(tmp_path, "status")
    assert len(calls) == gitops.LOCK_RETRIES + 1


def test_other_failures_are_never_retried(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops, "_config_overrides", lambda cwd: [])
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
    monkeypatch.setattr(gitops, "_config_overrides", lambda cwd: [])
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls))
    gitops.git(tmp_path, "merge", "x")
    gitops.git(tmp_path, "merge", "x", ident=False)
    for argv in calls:
        pairs = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-c"]
        assert f"core.hooksPath={gitops.NO_HOOKS}" in pairs and "core.fsmonitor=false" in pairs
        assert argv.index("merge") > max(i for i, a in enumerate(argv) if a == "-c")


def _cmd(marker: Path, tail: str = "cat") -> str:
    """A shell command for a git config value that leaves a trace, then behaves (`cat` passes a filter's data on)."""
    return f"echo ran >> '{marker.as_posix()}'; {tail}"


def _set(repo: Path, *pairs: str) -> None:
    for key, value in zip(pairs[0::2], pairs[1::2]):        # plain git: the agent's write, not a JARVES call
        subprocess.run(["git", "config", key, value], cwd=repo, check=True)


@pytest.fixture
def owner_home(tmp_path, monkeypatch) -> Path:
    """#1390 review R7: no developer or system git config in these tests; the test writes the owner's global one."""
    home = tmp_path / "owner-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_NOSYSTEM"))
    return home


def test_shared_config_filters_never_run_on_add_commit_or_checkout(tmp_path, owner_home):
    """#1390: `filter.<x>.clean/smudge/process` in the shared config run on add and checkout for any file a
    `.gitattributes` entry maps to them, here also through `include.path`."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "ran.txt"
    (repo / ".gitattributes").write_text("*.txt filter=evil\n*.md filter=inc\n", encoding="utf-8")
    gitops.commit_all(repo, "attrs")
    (tmp_path / "inc.cfg").write_text(f'[filter "inc"]\n\tclean = "{_cmd(marker)}"\n', encoding="utf-8")
    _set(repo, "filter.evil.clean", _cmd(marker), "filter.evil.smudge", _cmd(marker),
         "filter.evil.process", _cmd(marker, "false"), "include.path", (tmp_path / "inc.cfg").as_posix())
    (repo / "g.txt").write_text("g", encoding="utf-8")
    (repo / "h.md").write_text("h", encoding="utf-8")
    assert gitops.commit_all(repo, "files")
    gitops.ensure_integration(home, str(repo), 1)                                    # a checkout (smudge)
    assert (home / "projects" / "1" / "integration" / "g.txt").read_text(encoding="utf-8") == "g"
    assert not marker.exists()


def test_a_shared_config_merge_driver_never_runs_and_the_merge_still_works(tmp_path, owner_home):
    """#1390: `merge.<x>.driver` runs on a three-way merge of a file `.gitattributes` maps to it; JARVES uses git's
    own text merge instead, so the merge result is the normal one."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    (repo / ".gitattributes").write_text("*.txt merge=evil\n", encoding="utf-8")
    (repo / "m.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    gitops.commit_all(repo, "base")
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "m.txt").write_text("A\nb\nc\nd\ne\n", encoding="utf-8")
    gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "m.txt").write_text("a\nb\nc\nd\nE\n", encoding="utf-8")
    gitops.commit_all(repo, "main")
    _set(repo, "merge.evil.driver", _cmd(marker, "false"))
    assert gitops.merge_in(repo, "side") == []
    assert (repo / "m.txt").read_text(encoding="utf-8") == "A\nb\nc\nd\nE\n" and not marker.exists()


def test_shared_config_external_diff_and_textconv_never_run(tmp_path, owner_home):
    """#1390: `diff.external`, `diff.<x>.command` and `diff.<x>.textconv` run on diff/show/log -p/blame; JARVES's
    own calls still produce the plain output. A leading global option is refused (the flags need the subcommand)."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    (repo / ".gitattributes").write_text("*.txt diff=evil\n", encoding="utf-8")
    gitops.commit_all(repo, "attrs")
    (repo / "f.txt").write_text("y\n", encoding="utf-8")
    gitops.commit_all(repo, "change")
    _set(repo, "diff.external", _cmd(marker, "false"), "diff.evil.command", _cmd(marker, "false"),
         "diff.evil.textconv", _cmd(marker))
    assert "+y" in gitops.git(repo, "diff", "HEAD~1", "HEAD").stdout
    assert "+y" in gitops.git(repo, "show", "HEAD").stdout and "+y" in gitops.git(repo, "log", "-p", "-1").stdout
    assert gitops.git(repo, "blame", "--porcelain", "f.txt").stdout.rstrip().endswith("\ty")
    with pytest.raises(gitops.GitError, match="subcommand first"):
        gitops.git(repo, "-C", str(repo), "diff", "HEAD~1", check=False)
    assert not marker.exists()


def test_shared_config_push_targets_and_commands_never_run(tmp_path, owner_home):
    """#1390 (review R1, R3): the shared config can point a push at a local repository an agent made, whose
    receive-pack runs ITS hooks (the -c overrides do not reach it): through `remote.<x>.url` or a `pushInsteadOf`
    rewrite. It can also name `core.sshCommand`, `remote.<x>.receivepack`, `core.gitProxy`, an `ext::` or a
    `<helper>::` remote. Only https and ssh run; the owner's global sshCommand (`exit 1`, no real ssh) is used."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    (owner_home / ".gitconfig").write_text('[core]\n\tsshCommand = "exit 1"\n', encoding="utf-8")
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _hook(bare / "hooks", "pre-receive", marker)
    _hook(tmp_path / "bin", "proxy", marker)                    # git starts a proxy without a shell: a script
    _set(repo, "remote.origin.url", bare.as_posix(), "remote.origin.receivepack", _cmd(marker, "false"),
         f"url.{bare.as_posix()}.pushInsteadOf", "https://github.com/o/r",
         "remote.ssh.url", "ssh://jarves@127.0.0.1:1/r", "core.sshCommand", _cmd(marker, "false"),
         "remote.gp.url", "git://127.0.0.1:1/r", "core.gitProxy", (tmp_path / "bin" / "proxy").as_posix(),
         "remote.ext.url", f"ext::sh -c {_cmd(marker, 'false')!r}", "protocol.ext.allow", "always",
         "protocol.file.allow", "always", "remote.v.url", "x", "remote.v.vcs", "jarvestest")
    for target in ("origin", "https://github.com/o/r", "ssh", "gp", "ext", "v"):
        p = gitops.git(repo, "push", "--no-verify", target, "main:main", check=False)
        assert p.returncode != 0, target
    assert "not allowed" in gitops.git(repo, "push", "origin", "main:main", check=False).stderr
    assert not marker.exists()
    assert subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "--verify", "-q", "main"]).returncode != 0


def test_fetch_pull_and_ls_remote_name_gits_own_upload_pack(monkeypatch, tmp_path):
    """#1390 review R2: git takes the first `remote.<x>.uploadpack`, so these name git's own program."""
    calls: list = []
    monkeypatch.setattr(gitops, "_config_overrides", lambda cwd: [])
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls))
    for sub in ("fetch", "pull", "ls-remote"):
        gitops.git(tmp_path, sub, "origin")
    assert all(a[a.index(s) + 1] == "--upload-pack=git-upload-pack"
               for a, s in zip(calls, ("fetch", "pull", "ls-remote")))


def test_a_repository_only_required_filter_stops_the_call(tmp_path, owner_home):
    """#1390 review R4: switching off a filter the repository marks required (`git lfs install --local`) would
    commit unfiltered content into the owner's branch; JARVES stops instead and names the fix."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    _set(repo, "filter.lfs.clean", _cmd(marker), "filter.lfs.required", "true")
    with pytest.raises(gitops.GitError, match="global git config"):
        gitops.git(repo, "status", check=False)
    _set(repo, "filter.lfs.required", "false")                 # a later false wins, as in git
    assert gitops.git(repo, "status").returncode == 0 and not marker.exists()


def test_a_shared_credential_helper_never_runs_beside_the_owners(tmp_path, owner_home):
    """#1390 review R1: git runs every credential helper from every scope; restating the owner's helper would only
    add it again. The repository's helper must not run, the owner's must."""
    repo, owner, evil = _repo(tmp_path), tmp_path / "owner.txt", tmp_path / "ran.txt"
    (owner_home / ".gitconfig").write_text(f'[credential]\n\thelper = "!{_cmd(owner, "true")}"\n', encoding="utf-8")
    _set(repo, "credential.helper", f"!{_cmd(evil, 'true')}",
         "credential.https://x.invalid.helper", f"!{_cmd(evil, 'true')}")
    gitops.git(repo, "credential", "fill", stdin="protocol=https\nhost=x.invalid\n\n", check=False)
    assert owner.exists() and not evil.exists()


def test_the_owners_global_command_is_kept_when_the_shared_config_names_another(tmp_path, owner_home):
    """#1390: a repository copy of an owner key (an owner's global filter, then overridden in `.git/config`)
    gets the owner's global value back, not a blank one."""
    repo, owner, evil = _repo(tmp_path), tmp_path / "owner.txt", tmp_path / "ran.txt"
    (owner_home / ".gitconfig").write_text(f'[filter "own"]\n\tclean = "{_cmd(owner)}"\n', encoding="utf-8")
    (repo / ".gitattributes").write_text("*.bin filter=own\n", encoding="utf-8")
    _set(repo, "filter.own.clean", _cmd(evil))
    (repo / "x.bin").write_text("b", encoding="utf-8")
    assert gitops.commit_all(repo, "bin")
    assert owner.exists() and not evil.exists()


def test_an_unreadable_shared_config_stops_every_git_call(tmp_path, owner_home):
    """#1390: a config git cannot read (or an include it refuses) must never read as empty output to a
    check=False caller, which would skip a review or a scope check."""
    repo = _repo(tmp_path)
    (repo / ".git" / "config").write_text("[core\n", encoding="utf-8")
    with pytest.raises(gitops.GitError, match="config unreadable"):
        gitops.git(repo, "diff", "--name-only", "HEAD", check=False)
    assert gitops.is_repo(str(repo)) is False      # its one caller (project add) then refuses with NOT_GIT


def test_a_shared_command_key_with_an_equals_sign_is_refused(tmp_path, owner_home):
    """#1390: `-c` splits at the first '=', so `filter.a=b.clean` cannot be overridden; the call stops instead."""
    repo = _repo(tmp_path)
    _set(repo, "filter.a=b.clean", "evil")
    with pytest.raises(gitops.GitError, match="refusing a shared git config key"):
        gitops.git(repo, "status", check=False)


def test_every_command_key_set_in_the_shared_config_gets_a_safe_value(tmp_path, owner_home):
    """#1390: editor, pager, askpass, credential (list cleared, then the owner's helpers), gpg (both names of the
    openpgp program, ssh default-key command), trailer and config-hook keys at repository scope are replaced: by
    the owner's global value when there is one, else git's safe default."""
    repo = _repo(tmp_path)
    (owner_home / ".gitconfig").write_text("[gpg]\n\tprogram = owner-gpg\n[credential]\n\thelper = a\n\thelper = b\n"
                                           '[credential "https://y.invalid"]\n\thelper = c\n'
                                           '[credential "https://h/?a=b"]\n\thelper = d\n', encoding="utf-8")
    keys = ("core.editor", "sequence.editor", "core.pager", "core.askPass", "credential.helper",
            "credential.https://x.invalid.helper", "gpg.openpgp.program", "gpg.ssh.program", "hook.evil.command",
            "core.sshCommand", "gpg.ssh.defaultKeyCommand", "trailer.t.cmd", "trailer.t.command")
    _set(repo, *[x for k in keys for x in (k, "evil")])
    pairs = gitops._config_overrides(repo)[1::2]
    assert pairs[:4] == ["credential.helper=", "credential.helper=a", "credential.helper=b",
                         "credential.https://y.invalid.helper=c"]           # cleared once, then the owner's, in order
    assert not any(x.endswith("helper=d") for x in pairs)      # review R5: a URL with '=' cannot be a -c key
    assert dict(x.split("=", 1) for x in pairs[4:]) == {
        "core.editor": ":", "sequence.editor": ":", "core.pager": "", "core.askpass": "",
        "gpg.openpgp.program": "owner-gpg",
        "gpg.ssh.program": "ssh-keygen", "hook.evil.command": "", "hook.evil.enabled": "false",
        "core.sshcommand": "ssh", "gpg.ssh.defaultkeycommand": "", "trailer.t.cmd": "", "trailer.t.command": ""}
