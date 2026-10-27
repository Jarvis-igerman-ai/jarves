"""#539: the stable gate accepts a tag commit that is a docs-only descendant of the signed (tested) commit.
Offline fake git repo in a temp dir; no network, no clock."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarves import release_signoff as so

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0, reason="no git")


def _tool(name):
    spec = importlib.util.spec_from_file_location(name + "_t539", ROOT / "core" / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name + "_t539"] = m
    spec.loader.exec_module(m)
    return m


gate = _tool("release_gate")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
       "GIT_AUTHOR_DATE": "2026-10-21T12:00:00Z", "GIT_COMMITTER_DATE": "2026-10-21T12:00:00Z"}


def git(repo, *a):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=ENV, check=True)
    return r.stdout.strip()


def commit(repo, files: dict[str, str], msg="c"):
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def sign(repo, tested):
    s = so.Sheet(version="4.5.0", intro="x", rows=[so.Row("C1", "Changed", "try", "works")])
    s.set_result("C1", "pass")
    s.sign("4.5.0-beta.2", tested, "2026-10-21", owner="Owner")
    return {so.sheet_path(".", "4.5.0").as_posix(): s.render()}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    tested = commit(r, {"core/a.py": "x = 1\n", "docs/a.md": "a\n"}, "tested")
    return r, tested


def run(repo, tag_sha, verified, **kw):
    return gate.run_gate(tag="v4.5.0", sha=tag_sha, verified_sha=verified, installer_sha=verified, build_version="4.5.0.9", root=repo,
                         checklist_text=None, bug_tables=[], checks_runs=[{"head_sha": verified, "conclusion": "success"}],
                         is_ancestor=kw.get("is_ancestor", gate.git_is_ancestor(repo)),
                         changed_files=kw.get("changed_files", gate.git_changed(repo)), issues=[])


def test_equal_sha_passes(repo):
    r, tested = repo
    commit(r, sign(r, tested))                      # sheet on disk, tag points at the tested commit's content check below
    # the sheet must live in the tag tree: tag the commit that holds it, sheet names the tested parent
    tag = git(r, "rev-parse", "HEAD")
    assert run(r, tag, tested) == []                # also: signed sheet commit == the tag commit's PARENT


def test_tag_equals_tested_when_sheet_names_the_tag_commit(tmp_path):
    """Sheet signed for a commit that already holds it is impossible, but an equal sha must still pass the equality rule."""
    r = tmp_path / "e"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    sha = commit(r, {"core/a.py": "x\n"})
    s = so.Sheet(version="4.5.0", intro="x", rows=[so.Row("C1", "Changed", "try", "works")])
    s.set_result("C1", "pass")
    s.sign("4.5.0-beta.1", sha, "2026-10-21", owner="Owner")
    so.save(r, s)                                   # uncommitted file: the root only needs the sheet text here
    assert run(r, sha, sha) == []


def test_ancestor_with_docs_only_and_release_folder_passes(repo):
    r, tested = repo
    commit(r, {"docs/release/SIGN_OFF_4.5.0.md": "signed\n", "NOTES.md": "n\n", **sign(r, tested)})
    tag = commit(r, {"docs/more.md": "m\n"})
    assert run(r, tag, tested) == []


def test_ancestor_with_code_change_after_signing_refuses(repo):
    r, tested = repo
    for rel in ("core/a.py", ".github/workflows/release.yml", "packaging/x.iss", "core/tools/t.py", "core/tests/test_a.py"):
        commit(r, {rel: "changed\n", **sign(r, tested)})
        tag = git(r, "rev-parse", "HEAD")
        probs = run(r, tag, tested)
        assert any("code changed" in p and rel in p for p in probs), (rel, probs)
        git(r, "reset", "-q", "--hard", tested)


def test_move_out_of_code_and_prefix_lookalikes_refuse(repo):
    """A code file moved into docs/ (rename detection would list only docs/...) and top-level paths that merely start
    with LICENSE or .gitignore are code changes, not documentation."""
    r, tested = repo
    git(r, "mv", "core/a.py", "docs/a.py")
    commit(r, sign(r, tested))
    probs = run(r, git(r, "rev-parse", "HEAD"), tested)
    assert any("code changed" in p and "core/a.py" in p for p in probs), probs
    git(r, "reset", "-q", "--hard", tested)
    for rel in ("LICENSE_x/evil.py", ".gitignored.py"):
        commit(r, {rel: "x\n", **sign(r, tested)})
        probs = run(r, git(r, "rev-parse", "HEAD"), tested)
        assert any("code changed" in p and rel in p for p in probs), (rel, probs)
        git(r, "reset", "-q", "--hard", tested)
    commit(r, {"LICENSE": "mit\n", ".gitignore": "x\n", **sign(r, tested)})
    assert run(r, git(r, "rev-parse", "HEAD"), tested) == []


def test_non_ancestor_refuses(repo):
    r, tested = repo
    git(r, "checkout", "-q", "-b", "side")
    other = commit(r, {"docs/side.md": "s\n"})
    git(r, "checkout", "-q", "main")
    commit(r, sign(r, other))
    tag = git(r, "rev-parse", "HEAD")
    assert any("not an ancestor" in p for p in run(r, tag, other))


def test_shallow_clone_or_unknown_sha_refuses(repo, tmp_path):
    r, tested = repo
    commit(r, sign(r, tested))
    tag = commit(r, {"docs/b.md": "b\n"})
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", r.as_uri(), str(shallow)], check=True, capture_output=True, env=ENV)
    probs = run(shallow, tag, tested)
    assert probs and any("ancestor" in p or "cannot compare" in p for p in probs)
    assert run(r, tag, tested) == []               # the same call on full history passes
    unknown = "f" * 40
    assert any("sign-off" in p for p in run(r, tag, unknown))


def test_git_failure_refuses_not_crashes(repo):
    r, tested = repo
    commit(r, sign(r, tested))
    tag = git(r, "rev-parse", "HEAD")

    def boom(a, b):
        raise subprocess.CalledProcessError(128, "git")
    probs = run(r, tag, tested, changed_files=boom)
    assert any("cannot compare" in p for p in probs)


def test_installer_and_sheet_commit_must_equal_the_verified_sha(repo):
    r, tested = repo
    commit(r, sign(r, tested))
    tag = git(r, "rev-parse", "HEAD")
    other = "b" * 40
    assert any("installer commit" in p for p in gate.run_gate(
        tag="v4.5.0", sha=tag, verified_sha=tested, installer_sha=other, build_version="4.5.0.9", root=r, checklist_text=None,
        bug_tables=[], checks_runs=[{"head_sha": tested, "conclusion": "success"}],
        is_ancestor=gate.git_is_ancestor(r), changed_files=gate.git_changed(r), issues=[]))
    assert any("sheet commit" in p and "differs from the verified" in p for p in run(r, tag, other))


def test_sheet_rows_hash_catches_an_edit_after_signing(repo):
    """#508 (was documented as a gap in #539): the sign-off holds the owner and a hash of every row cell; a docs-only edit
    of a signed sheet that keeps every row passing is now detected by the gate."""
    r, tested = repo
    files = sign(r, tested)
    path = next(iter(files))
    commit(r, files)
    tag = git(r, "rev-parse", "HEAD")
    assert run(r, tag, tested) == []                 # as signed
    s0 = so.load(r, "4.5.0")
    assert s0.owner == "Owner" and len(s0.rows_hash) == 64
    edited = files[path].replace("works", "works (edited after signing)")
    assert edited != files[path]
    commit(r, {path: edited})
    tag2 = git(r, "rev-parse", "HEAD")
    assert any("rows changed after signing" in x for x in run(r, tag2, tested))
    s = so.load(r, "4.5.0")
    s.set_result("C1", "fail")
    assert s.problems()
