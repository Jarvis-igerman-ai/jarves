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
    assert perf_advisor.options(tmp_path) == {"auto_github": False, "last_sent": 0.0}
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
    findings = [{"issue_id": "JI-12345678", "metric": "tool_p95_ms"}]
    def forbidden(*args):
        raise AssertionError("network must never be touched")
    assert perf_advisor.auto_report(tmp_path, findings, token_fn=forbidden)["status"] == "disabled"
    perf_advisor.configure(tmp_path, True)
    assert perf_advisor.auto_report(tmp_path, findings, token_fn=lambda: "")["status"] == "no_token"


def test_autonomous_reporting_one_numeric_issue_then_cooldown_and_dedup(tmp_path):
    perf_advisor.configure(tmp_path, True)
    reg = issue_registry.Registry(tmp_path, clock=lambda: 1000)
    item = reg.record("speed", "PERF_TOOL_P95_MS", "P2",
                      {"median": 420}, title="Performance regression", source="perf_advisor")
    finding = {"issue_id": item["id"], "metric": "tool_p95_ms", "class": "owner",
               "median": 420, "baseline": 200, "change_pct": 110, "samples": 6,
               "recent_bad": 3, "advice": "Investigate slow tool calls",
               "reported_url": ""}
    requests = []
    def fake_api(method, url, token, data=None):
        requests.append((method, url, token, data))
        if method == "GET":
            return 200, []
        return 201, {"html_url": "https://github.com/igalgerman/jarves/issues/321"}
    result = perf_advisor.auto_report(tmp_path, [finding, finding], token_fn=lambda: "dummy",
                                      request=fake_api, clock=lambda: 1000)
    assert result["status"] == "filed"
    assert len(requests) == 2 and requests[0][0] == "GET" and requests[1][0] == "POST"
    body = requests[1][3]["body"]
    assert "jarves-issue:" in body and "420" in body
    assert not any(word in body for word in ["password", "email@", "C:\\Users\\", "conversation text"])
    assert reg.get(item["id"])["reported_url"] == "https://github.com/igalgerman/jarves/issues/321"
    assert perf_advisor.auto_report(tmp_path, [finding], token_fn=lambda: "dummy",
                                    request=fake_api, clock=lambda: 1001)["status"] == "cooldown"


def test_duplicate_existing_issue_no_new_issue(tmp_path):
    perf_advisor.configure(tmp_path, True)
    reg = issue_registry.Registry(tmp_path, clock=lambda: 50)
    item = reg.record("speed", "PERF_TOOL_P95_MS", "P2", title="Repeated")
    finding = {"issue_id": item["id"], "metric": "tool_p95_ms", "reported_url": ""}
    seen = []
    def fake(method, url, token, data=None):
        seen.append(method)
        return 200, [{"body": f"<!-- jarves-issue: {item['id']} -->",
                      "html_url": "https://github.com/igalgerman/jarves/issues/7"}]
    result = perf_advisor.auto_report(tmp_path, [finding], token_fn=lambda: "x",
                                      request=fake, clock=lambda: 500)
    assert result["status"] == "already_reported" and seen == ["GET"]
    assert reg.get(item["id"])["reported_url"].endswith("/7")


def test_list_errors_and_unbounded_duplicate_search_fail_closed(tmp_path):
    perf_advisor.configure(tmp_path, True)
    finding = {"issue_id": "JI-12345678", "metric": "tool_p95_ms", "reported_url": ""}
    for response in ((403, None), (200, [{}] * 100), (200, None)):
        def fake(method, url, token, data=None):
            assert method == "GET"
            return response
        assert perf_advisor.auto_report(tmp_path, [finding], token_fn=lambda: "x",
                                        request=fake, clock=lambda: 500)["status"] == "lookup_unavailable"
