"""Each V5 pillar's capability row names its own test files, and those files exist (#188)."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarves import capabilities
from jarves import continuity, devmanager, devstatus, goal_plans, goals, preferences, proactive  # noqa: F401

PILLARS = {"proactive_signals": ["test_v5_continuity.py"], "dev_manager": ["test_v5_followups.py"],
           "dev_status": ["test_v5_devstatus.py"], "goal_done_line": ["test_v5_pillars.py"],
           "goal_plans": ["test_v5_goal_plans.py"], "prefs_in_prompt": ["test_v5_pillars.py"],
           "proactive_intel": ["test_v5_followups.py", "test_v5_continuity.py"]}
TESTS = Path(__file__).parent


@pytest.mark.parametrize("fid", sorted(PILLARS))
def test_pillar_evidence_names_its_real_test_files(fid):
    ev = capabilities.get(fid).targets["windows"].evidence
    for name in PILLARS[fid]:
        assert name in ev
        assert (TESTS / name).is_file()
    assert capabilities.problems(fid) == []


@pytest.mark.parametrize("fid", sorted(PILLARS))
def test_ci_test_evidence_never_marks_a_pillar_verified_on_device(fid):
    """Automated (test-file) evidence is not device evidence: no pillar may claim verified_on_device (#188)."""
    for name, t in capabilities.get(fid).targets.items():
        assert t.status != "verified_on_device", f"{fid}/{name} claims device verification from CI evidence"


def test_verified_on_device_without_evidence_is_a_problem():
    """The guard itself: a verified_on_device row with no evidence is reported by `problems`."""
    capabilities.declare("v5_evidence_guard_probe", windows=capabilities.Target("verified_on_device", ""),
                         linux_arm64_pi=capabilities.Target("planned", blocker="probe"))
    try:
        assert any("verified without evidence" in p for p in capabilities.problems("v5_evidence_guard_probe"))
    finally:
        capabilities.MATRIX.pop("v5_evidence_guard_probe", None)


def test_pillars_no_longer_share_one_evidence_string():
    evs = {capabilities.get(f).targets["windows"].evidence for f in PILLARS}
    assert len(evs) >= 5
