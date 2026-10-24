"""Live no-installer PR proof acceptance guard (also forces a genuine test-only PR run).

This single-file regression ensures the cheap test-only proof can NEVER stand in
for a real installer; the main merge remains responsible for the installer tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import ci_plan  # noqa: E402
import ci_reuse  # noqa: E402


def test_test_only_pr_avoids_installer_but_main_still_builds_it():
    source = "core/tests/test_ci_no_installer_acceptance.py"
    pr = ci_plan.classify([source])
    assert pr["code"] and pr["full"]
    assert not pr["installer"]

    # A successful exact-tree PR test proof skips ONLY redundant tests on main.
    merged = ci_plan.classify([], event="push", reuse_tests_only=True)
    assert merged["installer"]
    assert {key for key, required in merged.items() if required} == {"installer"}

    # Unproven tree, cancelled PR, changed content etc must run all on main.
    fallback = ci_plan.classify([], event="push")
    assert all(fallback.values())


def test_proof_and_installer_artifacts_have_distinct_names():
    tree = "a" * 40
    assert ci_reuse.PROOF_PREFIX + tree != ci_reuse.artifact_name(tree)
    assert ci_reuse.resolve_tests("o/r", tree, lambda path: {"artifacts": []}) is None
