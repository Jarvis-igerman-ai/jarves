"""SW-V4 M: the performance gate itself. Budgets are well formed, the gate passes on the baseline, FAILS on a slowed
build (synthetic numbers and a really slowed in-process path), and `main` returns the exit code CI relies on."""
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import perf_gate as pg  # noqa: E402

BUDGETS = pg.load_budgets()


def baseline():
    return {k: b["baseline"] for k, b in BUDGETS["metrics"].items()}


def test_budget_file_is_well_formed():
    assert BUDGETS["meta"]["scenario"] == "combined"
    for name, b in BUDGETS["metrics"].items():
        for key in ("baseline", "unit", "source", "owner_max", "ci_max", "note"):
            assert key in b, (name, key)
        assert b["source"] in ("bench", "inproc", "log"), name
        assert b["baseline"] <= b["owner_max"] <= b["ci_max"], name        # strict owner, loose CI
    assert BUDGETS["history"], "every budget change is recorded"


def test_every_bench_metric_is_really_produced_by_bench_startup():
    src = (TOOLS / "bench_startup.py").read_text(encoding="utf-8")
    for name, b in BUDGETS["metrics"].items():
        if b["source"] == "bench":
            assert f'"{name}"' in src, name


@pytest.mark.parametrize("cls", ["owner", "ci"])
def test_gate_passes_on_the_baseline(cls):
    assert pg.failures(pg.check(baseline(), BUDGETS, cls)) == []


@pytest.mark.parametrize("cls", ["owner", "ci"])
@pytest.mark.parametrize("name", sorted(BUDGETS["metrics"]))
def test_gate_fails_when_any_metric_is_slowed(name, cls):
    m = baseline()
    m[name] = BUDGETS["metrics"][name][f"{cls}_max"] * 2 + 1
    bad = pg.failures(pg.check(m, BUDGETS, cls))
    assert [r["metric"] for r in bad] == [name]


def test_metric_at_the_ceiling_passes_and_unmeasured_is_not_a_failure():
    m = {"threads": BUDGETS["metrics"]["threads"]["owner_max"]}
    rows = {r["metric"]: r["status"] for r in pg.check(m, BUDGETS, "owner")}
    assert rows["threads"] == "ok" and rows["private_mb"] == "n/a"


def test_main_exit_codes_and_results_file(tmp_path, capsys):
    good, slow, out = tmp_path / "good.json", tmp_path / "slow.json", tmp_path / "out.json"
    good.write_text(json.dumps(baseline()), encoding="utf-8")
    slow.write_text(json.dumps({**baseline(), "idle_cpu_pct": 9.0}), encoding="utf-8")
    assert pg.main(["--from-json", str(good), "--class", "ci", "--results", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["class"] == "ci"
    assert pg.main(["--from-json", str(slow), "--class", "ci"]) == 1
    assert "PERF GATE FAILED: idle_cpu_pct=9.0" in capsys.readouterr().out


def test_propose_prints_a_line_per_metric(capsys):
    assert pg.main(["--propose"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == len(BUDGETS["metrics"]) and all(ln.startswith("[metrics.") for ln in lines)


def test_class_defaults_to_ci_on_a_runner(tmp_path, monkeypatch):
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"idle_cpu_pct": 1.0}), encoding="utf-8")        # fine for ci (2.0), too much for owner (0.5)
    monkeypatch.setenv("CI", "true")
    assert pg.main(["--from-json", str(f)]) == 0
    monkeypatch.delenv("CI")
    assert pg.main(["--from-json", str(f)]) == 1


def test_inprocess_timings_pass_and_a_slowed_build_fails(home, monkeypatch):
    """The real measurement: the fixed code answers fast; making features_list block (the V3 F4 regression) fails."""
    import time

    from jarves import features as feat
    m = pg.measure_inproc(repeats=1, settle_s=0.4)
    assert pg.failures(pg.check(m, BUDGETS, "ci")) == [], m
    assert m["dashboard_first_load_ms"] > 0
    real = feat.Features.list

    def slowed(self):
        time.sleep(0.4)
        return real(self)
    monkeypatch.setattr(feat.Features, "list", slowed)
    slow = pg.measure_inproc(repeats=1, settle_s=0.4)
    assert "features_list_ms" in [r["metric"] for r in pg.failures(pg.check(slow, BUDGETS, "owner"))]
    assert "features_list_ms" in [r["metric"] for r in pg.failures(pg.check(slow, BUDGETS, "ci"))]


def test_log_stage_budgets_read_a_real_style_log(tmp_path):
    log = tmp_path / "jarves.log"
    rows = [("CORE_STARTING", "2026-10-19T14:13:00.000Z"), ("CORE_READY", "2026-10-19T14:13:01.800Z"),
            ("LIVE_STARTED", "2026-10-19T14:13:08.600Z"), ("LIVE_FIRST_AUDIO", "2026-10-19T14:13:10.300Z")]
    log.write_text("\n".join(json.dumps({"ts": t, "code": c}) for c, t in rows), encoding="utf-8")
    m = pg.measure_log(log)
    assert m == {"live_started_s": 8.6, "first_audio_s": 10.3}
    assert pg.failures(pg.check({**baseline(), **m}, BUDGETS, "owner")) == []
