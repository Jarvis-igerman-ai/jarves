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


def test_pillars_no_longer_share_one_evidence_string():
    evs = {capabilities.get(f).targets["windows"].evidence for f in PILLARS}
    assert len(evs) >= 5
