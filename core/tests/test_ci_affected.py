"""Affected-test selection for the light PR CI on the `next` flow (tools/ci_affected.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ci_affected as ca  # noqa: E402


def test_ci_and_lock_changes_select_everything():
    assert ca.affected([".github/workflows/jarves-checks.yml"]) is None
    assert ca.affected(["core/uv.lock"]) is None
    assert ca.affected(["core/tests/conftest.py"]) is None
    assert ca.affected(["core/tools/ci_plan.py"]) is None


def test_changed_test_file_selects_itself_only():
    assert ca.affected(["core/tests/test_netguard.py"]) == ["test_netguard.py"]


def test_changed_module_selects_tests_importing_it():
    res = ca.affected(["core/src/jarves/updates.py"])
    assert res and any(name.startswith("test_updates") for name in res)
    assert len(res) < len(list(ca.TESTS.glob("test_*.py")))   # a subset, not everything


def test_data_rules():
    res = ca.affected(["core/src/jarves/guides/data/timers.toml"])
    assert "test_guides.py" in res and "test_cli_parity.py" in res
    assert ca.affected(["docs/NOW.md"]) == ["test_docs_links.py"]


def test_unrelated_file_selects_nothing():
    assert ca.affected(["README.md"]) == []
