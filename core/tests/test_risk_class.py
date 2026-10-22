"""Auto-label risk classifier (tools/risk_class.py): example file lists for every class and every high-risk path."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import risk_class as rc  # noqa: E402

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def cls(files, **kw):
    return rc.classify(files, **kw)[0]


def test_docs_only():
    assert cls(["docs/NOW.md", "README.md", "CHANGELOG.md", "docs/DOC_MAP.md"]) == "docs"


def test_tests_only():
    assert cls(["core/tests/test_timers.py"]) == "tests"
    assert cls(["core/tests/test_timers.py", "docs/X.md"]) == "tests"


def test_guides_and_locales_only():
    assert cls(["core/src/jarves/guides/data/timers.toml"]) == "guides"
    assert cls(["core/src/jarves/dashboard_static/locales/he.json", "docs/a.md"]) == "guides"


@pytest.mark.parametrize("path", [
    ".github/workflows/jarves-checks.yml", "packaging/release.py", "CLAUDE.md", "core/pyproject.toml", "core/uv.lock",
    "core/src/jarves/approvals.py", "core/src/jarves/helper_approvals.py", "core/src/jarves/isolation.py",
    "core/src/jarves/winsandbox.py", "core/src/jarves/mobile_auth.py", "core/src/jarves/secrets.py",
    "core/src/jarves/dashboard_auth.py", "core/tools/release.py", "core/tools/promote.py", "core/tests/conftest.py",
])
def test_high_risk_paths_never_label(path):
    assert cls([path, "docs/a.md"], labels={"bugfix"}, lines=3, mode="all") is None
    assert cls([path]) is None


def test_bugfix_needs_label_small_diff_and_mode_all():
    files = ["core/src/jarves/timers.py", "core/tests/test_timers.py"]
    assert cls(files, labels={"bugfix"}, lines=40) is None                    # default mode: not enabled
    assert cls(files, labels={"bugfix"}, lines=40, mode="all") == "bugfix"
    assert cls(files, labels={"bugfix"}, lines=150, mode="all") is None
    assert cls(files, labels=set(), lines=10, mode="all") is None


def test_blocking_labels_and_switch():
    assert cls(["docs/a.md"], labels={"needs-review"}) is None
    assert cls(["docs/a.md"], labels={"no-automerge"}) is None
    assert cls(["docs/a.md"], mode="false") is None


def test_dependabot_is_left_to_its_own_workflow():
    assert cls(["core/uv.lock"], author="dependabot[bot]") is None


def test_workflow_contract():
    doc = yaml.safe_load((WF / "autolabel.yml").read_text(encoding="utf-8"))
    job = doc["jobs"]["label"]
    assert "ENABLE_AUTOLABEL" in job["if"]
    for s in job["steps"]:
        assert "ref" not in s.get("with", {})                 # base branch only (pull_request_target), never PR code
    text = (WF / "autolabel.yml").read_text(encoding="utf-8")
    assert "risk_class.py" in text
    am = (WF / "automerge.yml").read_text(encoding="utf-8")
    assert "needs-review" in am and "no-automerge" in am and "--workflow jarves-checks" in am   # blocks + waits for main runs
