"""#467 hotfix recovery entry: standalone (standard library only) restore from the hotfix backups. Fake install tree,
fake hooks, frozen clock; never touches a real install."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from jarves import hotfix as hf
from jarves import hotfix_recover as rec
from test_hotfix_install import FakeHooks, source, tree, write  # noqa: F401  (tree is a fixture)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    ids = iter(["20261026-100000", "20261026-110000", "20261026-120000"])
    monkeypatch.setattr(hf, "_now_id", lambda: next(ids))


def applied(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    write(src, "new.py", "N=1\n")
    m = hf.apply(inst, source(src), home, FakeHooks().hooks())
    assert (inst / "a.py").read_text() == "A=2\n"
    return inst, home, m["id"]


def test_module_imports_only_the_standard_library():
    tree_ = ast.parse(Path(rec.__file__).read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree_):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            assert n.level == 0, "no relative (jarves) imports"
            mods.add((n.module or "").split(".")[0])
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


def test_recover_latest_restores_like_revert(tree, capsys):
    inst, home, hid = applied(tree)
    orig_info = json.loads((home / "hotfix" / hid / "originals" / hf.BUILD_INFO_NAME).read_text())
    assert rec.main(["--latest", "--home", str(home), "--install", str(inst)]) == 0
    out = capsys.readouterr().out
    assert hid in out and "restored" in out
    assert (inst / "a.py").read_text() == "A=1\n" and not (inst / "new.py").exists()
    assert json.loads((inst / hf.BUILD_INFO_NAME).read_text()) == orig_info
    assert json.loads((home / "hotfix" / hid / "manifest.json").read_text())["status"] == "reverted"
    assert rec.main(["--home", str(home), "--install", str(inst)]) == 2          # already reverted: refused, not an error
    assert "RECOVER_NOT_NEEDED" in capsys.readouterr().err


def test_recover_works_when_the_package_is_half_written(tree):
    inst, home, hid = applied(tree)
    (inst / "a.py").write_text("garbage {{{")                                    # interrupted copy
    (inst / hf.BUILD_INFO_NAME).unlink()
    r = rec.recover(inst, home)
    assert r["status"] == "restored" and (inst / "a.py").read_text() == "A=1\n"


def test_interrupted_apply_is_restorable(tree):
    inst, home, hid = applied(tree)
    mp = home / "hotfix" / hid / "manifest.json"
    man = json.loads(mp.read_text())
    man["status"] = "applying"
    mp.write_text(json.dumps(man))
    assert rec.recover(inst, home, hid)["id"] == hid
    assert (inst / "a.py").read_text() == "A=1\n"


def test_dry_run_changes_nothing(tree, capsys):
    inst, home, hid = applied(tree)
    assert rec.main(["--dry-run", "--home", str(home), "--install", str(inst)]) == 0
    assert "would restore" in capsys.readouterr().out
    assert (inst / "a.py").read_text() == "A=2\n"
    assert json.loads((home / "hotfix" / hid / "manifest.json").read_text())["status"] == "applied"


def test_tampered_backup_is_refused_before_anything_changes(tree, capsys):
    inst, home, hid = applied(tree)
    (home / "hotfix" / hid / "originals" / "a.py").write_text("EVIL\n")
    assert rec.main(["--home", str(home), "--install", str(inst)]) == 1
    assert "RECOVER_ORIGINAL_BAD" in capsys.readouterr().err
    assert (inst / "a.py").read_text() == "A=2\n" and (inst / "new.py").exists()


def test_path_escape_in_manifest_is_refused(tree):
    inst, home, hid = applied(tree)
    mp = home / "hotfix" / hid / "manifest.json"
    man = json.loads(mp.read_text())
    man["added"].append({"path": "../../outside.txt"})
    mp.write_text(json.dumps(man))
    with pytest.raises(rec.Recover) as e:
        rec.recover(inst, home)
    assert e.value.code == "RECOVER_UNSAFE_PATH" and (inst / "a.py").read_text() == "A=2\n"


def test_list_unknown_id_and_no_backup(tree, capsys, tmp_path):
    inst, home, hid = applied(tree)
    assert rec.main(["--list", "--home", str(home)]) == 0
    assert f"{hid}  applied" in capsys.readouterr().out
    assert rec.main(["--id", "nope", "--home", str(home), "--install", str(inst)]) == 2
    assert rec.main(["--home", str(tmp_path / "empty"), "--install", str(inst)]) == 2
    assert "RECOVER_NONE" in capsys.readouterr().err


def test_recover_restores_dependency_files_and_bridge(tree):
    inst, home, hid = applied(tree)
    sp = inst.parent
    d = home / "hotfix" / hid
    write(sp, "dep/__init__.py", "V=2\n")                                       # state after the hotfix
    write(sp, "dep/new_mod.py", "X=1\n")
    write(sp, "dep-2.0.dist-info/METADATA", "x")
    write(d / "deps_originals" / "dep", "dep/__init__.py", "V=1\n")
    bridge = sp / "bridge" / "whatsapp_bridge.exe"
    write(sp, "bridge/whatsapp_bridge.exe", "NEW")
    write(d / "bridge_original", "exe", "OLD")
    man = json.loads((d / "manifest.json").read_text())
    man["deps"] = [{"name": "Dep", "old_version": "1.0", "new_version": "2.0", "new_paths": ["dep/__init__.py", "dep/new_mod.py"],
                    "new_dist": "dep-2.0.dist-info", "old_paths": [], "old_dist": None,
                    "backed_up": [{"path": "dep/__init__.py", "sha256": hf.sha256(d / "deps_originals" / "dep" / "dep" / "__init__.py")}]}]
    man["bridge"] = {"dest": str(bridge), "sha256_original": hf.sha256(d / "bridge_original" / "exe"), "had_sidecar": False}
    (d / "manifest.json").write_text(json.dumps(man))
    rec.recover(inst, home)
    assert (sp / "dep" / "__init__.py").read_text() == "V=1\n" and not (sp / "dep" / "new_mod.py").exists()
    assert not (sp / "dep-2.0.dist-info").exists() and bridge.read_text() == "OLD"


def test_bridge_dest_outside_install_is_refused(tree, tmp_path):
    inst, home, hid = applied(tree)
    d = home / "hotfix" / hid
    write(d / "bridge_original", "exe", "OLD")
    victim = tmp_path.parents[3] / "victim_outside.exe"
    man = json.loads((d / "manifest.json").read_text())
    man["bridge"] = {"dest": str(victim), "sha256_original": hf.sha256(d / "bridge_original" / "exe"), "had_sidecar": False}
    (d / "manifest.json").write_text(json.dumps(man))
    with pytest.raises(rec.Recover) as e:
        rec.recover(inst, home)
    assert e.value.code == "RECOVER_UNSAFE_PATH" and not victim.exists() and (inst / "a.py").read_text() == "A=2\n"


def test_recover_skips_an_already_reverted_backup(tree, monkeypatch):
    """#501: the newest backup that is still applied is picked, not a reverted one."""
    inst, src, home = tree
    ids = iter(["20261026-100000", "20261026-110000"])
    monkeypatch.setattr(hf, "_now_id", lambda: next(ids))
    write(src, "a.py", "A=2\n")
    first = hf.apply(inst, source(src), home, FakeHooks().hooks())["id"]
    write(src, "a.py", "A=3\n")
    second = hf.apply(inst, source(src), home, FakeHooks().hooks())["id"]
    man = home / "hotfix" / second / "manifest.json"
    m = json.loads(man.read_text())
    m["status"] = "reverted"
    man.write_text(json.dumps(m))
    r = rec.recover(inst, home)
    assert r["id"] == first


def test_recover_reports_the_partial_state_when_a_restore_fails_midway(tree, monkeypatch):
    inst, home, hid = applied(tree)
    real = rec._atomic_copy
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("disk full")
        real(src, dst)

    monkeypatch.setattr(rec, "_atomic_copy", flaky)
    with pytest.raises(rec.Recover) as e:
        rec.recover(inst, home)
    assert e.value.code == "RECOVER_PARTIAL" and "disk full" in e.value.message
    assert json.loads((home / "hotfix" / hid / "manifest.json").read_text())["status"] == "revert-failed"
    monkeypatch.setattr(rec, "_atomic_copy", real)                       # a rerun finishes the job
    assert rec.recover(inst, home)["status"] == "restored"
    assert (inst / "a.py").read_text() == "A=1\n"


def test_recover_refuses_clearly_when_a_file_is_locked(tree, monkeypatch):
    inst, home, hid = applied(tree)
    real_open = open

    def locked(p, mode="r", *a, **k):
        if mode == "r+b" and str(p).endswith("a.py"):
            raise PermissionError("locked")
        return real_open(p, mode, *a, **k)

    monkeypatch.setattr("builtins.open", locked)
    with pytest.raises(rec.Recover) as e:
        rec.recover(inst, home)
    assert e.value.code == "RECOVER_CORE_RUNNING" and e.value.refused
    monkeypatch.undo()
    assert (inst / "a.py").read_text() == "A=2\n"                        # nothing was changed
