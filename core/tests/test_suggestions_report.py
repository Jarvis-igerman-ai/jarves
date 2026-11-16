"""JAR-67: the suggestion report has 5-8 ideas, each with evidence, cost, benefit and tier (doc-only check)."""
from __future__ import annotations

import re
from pathlib import Path

REPORT = Path(__file__).resolve().parents[2] / "docs" / "reports" / "2026-11-16-suggestions.md"


def _ideas() -> list[str]:
    text = REPORT.read_text(encoding="utf-8")
    return re.split(r"^## \d+\. ", text, flags=re.M)[1:]


def test_report_has_five_to_eight_ideas():
    assert 5 <= len(_ideas()) <= 8


def test_every_idea_states_evidence_cost_benefit_and_tier():
    for idea in _ideas():
        title = idea.splitlines()[0]
        for field in ("**Evidence:**", "**Cost:**", "**Benefit:**", "**Tier:**"):
            assert field in idea, f"{title}: missing {field}"
        assert re.search(r"\*\*Tier:\*\* (N|R)\b", idea), f"{title}: tier must be N or R"
