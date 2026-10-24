"""Deterministic, offline checks for the JARVES performance advisor and safe GitHub issue gate."""
from __future__ import annotations

import json
from types import SimpleNamespace

from jarves import issue_registry, perf_advisor, selfspeed


def row(ts, value, status, *, cls="owner", metric="tool_p95_ms"):
    return {"ts": ts, "status": status, "class": cls, "values": {metric: value},
            "statuses": {metric: status}}


def test_warn_needs_repeated_evidence_and_comparable_baseline():
    rows = [row(i, 100, "pass") for i in range(3)]
    assert perf_advisor.recommendations(rows) == []
    rows += [row(i + 3, 160, "warn") for i in range(3)]
    got = perf_advisor.recommendations(rows)
    assert len(got) == 1
    assert got[0]["metric"] == "tool_p95_ms" and got[0]["severity"] == "P3"
    assert got[0]["change_pct"] == 60 and got[0]["samples"] == 6
    # Recent transient noise cannot become an autonomous ticket.
    assert perf_advisor.recommendations(rows[:4]) == []
    assert perf_advisor.recommendations(rows[:3] + [row(4, 160, "warn"),
                                                      row(5, 100, "pass"),
                                                      row(6, 100, "pass")]) == []


def test_two_repeated_budget_failures_are_actionable_even_without_baseline():
    found = perf_advisor.recommendations([row(1, 250, "fail"), row(2, 240, "fail"), row(3, 260, "fail")])
    assert len(found) == 1 and found[0]["severity"] == "P2"
    assert found[0]["baseline"] is None
    assert perf_advisor.recommendations([row(1, 250, "fail"), row(2, 240, "pass"),
                                         row(3, 240, "pass")]) == []


def test_no_mixed_devices_invalid_values_or_unrecognized_metrics():
    rows = [row(i, 100, "pass", cls="ci") for i in range(3)]
    rows += [row(i + 3, 300, "warn", cls="owner") for i in range(3)]
    assert perf_advisor.recommendations(rows) == []
    assert perf_advisor.recommendations([row(1, float("nan"), "fail"),
                                         row(2, float("inf"), "fail"),
                                         row(3, -1, "fail")]) == []
    assert perf_advisor.recommendations([row(i, 999, "fail", metric="private_unknown") for i in range(4)]) == []


def test_history_creates_only_one_deduplicated_local_candidate(tmp_path):
    history = selfspeed.History(tmp_path, clock=lambda: 1000)
    def measured(ts):
        return {"ts": ts, "class": "owner", "status": "fail", "trigger": "schedule", "ms": 10,
                "metrics": [{"name": "tool_p95_ms", "status": "fail", "value": 500,
                             "max": 200, "unit": "ms", "trend_pct": None}]}
    for i in range(3):
        history.append("speedtest", measured(100 + i))
    reg = issue_registry.Registry(tmp_path)
    findings = perf_advisor.recommendations(history.rows("speedtest"))
    assert len(findings) == 1
    issue = reg.get(issue_registry.issue_id("speed", "PERF_TOOL_P95_MS"))
    assert issue is not None and issue["count"] == 1
    assert "performance" in findings[0]["advice"].lower() or findings[0]["advice"]
    assert perf_advisor.options(tmp_path)["auto_github"] is False


def test_owner_opt_in_settings_and_invalid_config(tmp_path):
    assert perf_advisor.options(tmp_path)["auto_github"] is False
    assert perf_advisor.options(tmp_path)["tracker_url"] == ""
    assert perf_advisor.configure(tmp_path, True)["auto_github"] is True
    assert perf_advisor.options(tmp_path)["auto_github"] is True
    assert perf_advisor.configure(tmp_path, False)["auto_github"] is False
    (tmp_path / perf_advisor.CONFIG).write_text("{broken", encoding="utf-8")
    assert perf_advisor.options(tmp_path)["auto_github"] is False


def test_dashboard_toggle_rejects_non_boolean_and_has_explicit_consent(tmp_path):
    ctx = SimpleNamespace(home=tmp_path)
    assert selfspeed.api_autoreport(ctx, None, {"enabled": "true"})[0] == 400
    assert selfspeed.api_autoreport(ctx, None, {"enabled": True})[1]["auto_github"] is True
    assert selfspeed.api_autoreport(ctx, None, {"enabled": False})[1]["auto_github"] is False


def test_issue_reporting_is_off_without_owner_opt_in_or_token(tmp_path):
    findings = [{"issue_id": "JI-12345678", "metric": "tool_p95_ms", "class": "owner",
                 "severity": "P2", "median": 400, "change_pct": 90,
                 "samples": 3, "recent_bad": 3}]
    def forbidden(*args):
        raise AssertionError("network must never be touched")
    assert perf_advisor.auto_report(tmp_path, findings, token_fn=forbidden)["status"] == "disabled"
    perf_advisor.configure(tmp_path, True)
    assert perf_advisor.auto_report(tmp_path, findings, token_fn=lambda: "")["status"] == "no_token"


def issue_finding(reg, metric, value=420, *, severity="P2", cls="owner"):
    item = reg.record("speed", f"PERF_{metric.upper()}", severity,
                      {"median": value}, title="Performance regression", source="perf_advisor")
    return {"issue_id": item["id"], "metric": metric, "class": cls, "severity": severity,
            "median": value, "baseline": 200, "change_pct": 110, "samples": 6,
            "recent_bad": 3, "advice": "Investigate metrics", "reported_url": ""}


def test_one_issue_for_multiple_metric_findings_and_no_update_if_unchanged(tmp_path):
    perf_advisor.configure(tmp_path, True)
    reg = issue_registry.Registry(tmp_path, clock=lambda: 1000)
    a = issue_finding(reg, "tool_p95_ms")
    b = issue_finding(reg, "dashboard_api_max_ms", 700, severity="P3")
    calls = []
    def fake(method, url, token, data=None):
        calls.append((method, url, data))
        if method == "GET":
            return 200, []
        assert method == "POST"
        return 201, {"number": 321, "html_url": "https://github.com/igalgerman/jarves/issues/321"}
    result = perf_advisor.auto_report(tmp_path, [a, b], token_fn=lambda: "token", request=fake,
                                      clock=lambda: 1000)
    assert result["status"] == "filed" and result["metrics"] == sorted(["tool_p95_ms", "dashboard_api_max_ms"])
    assert [x[0] for x in calls] == ["GET", "POST"]
    body = calls[1][2]["body"]
    assert perf_advisor.TRACKER_MARKER in body
    assert "tool_p95_ms" in body and "dashboard_api_max_ms" in body
    assert "Investigate metrics" not in body  # advice comes only from static safe mapping
    assert not any(word in body for word in ["email@", "C:\\Users\\", "conversation text"])
    assert reg.get(a["issue_id"])["reported_url"].endswith("/321")
    assert reg.get(b["issue_id"])["reported_url"].endswith("/321")
    def forbidden(*args, **kwargs):
        raise AssertionError("unchanged issue must not touch GitHub")
    assert perf_advisor.auto_report(tmp_path, [a, b], token_fn=forbidden,
                                    clock=lambda: 90000)["status"] == "unchanged"


def test_meaningful_change_updates_existing_tracker_after_24_hours(tmp_path):
    perf_advisor.configure(tmp_path, True)
    old = {"metric": "tool_p95_ms", "class": "owner", "severity": "P2", "change_pct": 110}
    fresh = {"issue_id": "JI-12345678", "metric": "tool_p95_ms", "class": "owner",
             "severity": "P2", "median": 500, "baseline": 200,
             "change_pct": 155, "samples": 6, "recent_bad": 3}
    url = "https://github.com/igalgerman/jarves/issues/321"
    perf_advisor._save_options(tmp_path, {**perf_advisor.options(tmp_path),
        "last_sent": 1000, "last_signature": perf_advisor._signature([old]),
        "last_active": ["tool_p95_ms"], "tracker_url": url})
    seen = []
    def fake(method, endpoint, token, data=None):
        seen.append((method, endpoint, data))
        if method == "GET":
            return 200, [{"number": 321, "state": "open", "html_url": url,
                          "body": perf_advisor.TRACKER_MARKER}]
        assert method == "PATCH" and endpoint.endswith("/issues/321")
        assert "median: 500" in data["body"]
        return 200, {"html_url": url}
    assert perf_advisor.auto_report(tmp_path, [fresh], token_fn=lambda: "token",
                                    request=fake, clock=lambda: 1500)["status"] == "cooldown"
    assert seen == []
    out = perf_advisor.auto_report(tmp_path, [fresh], token_fn=lambda: "token",
                                  request=fake, clock=lambda: 1000 + 86400)
    assert out["status"] == "updated" and out["issue_url"] == url and len(seen) == 2
    assert seen[-1][0] == "PATCH"
    assert perf_advisor.options(tmp_path)["last_signature"] == perf_advisor._signature([fresh])


def test_small_numeric_noise_does_not_cause_a_new_update():
    first = {"metric": "tool_p95_ms", "class": "owner", "severity": "P2", "change_pct": 109}
    close = {**first, "change_pct": 111}
    changed = {**first, "change_pct": 160}
    assert perf_advisor._signature([first]) == perf_advisor._signature([close])
    assert perf_advisor._signature([first]) != perf_advisor._signature([changed])


def test_tracker_recovery_needs_three_green_measurements(tmp_path):
    perf_advisor.configure(tmp_path, True)
    url = "https://github.com/igalgerman/jarves/issues/7"
    perf_advisor._save_options(tmp_path, {**perf_advisor.options(tmp_path),
        "last_sent": 100, "last_signature": perf_advisor._signature([
            {"metric": "tool_p95_ms", "class": "owner", "severity": "P2", "change_pct": 100}]),
        "last_active": ["tool_p95_ms"], "tracker_url": url})
    rows = [row(i, 90, "pass") for i in range(3)]
    (tmp_path / selfspeed.HISTORY_FILE).write_text(json.dumps({
        "latest": {}, "speedtest": rows[:2], "selftest": []}), encoding="utf-8")
    assert perf_advisor.auto_report(tmp_path, [], clock=lambda: 90000)["status"] == "awaiting_confirmation"
    (tmp_path / selfspeed.HISTORY_FILE).write_text(json.dumps({
        "latest": {}, "speedtest": rows, "selftest": []}), encoding="utf-8")
    calls = []
    def fake(method, endpoint, token, data=None):
        calls.append(method)
        if method == "GET":
            return 200, [{"number": 7, "state": "open", "html_url": url,
                          "body": perf_advisor.TRACKER_MARKER}]
        assert "recovered" in data["body"]
        return 200, {"html_url": url}
    result = perf_advisor.auto_report(tmp_path, [], token_fn=lambda: "token", request=fake,
                                      clock=lambda: 90000)
    assert result["status"] == "updated" and result["recovered"] is True
    assert calls == ["GET", "PATCH"]
    assert perf_advisor.options(tmp_path)["last_active"] == []
    assert perf_advisor.auto_report(tmp_path, [], clock=lambda: 200000)["status"] == "no_finding"


def test_owner_closed_tracker_is_not_reopened_and_multiple_matches_fail_closed(tmp_path):
    perf_advisor.configure(tmp_path, True)
    reg = issue_registry.Registry(tmp_path)
    finding = issue_finding(reg, "tool_p95_ms")
    tracker = {"number": 7, "state": "closed",
               "html_url": "https://github.com/igalgerman/jarves/issues/7",
               "body": perf_advisor.TRACKER_MARKER}
    def closed(method, url, token, data=None):
        assert method == "GET"
        return 200, [tracker]
    assert perf_advisor.auto_report(tmp_path, [finding], token_fn=lambda: "x",
                                    request=closed, clock=lambda: 1000)["status"] == "tracker_closed"
    def many(method, url, token, data=None):
        assert method == "GET"
        return 200, [tracker, {**tracker, "number": 8}]
    assert perf_advisor.auto_report(tmp_path, [finding], token_fn=lambda: "x",
                                    request=many, clock=lambda: 1000)["status"] == "duplicate_trackers"


def test_lookup_errors_and_pagination_fail_closed(tmp_path):
    perf_advisor.configure(tmp_path, True)
    reg = issue_registry.Registry(tmp_path)
    finding = issue_finding(reg, "tool_p95_ms")
    for response in ((403, None), (200, [{}] * 100), (200, None)):
        def fake(method, url, token, data=None):
            assert method == "GET"
            return response
        assert perf_advisor.auto_report(tmp_path, [finding], token_fn=lambda: "x",
                                        request=fake, clock=lambda: 1000)["status"] == "lookup_unavailable"


def test_all_budgeted_metrics_can_be_considered_not_only_four(tmp_path):
    # Each metric has three comparable failures so the advisor must not silently drop the fifth.
    names = ["tool_p95_ms", "tool_p50_ms", "dashboard_api_max_ms", "core_start_ms",
             "idle_cpu_pct", "spawn_to_ready_s"]
    rows = [{"ts": i, "class": "owner", "values": {k: 500 for k in names},
             "statuses": {k: "fail" for k in names}, "status": "fail"}
            for i in range(3)]
    got = perf_advisor.recommendations(rows)
    assert len(got) == len(names)
