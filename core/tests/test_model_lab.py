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


def test_hardening_529_max_tokens_clamp_csv_scrub_and_unique_archive(tmp_path):
    clk = Clock()
    aws = "AKIA" + "IOSFODNN7EXAMPLE"
    prov = ml.FakeProvider({("free-a", "he-1"): f"\tcell {aws}", ("free-a", "he-2"): "\rcell"}, clock=clk, tokens=(-5, -7))
    rep = ml.run_eval(REG, SpendPolicy("free-only"), prov, ["free-a"], tasks=ml.TASKS[:2], clock=clk)
    assert prov.max_tokens == ml.MAX_OUT_TOKENS                                   # item 1: the limit is passed to the call
    assert all(r["tokens_in"] == 0 and r["tokens_out"] == 0 and r["cost_usd"] >= 0 for r in rep["rows"])
    p1 = ml.archive(tmp_path, rep, wall=lambda: 0)
    p2 = ml.archive(tmp_path, rep, wall=lambda: 0)                                # item 4: same second, no overwrite
    assert p1 != p2 and p1.exists() and p2.exists()
    csv_text = p1.with_suffix(".csv").open(encoding="utf-8", newline="").read()      # keep the raw CR
    assert aws not in csv_text and "[redacted]" in csv_text
    assert "'" + chr(9) + "cell" in csv_text and "'" + chr(13) + "cell" in csv_text   # item 2: tab and CR cells are guarded too


def test_567_aws_secret_key_leading_space_formula_and_atomic_archive_name(tmp_path):
    aws_secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    assert aws_secret not in ml.scrub("secret " + aws_secret)
    assert ml._cell("  =1+1").startswith("'") and ml._cell("plain") == "plain"
    clk = Clock()
    rep = ml.run_eval(REG, SpendPolicy("free-only"), ml.FakeProvider({}, clock=clk), ["free-a"], tasks=ml.TASKS[:1], clock=clk)
    names = {ml.archive(tmp_path, rep, wall=lambda: 0).name for _ in range(3)}
    assert names == {"19700101-000000.json", "19700101-000000-2.json", "19700101-000000-3.json"}


# ---- V7 M4 (#418): results store, compare, dashboard routes (fakes, no network, no real clock)
def _store(tmp_path, *, stamps=(1_000_000_000, 1_000_000_100)):
    for i, t in enumerate(stamps):
        rep, _ = run(["free-a", "loc", "pay"] if i == 0 else ["free-a"])
        ml.archive(tmp_path, rep, wall=lambda t=t: t)


def test_store_lists_newest_first_and_skips_damage(tmp_path):
    _store(tmp_path)
    d = tmp_path / "modellab"
    (d / "20250101-000000.json").write_text("{not json", encoding="utf-8")
    (d / "notes.json").write_text("{}", encoding="utf-8")
    runs = ml.list_runs(tmp_path)
    assert [r["id"] for r in runs] == ["20010909-014820", "20010909-014640"]
    assert runs[1]["models"] == ["free-a", "loc", "pay"] and ml.list_runs(tmp_path / "none") == []


def test_load_run_rejects_path_tricks(tmp_path):
    _store(tmp_path)
    for bad in ("../x", "20010909-014640.json", "..\\..\\x", "", None, 5):
        assert ml.load_run(tmp_path, bad) is None
    assert ml.load_run(tmp_path, "20010909-014640")["mode"] == "free-only"


def test_compare_is_free_only_by_default(tmp_path):
    _store(tmp_path)
    reps = {i["id"]: ml.load_run(tmp_path, i["id"]) for i in ml.list_runs(tmp_path)}
    c = ml.compare(reps)
    assert c["free_only"] and [m["model"] for m in c["models"]] == ["free-a", "loc"]
    assert c["run_ids"] == sorted(reps) and set(c["models"][0]["runs"]) == set(reps)
    assert set(c["models"][1]["runs"]) == {"20010909-014640"}
    assert "pay" in [m["model"] for m in ml.compare(reps, free_only=False)["models"]]


def test_routes_read_only_local_only_no_key(tmp_path, monkeypatch):
    import re
    from types import SimpleNamespace
    from jarves import model_lab_surface as s
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secretsecret123456")
    _store(tmp_path)
    routes = []
    s.dashboard_routes(lambda meth, pat, fn: routes.append((meth, re.compile(pat), fn)))
    assert {m for m, _, _ in routes} == {"GET"}                       # nothing here can start a run
    ctx = SimpleNamespace(home=tmp_path, principal={"kind": "local"}, _features=SimpleNamespace(active=lambda f: True))
    code, body = s.api_runs(ctx, None, None)
    assert code == 200 and len(body["runs"]) == 2 and body["max_run_cap_usd"] == ml.MAX_RUN_CAP_USD
    m = next(p for _, p, f in routes if f is s.api_compare).fullmatch("/api/modellab/compare/20010909-014640,20010909-014820")
    code, cmp_ = s.api_compare(ctx, m, None)
    assert code == 200 and [x["model"] for x in cmp_["models"]] == ["free-a", "loc"]
    assert "secretsecret" not in json.dumps(cmp_) + json.dumps(body)
    gone = next(p for _, p, f in routes if f is s.api_compare).fullmatch("/api/modellab/all/20990101-000000")
    assert s.api_compare(ctx, gone, None)[0] == 404
    ctx.principal = {"kind": "device"}
    assert s.api_runs(ctx, None, None)[0] == 403 and s.api_compare(ctx, m, None)[0] == 403


def test_page_wired_and_js_has_no_run_or_key_call():
    from pathlib import Path
    from jarves import page_registry as pr
    assert any(p["page"] == "modellab" and p["group"] == "dev" for p in pr.pages())
    js = (Path(ml.__file__).parent / "dashboard_static" / "modellab.js").read_text(encoding="utf-8")
    assert 'method: "POST"' not in js and "secrets" not in js and "/api/modellab/" in js and "/api/features" not in js


def _ctx(tmp_path, on):
    from types import SimpleNamespace
    return SimpleNamespace(home=tmp_path, principal={"kind": "local"}, _features=SimpleNamespace(active=lambda f: on))


def test_routes_refuse_when_feature_off_and_state_answers(tmp_path):
    import re
    from jarves import model_lab_surface as s
    _store(tmp_path)
    m = re.fullmatch(r"/api/modellab/(compare|all)/([0-9,\-]+)", "/api/modellab/compare/20010909-014640")
    off = _ctx(tmp_path, False)
    for code, body in (s.api_runs(off, None, None), s.api_compare(off, m, None)):
        assert code == 409 and body["code"] == "MODELLAB_OFF" and "model_lab" in body["error"]
    assert s.api_state(off, None, None) == (200, {"active": False})
    on = _ctx(tmp_path, True)
    assert s.api_state(on, None, None) == (200, {"active": True})
    assert s.api_runs(on, None, None)[0] == 200 and s.api_compare(on, m, None)[0] == 200


def test_big_report_refused_and_listing_skips_it(tmp_path):
    import re
    from jarves import model_lab_surface as s
    _store(tmp_path)
    big = tmp_path / "modellab" / "20300101-000000.json"
    big.write_text(" " * (ml.MAX_REPORT_BYTES + 1), encoding="utf-8")
    with pytest.raises(ml.ReportTooLarge):
        ml.load_run(tmp_path, "20300101-000000")
    assert "20300101-000000" not in [r["id"] for r in ml.list_runs(tmp_path)]
    m = re.fullmatch(r"/api/modellab/(compare|all)/([0-9,\-]+)", "/api/modellab/compare/20300101-000000")
    code, body = s.api_compare(_ctx(tmp_path, True), m, None)
    assert code == 413 and body["code"] == "MODELLAB_REPORT_TOO_LARGE"


def test_listing_is_bounded(tmp_path, monkeypatch):
    d = tmp_path / "modellab"
    d.mkdir()
    for i in range(30):
        (d / f"20300101-{i:06d}.json").write_text("{}", encoding="utf-8")
    opened = []
    real = ml._read_report
    monkeypatch.setattr(ml, "_read_report", lambda p: (opened.append(p.name), real(p))[1])
    monkeypatch.setattr(ml, "MAX_SCAN", 5)
    assert ml.list_runs(tmp_path, limit=3) == [] and len(opened) == 5       # only the newest 5 names are opened
    assert opened[0] == "20300101-000029.json"
