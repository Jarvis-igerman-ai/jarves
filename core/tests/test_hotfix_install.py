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
def test_refuses_binaries_unknown_types_and_installer_code(tree, rel, text):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    write(src, rel, text)
    with pytest.raises(hf.Refused) as e:
        hf.apply(inst, source(src), home, FakeHooks().hooks())
    assert e.value.code == "HOTFIX_NEEDS_INSTALLER" and "full installer" in e.value.action
    assert (inst / "a.py").read_text() == "A=1\n" and not (home / "hotfix").exists()      # nothing touched, no backup


@pytest.mark.parametrize("path", ["packaging/build_windows.ps1", "packaging/bridge_pin.json", "core/pyproject.toml",
                                  "core/uv.lock", "bridge/whatsapp/main.go", "godot/project.godot"])
def test_refuses_when_the_repo_change_touches_packaging_bridge_or_dependencies(tree, path):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    with pytest.raises(hf.Refused) as e:
        hf.apply(inst, source(src, ["core/src/jarves/a.py", path]), home, FakeHooks().hooks())
    assert e.value.code == "HOTFIX_NEEDS_INSTALLER"
    assert (inst / "a.py").read_text() == "A=1\n"


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
        with pytest.raises(hf.Refused):
            hf.check_policy(hf.Plan(replace=["a.py"]), s2.repo_changes)
    finally:
        shutil.rmtree(s2.cleanup, ignore_errors=True)
    with pytest.raises(hf.Refused) as e:                          # no base commit known: fail closed
        hf.resolve_source("HEAD", None, repo=repo)
    assert e.value.code == "HOTFIX_NO_BASE"
