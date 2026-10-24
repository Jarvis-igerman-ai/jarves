"""V4 perf fix (perf gate: features_list_ms 357 ms > 150 ms after the V4 packages merged): with EVERY feature module
registered (as in the Core), a slow probe never runs on the caller's thread, features_list stays under the gate's
budget, stale results are labelled (check / checked_s_ago) and the background re-check really updates them.

The slow probe here runs a fake `claude` CLI that sleeps 2 s, found on PATH (the shape of a probe that would call
`claude agents --json`); the real agent_orchestrator probe sees the same fake binary."""
import importlib
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import perf_gate as pg  # noqa: E402

from jarves import features as feat  # noqa: E402

# every module that registers features (the Core imports all of them)
FEATURE_MODULES = ("appearance", "backup", "briefing", "browser", "connect", "context_trace", "guides",
                   "homeassistant", "diag.service", "instagram_channel", "operations", "profiles", "recall", "runtime",
                   "selfcheck", "orchestrator", "skills.generated", "telegram_channel", "updates", "usage",
                   "skills.helper", "whatsapp", "whatsapp_bridge")
BUDGET_MS = pg.load_budgets()["metrics"]["features_list_ms"]["ci_max"]
FID = "zz_perf_slow_cli"


def _import_all() -> None:
    for m in FEATURE_MODULES:
        importlib.import_module("jarves." + m)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A `claude` on PATH that takes 2 s; it answers ok while <bin>/ok exists, else exits 1."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "fake_claude.py"
    script.write_text("import os, sys, time\ntime.sleep(2)\nsys.exit(0 if os.path.exists(os.path.join("
                      f"{str(bindir)!r}, 'ok')) else 1)\n", encoding="utf-8")
    if sys.platform == "win32":
        (bindir / "claude.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        sh = bindir / "claude"
        sh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        sh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    (bindir / "ok").write_text("", encoding="utf-8")
    return bindir


@pytest.fixture
def slow_cli_feature(fake_claude):
    """A feature whose probe RUNS the fake claude (2 s), declared slow; records the threads it ran on."""
    calls: list[int] = []

    def probe(_c):
        calls.append(threading.get_ident())
        exe = shutil.which("claude")
        r = subprocess.run([exe, "agents", "--json"], capture_output=True, timeout=20)
        return (True, "") if r.returncode == 0 else (False, "claude agents failed")
    feat.register(FID, "בדיקה", "Perf test CLI", True, probe=probe, slow=True)
    try:
        yield calls
    finally:
        feat.CATALOG.pop(FID, None)
        feat.SLOW_PROBES.discard(FID)


def _timed_list(f):
    t = time.perf_counter()
    rows = {r["id"]: r for r in f.list()}
    return (time.perf_counter() - t) * 1000, rows


def _wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _expire(f):
    for k, (t, ok, why) in list(f._probe_cache.items()):
        f._probe_cache[k] = (t - feat.PROBE_TTL_S - 1, ok, why)


def _prime_process(cfg, home) -> None:
    """Run every probe except the slow CLI one once, on a throwaway Features. The FIRST-EVER call of some probes does
    one-off work in line (lazy imports, an archive open: measured whatsapp_archive 293 ms, tray_icon 146 ms), and the
    list budget is only checked BETWEEN probes, so one such probe overshoots it. Whether it is first-ever depends on
    which tests ran before it in the shard, which is what made this test flaky (#163). This test is about the steady
    state; that one-off cost belongs to the process, not to the stale-while-revalidate logic."""
    warm = feat.Features(cfg, home, components=lambda: {}, skills=lambda: set())
    for fid in list(feat.CATALOG):
        if fid != FID:
            warm._run_probe(fid)


def test_slow_cli_probe_never_blocks_features_list_and_its_result_lands(home, cfg, fake_claude, slow_cli_feature):
    _import_all()
    _prime_process(cfg, home)
    calls = slow_cli_feature
    f = feat.Features(cfg, home, components=lambda: {}, skills=lambda: set())
    ms, rows = _timed_list(f)                                  # cold: nothing checked yet
    assert ms < BUDGET_MS, ms
    r = rows[FID]
    assert (r["available"], r["check"], r["health"], r["reason"]) == (False, "checking", "checking",
                                                                    feat.CHECKING_REASON)
    assert r["needs_attention"] == bool(r["matrix_problems"])  # "checking" itself is not a problem to report
    assert _wait(lambda: FID in f._probe_cache), "the background check never finished"
    assert threading.get_ident() not in calls                  # the 2 s subprocess ran off the caller's thread
    ms, rows = _timed_list(f)
    assert rows[FID]["available"] is True and rows[FID]["check"] == "fresh"
    assert _wait(lambda: "agent_orchestrator" in f._probe_cache)
    assert f.available("agent_orchestrator") == (True, "")     # the fake claude on PATH is found

    # stale-path timing: median of 4 (3 while claude still works, then the one after it breaks), the gate's own
    # statistic; one scheduler stall on a shared runner (168 ms once) must not fail it, a real regression moves all
    samples = []
    for _ in range(3):
        _expire(f)
        samples.append(_timed_list(f)[0])
        assert _wait(lambda: not f._pending)                   # let the background re-check land before the next
    (fake_claude / "ok").unlink()                              # claude breaks; 15 s pass
    _expire(f)
    ms, rows = _timed_list(f)
    samples.append(ms)
    assert statistics.median(samples) < BUDGET_MS, samples     # answered from the last result...
    r = rows[FID]
    assert r["available"] is True and r["check"] == "stale" and r["checked_s_ago"] >= feat.PROBE_TTL_S
    assert _wait(lambda: f._probe_cache[FID][1] is False)      # ...and the re-check lands
    ms, rows = _timed_list(f)
    assert (rows[FID]["available"], rows[FID]["check"], rows[FID]["reason"]) == (False, "fresh",
                                                                               "claude agents failed")
    assert threading.get_ident() not in calls and len(calls) == 5   # cold, 3 stale samples, broken-claude


def test_an_undeclared_probe_measured_slow_is_rechecked_in_the_background(home, cfg):
    state = {"ok": False, "threads": []}

    def probe(_c):
        state["threads"].append(threading.get_ident())
        time.sleep(0.3)
        return state["ok"], "" if state["ok"] else "not yet"
    feat.register("zz_perf_undeclared", "בדיקה", "Undeclared slow", True, probe=probe)
    try:
        f = feat.Features(cfg, home, components=lambda: {}, skills=lambda: set())
        assert f.available("zz_perf_undeclared") == (False, "not yet")   # first ever: in line, as before
        assert f.is_slow("zz_perf_undeclared")                            # ...and now known to be slow
        state["ok"] = True
        _expire(f)
        t = time.perf_counter()
        assert f.list() and (time.perf_counter() - t) < 0.3               # stale answer, no 0.3 s wait
        assert _wait(lambda: f._probe_cache["zz_perf_undeclared"][1] is True)
        assert state["threads"][0] == threading.get_ident() != state["threads"][1]
    finally:
        feat.CATALOG.pop("zz_perf_undeclared", None)


def test_one_shot_cli_list_checks_everything_in_line(home, cfg, fake_claude, slow_cli_feature):
    f = feat.Features(cfg, home, components=lambda: {}, skills=lambda: set())
    rows = {r["id"]: r for r in f.list(wait=True)}            # `jarvis features` prints once and exits
    assert rows[FID]["available"] is True and rows[FID]["check"] == "fresh"
    assert slow_cli_feature == [threading.get_ident()]


def test_perf_gate_inprocess_with_every_v4_module_registered(home, monkeypatch):
    """The gate's own measurement in the full-suite condition (all packages registered), where it measured 357 ms."""
    from jarves import speedtest
    _import_all()
    # This regression measures feature-probe latency, not dashboard HTTP startup under xdist contention.
    # test_perf_gate.py and the real CI perf job still exercise the dashboard measurement and its budgets.
    monkeypatch.setattr(speedtest, "measure_dashboard", lambda home: {})
    # median of 3 (the real gate's statistic): one scheduler hiccup on a loaded Windows runner (178 ms vs the 150 ms
    # budget, once) must not fail it, while a real regression (357 ms before) moves the median and still does.
    m = pg.measure_inproc(repeats=3, settle_s=0.4)
    assert m["features_list_ms"] < BUDGET_MS, m
    assert m["live_tools_probe_ms"] < pg.load_budgets()["metrics"]["live_tools_probe_ms"]["ci_max"], m


def test_prewarm_fills_every_probe_off_the_caller_thread(home, cfg):
    _import_all()
    f = feat.Features(cfg, home, components=lambda: {}, skills=lambda: set())
    f.prewarm()
    assert _wait(lambda: all(fid in f._probe_cache for fid in feat.CATALOG) and not f._pending)
    rows = f.list()
    assert all(r["check"] == "fresh" for r in rows), [r["id"] for r in rows if r["check"] != "fresh"]
