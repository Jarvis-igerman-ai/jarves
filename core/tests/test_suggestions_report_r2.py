"""JAR-116: suggestion report r2 has at most 5 ideas, each with evidence, cost, benefit and tier, none repeating r1."""
from __future__ import annotations

import re
from pathlib import Path

REPORTS = Path(__file__).resolve().parents[2] / "docs" / "reports"
REPORT = REPORTS / "2026-11-16-suggestions-r2.md"
R1 = REPORTS / "2026-11-16-suggestions.md"


def _ideas(path: Path) -> list[str]:
    return re.split(r"^## \d+\. ", path.read_text(encoding="utf-8"), flags=re.M)[1:]


def test_report_has_three_to_five_ideas():
    assert 3 <= len(_ideas(REPORT)) <= 5


def test_every_idea_states_evidence_cost_benefit_and_tier():
    for idea in _ideas(REPORT):
        title = idea.splitlines()[0]
        for field in ("**Evidence:**", "**Cost:**", "**Benefit:**", "**Tier:**"):
            assert field in idea, f"{title}: missing {field}"
        assert re.search(r"\*\*Tier:\*\* (N|R)\b", idea), f"{title}: tier must be N or R"


def test_no_idea_repeats_a_r1_title():
    r1_titles = {i.splitlines()[0].strip().lower() for i in _ideas(R1)}
    for idea in _ideas(REPORT):
        assert idea.splitlines()[0].strip().lower() not in r1_titles
