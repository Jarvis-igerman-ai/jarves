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
    "can_admins_bypass": False,
    "protection_rules": [{"type": "required_reviewers", "reviewers": [{"type": "User", "reviewer": {"login": "o"}}]}],
    "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}
TAG_ONLY = {"total_count": 1, "branch_policies": [{"name": "v*.*.*", "type": "tag"}]}


def test_protected_environment_passes():
    assert guard.problems(PROTECTED, TAG_ONLY) == []
    assert guard.warnings(PROTECTED) == []


def test_unprotected_or_malformed_environment_is_refused():
    auto_created = {"name": "stable-release", "protection_rules": [], "deployment_branch_policy": None}
    # the rule list is not consulted for a non-custom policy (it is not fetched then, review R2)
    assert len(guard.problems(auto_created, None)) == 2
    no_reviewers = dict(PROTECTED, protection_rules=[{"type": "required_reviewers", "reviewers": []}])
    assert any("reviewer" in p for p in guard.problems(no_reviewers, TAG_ONLY))
    any_branch = dict(PROTECTED, deployment_branch_policy={"protected_branches": True, "custom_branch_policies": False})
    assert any("tag" in p for p in guard.problems(any_branch, TAG_ONLY))
    assert any("name" in p for p in guard.problems(dict(PROTECTED, name="other"), TAG_ONLY))
    for bad in (None, [], "x", {"protection_rules": "x", "deployment_branch_policy": []}):
        assert guard.problems(bad, TAG_ONLY), bad


def test_branch_rules_or_missing_tag_rule_are_refused():
    # R1: a custom policy that also admits a branch (main, *) is not tag-only
    mixed = {"total_count": 2, "branch_policies": [{"name": "v*.*.*", "type": "tag"}, {"name": "main", "type": "branch"}]}
    assert any("main" in p for p in guard.problems(PROTECTED, mixed))
    only_branch = {"total_count": 1, "branch_policies": [{"name": "*", "type": "branch"}]}
    found = guard.problems(PROTECTED, only_branch)
    assert any("no tag" in p for p in found) and any("branch" in p for p in found)
    for bad in (None, [], {"branch_policies": "x"}, {"branch_policies": ["x"]}):
        assert any("not readable" in p for p in guard.problems(PROTECTED, bad)), bad


def test_incomplete_policy_page_is_refused():
    # review R1: a branch rule on a later page must not be missed; refuse unless one page holds every rule
    truncated = dict(TAG_ONLY, total_count=31)
    assert any("incomplete" in p for p in guard.problems(PROTECTED, truncated))
    for total in (None, "1", True):
        assert any("incomplete" in p for p in guard.problems(PROTECTED, dict(TAG_ONLY, total_count=total))), total


def test_policies_fetched_only_for_a_custom_policy(monkeypatch, capsys):
    calls = []

    def fake_gh(path):
        calls.append(path)
        if path.endswith("/stable-release"):
            return dict(PROTECTED, deployment_branch_policy=None)
        raise RuntimeError("404")

    monkeypatch.setattr(guard, "_gh", fake_gh)
    assert guard.main(["--repo", "o/r"]) == 1
    out = capsys.readouterr().out
    assert calls == ["repos/o/r/environments/stable-release"] and "not limited to selected tags" in out
    calls.clear()
    monkeypatch.setattr(guard, "_gh", lambda path: calls.append(path) or (PROTECTED if path.endswith("release") else TAG_ONLY))
    assert guard.main(["--repo", "o/r"]) == 0
    assert calls[1] == "repos/o/r/environments/stable-release/deployment-branch-policies?per_page=100"


def test_admin_bypass_is_a_warning(tmp_path, capsys):
    # R2: GitHub defaults can_admins_bypass to true; report it, do not refuse
    bypass = dict(PROTECTED, can_admins_bypass=True)
    assert guard.problems(bypass, TAG_ONLY) == [] and guard.warnings(bypass)
    assert guard.warnings({k: v for k, v in PROTECTED.items() if k != "can_admins_bypass"})
    env, pol = tmp_path / "e.json", tmp_path / "p.json"
    env.write_text(json.dumps(bypass), encoding="utf-8")
    pol.write_text(json.dumps(TAG_ONLY), encoding="utf-8")
    assert guard.main(["--from-file", str(env), "--policies-file", str(pol)]) == 0
    assert "::warning::" in capsys.readouterr().out


def test_cli_fails_closed(tmp_path, capsys):
    ok, pol = tmp_path / "ok.json", tmp_path / "p.json"
    ok.write_text(json.dumps(PROTECTED), encoding="utf-8")
    pol.write_text(json.dumps(TAG_ONLY), encoding="utf-8")
    assert guard.main(["--from-file", str(ok), "--policies-file", str(pol)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert guard.main(["--from-file", str(bad), "--policies-file", str(pol)]) == 1
    assert "::error::" in capsys.readouterr().out
    assert guard.main(["--from-file", str(ok), "--policies-file", str(bad)]) == 1
    assert guard.main(["--from-file", str(tmp_path / "missing.json"), "--policies-file", str(pol)]) == 1


def test_release_workflow_runs_guard_right_before_the_secret_step():
    job = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))["jobs"]["stable"]
    steps = job["steps"]
    # review R3: pick the secrets step by name, and check no other step reads the secrets
    idx = next(i for i, s in enumerate(steps) if str(s.get("name", "")).startswith("Publish the stable release"))
    assert "secrets." in str(steps[idx])
    assert [s.get("name") for s in steps if "secrets." in str(s)] == [steps[idx]["name"]]
    step = steps[idx - 1]
    # review R1: the guard's gh api call has a token on the step itself, under a job that can read environments
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert job["permissions"]["actions"] == "read"
    assert "secrets." not in str(step)
    assert "tools/release_env_guard.py" in step["run"]
    # same condition as the publish step: whenever the secrets step would run, the guard ran first and passed
    assert step.get("if") == steps[idx]["if"]


def test_broad_tag_rule_passes_with_a_warning():
    # review R2: a tag rule other than exactly v*.*.* is not refused, but it is reported
    broad = {"total_count": 1, "branch_policies": [{"name": "*", "type": "tag"}]}
    assert guard.problems(PROTECTED, broad) == []
    assert any("v*.*.*" in w for w in guard.warnings(PROTECTED, broad))
    assert guard.warnings(PROTECTED, TAG_ONLY) == []
