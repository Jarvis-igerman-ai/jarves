"""SW-V4 O (#17): the continuous health monitor. Real logging (logs.add_sink), real store.db in a tmp dir, a fixed
clock. Counts per code, numeric turn metrics, tool failures per conversation, component changes, the sampler,
1-minute -> hourly roll-ups with retention, no content ever stored, and the overhead budget."""
from __future__ import annotations

import time

import pytest

from jarves import logs
from jarves.config import Config
from jarves.diag import db
from jarves.diag.metrics import HOUR_KEEP_S, MINUTE_KEEP_S, MetricStore, code_counts, events, series
from jarves.diag.monitor import Monitor, config_snapshot, record_config
from jarves.health import Health
from jarves.store import Store

T0 = 1_792_500_000.0 // 3600 * 3600      # a fixed, hour-aligned clock


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "store.db")
    yield s
    s.close()


@pytest.fixture
def mon(store, tmp_path):
    clock = {"t": T0 + 10}
    m = Monitor(store, home=tmp_path, cfg=Config(), clock=lambda: clock["t"], version=("4.0.0", "abc"))
    m.clock_box = clock
    yield m
    if m._sink is not None:
        logs.remove_sink(m._sink)


def test_log_records_become_counts_and_turn_metrics(mon, store):
    mon.attach()
    try:
        logs.info("live", "LIVE_STARTED", "GPT-Live session started")
        logs.info("wake", "WAKE_WORD", "wake word heard", score=0.9)
        logs.info("live", "LIVE_FIRST_AUDIO", "first audio")
        logs.info("live", "LIVE_JARVES_SAID", "JARVES said", text="סוד אישי מאוד", wait_s=9.5)
        logs.warn("skills", "TOOL_RESULT", "calendar_list -> GOOGLE_API_ERROR", tool="calendar_list", ok=False, ms=840)
        logs.warn("live", "LIVE_RECONNECT", "reconnecting")
        logs.error("x", "SOME_ERROR", "boom")
        logs.info("live", "LIVE_STOPPED", "closed")
        logs.warn("skills", "TOOL_RESULT", "calendar_list -> GOOGLE_API_ERROR", tool="calendar_list", ok=False, ms=500)
    finally:
        logs.remove_sink(mon._sink)
        mon._sink = None
    mon.clock_box["t"] = T0 + 3600 + 5            # the next hour: the minute is complete and the hour rolls up
    mon.metrics.flush()
    counts = code_counts(store, T0 - 1, levels=("ERROR", "WARNING", "INFO"))
    assert sum(counts["TOOL_RESULT"].values()) == 2 and sum(counts["SOME_ERROR"].values()) == 1
    assert series(store, "turn_wait_s", T0 - 1) == [(int(T0), 9.5)]
    assert series(store, "dead_air_s", T0 - 1)[0][1] == 9.5            # above history.dead_air_s
    assert series(store, "tool_ms", T0 - 1)[0][1] == pytest.approx(670)
    assert series(store, "wake_to_first_audio_s", T0 - 1)
    fails = events(store, "tool_fail", T0 - 1)
    assert [e["name"] for e in fails] == ["calendar_list", "calendar_list"]
    assert fails[0]["ref"] != fails[1]["ref"]                           # two different conversations
    blob = " ".join(str(r) for t in ("diag_metrics", "diag_codes", "diag_events")
                    for r in db.rows(store, f"SELECT * FROM {t}"))
    assert "סוד" not in blob and "reconnecting" not in blob             # never message text or content
    assert all(r["app_version"] == "4.0.0" and r["git_commit"] == "abc" for r in db.rows(store, "SELECT * FROM diag_codes"))


def test_component_changes_are_events_only_on_change(mon, store):
    h = Health()
    mon.attach(h)
    try:
        h.set("mic", "ok", "fine")
        h.set("mic", "ok", "fine")                     # same state: no event
        h.set("mic", "degraded", "no input")
        h.set("ai", "ok", "key")
    finally:
        logs.remove_sink(mon._sink)
        mon._sink = None
    mon.metrics.flush(all_=True)
    ev = [(e["name"], e["detail"]) for e in events(store, "component", 0)]
    assert ev == [("mic", "->ok"), ("mic", "ok->degraded"), ("ai", "->ok")]


def test_sampler_and_overhead_budget(mon, store, tmp_path):
    (tmp_path / "f.bin").write_bytes(b"x" * 100_000)
    for _ in range(3):
        d = mon.sample(loop_lag_s=0.002)
        mon.clock_box["t"] += 60
    assert d["ram_mb"] > 1 and d["threads"] >= 1 and d["disk_state_mb"] >= 0.1 and "cpu_pct" in d
    assert d["loop_lag_ms"] == pytest.approx(2.0)
    # event-driven cost: 2000 records in one minute (far more than real use), measured
    t = time.thread_time()
    for i in range(2000):
        mon.on_record("TOOL_RESULT" if i % 3 else "WAKE_WORD", "INFO", "x", {"ok": True, "ms": 5}, mon.clock_box["t"])
    per_record = (time.thread_time() - t) / 2000
    sample_cost = mon.own_cpu_s / max(mon.samples, 1)
    # budget: <= 0.1% CPU at ~100 records/min + one sample per minute
    assert (per_record * 100 + sample_cost) / 60 <= 0.001, (per_record, sample_cost)
    ov = mon.overhead(wall_s=3600)
    assert ov["buffer_mb"] <= 5 and ov["ok"]
    assert len(mon.metrics._c) <= 4                                    # bounded by distinct codes, not records


def test_rollup_and_retention(store):
    clock = {"t": T0}
    ms = MetricStore(store, clock=lambda: clock["t"], version=("4.0.0", ""))
    old = T0 - MINUTE_KEEP_S - 3600
    ms.add("cpu_pct", 1.0, old)
    ms.add("cpu_pct", 3.0, old + 60)
    ms.count("X_CODE", "WARNING", "c", old, 4)
    ms.flush(all_=True)
    assert series(store, "cpu_pct", old - 1, res="h")[0][1] == pytest.approx(2.0)     # hourly mean of the minutes
    assert not series(store, "cpu_pct", 0, res="m")                                     # minutes older than 7 d pruned
    assert sum(code_counts(store, 0)["X_CODE"].values()) == 4                           # hourly kept 90 days
    clock["t"] = old + HOUR_KEEP_S + 7200
    ms.flush()
    assert not series(store, "cpu_pct", 0, res="h") and not code_counts(store, 0)


def test_config_snapshot_keeps_only_non_secret_values(store):
    c = Config()
    c.assistant_name = "Yossi Plantedman"
    snap = config_snapshot(c)
    blob = str(snap)
    assert "Yossi" not in blob
    assert snap["voice.wake_threshold"] == c.voice.wake_threshold and snap["voice.mode"] == c.voice.mode
    assert record_config(store, snap, T0) and not record_config(store, snap, T0 + 1)   # unchanged: not stored again
    c.voice.wake_threshold = 0.61
    assert record_config(store, config_snapshot(c), T0 + 2)


async def test_monitor_runs_inside_the_core(core):
    hs = core.ops.health_diag
    assert hs is not None and hs.monitor._sink is not None and hs.monitor._task is not None
    logs.warn("test", "MONITOR_PROBE", "probe")
    hs.monitor.metrics.flush(all_=True)
    assert sum(code_counts(hs.store, 0, res="m").get("MONITOR_PROBE", {}).values()) == 1
    assert {"health_summary", "health_ai_review_summary", "health_remediate"} <= set(core.registry._tools)
