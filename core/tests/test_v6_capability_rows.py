"""V6 phone / car capability rows (#215): honest statuses, each row names real test files, Hebrew exists."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarves import capabilities as cap
from jarves import drive_briefing, drive_mode, hebrew, journey  # noqa: F401
from jarves import features as feat

TESTS = Path(__file__).parent
HE = re.compile(r"[\u0590-\u05ff]")


def test_rows_declared_only_for_v6_features_and_other_features_keep_two_targets():
    assert set(cap.V6_ROWS) <= set(feat.CATALOG) | set(cap.MATRIX)
    other = next(f for f in feat.CATALOG if f not in cap.V6_ROWS)
    assert set(cap.view(other)["targets"]) == set(cap.TARGETS)
    for fid in cap.V6_ROWS:
        assert set(cap.view(fid)["targets"]) == {*cap.TARGETS, *cap.EXTRA_TARGETS}


@pytest.mark.parametrize("fid", sorted(cap.V6_ROWS))
def test_row_is_complete_and_nothing_is_verified_on_a_device(fid):
    v = cap.view(fid)
    assert v["problems"] == [], v["problems"]
    for name in cap.EXTRA_TARGETS:
        t = v["targets"][name]
        assert t["status"] != "verified_on_device"          # no real phone or car was tested
        if t["status"] == "implemented_unverified":
            assert t["blocker"].startswith("NOT-RUN")


@pytest.mark.parametrize("fid", sorted(cap.V6_ROWS))
def test_evidence_names_real_test_files_and_has_hebrew(fid):
    for name in cap.EXTRA_TARGETS:
        t = cap.get(fid).targets[name]
        for f in re.findall(r"test_\w+\.py", t.evidence):
            assert (TESTS / f).is_file(), f
        for text in (t.evidence, t.blocker):
            if text:
                assert HE.search(hebrew.he(text)), text


def test_a_verified_phone_row_without_evidence_is_a_problem():
    feat.register("zz_v6_row", "x", "X", False, targets={
        "windows": {"status": "implemented_unverified", "evidence": "e"},
        "linux_arm64_pi": {"status": "not_applicable"}})
    try:
        cap.declare_extra("zz_v6_row", phone={"status": "verified_on_device"},
                          car={"status": "implemented_unverified", "evidence": "t"})
        probs = cap.problems("zz_v6_row")
        assert any("phone: verified without evidence" in p for p in probs)
        assert any("car: not run on a real device" in p for p in probs)
    finally:
        feat.CATALOG.pop("zz_v6_row", None)
        cap.EXTRA.pop("zz_v6_row", None)
        cap.MATRIX.pop("zz_v6_row", None)
