"""#464 hotfix classifier limits: fake trees only, fail closed. Nothing touches a real install."""
from __future__ import annotations

import pytest

from jarves import hotfix as hf


def _trees(tmp_path, new_text, old_text="A=1\n"):
    sp = tmp_path / "site"
    inst = sp / "jarves"
    new = tmp_path / "newsrc"
    inst.mkdir(parents=True)
    new.mkdir()
    (inst / "a.py").write_text(old_text, encoding="utf-8")
    (new / "a.py").write_text(new_text, encoding="utf-8")
    return sp, inst, new


@pytest.mark.parametrize("code", ["import importlib\nimportlib.import_module('nope_zz')\n", "__import__('nope_zz')\n",
                                  "from importlib import import_module\nimport_module('nope_zz.sub')\n"])
def test_constant_dynamic_imports_are_checked(tmp_path, code):
    sp, inst, new = _trees(tmp_path, code)
    out = hf.scan_new_imports(new, ["a.py"], inst, [sp])
    assert out and "nope_zz" in out[0]


def test_dynamic_import_present_module_passes_and_computed_name_fails_closed(tmp_path):
    sp, inst, new = _trees(tmp_path, "__import__('present_zz')\n")
    (sp / "present_zz").mkdir()
    assert hf.scan_new_imports(new, ["a.py"], inst, [sp]) == []
    (new / "a.py").write_text("import importlib\nimportlib.import_module(name)\n", encoding="utf-8")
    assert hf.scan_new_imports(new, ["a.py"], inst, [sp]) == ["<dynamic import with a computed name> (in a.py)"]
    # not new: the installed copy already had one
    (inst / "a.py").write_text("import importlib\nimportlib.import_module(other)\n", encoding="utf-8")
    assert hf.scan_new_imports(new, ["a.py"], inst, [sp]) == []


def test_dotted_import_needs_the_submodule_not_just_the_package(tmp_path):
    sp, inst, new = _trees(tmp_path, "import google_zz.cloud\n")
    (sp / "google_zz").mkdir()
    assert hf.scan_new_imports(new, ["a.py"], inst, [sp]) == ["google_zz.cloud (in a.py)"]
    (sp / "google_zz" / "cloud").mkdir()
    assert hf.scan_new_imports(new, ["a.py"], inst, [sp]) == []


def test_constraints_file_is_a_dependency_file_and_refused():
    assert hf._is_dependency_file("core/constraints.txt") and hf._is_dependency_file("constraints-dev.in")
    with pytest.raises(hf.Refused) as e:
        hf.classify(hf.Plan(), ["core/constraints.txt"])
    assert e.value.code == "HOTFIX_NEEDS_SLICE2"


def test_docs_scripts_tools_changes_are_listed_not_ignored():
    ch = ["docs/x.md", "scripts/run.ps1", "core/tools/t.py", "core/src/jarves/a.py"]
    assert hf.classify(hf.Plan(replace=["a.py"]), ch)[1] == ["core/tools/t.py", "docs/x.md", "scripts/run.ps1"]


def test_folder_checkout_with_dependency_files_is_refused(tmp_path):
    co = tmp_path / "repo"
    (co / "core").mkdir(parents=True)
    (co / "core" / "uv.lock").write_text("x", encoding="utf-8")
    ctx = hf.Slice2Ctx(hf.Source(tmp_path, None, None, checkout=co), tmp_path, None)
    with pytest.raises(hf.Refused) as e:
        hf.classify(hf.Plan(), None, ctx)
    assert e.value.code == "HOTFIX_NEEDS_SLICE2" and "uv.lock" in e.value.message
    (co / "core" / "uv.lock").unlink()
    assert hf.classify(hf.Plan(), None, ctx)[1] == []
