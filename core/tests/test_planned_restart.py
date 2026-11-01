"""V4.5.1 #620: a planned hotfix stop must not look like a crash at the next start. Hermetic, fixed clock."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from jarves import selfcheck
from jarves.runtime import shutdown

T0 = 1_800_000_000.0


def _log(home, stopped: bool):
    (home / "logs").mkdir(parents=True, exist_ok=True)

    def r(t, code, **f):
        return json.dumps({"ts": datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds"),
                           "level": "INFO", "component": "core", "code": code, "msg": "-", "session": "old", **f})
    lines = [r(T0 - 3000, "CORE_STARTING", version="4.5.1")]
    if stopped:
        lines.append(r(T0 - 100, "CORE_STOPPED"))
    (home / "logs" / "jarves.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _svc(home):
    db = SimpleNamespace(setting=lambda k: {"self_check.on_start": "crash"}.get(k))
    svc = selfcheck.SelfCheckService(SimpleNamespace(home=home), SimpleNamespace(db=db))
    svc.clock = lambda: T0
    calls = []

    async def auto(trigger, prev=None):
        calls.append(trigger)
    svc.auto = auto
    return svc, calls


def test_marker_fresh_stale_and_single_use(tmp_path):
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0)
    assert shutdown.consume_planned_restart(tmp_path, lambda: T0 + 60) == "hotfix"
    assert shutdown.consume_planned_restart(tmp_path, lambda: T0 + 60) is None          # consumed
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0)
    assert shutdown.consume_planned_restart(tmp_path, lambda: T0 + 3600) is None        # stale
    assert not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()


def test_unclean_stop_without_marker_is_still_a_crash(tmp_path):
    _log(tmp_path, stopped=False)
    svc, calls = _svc(tmp_path)
    asyncio.run(svc._startup())
    assert calls == ["crash"]


def test_planned_hotfix_stop_is_not_a_crash(tmp_path):
    _log(tmp_path, stopped=False)
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0 - 30)
    svc, calls = _svc(tmp_path)
    asyncio.run(svc._startup())
    assert calls == []
    assert not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()


def _svc_mode(home, mode):
    svc, calls = _svc(home)
    svc.db = SimpleNamespace(setting=lambda k: {"self_check.on_start": mode}.get(k))
    return svc, calls


def test_marker_after_clean_stop_does_not_hide_a_later_crash(tmp_path):
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0 - 30)
    _log(tmp_path, stopped=True)
    svc, calls = _svc(tmp_path)
    asyncio.run(svc._startup())                                     # clean previous run: marker consumed anyway
    assert calls == []
    assert not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()
    _log(tmp_path, stopped=False)                                   # a real crash within the 10 minutes
    svc, calls = _svc(tmp_path)
    asyncio.run(svc._startup())
    assert calls == ["crash"]


def test_marker_deleted_when_on_start_never_or_no_previous_run(tmp_path):
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0 - 30)
    svc, calls = _svc_mode(tmp_path, "never")
    asyncio.run(svc._startup())
    assert calls == [] and not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0 - 30)
    svc, calls = _svc(tmp_path)                                     # no log at all: no previous run
    asyncio.run(svc._startup())
    assert calls == [] and not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()


def test_crash_of_hotfixed_core_before_consuming_marker_is_reported(tmp_path):
    """Opus blocker 2: S1 is stopped by the hotfix (marker names S1); the hotfixed Core S2 dies before its startup
    consumed the marker; the revert's Core S3 must report S2 as a crash, not as a planned restart."""
    def r(t, code, s):
        return json.dumps({"ts": datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds"),
                           "level": "INFO", "component": "core", "code": code, "msg": "-", "session": s})
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0 - 200, session="S1")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "jarves.log").write_text("\n".join([
        r(T0 - 3000, "CORE_STARTING", "S1"), r(T0 - 190, "CORE_STOPPED", "S1"), r(T0 - 150, "CORE_STARTING", "S2")]) + "\n",
        encoding="utf-8")
    svc, calls = _svc(tmp_path)
    asyncio.run(svc._startup())
    assert calls == ["crash"]
    assert not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()


def test_marker_naming_the_previous_session_still_explains_it(tmp_path):
    _log(tmp_path, stopped=False)                                    # session "old"
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0 - 30, session="old")
    svc, calls = _svc(tmp_path)
    asyncio.run(svc._startup())
    assert calls == []


def test_last_core_session_reads_the_log(tmp_path):
    assert selfcheck.last_core_session(tmp_path, lambda: T0) is None
    _log(tmp_path, stopped=False)
    assert selfcheck.last_core_session(tmp_path, lambda: T0) == "old"


def test_hotfix_stop_failure_removes_the_marker(tmp_path, monkeypatch):
    import pytest
    from jarves import cli, hotfix
    _log(tmp_path, stopped=False)
    monkeypatch.setattr(cli, "_ws_call", lambda *a, **k: {"ok": True})        # the Core never stops
    monkeypatch.setattr(cli, "cmd_stop", lambda *a, **k: None)
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(hotfix.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(hotfix.time, "sleep", lambda s: None)
    h = hotfix.default_hooks(SimpleNamespace(link=SimpleNamespace(port=1)), tmp_path, tmp_path / "pkg")
    with pytest.raises(hotfix.HotfixError):
        h.stop_core()
    assert not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()


def test_future_timestamp_marker_is_rejected(tmp_path):
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0 + 500)
    assert shutdown.consume_planned_restart(tmp_path, lambda: T0) is None
    assert not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()
