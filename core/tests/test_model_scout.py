"""JAR-233: monthly best-in-class scout. Hermetic: a fake fetch, a fixed date, no network."""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace as NS

from jarves import model_scout as ms

PAGES = {
    ms.WATCH[0].url: "gpt-live-1 ... gpt-live-2 ... gpt-realtime-2.1 gpt-realtime-2.1-mini gpt-realtime-3-mini",
    ms.WATCH[2].url: '{"info": {"name": "openai-agents", "version": "0.26.1"}}',
}
RECORDED = {"live_model": "gpt-live-1", "realtime_model": "gpt-realtime-2.1", "openai-agents": "0.24.0"}


def _fetch(calls):
    def f(url):
        calls.append(url)
        return PAGES[url]
    return f


def test_newer_found_mini_ignored_and_only_public_urls_fetched(tmp_path):
    calls = []
    out = ms.scout(tmp_path, RECORDED, _fetch(calls), today=date(2026, 11, 12))
    got = {(c["component"], c["candidate"]) for c in out["cards"]}
    assert got == {("live_model", "gpt-live-2"), ("openai-agents", "openai-agents 0.26.1")}
    assert set(calls) <= {w.url for w in ms.WATCH} and all("?" not in u for u in calls)
    card = out["cards"][0]
    assert card["measured"] is False and card["checked"] == "2026-11-12" and card["source"].startswith("https://")
    assert card["decision"] == "owner" and "modellab" in card["next"]
    saved = json.loads((tmp_path / "scout" / "2026-11.json").read_text(encoding="utf-8"))
    assert saved["cards"] == out["cards"]


def test_measured_reason_comes_from_the_model_lab_report(tmp_path):
    (tmp_path / "modellab").mkdir()
    rep = {"summary": [{"model": "gpt-live-2", "quality": 0.9, "avg_latency_s": 0.4, "cost_usd": 0.01}]}
    (tmp_path / "modellab" / "20261101-000000.json").write_text(json.dumps(rep), encoding="utf-8")
    out = ms.scout(tmp_path, RECORDED, _fetch([]), today=date(2026, 11, 12))
    card = next(c for c in out["cards"] if c["candidate"] == "gpt-live-2")
    assert card["measured"] is True
    assert card["reason"] == {"quality": 0.9, "avg_latency_s": 0.4, "cost_usd": 0.01}


def test_a_failing_source_is_reported_not_invented(tmp_path):
    def bad(url):
        raise OSError("down")
    out = ms.scout(tmp_path, RECORDED, bad, today=date(2026, 11, 12))
    assert out["cards"] == [] and len(out["errors"]) == len(ms.WATCH)


def test_due_monthly(tmp_path):
    assert ms.status(tmp_path, today=date(2026, 11, 12))["due"] is True
    ms.scout(tmp_path, RECORDED, _fetch([]), today=date(2026, 11, 1))
    assert ms.status(tmp_path, today=date(2026, 11, 12))["due"] is False
    assert ms.status(tmp_path, today=date(2026, 12, 1))["due"] is True


def test_feature_is_beta_and_off():
    from jarves import features as feat
    assert feat.stage_of(ms.FEATURE) == "beta" and feat.CATALOG[ms.FEATURE].default_enabled is False


def test_cli_run_refused_while_off_without_any_fetch(tmp_path, capsys, monkeypatch):
    import jarves.cli as cli
    calls = []
    monkeypatch.setattr(ms, "_live_fetch", _fetch(calls))
    monkeypatch.setattr(cli, "_cli_features", lambda cfg, home: NS(active=lambda _: False))
    assert ms.cmd_scout(NS(action="run"), None, tmp_path) == 1
    assert "SCOUT_OFF" in capsys.readouterr().out and calls == []
    assert ms.cmd_scout(NS(action="status"), None, tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["due"] is True
