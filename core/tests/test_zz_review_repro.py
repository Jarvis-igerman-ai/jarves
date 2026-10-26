import json
import pytest
from jarves import hotfix as hf
from jarves import hotfix_recover as rec
from test_hotfix_install import FakeHooks, source, tree, write  # noqa: F401

@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    ids = iter(["20261026-100000", "20261026-110000", "20261026-120000"])
    monkeypatch.setattr(hf, "_now_id", lambda: next(ids))

def test_recover_downgrades_after_full_installer_update(tree):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    hf.apply(inst, source(src), home, FakeHooks().hooks())          # hotfix A on old build: stays "applied"
    # a full installer update to a new version: new files, fresh build info without the hotfix
    (inst / "a.py").write_text("A=NEWVERSION\n")
    (inst / hf.BUILD_INFO_NAME).write_text(json.dumps({"version": "9.9.9"}))
    write(src, "a.py", "A=NEWHOTFIX\n")
    try:
        hf.apply(inst, source(src), home, FakeHooks().hooks())
    except Exception as e:
        print("apply2", e)
    found = rec.list_manifests(home)
    print([(d.name, m["status"]) for d, m in found])
    # mark newest as reverted (e.g. auto-revert after failure / user revert)
    d, m = found[0]; m["status"] = "reverted"; (d / "manifest.json").write_text(json.dumps(m))
    r = rec.recover(inst, home)
    print("recover picked", r["id"], "a.py now:", (inst / "a.py").read_text())
    assert (inst / "a.py").read_text() == "A=NEWVERSION\n", "old backup restored over new build"

def test_revert_failed_disables_replaced_guard(tree, monkeypatch):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    m = hf.apply(inst, source(src), home, FakeHooks().hooks())
    d = home / "hotfix" / m["id"]
    real = hf._atomic_copy
    monkeypatch.setattr(hf, "_atomic_copy", lambda *a: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        hf.revert(inst, home, FakeHooks().hooks())
    monkeypatch.setattr(hf, "_atomic_copy", real)
    print("status after failed revert:", json.loads((d / "manifest.json").read_text())["status"])
    # now a normal full update replaces the build
    (inst / "a.py").write_text("A=NEWVERSION\n")
    (inst / hf.BUILD_INFO_NAME).write_text(json.dumps({"version": "9.9.9"}))
    try:
        hf.revert(inst, home, FakeHooks().hooks())
        print("revert ran over new build, a.py:", (inst / "a.py").read_text())
        assert False, "HOTFIX_REPLACED guard bypassed"
    except hf.Refused as e:
        print("refused", e.code)
