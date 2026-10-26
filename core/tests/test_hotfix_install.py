"""#348 jarvis hotfix (installed-build hot-patch): a fake install tree and fake hooks only. Nothing here touches a
real JARVES install."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from jarves import hotfix as hf
from jarves import updates


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    inst = tmp_path / "site-packages" / "jarves"
    src = tmp_path / "src" / "jarves"
    for root in (inst, src):
        write(root, "__init__.py", "__version__='4.5.0'\n")
        write(root, "a.py", "A=1\n")
        write(root, "guides/data/x.toml", "k=1\n")
    write(inst, hf.BUILD_INFO_NAME, json.dumps({"version": "4.5.0", "commit": "abc123", "channel": "beta",
                                                "payload_sha256": {"a.py": hf.sha256(inst / "a.py")}}))
    return inst, src, tmp_path / "home"


class FakeHooks:
    def __init__(self, running=False, doctor=(0, 0), fail_start=False):
        self.running, self.doctor_rcs, self.fail_start = running, list(doctor), fail_start
        self.calls = []

    def hooks(self):
        def stop():
            self.calls.append("stop")
            self.running = False

        def start():
            self.calls.append("start")
            if self.fail_start:
                raise hf.HotfixError("HOTFIX_START", "no answer")
            self.running = True

        return hf.Hooks(lambda: self.running, stop, start, lambda: self.calls.append("backup") or "backup-1.zip",
                        lambda: self.calls.append("doctor") or self.doctor_rcs.pop(0), log=lambda s: None)


def source(src, changes=None, commit="def456"):
    return hf.Source(src, changes, commit)


def test_apply_copies_only_changed_files_and_writes_manifest(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    write(src, "new.py", "N=1\n")
    write(src, "tests/t.py", "ignored\n")
    before_other = (inst / "guides/data/x.toml").stat().st_mtime_ns
    f = FakeHooks()
    m = hf.apply(inst, source(src, ["core/src/jarves/a.py"]), home, f.hooks())
    assert (inst / "a.py").read_text() == "A=2\n" and (inst / "new.py").exists() and not (inst / "tests").exists()
    assert (inst / "guides/data/x.toml").stat().st_mtime_ns == before_other
    assert f.calls[0] == "backup" and "start" not in f.calls          # backup first; Core was not running -> not started
    man = json.loads((home / "hotfix" / m["id"] / "manifest.json").read_text())
    assert man["status"] == "applied" and man["backup"] == "backup-1.zip"
    rep = man["replaced"][0]
    assert rep["path"] == "a.py" and rep["sha256_original"] == hf.sha256(home / "hotfix" / m["id"] / "originals" / "a.py")
    assert man["added"][0]["path"] == "new.py"


def test_version_mark_keeps_numeric_version_and_updates_hashes(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    hf.apply(inst, source(src), home, FakeHooks().hooks())
    info = json.loads((inst / hf.BUILD_INFO_NAME).read_text())
    assert info["version"] == "4.5.0" and info["hotfix"]["n"] == 1 and info["hotfix"]["base_version"] == "4.5.0"
    assert info["commit"] == "def456" and info["payload_sha256"]["a.py"] == hf.sha256(inst / "a.py")
    assert updates.is_newer("4.5.1", "4.5.0+hotfix.1") and not updates.is_newer("4.5.0", "4.5.0+hotfix.1")
    write(src, "a.py", "A=3\n")
    hf.apply(inst, source(src), home, FakeHooks().hooks())
    assert json.loads((inst / hf.BUILD_INFO_NAME).read_text())["hotfix"]["n"] == 2


def test_build_info_reader_and_version_line_show_hotfix(tree, monkeypatch):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    hf.apply(inst, source(src), home, FakeHooks().hooks())
    monkeypatch.setattr(updates, "BUILD_INFO", inst / hf.BUILD_INFO_NAME)
    assert updates.build_info()["hotfix"]["n"] == 1
    assert "+hotfix.1" in updates.version_line()


def test_revert_restores_originals_and_removes_added(tree):
    inst, src, home = tree
    orig = (inst / hf.BUILD_INFO_NAME).read_text()
    write(src, "a.py", "A=2\n")
    write(src, "new.py", "N=1\n")
    hf.apply(inst, source(src), home, FakeHooks().hooks())
    r = hf.revert(inst, home, FakeHooks().hooks())
    assert r["status"] == "reverted"
    assert (inst / "a.py").read_text() == "A=1\n" and not (inst / "new.py").exists()
    assert (inst / hf.BUILD_INFO_NAME).read_text() == orig
    with pytest.raises(hf.Refused):                      # already reverted
        hf.revert(inst, home, FakeHooks().hooks())


def test_revert_refused_after_a_normal_update_replaced_the_hotfix(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    hf.apply(inst, source(src), home, FakeHooks().hooks())
    write(inst, hf.BUILD_INFO_NAME, json.dumps({"version": "4.6.0"}))          # a full install wrote a new build info
    with pytest.raises(hf.Refused) as e:
        hf.revert(inst, home, FakeHooks().hooks())
    assert e.value.code == "HOTFIX_REPLACED"


@pytest.mark.parametrize("rel,text", [("tool.exe", "x"), ("lib.pyd", "x"), ("blob.dat", "x"), ("installer_state.py", "x=1")])
def test_binaries_unknown_types_and_installer_code_are_skipped_and_listed(tree, rel, text):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    write(src, rel, text)
    m = hf.apply(inst, source(src), home, FakeHooks().hooks())
    assert (inst / "a.py").read_text() == "A=2\n" and not (inst / rel).exists()
    assert m["pending_installer"] == ["core/src/jarves/" + rel]


@pytest.mark.parametrize("path", ["packaging/build_windows.ps1", "packaging/windows/jarves.iss", "godot/project.godot",
                                  ".github/workflows/ci.yml"])
def test_build_only_repo_changes_are_listed_not_refused(tree, path):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    m = hf.apply(inst, source(src, ["core/src/jarves/a.py", path]), home, FakeHooks().hooks())
    assert (inst / "a.py").read_text() == "A=2\n" and m["pending_installer"] == [path]


@pytest.mark.parametrize("path,code", [("core/uv.lock", "HOTFIX_DEP_UNVERIFIED"), ("core/requirements.txt", "HOTFIX_NEEDS_SLICE2"),
                                       ("packaging/bridge_pin.json", "HOTFIX_BRIDGE_UNVERIFIED"),
                                       ("bridge/whatsapp/main.go", "HOTFIX_BRIDGE_UNVERIFIED")])
def test_dependency_and_bridge_changes_are_verified_or_refused(tree, path, code):
    """Slice 2 (#460): a lock/bridge change that cannot be verified (no readable lock/pin in this fake source) is refused
    with its own code; other dependency files keep the slice-2 refusal. Nothing is touched."""
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    with pytest.raises(hf.Refused) as e:
        hf.apply(inst, source(src, ["core/src/jarves/a.py", path]), home, FakeHooks().hooks())
    assert e.value.code == code
    assert (inst / "a.py").read_text() == "A=1\n" and not (home / "hotfix").exists()


def test_docs_and_tests_changes_in_the_repo_do_not_block(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    hf.apply(inst, source(src, ["core/src/jarves/a.py", "docs/NOW.md", "core/tests/test_x.py"]), home, FakeHooks().hooks())
    assert (inst / "a.py").read_text() == "A=2\n"


def test_nothing_to_do_and_dry_run(tree):
    inst, src, home = tree
    assert hf.apply(inst, source(src), home, FakeHooks().hooks())["status"] == "nothing"
    write(src, "a.py", "A=2\n")
    r = hf.apply(inst, source(src), home, FakeHooks().hooks(), dry_run=True)
    assert r["status"] == "dry-run" and r["files"] == ["a.py"] and (inst / "a.py").read_text() == "A=1\n"
    assert not (home / "hotfix").exists()


def test_restart_only_if_it_was_running(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    off = FakeHooks(running=False)
    hf.apply(inst, source(src), home, off.hooks())
    assert "stop" not in off.calls and "start" not in off.calls
    write(src, "a.py", "A=3\n")
    on = FakeHooks(running=True)
    m = hf.apply(inst, source(src), home, on.hooks())
    assert on.calls.index("stop") < on.calls.index("start") and on.running and m["core_was_running"]
    write(src, "a.py", "A=4\n")
    quiet = FakeHooks(running=True)
    hf.apply(inst, source(src), home, quiet.hooks(), restart=False)
    assert "stop" not in quiet.calls


def test_fail_closed_when_doctor_gets_worse_and_core_is_restarted_on_old_code(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    write(src, "new.py", "N=1\n")
    f = FakeHooks(running=True, doctor=(0, 1))
    with pytest.raises(hf.HotfixError) as e:
        hf.apply(inst, source(src), home, f.hooks())
    assert e.value.code == "HOTFIX_REVERTED"
    assert (inst / "a.py").read_text() == "A=1\n" and not (inst / "new.py").exists()
    assert f.calls.count("start") == 2 and f.running            # restarted after the apply and again after the revert
    man = json.loads(next((home / "hotfix").iterdir()).joinpath("manifest.json").read_text())
    assert man["status"] == "reverted"
    assert "hotfix" not in json.loads((inst / hf.BUILD_INFO_NAME).read_text())


def test_doctor_already_failing_before_is_not_blamed_on_the_hotfix(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    hf.apply(inst, source(src), home, FakeHooks(doctor=(1, 1)).hooks())
    assert (inst / "a.py").read_text() == "A=2\n"


def test_fail_closed_on_restart_error_and_on_copy_error(tree, monkeypatch):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    f = FakeHooks(running=True, fail_start=True)
    with pytest.raises(hf.HotfixError):                            # the revert's own restart also fails -> loud; files are back
        hf.apply(inst, source(src), home, f.hooks())
    assert (inst / "a.py").read_text() == "A=1\n"

    write(src, "b.py", "B=1\n")
    real = hf._atomic_copy
    n = {"i": 0}

    def flaky(s, d):
        n["i"] += 1
        if n["i"] == 2:
            raise OSError("disk full")
        real(s, d)

    monkeypatch.setattr(hf, "_atomic_copy", flaky)
    with pytest.raises(hf.HotfixError) as e:
        hf.apply(inst, source(src), home, FakeHooks().hooks())
    monkeypatch.setattr(hf, "_atomic_copy", real)
    assert e.value.code in ("HOTFIX_REVERTED", "HOTFIX_REVERT_FAILED")
    assert (inst / "a.py").read_text() == "A=1\n" and not (inst / "b.py").exists()


def test_revert_refuses_a_tampered_original(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    m = hf.apply(inst, source(src), home, FakeHooks().hooks())
    (home / "hotfix" / m["id"] / "originals" / "a.py").write_text("EVIL\n")
    with pytest.raises(hf.HotfixError) as e:
        hf.revert(inst, home, FakeHooks().hooks())
    assert e.value.code == "HOTFIX_ORIGINAL_BAD" and (inst / "a.py").read_text() == "A=2\n"


def test_resolve_source_folder_modes(tmp_path):
    pkg = tmp_path / "pkg"
    write(pkg, "__init__.py", "")
    s = hf.resolve_source(str(pkg), None)
    assert s.pkg == pkg and s.repo_changes is None
    repo = tmp_path / "repo"
    write(repo, "core/src/jarves/__init__.py", "")
    s = hf.resolve_source(str(repo), None)                       # a checkout without .git: package files only
    assert s.pkg == repo / "core" / "src" / "jarves"
    with pytest.raises(hf.Refused):
        hf.resolve_source(str(tmp_path), None)


def test_locate_installed_refuses_a_dev_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "is_installed_build", lambda: False)
    with pytest.raises(hf.Refused) as e:
        hf.locate_installed(None)
    assert e.value.code == "HOTFIX_NOT_INSTALLED"
    assert hf.locate_installed(str(tmp_path)) == tmp_path


def test_git_ref_on_a_throwaway_repo(tmp_path):
    repo = tmp_path / "r"
    write(repo, "core/src/jarves/__init__.py", "")
    write(repo, "core/src/jarves/a.py", "A=1\n")
    write(repo, "packaging/p.ps1", "1\n")

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                              capture_output=True, text=True, check=True).stdout.strip()

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    write(repo, "core/src/jarves/a.py", "A=2\n")
    git("commit", "-qam", "fix")
    s = hf.resolve_source("HEAD", None, repo=repo, base=base)
    try:
        assert s.repo_changes == ["core/src/jarves/a.py"] and (s.pkg / "a.py").read_text() == "A=2\n"
    finally:
        shutil.rmtree(s.cleanup, ignore_errors=True)
    write(repo, "packaging/p.ps1", "2\n")
    git("commit", "-qam", "pkg")
    s2 = hf.resolve_source("HEAD", None, repo=repo, base=base)
    try:
        assert "packaging/p.ps1" in s2.repo_changes
        assert hf.classify(hf.Plan(replace=["a.py"]), s2.repo_changes)[1] == ["packaging/p.ps1"]
    finally:
        shutil.rmtree(s2.cleanup, ignore_errors=True)
    with pytest.raises(hf.Refused) as e:                          # no base commit known: fail closed
        hf.resolve_source("HEAD", None, repo=repo)
    assert e.value.code == "HOTFIX_NO_BASE"


def test_symlink_in_source_is_ignored_and_case_clash_refused(tree, tmp_path):
    inst, src, home = tree
    secret = tmp_path / "secret.py"
    secret.write_text("S=1\n", encoding="utf-8")
    try:
        (src / "link.py").symlink_to(secret)
    except (OSError, NotImplementedError):
        link_ok = False
    else:
        link_ok = True
    plan = hf.build_plan(src, inst)
    if link_ok:
        assert "link.py" not in plan.files
    (src / "guides/data/x.toml").unlink()
    (src / "guides/data/X.toml").write_text("k=2", encoding="utf-8")  # case-only difference
    with pytest.raises(hf.Refused):
        hf.build_plan(src, inst)


# ---------------------------------------------------------------------- #459 --python-only
def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def po(tmp_path):
    """A throwaway git repo (base commit) plus an installed tree identical to the base."""
    repo = tmp_path / "repo"
    pkg = repo / "core/src/jarves"
    write(pkg, "__init__.py", "__version__='4.5.0'\n")
    write(pkg, "a.py", "A=1\n")
    write(repo, "packaging/windows/jarves.iss", "1\n")
    write(repo, "core/pyproject.toml", "[project]\ndependencies=[]\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    inst = tmp_path / "site-packages" / "jarves"
    write(inst, "__init__.py", "__version__='4.5.0'\n")
    write(inst, "a.py", "A=1\n")
    write(inst, hf.BUILD_INFO_NAME, json.dumps({"version": "4.5.0", "commit": base}))

    def commit(files):
        for rel, text in files.items():
            write(repo, rel, text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "change")
        return hf.resolve_source("HEAD", None, repo=repo, base=base)

    return inst, tmp_path / "home", commit, repo, base


def test_git_skips_packaging_lists_it_and_applies_python(po):
    inst, home, commit, repo, base = po
    s = commit({"core/src/jarves/a.py": "A=2\n", "packaging/windows/jarves.iss": "2\n",
                "packaging/smoke_windows.ps1": "x\n", "docs/NOW.md": "d\n"})
    try:
        r = hf.apply(inst, s, home, FakeHooks().hooks(), dry_run=True)
        assert r["status"] == "dry-run" and r["files"] == ["a.py"]
        assert r["pending_installer"] == ["packaging/smoke_windows.ps1", "packaging/windows/jarves.iss"]
        assert (inst / "a.py").read_text() == "A=1\n" and not (home / "hotfix").exists()
        f = FakeHooks()
        m = hf.apply(inst, s, home, f.hooks())
        assert (inst / "a.py").read_text() == "A=2\n" and f.calls[0] == "backup"
        man = json.loads((home / "hotfix" / m["id"] / "manifest.json").read_text())
        assert "packaging/windows/jarves.iss" in man["pending_installer"]
        st = hf.status(inst, home)                                          # status shows the pending installer files
        assert st["pending_installer"] == man["pending_installer"]
        hf.revert(inst, home, FakeHooks().hooks())
        assert (inst / "a.py").read_text() == "A=1\n" and hf.status(inst, home)["pending_installer"] == []
    finally:
        shutil.rmtree(s.cleanup, ignore_errors=True)


def test_git_skips_binary_and_installer_code_inside_the_package(po):
    inst, home, commit, repo, base = po
    s = commit({"core/src/jarves/a.py": "A=2\n", "core/src/jarves/installer_x.py": "x=1\n", "core/src/jarves/t.pyd": "b"})
    try:
        r = hf.apply(inst, s, home, FakeHooks().hooks())
        assert (inst / "a.py").read_text() == "A=2\n" and not (inst / "installer_x.py").exists()
        assert r["pending_installer"] == ["core/src/jarves/installer_x.py", "core/src/jarves/t.pyd"]
    finally:
        shutil.rmtree(s.cleanup, ignore_errors=True)


@pytest.mark.parametrize("path,code", [("core/uv.lock", "HOTFIX_DEP_UNVERIFIED"), ("core/requirements.txt", "HOTFIX_NEEDS_SLICE2"),
                                       ("packaging/bridge_pin.json", "HOTFIX_BRIDGE_UNVERIFIED"),
                                       ("bridge/whatsapp/bridge.exe", "HOTFIX_BRIDGE_UNVERIFIED")])
def test_git_source_refuses_unverifiable_dependency_and_bridge_changes(po, path, code):
    inst, home, commit, repo, base = po
    s = commit({"core/src/jarves/a.py": "A=2\n", path: "changed\n"})
    try:
        with pytest.raises(hf.Refused) as e:
            hf.apply(inst, s, home, FakeHooks().hooks())
        assert e.value.code == code
        assert (inst / "a.py").read_text() == "A=1\n" and not (home / "hotfix").exists()
    finally:
        shutil.rmtree(s.cleanup, ignore_errors=True)


def test_git_pyproject_only_change_is_listed_and_python_applies(po):
    inst, home, commit, repo, base = po
    s = commit({"core/src/jarves/a.py": "A=2\n", "core/pyproject.toml": "[project]\ndependencies=[]\n# x\n"})
    try:
        r = hf.apply(inst, s, home, FakeHooks().hooks())
        assert (inst / "a.py").read_text() == "A=2\n" and "core/pyproject.toml" in r["pending_installer"]
    finally:
        shutil.rmtree(s.cleanup, ignore_errors=True)


def test_git_refuses_a_new_third_party_import_but_allows_installed_and_stdlib(po):
    inst, home, commit, repo, base = po
    s = commit({"core/src/jarves/a.py": "import json\nfrom jarves import b\nimport okmod.sub\n",
                "core/src/jarves/b.py": "def f():\n    import zzz_not_installed_mod\n"})
    try:
        (inst.parent / "okmod").mkdir()
        with pytest.raises(hf.Refused) as e:
            hf.apply(inst, s, home, FakeHooks().hooks())
        assert e.value.code == "HOTFIX_NEW_DEPENDENCY" and "zzz_not_installed_mod" in e.value.message
        assert "okmod" not in e.value.message and (inst / "a.py").read_text() == "A=1\n"
        (inst.parent / "zzz_not_installed_mod.py").write_text("", encoding="utf-8")       # now it is installed
        hf.apply(inst, s, home, FakeHooks().hooks())
        assert "okmod" in (inst / "a.py").read_text()
    finally:
        shutil.rmtree(s.cleanup, ignore_errors=True)


def test_git_refuses_when_base_is_not_an_ancestor_or_source_is_not_git(po, tmp_path):
    inst, home, commit, repo, base = po
    _git(repo, "checkout", "-q", "--orphan", "other")
    write(repo, "core/src/jarves/a.py", "A=2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated")
    s = hf.resolve_source("HEAD", None, repo=repo, base=base)
    try:
        with pytest.raises(hf.Refused) as e:
            hf.apply(inst, s, home, FakeHooks().hooks())
        assert e.value.code == "HOTFIX_BASE_NOT_ANCESTOR" and (inst / "a.py").read_text() == "A=1\n"
    finally:
        shutil.rmtree(s.cleanup, ignore_errors=True)

