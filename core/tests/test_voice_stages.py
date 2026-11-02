"""Hermetic checks for voice stage latency aggregation (issue #122, software part)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from jarves import selfspeed, voice_stages


def rec(total, ts=1000.0, **stages):
    d = {"code": "LIVE_LATENCY", "_ts": ts, "total_s": total}
    d.update({f"{k}_s": v for k, v in stages.items()})
    return d


def test_empty_is_honest():
    res = voice_stages.summarize([])
    assert res["samples"] == 0 and res["confidence"] == "low" and res["bottleneck"] is None
    assert res["real_device"] == "UNVERIFIED"
    assert "wake_to_asr" in res["unavailable"]
    assert any("UNVERIFIED" in x for x in voice_stages.render(res))


def test_bottleneck_median_p95_and_confidence():
    recs = [rec(10.0 + i, to_delegation=1.0, backend=8.0 + i, backend_to_audio=0.5) for i in range(6)]
    res = voice_stages.summarize(recs)
    assert res["samples"] == 6 and res["confidence"] == "medium"
    assert res["bottleneck"] == "backend"
    assert res["stages"]["backend"]["p95_s"] == 13.0 and res["stages"]["to_delegation"]["median_s"] == 1.0


def test_few_samples_give_no_verdict():
    res = voice_stages.summarize([rec(3.0, model_direct=3.0)] * 4)
    assert res["bottleneck"] is None and res["confidence"] == "low"


def test_ignores_other_codes_old_and_corrupt_values():
    recs = [{"code": "OTHER", "total_s": 1.0}, rec(2.0, ts=1.0, model_direct=2.0),
            rec(float("nan"), model_direct=1.0), rec(True), rec(-1.0), rec(99999.0), rec(2.0, model_direct="x")]
    res = voice_stages.summarize(recs, since=500.0)
    assert res["samples"] == 1 and res["stages"] == {}


def test_run_reads_log_and_never_exposes_text(tmp_path):
    (tmp_path / "logs").mkdir()
    line = {"ts": "2026-11-02T10:00:00+00:00", "code": "LIVE_LATENCY", "total_s": 2.5, "model_direct_s": 2.5,
            "slowest": "model_direct", "text": "SECRET TRANSCRIPT"}
    (tmp_path / "logs" / "jarves.log").write_text(json.dumps(line) + "\n", encoding="utf-8")
    res = voice_stages.run(tmp_path, now=1793620000.0)
    assert res["samples"] == 1 and "SECRET" not in json.dumps(res)


def test_cli_voice_stages_flag(tmp_path):
    out = []
    a = SimpleNamespace(auto_github=None, json=True, voice_stages=True)
    assert selfspeed.cmd_performance(a, None, tmp_path, out=out.append) == 0
    assert json.loads(out[0])["voice_stages"]["samples"] == 0
    out.clear()
    a = SimpleNamespace(auto_github=None, json=False, voice_stages=True)
    selfspeed.cmd_performance(a, None, tmp_path, out=out.append)
    assert any("UNVERIFIED" in x for x in out)
