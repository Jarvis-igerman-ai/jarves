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


def test_claude_agent_may_not_write_the_shared_git_dir_of_its_worktree(tmp_path):
    # #1533: the shared `.git` (config, info/attributes, hooks) sits outside the task worktree; an agent that writes
    # it can make git run a filter/merge driver command in JARVES's own next git call
    from jarves.config import Config
    from jarves.projectmgr import runners
    repo = _repo(tmp_path)
    wt = tmp_path / "home" / "projects" / "1" / "wt" / "t1-a1"
    gitops.git(repo, "worktree", "add", "-b", "t1", str(wt))
    cfg = Config().projects
    cfg.claude_command = ["claude"]
    argv = runners.ClaudeRunner(cfg).build("p", str(wt)).argv
    denied = argv[argv.index("--disallowedTools") + 1:]
    def rule(path: Path) -> str:                               # Claude Code's absolute-path rule form
        root = "/" + path.resolve().as_posix()                 # POSIX "//x"; Windows "/C:/x" -> "//c/x"
        return "//" + root[1].lower() + root[3:] if os.name == "nt" else root
    root = rule(repo / ".git")
    assert f"Edit({root}/**)" in denied and f"Write({root}/**)" in denied
    assert gitops.common_dir(wt).resolve() == (repo / ".git").resolve()
    # review R2: the worktree's own `.git` pointer file, repointed at a planted gitdir, would bypass the rule above
    ptr = rule(wt / ".git")
    assert all(f"{t}({ptr}{s})" in denied for t in ("Edit", "Write") for s in ("", "/**"))
    # a folder that is not a repository gets no extra rule and no error
    empty = tmp_path / "empty"
    empty.mkdir()
    assert gitops.common_dir(empty) is None
    above = set(tmp_path.resolve().parents) | set(tmp_path.parents)  # hermetic: ignore a checkout holding tmp_path
    real_has = runners._has_git_entry
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runners, "_has_git_entry", lambda d: d not in above and real_has(d))
        assert runners._shared_git_deny(str(empty)) == []
    # a plain checkout: `.git` is the shared dir itself, so its four rules appear exactly once
    plain = runners.ClaudeRunner(cfg).build("p", str(repo)).argv
    own = rule(repo / ".git")
    assert all(plain.count(f"{t}({own}{s})") == 1 for t in ("Edit", "Write") for s in ("", "/**"))


@pytest.mark.parametrize("path,rule", [("/home/u/r/.git", "//home/u/r/.git"), (r"C:\work\r\.git", "//c/work/r/.git")])
def test_claude_absolute_rule_path_form(path, rule):
    from jarves.projectmgr import runners
    assert runners.abs_rule_path(path) == rule


def test_a_worktree_whose_shared_git_dir_cannot_be_found_is_refused_not_run_open(monkeypatch, tmp_path):
    # review R3: an old git (no --path-format) or unreadable repo must not silently drop the #1533 rules
    from jarves.config import Config
    from jarves.projectmgr import runners
    (tmp_path / ".git").write_text("gitdir: /nowhere", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(129, "error: unknown option `path-format'")] * 3, calls))
    assert gitops.common_dir(tmp_path) is None and len(calls) == 1
    cfg = Config().projects
    cfg.claude_command = ["claude"]
    with pytest.raises(ValueError, match="shared git folder"):
        runners.ClaudeRunner(cfg).build("p", str(tmp_path))
    started: list = []
    monkeypatch.setattr(runners.subprocess, "Popen", lambda *a, **k: started.append(a))
    r = runners.ClaudeRunner(cfg).run("p", str(tmp_path))
    assert not r.ok and "shared git folder" in r.error and started == [] and len(calls) == 3   # one git call per build


def test_a_worktree_whose_git_entry_cannot_be_inspected_is_refused(monkeypatch, tmp_path):
    # council (gpt) blocker: an OSError while inspecting `<cwd>/.git` must refuse the run, not drop the #1533 rules
    from jarves.config import Config
    from jarves.projectmgr import runners

    real_lstat = os.lstat

    def broken(path, *a, **k):                                   # an unreadable `.git` entry
        if os.path.basename(os.fspath(path)) == ".git":
            raise PermissionError("access denied")
        return real_lstat(path, *a, **k)
    cfg = Config().projects
    cfg.claude_command = ["claude"]
    started: list = []

    def popen(*a, **k):
        started.append(a)
        raise OSError("the agent must not start")
    monkeypatch.setattr(runners.subprocess, "Popen", popen)
    monkeypatch.setattr(runners.os, "lstat", broken)
    # review R2 (restart): Python 3.14 `Path.exists` turns every OSError into False; it must not hide the error
    monkeypatch.setattr(runners.Path, "exists", lambda self, *a, **k: False)
    r = runners.ClaudeRunner(cfg).run("p", str(tmp_path))
    assert not r.ok and "cannot inspect the git folder" in r.error and started == []


def test_a_subfolder_of_a_worktree_still_gets_the_shared_git_rules(tmp_path):
    # review R1 (round 3): a cwd below the worktree top has no `.git` of its own; the rules must still be added
    from jarves.config import Config
    from jarves.projectmgr import runners
    repo = _repo(tmp_path)
    wt = tmp_path / "wt"
    gitops.git(repo, "worktree", "add", "-b", "t1", str(wt))
    sub = wt / "pkg" / "deep"
    sub.mkdir(parents=True)
    cfg = Config().projects
    cfg.claude_command = ["claude"]
    at_top = runners._shared_git_deny(str(wt))
    assert at_top and runners._shared_git_deny(str(sub)) == at_top
    assert runners.ClaudeRunner(cfg).build("p", str(sub)).argv[-len(at_top):] == at_top


def test_an_old_git_that_echoes_the_unknown_option_is_not_taken_for_a_path(monkeypatch, tmp_path):
    # git < 2.31 rev-parse prints `--path-format=absolute` back and a relative dir with exit code 0
    out = "--path-format=absolute\n.git\n"
    monkeypatch.setattr(gitops.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 0, out, ""))
    assert gitops.common_dir(tmp_path) is None


def test_the_rules_cover_both_the_given_and_the_resolved_path(monkeypatch, tmp_path):
    # review R3: the agent may write through a symlinked folder (macOS /tmp); both spellings are denied.
    # The symlink is simulated through `resolve` so the test runs on Windows too (CI forbids skips).
    from jarves.projectmgr import runners
    (tmp_path / "given").mkdir()
    repo = _repo(tmp_path / "given")
    elsewhere = tmp_path / "resolved"
    real_resolve = runners.Path.resolve

    def resolve(self, *a, **k):
        r = real_resolve(self, *a, **k)
        try:
            return elsewhere / r.relative_to(real_resolve(repo))
        except ValueError:
            return r
    monkeypatch.setattr(runners.Path, "resolve", resolve)
    denied = runners._shared_git_deny(str(repo))
    assert f"Write({runners.abs_rule_path(repo / '.git')}/**)" in denied
    assert f"Write({runners.abs_rule_path(elsewhere / '.git')}/**)" in denied


@pytest.mark.parametrize("path", [r"\\srv\share\r\.git", "relative/.git", r"C:\a[1]\.git", "/h/a*b/.git",
                                  "/h/a?/.git", "/h/p(1)/.git", "/h/{a,b}/.git"])
def test_a_path_without_a_known_rule_form_is_refused(path):
    # review R3: a UNC path became `///srv/share/...`, a rule that matches nothing; refuse it instead.
    # council R1: glob characters (`[1]` is a character class) or parentheses inside `Edit(...)` would do the same
    from jarves.projectmgr import runners
    with pytest.raises(ValueError, match="no permission-rule form"):
        runners.abs_rule_path(path)


def test_a_project_on_a_mapped_network_drive_is_refused(monkeypatch, tmp_path):
    # review R3 (restart): Windows resolves a mapped drive `Z:\` to `\\srv\share\`; that spelling has no rule form,
    # so the run is refused (documented in the changelog), never run with a deny rule that matches nothing
    from jarves.projectmgr import runners
    repo = _repo(tmp_path)
    real_resolve = runners.Path.resolve
    monkeypatch.setattr(runners.Path, "resolve", lambda self, *a, **k:
                        Path(r"\\srv\share\r\.git") if self.name == ".git" else real_resolve(self, *a, **k))
    with pytest.raises(ValueError, match="no permission-rule form"):
        runners._shared_git_deny(str(repo))


def test_a_cwd_symlink_outside_the_repo_into_a_subfolder_still_gets_the_rules(monkeypatch, tmp_path):
    # council (gpt) blocker: `/outside/link -> /repo/pkg` has no `.git` among its lexical parents, but git works
    # in /repo; the shared git dir and the worktree pointer must still be denied. Simulated through `resolve`
    # (no real symlink) so it also runs on Windows, where CI forbids skips.
    from jarves.projectmgr import runners
    repo = _repo(tmp_path)
    wt = tmp_path / "wt"
    gitops.git(repo, "worktree", "add", "-b", "t1", str(wt))
    (wt / "pkg").mkdir()
    link = tmp_path / "outside" / "link"
    link.mkdir(parents=True)
    expected = runners._shared_git_deny(str(wt / "pkg"))
    real_resolve = runners.Path.resolve

    def resolve(self, *a, **k):
        r = real_resolve(self, *a, **k)
        try:
            return real_resolve(wt / "pkg") / r.relative_to(real_resolve(link))
        except ValueError:
            return r
    monkeypatch.setattr(runners.Path, "resolve", resolve)
    monkeypatch.setattr(gitops, "common_dir", lambda p: real_resolve(repo / ".git"))   # git sees the physical cwd
    denied = runners._shared_git_deny(str(link))
    assert expected and set(expected) <= set(denied)


def test_a_folder_below_a_stray_git_entry_names_that_entry_when_refused(monkeypatch, tmp_path):
    # council R3: a non-repo project under a parent with a broken `.git` (a home kept in git) is refused with a
    # message that names the `.git` found, not only "git 2.31 or newer is needed"
    from jarves.projectmgr import runners
    (tmp_path / ".git").write_text("gitdir: /nowhere", encoding="utf-8")
    (tmp_path / "proj").mkdir()
    monkeypatch.setattr(gitops, "common_dir", lambda p: None)
    with pytest.raises(ValueError, match="shared git folder") as e:
        runners._shared_git_deny(str(tmp_path / "proj"))
    assert str(tmp_path / ".git") in str(e.value)


def test_mixed_case_spellings_of_the_shared_git_dir_and_pointer_are_denied(tmp_path):
    # council (gpt) blocker: on a case-insensitive filesystem `.GIT` or `REPO/.Git` name the same folder
    import fnmatch
    from jarves.projectmgr import runners
    repo = _repo(tmp_path)
    wt = tmp_path / "wt"
    gitops.git(repo, "worktree", "add", "-b", "t1", str(wt))
    denied = runners._shared_git_deny(str(wt))
    for d in (repo / ".git", wt / ".git"):
        spelled = runners.abs_rule_path(d.resolve()).swapcase()     # e.g. //HOME/.../.GIT
        for tool in ("Edit", "Write"):
            pats = [r[len(tool) + 1:-1] for r in denied if r.startswith(tool + "(")]
            assert any(fnmatch.fnmatchcase(spelled, p) for p in pats)
            assert any(fnmatch.fnmatchcase(spelled + "/cOnFiG", p) for p in pats)


def test_a_read_only_run_gets_no_git_check_and_is_never_refused(monkeypatch, tmp_path):
    # council nit: read-only runs have no Edit/Write tools; a git check there only adds a refusal path
    from jarves.config import Config
    from jarves.projectmgr import runners
    cfg = Config().projects
    cfg.claude_command = ["claude"]
    monkeypatch.setattr(runners, "_shared_git_deny", lambda cwd: (_ for _ in ()).throw(ValueError("refused")))
    argv = runners.ClaudeRunner(cfg).build("p", str(tmp_path), read_only=True).argv
    assert "Edit" not in argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
    with pytest.raises(ValueError, match="refused"):
        runners.ClaudeRunner(cfg).build("p", str(tmp_path))
