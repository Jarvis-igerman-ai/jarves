"""Issue #544: a real stable publish refuses unless `stable-release` is a protected environment.

GitHub creates a missing environment on first use WITHOUT protection, so naming `environment: stable-release` alone
does not protect the release secrets. The guard reads the environment over the API and fails closed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "tools"))
import release_env_guard as guard  # noqa: E402

PROTECTED = {
    "name": "stable-release",
    "protection_rules": [{"type": "required_reviewers", "reviewers": [{"type": "User", "reviewer": {"login": "o"}}]}],
    "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}


def test_protected_environment_passes():
    assert guard.problems(PROTECTED) == []


def test_unprotected_or_malformed_environment_is_refused():
    auto_created = {"name": "stable-release", "protection_rules": [], "deployment_branch_policy": None}
    assert len(guard.problems(auto_created)) == 2
    no_reviewers = dict(PROTECTED, protection_rules=[{"type": "required_reviewers", "reviewers": []}])
    assert any("reviewer" in p for p in guard.problems(no_reviewers))
    any_branch = dict(PROTECTED, deployment_branch_policy={"protected_branches": True, "custom_branch_policies": False})
    assert any("tag" in p for p in guard.problems(any_branch))
    assert any("name" in p for p in guard.problems(dict(PROTECTED, name="other")))
    for bad in (None, [], "x", {"protection_rules": "x", "deployment_branch_policy": []}):
        assert guard.problems(bad), bad


def test_cli_fails_closed(tmp_path, capsys):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(PROTECTED), encoding="utf-8")
    assert guard.main(["--from-file", str(ok)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert guard.main(["--from-file", str(bad)]) == 1
    assert "::error::" in capsys.readouterr().out
    assert guard.main(["--from-file", str(tmp_path / "missing.json")]) == 1


def test_release_workflow_runs_guard_right_before_the_secret_step():
    job = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))["jobs"]["stable"]
    steps = job["steps"]
    idx = next(i for i, s in enumerate(steps) if "secrets." in str(s))
    step = steps[idx - 1]
    assert "secrets." not in str(step)
    assert "tools/release_env_guard.py" in step["run"]
    # same condition as the publish step: whenever the secrets step would run, the guard ran first and passed
    assert step.get("if") == steps[idx]["if"]
