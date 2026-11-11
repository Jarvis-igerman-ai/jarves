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


def _on(monkeypatch):
    monkeypatch.setattr("jarves.cli._cli_features", lambda cfg, home: SimpleNamespace(active=lambda f: True))


def test_cli_blocked_run_exits_2_keeps_baseline_and_writes_bug(tmp_path, capsys, monkeypatch):
    _on(monkeypatch)
    a = SimpleNamespace(action="run", fake=True, beta="b2")
    assert ce.cmd_evalsuite(a, None, tmp_path) == 0
    base = (tmp_path / "evalsuite" / "baseline.json").read_bytes()
    monkeypatch.setattr(ce, "scripted_reply", lambda s: "sorry" if s.kind == "device" else " ".join(s.expect))
    assert ce.cmd_evalsuite(a, None, tmp_path) == 2
    assert (tmp_path / "evalsuite" / "baseline.json").read_bytes() == base
    assert json.loads((tmp_path / "evalsuite" / "last.json").read_text(encoding="utf-8"))["gate"]["bug"]


def test_baseline_never_ratchets_down(tmp_path, capsys, monkeypatch):
    _on(monkeypatch)
    a = SimpleNamespace(action="run", fake=True, beta="b3")
    ce.cmd_evalsuite(a, None, tmp_path)
    base = (tmp_path / "evalsuite" / "baseline.json").read_bytes()
    monkeypatch.setattr(ce, "MAX_DROP", 0.5)
    monkeypatch.setattr(ce, "scripted_reply", lambda s: "sorry" if s.id == "cha-en-1" else " ".join(s.expect))
    assert ce.cmd_evalsuite(a, None, tmp_path) == 0          # a small drop passes ...
    assert (tmp_path / "evalsuite" / "baseline.json").read_bytes() == base   # ... but does not lower the baseline


def test_exactly_max_drop_does_not_block_and_corrupt_baseline_is_ignored(tmp_path, capsys, monkeypatch):
    cur = dict(score=0.95, by_kind={"chat": 0.95}, rows=[])
    assert not ce.gate(cur, dict(score=1.0, by_kind={"chat": 1.0}))["block"]
    _on(monkeypatch)
    (tmp_path / "evalsuite").mkdir()
    (tmp_path / "evalsuite" / "baseline.json").write_text("{bad", encoding="utf-8")
    assert ce.cmd_evalsuite(SimpleNamespace(action="run", fake=True, beta=""), None, tmp_path) == 0
    assert "EVALSUITE_BAD_BASELINE" in capsys.readouterr().out


def test_per_kind_drop_with_equal_overall_does_not_lower_the_baseline():
    base = dict(score=0.95, by_kind={"device": 1.0, "chat": 0.9})
    assert not ce.keeps_baseline(dict(score=0.95, by_kind={"device": 0.96, "chat": 0.94}), base)
    assert ce.keeps_baseline(dict(score=0.96, by_kind={"device": 1.0, "chat": 0.92}), base)
    assert ce.keeps_baseline(base, None)


def test_wrong_shape_baseline_is_bad_and_bug_lists_only_regressed_kinds(tmp_path, capsys, monkeypatch):
    _on(monkeypatch)
    (tmp_path / "evalsuite").mkdir()
    for bad in ('{"x": 1}', '"s"', '{"score": 1, "by_kind": []}'):
        (tmp_path / "evalsuite" / "baseline.json").write_text(bad, encoding="utf-8")
        assert ce.cmd_evalsuite(SimpleNamespace(action="run", fake=True, beta=""), None, tmp_path) == 0
        assert "EVALSUITE_BAD_BASELINE" in capsys.readouterr().out
    rows = [dict(id="c1", kind="chat", score=0.5), dict(id="d1", kind="device", score=0.0)]
    g = ce.gate(dict(score=0.25, by_kind={"chat": 0.5, "device": 0.0}, rows=rows),
                dict(score=0.75, by_kind={"chat": 0.5, "device": 1.0}))
    assert g["block"] and g["bug"]["body"].endswith("Failing scenarios: d1")
