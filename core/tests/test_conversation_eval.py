"""Conversation eval suite (JAR-231): scripted scenarios, injected judge, promotion gate. No network, no clock."""
from __future__ import annotations

import json
from types import SimpleNamespace

from jarves import conversation_eval as ce


def test_suite_has_30_plus_scenarios_in_both_languages_and_all_kinds():
    assert len(ce.SCENARIOS) >= 30
    assert len({s.id for s in ce.SCENARIOS}) == len(ce.SCENARIOS)
    assert {s.lang for s in ce.SCENARIOS} == {"he", "en"}
    assert {s.kind for s in ce.SCENARIOS} == {"reminder", "search", "chat", "device"}
    assert all(s.expect for s in ce.SCENARIOS)


def test_scripted_run_scores_full_and_judge_is_injected():
    rep = ce.run_suite(ce.scripted_reply, ce.keyword_judge)
    assert rep["score"] == 1.0 and rep["count"] == len(ce.SCENARIOS)
    bad = ce.run_suite(lambda s: "no idea", ce.keyword_judge)
    assert bad["score"] == 0.0 and set(bad["by_kind"]) == {"reminder", "search", "chat", "device"}


def test_score_drop_blocks_promotion_and_drafts_a_local_bug():
    base = ce.run_suite(ce.scripted_reply, ce.keyword_judge)
    worse = ce.run_suite(lambda s: ce.scripted_reply(s) if s.kind != "device" else "sorry", ce.keyword_judge)
    g = ce.gate(worse, base, beta="5.1.0b2")
    assert g["block"] and "device" in g["reason"]
    assert "5.1.0b2" in g["bug"]["title"] and g["bug"]["labels"] == ["bug", "P1"]
    assert not ce.gate(base, base)["block"] and ce.gate(base, base)["bug"] is None
    assert not ce.gate(base, None)["block"]          # no baseline yet: nothing to compare, recorded as first run


def test_cli_off_by_default_then_fake_run_and_gate(tmp_path, capsys, monkeypatch):
    on = {"v": False}
    monkeypatch.setattr("jarves.cli._cli_features", lambda cfg, home: SimpleNamespace(active=lambda f: on["v"]))
    a = SimpleNamespace(action="run", fake=True, beta="b1")
    assert ce.cmd_evalsuite(a, None, tmp_path) == 1 and "EVALSUITE_OFF" in capsys.readouterr().out
    on["v"] = True
    assert ce.cmd_evalsuite(SimpleNamespace(action="run", fake=False, beta="b1"), None, tmp_path) == 1
    assert "EVALSUITE_NO_JUDGE" in capsys.readouterr().out
    assert ce.cmd_evalsuite(a, None, tmp_path) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["score"] == 1.0 and out["gate"]["block"] is False
    assert (tmp_path / "evalsuite" / "baseline.json").exists()
    assert ce.cmd_evalsuite(SimpleNamespace(action="scenarios", fake=False, beta=""), None, tmp_path) == 0
    assert ce.feat.CATALOG[ce.FEATURE].default_enabled is False
