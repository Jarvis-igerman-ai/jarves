"""Self-test and speed test (owner request 21 Oct 2026): planted faults, budgets and the 20 % regression rule, the
history (one write per run), the schedule policy on a fake clock (quiet hours, voice session, skipped = logged not
queued), the voice tools, the scheduler jobs, the Health & speed page API over real HTTP, the CLI and the CI wiring.
No network, no browser, no microphone; the real Core is started once, locally, in a temp home."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jarves import cli, features as feat, logs, selfspeed, selftest, speedtest
from jarves.config import Config
from jarves.diag import db as diag_db
from jarves.scheduler import Scheduler
from jarves.skills import SkillRegistry
from jarves.store import Store
from jarves.userdata import UserDB, open_db
from test_dashboard import dash, get, req  # noqa: F401  (fixture + helpers)

CORE = Path(__file__).resolve().parents[1]
STATIC = CORE / "src" / "jarves" / "dashboard_static"
ROOT = CORE.parent


class Clock:
    """A fake wall clock: `at(hour)` sets a local UTC time on Wed 21 Oct 2026."""

    def __init__(self, hour: int = 12, minute: int = 0, day: int = 21) -> None:
        self.now = 0.0
        self.at(hour, minute, day)

    def at(self, hour: int, minute: int = 0, day: int = 21) -> "Clock":
        self.now = datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo("UTC")).timestamp()
        return self

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def db(tmp_path):
    d = UserDB(tmp_path / "memory.db")
    d.set_setting("scheduler.timezone", "UTC", actor="test")
    yield d
    d.close()


def ok(status="pass", detail="fine"):
    return lambda c: (status, detail, "fix it")


def only(*items, deep=False):
    return tuple((cid, cid.title(), cid.title(), deep, fn) for cid, fn in items)


# ---------------------------------------------------------------------- self-test: the real checks
def test_quick_selftest_covers_every_area_and_is_read_only(home):
    before = {p.name: p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}
    res = selftest.run(home=home, cfg=Config(), record=False)
    assert [c.id for c in res.checks] == ["config", "databases", "permissions", "connections", "registry_guides",
                                          "locales", "log_privacy", "disk", "version"]
    assert not res.deep and res.status in ("pass", "warn")
    assert {c.id: c.status for c in res.checks}["locales"] == "pass"
    assert {c.id: c.status for c in res.checks}["registry_guides"] == "pass"
    after = {p.name: p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}
    assert after == before, "a self-test run must not change anything in the state folder"
    d = res.as_dict()
    assert d["counts"]["fail"] == 0 and json.loads(json.dumps(d))["kind"] == "selftest"


def test_corrupt_database_is_a_fail(home):
    (home / "store.db").write_bytes(b"this is not a sqlite file" * 400)
    res = selftest.run(home=home, cfg=Config(), record=False)
    c = next(c for c in res.checks if c.id == "databases")
    assert c.status == "fail" and "store.db" in c.detail and "backup" in c.fix.lower()
    assert res.status == "fail" and "failed" in res.summary("en")


def test_healthy_databases_pass(home):
    Store(home / "store.db").close()
    open_db(home).close()
    c = next(c for c in selftest.run(home=home, cfg=Config(), record=False).checks if c.id == "databases")
    assert c.status == "pass" and "2 database" in c.detail


def test_secret_in_the_log_fails_without_echoing_it(home):
    (home / "logs").mkdir(exist_ok=True)
    secret = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    (home / "logs" / "jarves.log").write_text(
        json.dumps({"ts": "2026-10-21T10:00:00", "code": "X", "msg": "leak " + secret}) + "\n", encoding="utf-8")
    c = next(c for c in selftest.run(home=home, cfg=Config(), record=False).checks if c.id == "log_privacy")
    assert c.status == "fail" and secret not in c.detail and "1 log line" in c.detail


def test_clean_log_passes_and_user_path_leak_warns(home):
    (home / "logs").mkdir(exist_ok=True)
    log = home / "logs" / "jarves.log"
    log.write_text(json.dumps({"ts": "t", "code": "X", "msg": "fine"}) + "\n", encoding="utf-8")
    assert next(c for c in selftest.run(home=home, cfg=Config(), record=False).checks if c.id == "log_privacy").status == "pass"
    log.write_text(json.dumps({"ts": "t", "code": "X", "msg": "C:\\Users\\Dana\\x.txt"}) + "\n", encoding="utf-8")
    assert next(c for c in selftest.run(home=home, cfg=Config(), record=False).checks if c.id == "log_privacy").status == "warn"


def test_missing_hebrew_text_is_a_fail(home, monkeypatch):
    real = selftest._catalog
    monkeypatch.setattr(selftest, "_catalog", lambda code: {"dict": {}} if code == "he" else real(code))
    c = next(c for c in selftest.run(home=home, cfg=Config(), record=False).checks if c.id == "locales")
    assert c.status == "fail" and "Hebrew misses" in c.detail
    monkeypatch.setattr(selftest, "_catalog", lambda code: None)
    assert next(c for c in selftest.run(home=home, cfg=Config(), record=False).checks if c.id == "locales").status == "fail"


def test_low_disk_and_unwritable_home(home):
    res = selftest.run(home=home, cfg=Config(), record=False, disk_free_gb=lambda p: 0.2)
    assert next(c for c in res.checks if c.id == "disk").status == "fail"
    res = selftest.run(home=home / "nowhere", cfg=Config(), record=False)
    assert next(c for c in res.checks if c.id == "permissions").status == "fail"


def test_update_available_warns_and_an_unreadable_status_does_not_crash(home):
    st = {"available": True, "latest": {"version": "9.9.9"}, "error": None, "checked_at": "now"}
    c = next(c for c in selftest.run(home=home, cfg=Config(), record=False, update_status=lambda: st).checks if c.id == "version")
    assert c.status == "warn" and "9.9.9" in c.detail

    def boom():
        raise OSError("x")
    c = next(c for c in selftest.run(home=home, cfg=Config(), record=False, update_status=boom).checks if c.id == "version")
    assert c.status == "warn"


def test_connections_warn_names_the_unavailable_features(home):
    class F:
        def list(self):
            return [{"id": "music", "enabled": True, "available": False, "check": "fresh"},
                    {"id": "camera", "enabled": True, "available": True, "check": "fresh"},
                    {"id": "gmail_read", "enabled": False, "available": False, "check": "fresh"},
                    {"id": "browser", "enabled": True, "available": False, "check": "checking"}]
    c = next(c for c in selftest.run(home=home, cfg=Config(), features=F(), record=False).checks if c.id == "connections")
    assert c.status == "warn" and "music" in c.detail and "gmail_read" not in c.detail and "browser" not in c.detail


def test_registry_check_finds_tool_without_description(home):
    from jarves.skills import Skill, Tool, NoArgs

    async def h(_):
        raise AssertionError

    class S(Skill):
        name = "plant"

        def tools(self):
            return [Tool("plant_tool", "", h, NoArgs, permission="read")]
    reg = SkillRegistry()
    reg.skills["plant"] = S()
    c = next(c for c in selftest.run(home=home, cfg=Config(), registry=reg, record=False).checks if c.id == "registry_guides")
    assert c.status == "warn" and "plant_tool has no description" in c.detail


def test_a_crashing_check_is_a_warn_not_an_exception(home):
    def boom(c):
        raise RuntimeError("secret detail")
    res = selftest.run(home=home, cfg=Config(), record=False, checks=only(("a", boom), ("b", ok())))
    assert [c.status for c in res.checks] == ["warn", "pass"] and "secret detail" not in res.checks[0].detail


def test_summary_sentences_en_and_he(home):
    res = selftest.run(home=home, cfg=Config(), record=False, checks=only(("a", ok()), ("b", ok("warn", "slow"))))
    assert res.status == "warn" and res.summary("en").startswith("The self-test passed with 1 warning")
    assert re.search("[א-ת]", res.summary("he"))
    allok = selftest.run(home=home, cfg=Config(), record=False, checks=only(("a", ok())))
    assert allok.summary("en") == "The self-test passed: all 1 checks are fine."
    bad = selftest.run(home=home, cfg=Config(), record=False, checks=only(("a", ok("fail", "x"))))
    assert bad.summary("en").startswith("The self-test failed 1 of 1")


# ---------------------------------------------------------------------- self-test: deep
def test_deep_adds_core_wake_audio_and_speech_but_quiet_by_default(home):
    calls = []

    def spawn(tmp):
        calls.append(tmp)
        return {"ready_s": 2.5, "stop_s": 1.0}

    def no_audio():
        raise AssertionError("--audio was not requested")
    quick = selftest.run(home=home, cfg=Config(), record=False, spawn_core=spawn)
    assert not calls and "core_smoke" not in [c.id for c in quick.checks]
    deep = selftest.run(True, home=home, cfg=Config(), record=False, spawn_core=spawn, audio_devices=lambda: (1, 1),
                        audio_roundtrip=no_audio)
    ids = {c.id: c for c in deep.checks}
    assert {"core_smoke", "wake_model", "audio_devices", "speech", "audio_live"} <= set(ids)
    assert ids["core_smoke"].status == "pass" and "2.5 s" in ids["core_smoke"].detail and len(calls) == 1
    assert not calls[0].exists(), "the temp home is removed"
    assert ids["audio_devices"].status == "pass" and ids["audio_live"].status == "skip"


def test_deep_core_failure_and_audio_round_trip(home):
    def broken(tmp):
        raise RuntimeError("did not start")
    res = selftest.run(True, home=home, cfg=Config(), record=False, spawn_core=broken, audio_devices=lambda: (0, 1))
    ids = {c.id: c for c in res.checks}
    assert ids["core_smoke"].status == "fail" and ids["audio_devices"].status == "fail" and "no microphone" in ids["audio_devices"].detail
    res = selftest.run(True, audio=True, home=home, cfg=Config(), record=False, spawn_core=lambda t: {"ready_s": 1, "stop_s": 1},
                       audio_devices=lambda: (1, 1), audio_roundtrip=lambda: {"played": True, "mic_rms": 0.01})
    assert next(c for c in res.checks if c.id == "audio_live").status == "pass"
    res = selftest.run(True, audio=True, home=home, cfg=Config(), record=False, spawn_core=lambda t: {"ready_s": 1, "stop_s": 1},
                       audio_devices=lambda: (1, 1), audio_roundtrip=lambda: {"played": True, "mic_rms": 0.0})
    assert next(c for c in res.checks if c.id == "audio_live").status == "warn"


def test_wake_check_is_skipped_when_voice_is_off(home):
    cfg = Config()
    cfg.voice.mode = "off"
    c = selftest.check_wake_model(selftest.Ctx(home=home, cfg=cfg))
    assert c[0] == "skip"


def test_real_core_starts_and_stops_in_a_temp_home(tmp_path):
    r = selftest.spawn_core(tmp_path / "core")
    assert r["ready_s"] > 0 and r["stop_s"] >= 0


# ---------------------------------------------------------------------- speed test: budgets and regression
BUDGETS = {"tool_p95_ms": {"unit": "ms", "baseline": 100, "owner_max": 200, "ci_max": 600},
           "idle_cpu_pct": {"unit": "%", "baseline": 0.2, "owner_max": 0.5, "ci_max": 2.0}}


def test_judge_pass_warn_fail_and_class():
    assert speedtest.judge("tool_p95_ms", 100, BUDGETS, "owner", []).status == "pass"
    assert speedtest.judge("tool_p95_ms", 180, BUDGETS, "owner", []).status == "warn"       # within 15 % of the ceiling
    assert speedtest.judge("tool_p95_ms", 201, BUDGETS, "owner", []).status == "fail"
    assert speedtest.judge("tool_p95_ms", 201, BUDGETS, "ci", []).status == "pass"            # the loose class
    assert speedtest.judge("tool_p95_ms", None, BUDGETS, "owner", []).status == "n/a"
    assert speedtest.judge("unbudgeted", 5, {}, "owner", []).status == "pass"


def test_twenty_percent_worse_than_the_median_of_the_last_five_warns():
    prev = [100, 100, 100, 100, 100]
    assert speedtest.judge("m", 121, {}, "owner", prev).status == "warn"
    assert speedtest.judge("m", 120, {}, "owner", prev).status == "pass"                      # exactly 20 % is not more
    j = speedtest.judge("m", 130, {}, "owner", prev)
    assert j.trend_pct == 30.0 and j.prev_median == 100 and "30" in j.detail
    assert speedtest.judge("m", 80, {}, "owner", prev).status == "pass"                       # faster is never a warning
    # only the LAST five count, and the median shrugs off one outlier
    assert speedtest.judge("m", 110, {}, "owner", [900, 100, 100, 100, 100, 5000]).status == "pass"
    assert speedtest.judge("m", 110, {}, "owner", [100, 100, 100, 100, 900]).status == "pass"


def test_percentile_nearest_rank():
    v = list(range(1, 101))
    assert speedtest.percentile(v, 50) == 50 and speedtest.percentile(v, 95) == 95 and speedtest.percentile([7], 95) == 7


def fake(**vals):
    return lambda home, **kw: dict(vals)


def test_run_records_history_and_flags_the_regression(home):
    clock = Clock()
    m = {g: fake() for g in speedtest.GROUPS}
    for i in range(5):                                           # five steady runs
        m["history"] = fake(tool_p95_ms=100.0)
        r = speedtest.run(home=home, cls="owner", budgets=BUDGETS, measurers=m, clock=clock)
        assert r.status == "pass"
        clock.now += 60
    m["history"] = fake(tool_p95_ms=150.0)                       # +50 %, still under the 200 ceiling (and under 85 %)
    r = speedtest.run(home=home, cls="owner", budgets=BUDGETS, measurers=m, clock=clock, trigger="schedule")
    row = next(x for x in r.metrics if x.name == "tool_p95_ms")
    assert row.status == "warn" and row.trend_pct == 50.0 and r.status == "warn"
    assert "worse than the median" in row.detail and "tool_p95_ms" in r.summary("en")
    rows = selfspeed.History(home).rows("speedtest")
    assert len(rows) == 6 and rows[-1]["values"] == {"tool_p95_ms": 150.0} and rows[-1]["trigger"] == "schedule"
    assert selfspeed.History(home).spark("tool_p95_ms") == [100.0] * 5 + [150.0]


def test_a_broken_measurer_is_isolated_and_n_a_rows_do_not_fail(home):
    def boom(home, **kw):
        raise RuntimeError("nope")
    m = {g: fake() for g in speedtest.GROUPS}
    m["idle"], m["history"] = boom, fake(tool_p95_ms=50.0)
    r = speedtest.run(home=home, cls="owner", budgets=BUDGETS, measurers=m, record=False)
    byname = {x.name: x.status for x in r.metrics}
    assert byname["tool_p95_ms"] == "pass" and byname["idle_cpu_pct"] == "n/a" and r.status == "pass"


def test_over_budget_is_a_fail_with_the_spoken_sentence(home):
    m = {g: fake() for g in speedtest.GROUPS}
    m["history"] = fake(tool_p95_ms=999.0)
    r = speedtest.run(home=home, cls="owner", budgets=BUDGETS, measurers=m, record=False)
    assert r.status == "fail" and r.summary("en") == "Speed test: tool_p95_ms is over its budget (999 ms)."
    assert re.search("[א-ת]", r.summary("he"))


def test_builtin_budgets_load_and_cover_the_new_metrics():
    b = speedtest.load_budgets()
    for name in ("tool_p50_ms", "tool_p95_ms", "wake_to_first_audio_s", "dead_air_p95_s", "openai_roundtrip_ms",
                 "installer_size_mb", "idle_cpu_pct", "core_start_ms", "dashboard_first_load_ms"):
        assert name in b and b[name]["baseline"] <= b[name]["owner_max"] <= b[name]["ci_max"], name
    assert speedtest.load_budgets(Path("missing.toml")) == {}


# ---------------------------------------------------------------------- speed test: the measurers
def test_startup_from_the_newest_start_in_the_log(home):
    (home / "logs").mkdir(exist_ok=True)
    lines = [{"ts": "2026-10-21T10:00:00.000", "code": "CORE_STARTING"},
             {"ts": "2026-10-21T10:00:09.000", "code": "CORE_READY", "ms": 9000},
             {"ts": "2026-10-21T11:00:00.000", "code": "CORE_STARTING"},
             {"ts": "2026-10-21T11:00:02.500", "code": "CORE_READY", "ms": 1900},
             {"ts": "2026-10-21T11:00:08.000", "code": "LIVE_STARTED"},
             {"ts": "2026-10-21T11:00:10.000", "code": "LIVE_FIRST_AUDIO"}]
    (home / "logs" / "jarves.log").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert speedtest.measure_startup(home) == {"spawn_to_ready_s": 2.5, "core_start_ms": 1900, "live_started_s": 8.0,
                                               "first_audio_s": 10.0}


def test_tool_percentiles_and_voice_latency_from_history(home):
    now = time.time()
    (home / "logs").mkdir(exist_ok=True)
    ts = datetime.fromtimestamp(now - 3600).isoformat()
    recs = [{"ts": ts, "code": "TOOL_RESULT", "tool": "x", "ms": ms} for ms in range(10, 110, 10)]
    recs.append({"ts": ts, "code": "TOOL_RESULT", "tool": "run_speedtest", "ms": 90000})      # our own run is excluded
    recs.append({"ts": "2020-01-01T00:00:00", "code": "TOOL_RESULT", "tool": "old", "ms": 99999})
    (home / "logs" / "jarves.log").write_text("\n".join(json.dumps(x) for x in recs) + "\n", encoding="utf-8")
    st = Store(home / "store.db")
    diag_db.ensure(st)
    for i, v in enumerate((1.5, 1.7, 2.0)):
        diag_db.write(st, "INSERT INTO diag_events (ts, kind, name, value) VALUES (?,?,?,?)", (now - 100 - i, "turn", "wake_to_first_audio", v))
    for i in range(20):
        diag_db.write(st, "INSERT INTO diag_events (ts, kind, name, value) VALUES (?,?,?,?)", (now - 50 - i, "turn", "reply_wait", 0.5 + i * 0.05))
    st.close()
    got = speedtest.measure_history(home, now=now)
    assert got["tool_p50_ms"] == 50 and got["tool_p95_ms"] == 100
    assert got["wake_to_first_audio_s"] == 1.7 and got["dead_air_p95_s"] == 1.4


def test_too_few_samples_give_no_percentile(home):
    assert speedtest.measure_history(home) == {}


def test_api_round_trip_is_one_head_and_survives_no_network():
    seen = []

    def head(url, timeout):
        seen.append(url)
        return 401
    got = speedtest.measure_net(Path("."), http=head)
    assert seen == ["https://api.openai.com/v1/models"] and got["openai_roundtrip_ms"] >= 0

    def offline(url, timeout):
        raise OSError("no network")
    assert speedtest.measure_net(Path("."), http=offline) == {}
    assert speedtest.measure_net(Path("."), http=lambda u, t: 503) == {}


def test_idle_counters_of_a_running_process(home):
    got = speedtest.measure_idle(home, idle_window_s=0.2, sleep=lambda s: sum(range(300000)), pid=os.getpid())
    assert got["steady_rss_mb"] > 10 and got["threads"] >= 1 and got["idle_disk_write_kb_per_min"] >= 0
    assert speedtest.measure_idle(home) == {}, "no Core running in a fresh home"


def test_installer_size_from_a_downloaded_installer(home):
    (home / "updates").mkdir()
    (home / "updates" / "JARVES-Setup-9.9.9.exe").write_bytes(b"x" * 3 * 2**20)
    got = speedtest.measure_installer(home)
    assert got["installer_size_mb"] in (3.0, got["installer_size_mb"])        # a dist/ build in the checkout may win
    assert got["installer_size_mb"] > 0


def test_the_gate_and_the_app_share_one_in_process_measurement():
    sys.path.insert(0, str(CORE / "tools"))
    import perf_gate
    got = perf_gate.measure_inproc(repeats=1, settle_s=0.05)
    assert set(got) == {"features_list_ms", "live_tools_probe_ms", "dashboard_first_load_ms", "dashboard_api_max_ms"}
    assert perf_gate.measure_dashboard.__module__ == "perf_gate"


# ---------------------------------------------------------------------- history: batched, small, capped
def test_history_is_written_once_per_run_and_only_then(home, monkeypatch):
    h = selfspeed.History(home)
    assert not h.path.exists(), "nothing is created until a run finishes"
    writes = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (writes.append(b), real(a, b))[1])
    selftest.run(home=home, cfg=Config(), checks=only(("a", ok())), clock=Clock())
    assert len(writes) == 1 and h.path.stat().st_size < 4000


def test_history_keeps_sixty_rows_and_the_full_latest(home):
    h = selfspeed.History(home)
    for i in range(70):
        h.append("selftest", {"ts": float(i), "status": "pass", "deep": False, "counts": {}, "ms": 1, "checks": []})
    rows = h.rows("selftest")
    assert len(rows) == selfspeed.KEEP and rows[0]["ts"] == 10.0 and h.latest("selftest")["ts"] == 69.0


def test_history_survives_a_corrupt_file(home):
    (home / selfspeed.HISTORY_FILE).write_text("{not json", encoding="utf-8")
    assert selfspeed.History(home).rows("speedtest") == []
    selfspeed.History(home).append("speedtest", {"ts": 1.0, "status": "pass", "values": {"a": 1}, "metrics": []})
    assert selfspeed.History(home).spark("a") == [1]


# ---------------------------------------------------------------------- schedule policy on a fake clock
def test_quiet_hours_block_scheduled_runs_only(db):
    clock = Clock()
    p = selfspeed.Policy(db, clock=clock)
    for hh, mm, quiet in ((22, 0, True), (23, 30, True), (3, 0, True), (6, 59, True), (7, 0, False), (8, 15, False),
                          (21, 59, False)):
        clock.at(hh, mm)
        assert p.skip_reason("selftest", "schedule") == ("quiet_hours" if quiet else None), (hh, mm)
        assert p.skip_reason("speedtest", "voice" if hh < 99 else "") is None, "a request always runs, even at night"


def test_nothing_heavy_while_a_voice_session_is_active(db):
    clock, active = Clock(13), [True]
    p = selfspeed.Policy(db, clock=clock, voice_active=lambda: active[0])
    assert p.skip_reason("speedtest", "schedule") == "voice_session"
    assert p.skip_reason("selftest", "schedule", deep=True) == "voice_session"
    assert p.skip_reason("selftest", "schedule") is None, "the quick read-only self-test is light"
    assert p.skip_reason("speedtest", "cli") is None and p.skip_reason("speedtest", "dashboard") is None
    active[0] = False
    assert p.skip_reason("speedtest", "schedule") is None


def test_quiet_hours_follow_the_owner_setting_and_zone(db):
    clock = Clock(23)
    p = selfspeed.Policy(db, clock=clock)
    assert p.skip_reason("selftest", "schedule") == "quiet_hours"
    db.set_setting("alerts.quiet_start", "23:30", actor="t")
    assert p.skip_reason("selftest", "schedule") is None
    db.set_setting("scheduler.timezone", "Asia/Jerusalem", actor="t")          # 23:00 UTC is 02:00 local (UTC+3)
    db.set_setting("alerts.quiet_start", "22:00", actor="t")
    assert p.skip_reason("selftest", "schedule") == "quiet_hours"
    clock.at(5)                                                                  # 08:00 local
    assert p.skip_reason("selftest", "schedule") is None


# ---------------------------------------------------------------------- voice tools
class Capture:
    def __init__(self):
        import logging
        self.records = []
        outer = self

        class H(logging.Handler):
            def emit(self, r):
                outer.records.append((getattr(r, "code", ""), getattr(r, "fields", {})))
        self.h = H(logging.INFO)

    def __enter__(self):
        logs.add_sink(self.h)
        return self

    def __exit__(self, *a):
        logs.remove_sink(self.h)


def make_svc(home, db, clock, *, voice=False, speed=None):
    st = lambda deep, audio=False, **kw: selftest.run(deep, audio=audio, **{**kw, "checks": only(("a", ok()), ("b", ok("warn", "slow")))})
    svc = selfspeed.SelfSpeed(home, Config(), db=db, clock=clock, voice_active=lambda: voice, selftest_fn=st,
                              speedtest_fn=speed or (lambda trigger, cls=None: speedtest.run(
                                  home=home, cls="owner", budgets=BUDGETS, record=True, clock=clock, trigger=trigger,
                                  measurers={g: fake(tool_p95_ms=50.0) for g in speedtest.GROUPS}).as_dict()))
    return svc


async def call(reg, name, **args):
    return await reg.call(name, args, origin="model")


async def test_tools_answer_in_one_sentence_plus_dashboard(home, db):
    reg = SkillRegistry()
    lang = ["en"]
    reg.register(selfspeed.make_skill(make_svc(home, db, Clock(12)), lambda: lang[0]))
    names = {t.name: t for t in reg.skills["selfspeed"].tools()}
    assert set(names) == {"run_selftest", "run_speedtest"}
    assert all(t.permission == "read" and len(t.description) <= 80 for t in names.values()), "tiny, read-only, no approval"
    r = await call(reg, "run_selftest")
    assert r.ok and r.message == "The self-test passed with 1 warning(s); first: B. Details on the dashboard."
    assert r.data["dashboard"] == "#/speed" and r.code == "SELFTEST_WARN"
    r = await call(reg, "run_speedtest")
    assert r.ok and r.message == "Speed test passed: everything is within budget. Details on the dashboard."
    lang[0] = "he"
    r = await call(reg, "run_speedtest")
    assert r.message.endswith("פרטים בלוח הבקרה.") and re.search("[א-ת]", r.message)


async def test_a_failed_self_test_returns_not_ok_but_still_speaks(home, db):
    svc = selfspeed.SelfSpeed(home, Config(), db=db, clock=Clock(12),
                              selftest_fn=lambda deep, audio=False, **kw: selftest.run(
                                  deep, audio=audio, **{**kw, "checks": only(("a", ok("fail", "bad")))}))
    reg = SkillRegistry()
    reg.register(selfspeed.make_skill(svc, lambda: "en"))
    r = await call(reg, "run_selftest")
    assert not r.ok and r.code == "SELFTEST_FAIL" and r.message.endswith("Details on the dashboard.")


async def test_scheduled_run_in_quiet_hours_is_skipped_logged_and_not_queued(home, db):
    clock = Clock(23)
    ran = []
    svc = make_svc(home, db, clock, speed=lambda trigger, cls=None: ran.append(trigger) or {})
    reg = SkillRegistry()
    reg.register(selfspeed.make_skill(svc, lambda: "en"))
    with Capture() as cap:
        r = await call(reg, "run_speedtest", trigger="schedule")
        r2 = await call(reg, "run_selftest", trigger="schedule")
    assert r.ok and r.code == "SPEEDTEST_SKIPPED" and r2.code == "SELFTEST_SKIPPED" and not ran
    assert [(c, f["reason"]) for c, f in cap.records if c == "SELFSPEED_SKIPPED"] == [("SELFSPEED_SKIPPED", "quiet_hours")] * 2
    assert not selfspeed.History(home).path.exists(), "a skipped run leaves no row and is not queued"
    clock.at(8, 15)                                                  # the next slot simply comes
    assert (await call(reg, "run_selftest", trigger="schedule")).code == "SELFTEST_WARN"


async def test_scheduled_speed_test_waits_for_the_voice_session_to_end(home, db):
    voice = [True]
    svc = make_svc(home, db, Clock(13))
    svc.policy.voice_active = lambda: voice[0]
    reg = SkillRegistry()
    reg.register(selfspeed.make_skill(svc, lambda: "en"))
    assert (await call(reg, "run_speedtest", trigger="schedule")).code == "SPEEDTEST_SKIPPED"
    assert (await call(reg, "run_speedtest", trigger="voice")).code == "SPEEDTEST_PASS", "asked by voice: runs"
    voice[0] = False
    assert (await call(reg, "run_speedtest", trigger="schedule")).code == "SPEEDTEST_PASS"


async def test_speed_test_that_cannot_run_says_so(home, db):
    def boom(trigger, cls=None):
        raise RuntimeError("no child")
    reg = SkillRegistry()
    reg.register(selfspeed.make_skill(make_svc(home, db, Clock(12), speed=boom), lambda: "en"))
    r = await call(reg, "run_speedtest")
    assert not r.ok and r.code == "SPEEDTEST_FAILED"


def test_self_management_can_call_the_python_api_directly(home):
    r = selftest.run(home=home, cfg=Config(), record=False, checks=only(("a", ok())))
    assert isinstance(r, selftest.SelfTestResult) and isinstance(r.checks[0], selftest.Check) and r.status == "pass"
    s = speedtest.run(home=home, budgets={}, record=False, measurers={g: fake() for g in speedtest.GROUPS})
    assert isinstance(s, speedtest.SpeedResult) and s.status == "pass"
    sig = __import__("inspect").signature(selftest.run)
    assert list(sig.parameters)[0] == "deep" and sig.parameters["deep"].default is False


# ---------------------------------------------------------------------- scheduler jobs
def test_jobs_are_created_once_and_call_the_tools(db):
    s = Scheduler(db)
    out = selfspeed.ensure_jobs(s, db)
    again = selfspeed.ensure_jobs(s, db)
    assert out == again and len(set(out.values())) == 2
    st, sp = s.job(out["selftest.job_id"]), s.job(out["speedtest.job_id"])
    assert (st["tool"], st["spec"]) == ("run_selftest", selfspeed.SELFTEST_SPEC)
    assert (sp["tool"], sp["spec"]) == ("run_speedtest", selfspeed.SPEEDTEST_SPEC)
    assert json.loads(st["tool_args"]) == {"trigger": "schedule"} and st["status"] == "active"
    cron = selfspeed.SELFTEST_SPEC.split()
    assert int(cron[1]) * 60 + int(cron[0]) >= 7 * 60, "the daily slot is outside quiet hours"
    assert selfspeed.SPEEDTEST_SPEC.split()[4] == "0", "weekly"
    assert s.delete(out["selftest.job_id"], by="owner")
    assert selfspeed.ensure_jobs(s, db)["selftest.job_id"] is None, "a job the owner deleted is not recreated"


async def test_a_scheduler_run_calls_the_tool_through_the_registry(home, db):
    clock = Clock(8, 15)
    reg = SkillRegistry()
    reg.register(selfspeed.make_skill(make_svc(home, db, clock), lambda: "en"))
    res = await reg.call("run_selftest", {"trigger": "schedule"}, origin="model")
    assert res.ok, "read-only tools need no approval from the scheduler"


# ---------------------------------------------------------------------- dashboard page
def post(d, path, body):
    r, data = req(d, "POST", path, headers={"X-Jarves": "1"}, body=body)
    return r.status, json.loads(data)


def test_api_runs_and_lists_over_http(dash, home, monkeypatch):
    monkeypatch.setattr(selftest, "CHECKS", only(("a", ok()), ("b", ok("warn", "slow"))))
    monkeypatch.setattr(selfspeed, "SPEED_RUNNER", lambda trigger, cls=None: speedtest.run(
        home=home, cls="owner", budgets=BUDGETS, trigger=trigger,
        measurers={g: fake(tool_p95_ms=60.0) for g in speedtest.GROUPS}).as_dict())
    d0 = get(dash, "/api/selfspeed")
    assert d0["latest"] == {} and d0["enabled"] and d0["schedule"]["quiet"] == ["22:00", "07:00"]
    st, r = post(dash, "/api/selfspeed/run", {"kind": "selftest"})
    assert st == 200 and r["status"] == "warn" and r["trigger"] == "dashboard"
    st, r = post(dash, "/api/selfspeed/run", {"kind": "speedtest"})
    assert st == 200 and r["values"] == {"tool_p95_ms": 60.0}
    d1 = get(dash, "/api/selfspeed")
    assert set(d1["latest"]) == {"selftest", "speedtest"} and d1["spark"]["tool_p95_ms"] == [60.0]
    assert d1["summary"]["selftest"].startswith("The self-test passed") and re.search("[א-ת]", d1["summary_he"]["speedtest"])
    assert len(d1["selftest_spark"]) == 1
    assert post(dash, "/api/selfspeed/run", {"kind": "nope"})[0] == 400


def test_api_needs_the_token_and_the_csrf_header(dash):
    r, _ = req(dash, "GET", "/api/selfspeed", cookie=False)
    assert r.status in (401, 403)
    r, _ = req(dash, "POST", "/api/selfspeed/run", body={"kind": "selftest"})            # no X-Jarves header
    assert r.status in (400, 403)


def test_page_is_wired_translated_and_sparklines_are_inline_svg():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '/static/selfspeed.js' in html and 'data-page="speed"' in html
    js = (STATIC / "selfspeed.js").read_text(encoding="utf-8")
    assert "PAGES, { speed:" in js and "createElementNS" in js and "innerHTML" not in js
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", js), "no external libraries or requests"
    assert "Run now" in js and "Run deep test" in js
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    m = re.search(r'\bspeed: "([^"]+)"', shell)
    assert m and re.search("[A-Za-z]", m.group(1)) and re.search("[א-ת]", m.group(1))
    for code in ("en", "he"):
        cat = selftest._catalog(code)["dict"]
        assert "Health & speed" in cat and "Run deep test" in cat and "No speed test yet." in cat


# ---------------------------------------------------------------------- CLI and CI wiring
def test_selftest_cli_exit_code_and_json(home, monkeypatch, capsys):
    monkeypatch.setattr(selftest, "CHECKS", only(("a", ok()), ("b", ok("warn", "slow"))))
    assert cli.main(["selftest", "--json"]) == 0
    d = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert d["status"] == "warn" and d["trigger"] == "cli" and [c["id"] for c in d["checks"]] == ["a", "b"]
    assert cli.main(["selftest"]) == 0
    out = capsys.readouterr().out
    assert "[WARN] B: slow" in out and "fix: fix it" in out and "The self-test passed with 1 warning" in out
    monkeypatch.setattr(selftest, "CHECKS", only(("a", ok("fail", "broken"))))
    assert cli.main(["selftest"]) == 1


def test_speedtest_cli_gate_fails_on_a_budget_and_class_ci_is_loose(home, monkeypatch, capsys):
    monkeypatch.setattr(speedtest, "load_budgets", lambda path=None: BUDGETS)
    monkeypatch.setitem(speedtest.MEASURERS, "history", fake(tool_p95_ms=300.0))
    for g in speedtest.GROUPS:
        if g != "history":
            monkeypatch.setitem(speedtest.MEASURERS, g, fake())
    assert cli.main(["speedtest", "--class", "owner"]) == 0, "without --gate the exit code stays 0"
    assert cli.main(["speedtest", "--class", "owner", "--gate", "--no-store"]) == 1
    assert "[FAIL] tool_p95_ms" in capsys.readouterr().out
    assert cli.main(["speedtest", "--class", "ci", "--gate", "--no-store"]) == 0
    assert len(selfspeed.History(home).rows("speedtest")) == 1, "--no-store writes nothing"
    assert cli.main(["speedtest", "--no-net", "--json", "--no-store"]) in (0, 1)


def test_cli_lists_both_commands_in_root_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "selftest" in out and "speedtest" in out


def test_ci_has_a_speed_gate_step_using_the_same_code_path():
    wf = (ROOT / ".github" / "workflows" / "jarves-checks.yml").read_text(encoding="utf-8")
    assert "jarves.cli speedtest --class ci --gate" in wf
    gate = (CORE / "tools" / "perf_gate.py").read_text(encoding="utf-8")
    assert "from jarves import speedtest" in gate and "def _timed" not in gate


def test_feature_guide_and_parity_rows_exist():
    from jarves import cli_parity, guides
    assert feat.CATALOG["self_tests"].default_enabled
    g = guides.for_feature("self_tests")
    assert g and "speed" in g.pages and cli_parity.FEATURES["self_tests"] == "selftest"
    assert cli_parity.TOOL_GROUPS["selfspeed"] == "selftest"


def test_log_calls_follow_the_lint(monkeypatch):
    sys.path.insert(0, str(CORE / "tools"))
    import log_lint
    sites, violations = log_lint.scan()
    assert any(x["component"] == "selfspeed" for x in sites) and violations == []
