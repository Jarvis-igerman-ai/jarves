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


def test_future_timestamp_marker_is_rejected(tmp_path):
    shutdown.mark_planned_restart(tmp_path, "hotfix", lambda: T0 + 500)
    assert shutdown.consume_planned_restart(tmp_path, lambda: T0) is None
    assert not (tmp_path / shutdown.PLANNED_RESTART_FILE).exists()
