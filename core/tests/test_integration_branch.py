"""integration_branch.py: plan / build / close-superseded with temporary git repos and a fake gh."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import integration_branch as ib  # noqa: E402

real_run = ib._run


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class FakeGh:
    def __init__(self):
        self.prs = {}
        self.calls = []
        self.listing = []

    def __call__(self, cmd, cwd=None, check=True):
        if cmd[0] != "gh":
            return real_run(cmd, cwd, check)
        self.calls.append(cmd)
        out = ""
        if cmd[1:3] == ["pr", "view"]:
            out = json.dumps(self.prs[int(cmd[3])])
        elif cmd[1:3] == ["pr", "list"]:
            out = json.dumps([{"number": n} for n in self.listing])
        elif cmd[1:3] == ["pr", "create"]:
            out = "https://example.test/pull/900"
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@example.test")
    git(work, "config", "user.name", "t")
    git(work, "config", "commit.gpgsign", "false")
    (work / "shared.txt").write_text("base\n")
    (work / "changelog.d").mkdir()
    (work / "changelog.d" / ".keep").write_text("")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "base")
    git(work, "remote", "add", "jarves", str(remote))
    git(work, "push", "-q", "jarves", "main")
    gh = FakeGh()
    monkeypatch.setattr(ib, "_run", gh)
    return work, gh


def make_pr(work, gh, number, files, *, checks="SUCCESS", labels=(), fragment="- Added: thing"):
    """files: {path: text}; a changelog fragment is added unless fragment is None."""
    head = f"pr{number}"
    git(work, "checkout", "-q", "-b", head, "jarves/main")
    files = dict(files)
    if fragment is not None:
        files[f"changelog.d/frag-{number}.md"] = fragment + "\n"
    for path, text in files.items():
        p = work / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-qm", f"pr {number}")
    git(work, "push", "-q", "jarves", head)
    git(work, "checkout", "-q", "main")
    gh.prs[number] = {"number": number, "title": f"PR {number}", "headRefName": head, "isDraft": False, "state": "OPEN",
                      "labels": [{"name": x} for x in labels], "files": [{"path": p} for p in files],
                      "statusCheckRollup": [{"conclusion": checks}], "mergeStateStatus": "CLEAN"}


def run_main(work, *args):
    return ib.main(["--repo", str(work), *args])


def plan(work, capsys, *args):
    assert run_main(work, "plan", *args) == 0
    return json.loads(capsys.readouterr().out)


def test_plan_integration_for_three_independent(env, capsys):
    work, gh = env
    for n in (1, 2, 3):
        make_pr(work, gh, n, {f"docs/f{n}.md": "x"})
    out = plan(work, capsys, "1", "2", "3")
    assert out["recommendation"] == "integration"
    assert not out["needs_no_automerge"]
    assert [p["tier"] for p in out["prs"]] == ["L", "L", "L"]


def test_plan_individual_when_few_or_overlapping(env, capsys):
    work, gh = env
    make_pr(work, gh, 1, {"docs/a.md": "x"})
    make_pr(work, gh, 2, {"docs/b.md": "x"})
    assert plan(work, capsys, "1", "2")["recommendation"] == "individual"
    make_pr(work, gh, 3, {"shared.txt": "three\n"})
    make_pr(work, gh, 4, {"shared.txt": "four\n"})
    out = plan(work, capsys, "1", "3", "4")
    assert out["recommendation"] == "individual"
    assert out["conflict_forecast"]["file_overlap"][0]["prs"] == [3, 4]


def test_plan_tier_r_needs_no_automerge_and_auto_filters(env, capsys):
    work, gh = env
    make_pr(work, gh, 1, {"core/src/jarves/approvals_x.py": "x = 1\n"})
    make_pr(work, gh, 2, {"docs/b.md": "x"})
    make_pr(work, gh, 3, {"docs/c.md": "x"})
    make_pr(work, gh, 4, {"docs/d.md": "x"}, checks="FAILURE")
    gh.listing = [1, 2, 3, 4]
    out = plan(work, capsys, "--auto")
    assert out["recommendation"] == "integration"
    assert out["needs_no_automerge"]
    assert [p["tier"] for p in out["prs"]] == ["R", "L", "L"]
    assert [r["number"] for r in out["not_eligible"]] == [4]


def test_build_skips_conflicting_pr_and_labels_tier_r(env, capsys):
    work, gh = env
    make_pr(work, gh, 1, {"core/src/jarves/approvals_x.py": "x = 1\n"})
    make_pr(work, gh, 2, {"shared.txt": "two\n"})
    make_pr(work, gh, 3, {"shared.txt": "three\n"})
    assert run_main(work, "build", "1", "2", "3", "--open-pr", "--no-prepush") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["included"] == [1, 2]
    assert out["skipped"][0][0] == 3 and "conflict" in out["skipped"][0][1]
    assert out["no_automerge"]
    create = [c for c in gh.calls if c[1:3] == ["pr", "create"]][0]
    assert "no-automerge" in create
    body = create[create.index("--body") + 1]
    assert "Supersedes: #1 #2" in body and "#3" in body and body.endswith("https://claude.com/claude-code)")
    assert (work / "shared.txt").read_text() == "two\n"
    assert real_run(["git", "ls-remote", "--heads", "jarves", out["branch"]], work).stdout.strip()


def test_build_no_label_without_tier_r(env, capsys):
    work, gh = env
    make_pr(work, gh, 1, {"docs/a.md": "x"})
    assert run_main(work, "build", "1", "--open-pr", "--no-prepush") == 0
    create = [c for c in gh.calls if c[1:3] == ["pr", "create"]][0]
    assert "--label" not in create


@pytest.mark.parametrize("fragment", ["- Added: a\n- Fixed: b", "plain text", "- Added:"])
def test_build_fails_on_bad_changelog_fragment(env, capsys, fragment):
    work, gh = env
    make_pr(work, gh, 1, {"docs/a.md": "x"}, fragment=fragment)
    assert run_main(work, "build", "1", "--no-prepush") == 2
    assert "bad changelog fragment" in capsys.readouterr().err


def test_close_superseded_refuses_until_merged(env, capsys):
    work, gh = env
    make_pr(work, gh, 1, {"docs/a.md": "x"})
    gh.prs[900] = {"state": "OPEN"}
    assert run_main(work, "close-superseded", "900", "1") == 2
    assert "not MERGED" in capsys.readouterr().err
    assert not [c for c in gh.calls if c[1:3] in (["pr", "close"], ["pr", "comment"])]


def test_close_superseded_closes_and_deletes_only_merged_branches(env, capsys):
    work, gh = env
    make_pr(work, gh, 1, {"docs/a.md": "x"})
    make_pr(work, gh, 2, {"docs/b.md": "x"})
    git(work, "merge", "-q", "--no-ff", "-m", "integ", "jarves/pr1")
    git(work, "push", "-q", "jarves", "main")
    gh.prs[900] = {"state": "MERGED"}
    assert run_main(work, "close-superseded", "900", "1", "2") == 0
    out = capsys.readouterr().out
    assert "branch pr1 deleted" in out and "branch pr2 kept" in out
    assert [c for c in gh.calls if c[1:3] == ["pr", "comment"]][0][-1] == "Merged via #900"
    assert len([c for c in gh.calls if c[1:3] == ["pr", "close"]]) == 2
