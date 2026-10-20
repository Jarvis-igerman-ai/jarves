"""Capability matrix (SW-V2 spec section 5, WP-M4): every feature shows a truthful Windows + Pi status; non-verified
Pi entries carry a blocker and a REAL roadmap issue; nothing is silently missing; the dashboard API has the columns."""
import pytest

from jarves import capabilities as cap
from jarves import features as feat
from jarves.config import Config


def test_every_feature_has_a_complete_truthful_matrix():
    for fid in feat.CATALOG:
        v = cap.view(fid)
        assert v["declared"], fid
        assert v["problems"] == [], (fid, v["problems"])
        for t in cap.TARGETS:
            assert v["targets"][t]["status"] in cap.STATUSES


def test_no_pi_feature_claims_device_verification():
    # there is no Raspberry Pi yet: nothing may say verified_on_device for it (spec section 1, F02)
    assert not [f for f, c in cap.MATRIX.items() if c.targets["linux_arm64_pi"].status == "verified_on_device"]
    assert cap.WAKE_PHRASE["targets"]["linux_arm64_pi"]["status"] != "verified_on_device"


def test_roadmap_links_are_real_issues_only():
    for c in cap.MATRIX.values():
        for t in c.targets.values():
            assert t.roadmap_issue in ("", *cap.KNOWN_ISSUES)
    with pytest.raises(ValueError):
        cap.Target("planned", blocker="x", roadmap_issue="https://example.com/made-up")
    with pytest.raises(ValueError):
        cap.Target("supported")


def test_undeclared_feature_is_shown_not_hidden():
    feat.register("zz_test_feature", "בדיקה", "Test", False)
    try:
        v = cap.view("zz_test_feature")
        assert not v["declared"] and v["targets"]["linux_arm64_pi"]["status"] == "planned"
        assert "platform status not declared" in v["problems"]
        st = feat.Features(Config()).status("zz_test_feature")
        assert st["needs_attention"] and not st["pi_ready"]
    finally:
        feat.CATALOG.pop("zz_test_feature", None)


def test_register_with_targets_declares_the_matrix():
    feat.register("zz_declared", "x", "X", True, targets={
        "windows": {"status": "verified_on_device", "evidence": "QA run 1"},
        "linux_arm64_pi": {"status": "planned", "blocker": "no Pi", "roadmap_issue": cap.PI_ROADMAP}},
        permissions=["read"], settings="config x")
    try:
        st = feat.Features(Config()).status("zz_declared")
        assert st["targets"]["windows"]["status"] == "verified_on_device" and st["permissions"] == ["read"]
        assert st["settings"] == "config x" and st["matrix_problems"] == []
    finally:
        feat.CATALOG.pop("zz_declared", None)
        cap.MATRIX.pop("zz_declared", None)


def test_verified_without_evidence_is_a_problem():
    cap.declare("zz_bad", windows={"status": "verified_on_device"}, linux_arm64_pi={"status": "planned"})
    try:
        p = cap.problems("zz_bad")
        assert "windows: verified without evidence" in p and "linux_arm64_pi: planned without a blocker" in p
        assert "linux_arm64_pi: no real roadmap issue linked" in p
    finally:
        cap.MATRIX.pop("zz_bad", None)


def test_dashboard_api_has_the_columns_and_filters(home):
    class Ctx:
        pass
    ctx = Ctx()
    ctx.home, ctx.cfg = home, Config()
    st, body = feat.api_features(ctx, None, None)
    assert st == 200 and body["filters"] == ["all", "enabled", "not_ready_on_pi", "needs_attention"]
    f = next(x for x in body["features"] if x["id"] == "music")
    for k in ("enabled", "health", "settings", "permissions", "targets", "pi_ready", "needs_attention", "last_checked"):
        assert k in f
    assert f["targets"]["windows"]["status"] == "verified_on_device"
    assert f["targets"]["linux_arm64_pi"]["roadmap_issue"] == cap.PI_ROADMAP
    assert {x["id"] for x in body["features"]} >= {"calendar_write", "dashboard_lan", "assistant_identity"}
