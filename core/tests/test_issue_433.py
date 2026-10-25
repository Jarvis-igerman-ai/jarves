"""#433: expected states are information, real failures stay problems. Fake data only."""
from __future__ import annotations

import pytest

from jarves import issue_registry as ir, issue_text as it, selfcheck
from jarves.skills import expected_tool_refusal
from test_issue_registry import Clock, _rec, reg  # noqa: F401


@pytest.mark.parametrize("code,fp", [("LIVE_EXTRA_REJECTED", "log:WARNING:LIVE_EXTRA_REJECTED"),
                                     ("WAKE_MIC_QUIET", "log:WARNING:WAKE_MIC_QUIET")])
def test_expected_codes_are_information_with_both_languages(reg, code, fp):
    reg.record("health", f"FINDING_{fp}", "P2", {"finding": fp})
    c = reg.counts()
    assert c["open"] == 0 and c["info"] == 1 and c["urgent"] == 0
    d = it.describe(reg.list(None)[0])
    assert d["kind"] == "info" and d["hint"] and d["hint_he"] and d["title_he"]
    assert it._HEB.search(d["title_he"]) and it._HEB.search(d["next_step_he"])


def test_same_area_genuine_failures_still_count(reg):
    # severity is never lowered by the area or by a look-alike code
    for area, code, sev in [("health", "FINDING_log:ERROR:LIVE_EXTRA_REJECTED_CRASH", "P1"),
                            ("health", "FINDING_log:ERROR:WAKE_MIC_QUIET_BROKEN", "P2"),
                            ("health", "FINDING_log:ERROR:LIVE_CONNECT_FAILED", "P1"),
                            ("wake", "WAKE_MODEL_LOAD_FAILED", "P2"), ("speed", "PERF_CORE_START_MS", "P2")]:
        reg.record(area, code, sev, {"finding": code})
    c = reg.counts()
    assert c["open"] == 5 and c["info"] == 0 and c["p1"] == 2 and c["p2"] == 3


def test_unknown_contact_is_not_a_tool_failure_but_other_wa_errors_are(reg):
    assert expected_tool_refusal("WA_UNKNOWN_CONTACT") and not expected_tool_refusal("WA_AMBIGUOUS")
    assert not expected_tool_refusal("WA_SEND_FAILED")
    sink = ir.IssueSink(reg, threshold=3)
    for _ in range(5):
        sink.emit(_rec("TOOL_RESULT", tool="whatsapp_chat", ok=False, result_code="WA_UNKNOWN_CONTACT", origin="model"))
    sink.flush()
    assert reg.list() == []
    for _ in range(5):
        sink.emit(_rec("TOOL_RESULT", tool="whatsapp_chat", ok=False, result_code="WA_SEND_FAILED", origin="model"))
    sink.flush()
    assert len(reg.list()) == 1


def test_selfcheck_tool_finding_ignores_unknown_contact_only(tmp_path):
    def run(code):
        recs = [{"code": "TOOL_RESULT", "tool": "whatsapp_chat", "ok": False, "result_code": code, "_t": 1.0 + i}
                for i in range(5)]
        out, metrics = [], {}
        selfcheck.SelfCheck._tools(object(), recs, out, metrics)
        return out
    assert run("WA_UNKNOWN_CONTACT") == []
    assert len(run("WA_SEND_FAILED")) == 1


def test_speed_is_information_until_three_runs(reg):
    for run in range(1, 4):
        reg.record("speed", "SPEED_CORE_START_MS", "P2", {"metric": "core_start_ms", "status": "fail"})
        c = reg.counts()
        assert (c["open"], c["info"]) == ((0, 1) if run < 3 else (1, 0))
    assert it.describe(reg.list(None)[0])["kind"] == "bug"


def _proc(session, t, version, stopped=False):
    recs = [{"code": "CORE_STARTING", "session": session, "_t": t, "version": version, "boot": 100.0}]
    if stopped:
        recs.append({"code": "CORE_STOPPED", "session": session, "_t": t + 5})
    return recs


def test_stop_after_update_is_its_own_kind_and_other_stops_stay_crashes():
    upd = selfcheck.processes(_proc("a", 10, "4.0.1.724") + _proc("b", 100, "4.0.1.760"))
    assert selfcheck.end_kind(upd[0], upd) == "update"
    same = selfcheck.processes(_proc("a", 10, "4.0.1.760") + _proc("b", 100, "4.0.1.760"))
    assert selfcheck.end_kind(same[0], same) == "crash"
    assert it.classify({"code": "FINDING_stop_update", "evidence": {"finding": "stop_update"}}) == "info"
    assert it.classify({"code": "FINDING_crash", "evidence": {"finding": "crash"}}) == "bug"


def test_expiry_semantics_untouched(reg):
    reg.record("tools", "TOOL_A_FAIL", "P2")
    reg.record("health", "FINDING_log:WARNING:WAKE_MIC_QUIET", "P2", {"finding": "log:WARNING:WAKE_MIC_QUIET"})
    reg.clock.t += (ir.EXPIRE_DAYS + 1) * 86400
    reg.tidy("")
    assert reg.counts()["open"] == 0 and reg.counts()["info"] == 0
    assert reg.get(ir.issue_id("tools", "TOOL_A_FAIL"))["closed_reason"] == "expired"
    reg.record("tools", "TOOL_A_FAIL", "P2")
    assert reg.counts()["open"] == 1
