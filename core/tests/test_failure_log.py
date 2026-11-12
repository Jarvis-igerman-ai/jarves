"""JAR-229: the local 'what went wrong' log. Only privacy-safe metadata is stored (kind, day, tool, code); never user
text. `jarvis report weekly` turns the last 7 days into a short list. Hermetic: tmp home, injected clock."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from jarves import cli, failure_log as fl
from jarves.features import CATALOG
from jarves.skills import NoArgs, Skill, SkillRegistry, Tool, ToolResult

NOW = datetime(2026, 11, 11, 12, 0)


def test_feature_is_beta_and_off_by_default():
    f = CATALOG[fl.FEATURE]
    assert f.default_enabled is False and f.stage == "beta"


def test_nothing_is_recorded_while_off(home):
    assert fl.record(home, "tool_error", tool="calendar", on=False, now=NOW) is False
    assert not fl.path(home).exists()


def test_only_safe_metadata_is_kept(home):
    with pytest.raises(ValueError):
        fl.record(home, "whatever", on=True, now=NOW)
    fl.record(home, "tool_error", tool="call Dana 050-1234567", code="my secret text", on=True, now=NOW)
    row = json.loads(fl.path(home).read_text(encoding="utf-8"))
    assert row == {"day": "2026-11-11", "kind": "tool_error", "tool": "other", "code": "other"}


def test_weekly_groups_last_seven_days_most_frequent_first(home):
    for _ in range(3):
        fl.record(home, "tool_error", tool="calendar", code="TIMEOUT", on=True, now=NOW)
    fl.record(home, "not_understood", on=True, now=NOW - timedelta(days=2))
    fl.record(home, "user_correction", on=True, now=NOW - timedelta(days=9))   # too old
    assert fl.weekly(home, now=NOW) == ["3x tool_error: calendar (TIMEOUT)", "1x not_understood"]


def test_the_log_is_bounded(home, monkeypatch):
    monkeypatch.setattr(fl, "MAX_ROWS", 5)
    for _ in range(8):
        fl.record(home, "repeated_request", on=True, now=NOW)
    assert len(fl.path(home).read_text(encoding="utf-8").splitlines()) == 5


def test_seven_day_window_boundary(home):
    fl.record(home, "tool_error", tool="six", on=True, now=NOW - timedelta(days=6))
    fl.record(home, "tool_error", tool="seven", on=True, now=NOW - timedelta(days=7))
    assert fl.weekly(home, now=NOW) == ["1x tool_error: six"]


def test_cli_report_weekly(home, capsys, monkeypatch):
    monkeypatch.setattr(fl, "_now", lambda: NOW)
    assert cli.main(["report", "weekly"]) == 0
    assert "No failures" in capsys.readouterr().out
    fl.record(home, "tool_error", tool="music", code="OFFLINE", on=True)
    assert cli.main(["report", "weekly"]) == 0
    assert "1x tool_error: music (OFFLINE)" in capsys.readouterr().out


class _Fail(Skill):
    name = "calendar"

    def tools(self):
        async def bad(_a):
            return ToolResult(False, "timed out for Dana", code="CAL_TIMEOUT")
        return [Tool("calendar_list", "x", bad, NoArgs, permission="read")]


@pytest.mark.parametrize("on", [True, False])
async def test_a_failed_tool_call_is_recorded_and_self_diagnosis_still_runs(home, monkeypatch, on):
    monkeypatch.setattr(fl, "_now", lambda: NOW)
    reg, seen = SkillRegistry(), []
    reg.register(_Fail())
    reg.on_failure = lambda tool, res, route: seen.append(tool)            # the existing self-diagnosis hook
    core = SimpleNamespace(home=home, registry=reg, features=SimpleNamespace(active=lambda fid: on))
    fl.attach(core)
    fl.attach(core)                                                         # a second attach never doubles rows
    assert not (await reg.call("calendar_list", {})).ok
    assert seen == ["calendar_list"]
    assert fl.weekly(home, now=NOW) == (["1x tool_error: calendar_list (CAL_TIMEOUT)"] if on else [])
    assert "Dana" not in (fl.path(home).read_text(encoding="utf-8") if on else "")


def test_a_missing_code_is_not_stored_as_none(home, monkeypatch):
    monkeypatch.setattr(fl, "_now", lambda: NOW)
    reg = SkillRegistry()
    hook = fl.attach(SimpleNamespace(home=home, registry=reg, features=SimpleNamespace(active=lambda fid: True)))
    hook("unknown_tool", SimpleNamespace(ok=False, code=None), "text")
    assert fl.weekly(home, now=NOW) == ["1x tool_error: other"]


class _Probe(_Fail):
    name = "zz_probe"

    def tools(self):
        t = super().tools()[0]
        t.name = "zz_probe_run"
        return [t]


async def test_the_started_core_records_one_row_per_failed_tool(core, monkeypatch):
    monkeypatch.setattr(fl, "_now", lambda: NOW)
    core.features.set_beta(True)
    core.features.set(fl.FEATURE, True)
    core.registry.register(_Probe())
    assert not (await core.registry.call("zz_probe_run", {})).ok
    assert fl.weekly(core.home, now=NOW) == ["1x tool_error: zz_probe_run (CAL_TIMEOUT)"]


def test_concurrent_writers_never_lose_a_row(home, monkeypatch):
    import threading
    monkeypatch.setattr(fl, "_now", lambda: NOW)
    go = threading.Barrier(8)

    def writer():
        go.wait()
        for _ in range(25):
            fl.record(home, "tool_error", tool="music", on=True)
    threads = [threading.Thread(target=writer) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert fl.weekly(home, now=NOW) == ["200x tool_error: music"]


def test_non_object_or_bad_rows_are_skipped(home):
    fl.record(home, "tool_error", tool="music", on=True, now=NOW)
    with fl.path(home).open("a", encoding="utf-8") as f:
        f.write('5\n[]\n"x"\nnot json\n{"day": 3, "kind": "tool_error"}\n{"day": "2026-11-11", "kind": ["x"]}\n')
    assert fl.weekly(home, now=NOW) == ["1x tool_error: music"]


def test_a_failing_write_is_logged_and_self_diagnosis_still_runs(home, monkeypatch):
    seen, warned = [], []
    reg = SkillRegistry()
    reg.on_failure = lambda tool, res, route: seen.append(tool)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(fl, "record", boom)
    monkeypatch.setattr(fl.logs, "warn", lambda comp, code, msg, **f: warned.append(code))
    hook = fl.attach(SimpleNamespace(home=home, registry=reg, features=SimpleNamespace(active=lambda fid: True)))
    hook("x", SimpleNamespace(ok=False, code="E"), "text")
    assert seen == ["x"] and warned == ["FAILURE_LOG_WRITE_FAILED"]


def test_concurrent_processes_never_lose_a_row(home):
    import subprocess
    import sys
    code = ("import sys; from datetime import datetime; from jarves import failure_log as fl\n"
            "for _ in range(20): fl.record(sys.argv[1], 'tool_error', tool='music', on=True, now=datetime(2026, 11, 11))")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(home)]) for _ in range(4)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    assert fl.weekly(home, now=NOW) == ["80x tool_error: music"]
    assert not list(home.glob("failure_log.jsonl.*.tmp"))       # every writer's own temp file was replaced
