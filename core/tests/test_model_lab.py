"""Model Lab harness (V7 M3, #417): fake provider, fake clock, no network, no real calls."""
from __future__ import annotations

import csv
import json

import pytest

from jarves import model_lab as ml
from jarves.model_policy import ModelConfigError, ModelInfo, ModelRegistry, SpendPolicy


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


GOOD = {"he-1": "ירושלים", "he-2": "שבע", "he-3": "Good morning",
        "tool-1": 'Sure {"tool": "get_weather", "args": {"city": "Haifa"}}',
        "tool-2": '{"tool": "set_timer", "args": {"minutes": 5}}',
        "code-1": "def add(a, b):\n    return a + b", "code-2": "s[::-1]"}


def script(model, good=True):
    return {(model, k): (v if good else "no") for k, v in GOOD.items()}


REG = ModelRegistry([ModelInfo("free-a", "openrouter", paid=False), ModelInfo("loc", "local", local=True, paid=False),
                     ModelInfo("pay", "openai", input_per_1m=100.0, output_per_1m=100.0),
                     ModelInfo("noprice", "openai")])


def run(models, policy=None, cap=0.0, **kw):
    clk = Clock()
    prov = ml.FakeProvider({**script("free-a"), **script("loc", False), **script("pay"), **script("noprice")},
                           clock=clk, latency_s={"free-a": 2.0, "pay": 0.5}, **kw)
    rep = ml.run_eval(REG, policy or SpendPolicy("free-only"), prov, models, run_cap_usd=cap, clock=clk)
    return rep, prov


def test_free_quality_latency_and_labels():
    rep, prov = run(["free-a", "loc"])
    s = {r["model"]: r for r in rep["summary"]}
    assert s["free-a"]["quality"] == 1.0 and s["free-a"]["avg_latency_s"] == 2.0 and s["free-a"]["label"] == "free"
    assert s["loc"]["quality"] == 0.0 and s["loc"]["label"] == "local" and s["loc"]["cost_per_accepted_usd"] is None
    assert rep["spent_usd"] == 0.0 and len(prov.calls) == 2 * len(ml.TASKS)


def test_paid_never_called_in_default_policy_and_no_fallback():
    rep, prov = run(["pay", "noprice"])
    assert prov.calls == []
    assert all(r["status"] == "skipped" for r in rep["rows"])
    assert "free-only" in rep["rows"][0]["note"]


def test_paid_without_allow_list_or_cap_is_blocked():
    pol = SpendPolicy("cheap", daily_cap_usd=1.0, task_cap_usd=1.0)        # no allow-list for the project
    rep, prov = run(["pay"], pol, cap=1.0)
    assert prov.calls == [] and "allow-list" in rep["rows"][0]["note"]


def test_run_cap_stops_paid_calls():
    pol = SpendPolicy("cheap", daily_cap_usd=2.0, task_cap_usd=2.0, allowed_providers={ml.PROJECT: ["openai"]})
    rep, prov = run(["pay"], pol, cap=0.0)
    assert prov.calls == [] and rep["rows"][0]["note"] == "run cap would be exceeded"
    rep, prov = run(["pay"], pol, cap=0.04)             # worst case ~0.034 per task, ~0.004 real: room for two calls
    assert len(prov.calls) == 2 and rep["spent_usd"] <= 0.04
    assert rep["spent_usd"] == pol.spent_today


@pytest.mark.parametrize("cap", [-1, 2.01, float("nan"), float("inf"), True, "1"])
def test_bad_cap_refused(cap):
    with pytest.raises(ModelConfigError):
        run(["free-a"], cap=cap)


def test_provider_error_keeps_class_only():
    rep, _ = run(["free-a"], fail=["free-a"])
    assert {r["status"] for r in rep["rows"]} == {"error"} and rep["rows"][0]["note"] == "RuntimeError"


def test_scrub_and_archive_no_secrets(tmp_path):
    key = "sk-abcdef1234567890SECRET"
    clk = Clock()
    prov = ml.FakeProvider({("free-a", "he-1"): f"=cmd {key} ירושלים"}, clock=clk)
    rep = ml.run_eval(REG, SpendPolicy("free-only"), prov, ["free-a"], tasks=ml.TASKS[:1], clock=clk)
    p = ml.archive(tmp_path, rep, wall=lambda: 0)
    assert p.name == "19700101-000000.json"
    blob = p.read_text(encoding="utf-8") + p.with_suffix(".csv").read_text(encoding="utf-8")
    assert key not in blob and "[redacted]" in blob
    rows = list(csv.reader(p.with_suffix(".csv").open(encoding="utf-8")))
    assert rows[0][0] == "model" and rows[1][-1].startswith("'=")
    assert ml.latest(tmp_path)["summary"][0]["accepted"] == 1


def test_tasks_fixed_and_public():
    assert len(ml.TASKS) == 7 and {t.kind for t in ml.TASKS} == {"hebrew", "tool_call", "coding"}
    assert len({t.id for t in ml.TASKS}) == 7


def test_grade_json_rejects_wrong_args():
    t = ml.TASKS[3]
    assert ml.grade(t, '{"tool": "get_weather", "args": {"city": "Tel Aviv"}}') is False
    assert ml.grade(t, "not json {") is False


def test_feature_is_beta_and_off():
    from jarves import features as feat
    assert feat.stage_of(ml.FEATURE) == "beta" and feat.CATALOG[ml.FEATURE].default_enabled is False


def test_cli_off_and_no_live_provider(tmp_path, capsys):
    from types import SimpleNamespace as NS

    class F:
        def __init__(self, on):
            self.on = on

        def active(self, _):
            return self.on

    import jarves.cli as cli
    orig = cli._cli_features
    try:
        cli._cli_features = lambda cfg, home: F(False)
        a = NS(action="run", models="free-remote", cap_usd=0.0, fake=True)
        assert ml.cmd_modellab(a, None, tmp_path) == 1 and "MODELLAB_OFF" in capsys.readouterr().out
        cli._cli_features = lambda cfg, home: F(True)
        a.fake = False
        assert ml.cmd_modellab(a, None, tmp_path) == 1 and "NO_PROVIDER" in capsys.readouterr().out
        a.fake = True
        assert ml.cmd_modellab(a, None, tmp_path) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["spent_usd"] == 0.0
        assert ml.cmd_modellab(NS(action="report"), None, tmp_path) == 0
    finally:
        cli._cli_features = orig
