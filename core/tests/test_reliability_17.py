"""Issue #17 reliability pass (owner's PC, `jarvis diagnose --days 3`): tools that "never returned", approvals nobody
answers, dev_suggest, music_play without the player, unclean-shutdown classification, slow-tool accounting.
Offline: fakes and short real timers only."""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from pydantic import BaseModel

from jarves import approvals as appr, devmanager as dev_mod
from jarves.runtime import shutdown
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult
from jarves.skills.music import FakePlayer, MusicSkill
from jarves.store import Store
from test_selfcheck import T0, check, rec, write_log


def codes(caplog) -> list[str]:
    return [getattr(r, "code", "") for r in caplog.records]


class Args(BaseModel):
    pass


def reg_with(*tools: Tool) -> SkillRegistry:
    class S(Skill):
        name = "t"
        description = "t"

        def tools(self):
            return list(tools)
    r = SkillRegistry()
    r.register(S())
    return r


# ---------------------------------------------------------------- every call ends in one log record
async def test_declined_and_forbidden_calls_are_closed_with_a_log_record(caplog):
    caplog.set_level("INFO")

    async def h(_):
        return ToolResult(True, "ok")
    reg = reg_with(Tool("danger", "d", h, Args, permission="destructive", confirm="sure?"))
    res = await reg.call("danger", {})
    assert res.code == "TOOL_DECLINED"                      # default confirm denies
    assert "TOOL_RESULT" in codes(caplog)                   # a refused call is closed by its TOOL_RESULT (ok=false)
    caplog.clear()
    assert (await reg.call("nope", {})).code == "TOOL_UNKNOWN"


async def test_cancelled_call_is_logged_not_lost(caplog):
    caplog.set_level("INFO")
    gate = asyncio.Event()

    async def ask(_p, **_m):
        await gate.wait()
        return True
    ask.accepts_meta = True

    async def h(_):
        return ToolResult(True, "ok")
    reg = reg_with(Tool("danger", "d", h, Args, permission="destructive", confirm="sure?"))
    reg.confirm = ask
    t = asyncio.ensure_future(reg.call("danger", {}))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert "TOOL_CANCELLED" in codes(caplog)


async def test_unanswered_approval_gets_a_hard_cap_and_an_honest_result(caplog):
    caplog.set_level("INFO")

    async def never(_p, **_m):
        await asyncio.sleep(60)
        return True
    never.accepts_meta = True

    async def h(_):
        return ToolResult(True, "ok")
    reg = reg_with(Tool("danger", "d", h, Args, permission="destructive", confirm="sure?"))
    reg.confirm, reg.ask_cap_s = never, 0.2
    res = await reg.call("danger", {})
    assert res.code == "TOOL_APPROVAL_TIMEOUT" and "approval on the screen" in res.message
    assert "APPROVAL_HARD_TIMEOUT" in codes(caplog) and reg.last_ask_outcome == "timeout"


async def test_approval_service_timeout_is_told_as_a_timeout_not_a_decline(home):
    svc = appr.ApprovalService(home, timeout_s=1)
    svc.channels.append(type("Ch", (), {"name": "x", "available": lambda s, a: True, "open": lambda s, a: None,
                                        "close": lambda s, a: None})())
    appr.attach(svc)
    try:
        async def h(_):
            return ToolResult(True, "ok")
        reg = reg_with(Tool("danger", "d", h, Args, permission="destructive", confirm="sure?"))
        reg.confirm = lambda p, **m: svc.ask(p, **m)
        reg.confirm.accepts_meta = True
        res = await reg.call("danger", {})
        assert res.code == "TOOL_APPROVAL_TIMEOUT"
    finally:
        appr.detach(svc)


async def test_no_channel_is_told_as_unavailable(home):
    svc = appr.ApprovalService(home, timeout_s=5)           # no channel can show it
    appr.attach(svc)
    try:
        async def h(_):
            return ToolResult(True, "ok")
        reg = reg_with(Tool("danger", "d", h, Args, permission="destructive", confirm="sure?"))
        reg.confirm = lambda p, **m: svc.ask(p, **m)
        reg.confirm.accepts_meta = True
        res = await reg.call("danger", {})
        assert res.code == "TOOL_APPROVAL_UNAVAILABLE"
    finally:
        appr.detach(svc)


async def test_handler_level_approval_time_does_not_count_against_the_tool_timeout():
    """feature_set asks inside its handler: its 30 s limit used to cut the 60 s approval short."""
    async def slow_answer(_p, **_m):
        await asyncio.sleep(0.5)
        return True
    holder = {}

    async def h(_):
        return ToolResult(await holder["reg"].ask("really?"), "done")
    reg = reg_with(Tool("asker", "a", h, Args, permission="action", timeout_s=0.2))
    holder["reg"] = reg
    reg.confirm = slow_answer
    reg.allowed_actions.add("asker")
    res = await reg.call("asker", {})
    assert res.ok and res.code == "ok"


async def test_timeout_message_is_honest_for_reads_and_actions():
    async def slow(_):
        await asyncio.sleep(5)
        return ToolResult(True, "x")
    reg = reg_with(Tool("r", "r", slow, Args, permission="read", timeout_s=0.1),
                   Tool("a", "a", slow, Args, permission="action", timeout_s=0.1))
    reg.allowed_actions.add("a")
    r = await reg.call("r", {})
    a = await reg.call("a", {})
    assert r.code == a.code == "TOOL_TIMEOUT"
    assert "nothing was changed" in r.message and "cannot confirm" in a.message


# ---------------------------------------------------------------- the diagnosis no longer sees "never returned"
def test_store_closes_refused_calls_and_uses_the_tools_own_time(home):
    import json
    st = Store(home / "store.db")
    lines = [rec(T0, "CORE_STARTING"),
             rec(T0 + 1, "LIVE_TOOL_CALL", "INFO", "live", tool="feature_set"),
             rec(T0 + 9, "TOOL_REFUSED", "INFO", "skills", tool="feature_set", result_code="TOOL_DECLINED", origin="model"),
             rec(T0 + 10, "LIVE_TOOL_CALL", "INFO", "live", tool="note_delete"),
             rec(T0 + 17, "TOOL_RESULT", "INFO", "skills", tool="note_delete", ok=True, result_code="ok",
                 origin="model", ms=40, approval_ms=6500)]
    for ln in lines:
        st.ingest(json.loads(ln))
    st.commit()
    by = {r["name"]: r for r in st.rows("SELECT * FROM tool_calls")}
    assert by["feature_set"]["done_ts"] is not None and by["feature_set"]["ok"] is None
    assert by["feature_set"]["code"] == "TOOL_DECLINED"
    assert by["note_delete"]["duration_s"] == pytest.approx(0.04)      # not 7 s of waiting for the person
    st.close()


# ---------------------------------------------------------------- dev_suggest
async def test_dev_suggest_accepts_the_words_the_model_uses(home):
    from jarves.diag import db as diag_db  # noqa: F401
    st = Store(home / "store.db")
    reg = SkillRegistry()
    reg.register(dev_mod.make_skill(lambda: Store(home / "store.db")))
    reg.allowed_actions |= dev_mod.ALLOWED_ACTIONS
    for kind in ("idea", "feature", "bug", "רעיון", "suggestion"):
        r = await reg.call("dev_suggest", {"title": f"voice idea {kind}", "body": "calmer", "kind": kind}, origin="model")
        assert r.ok, (kind, r.message, r.code)
    assert dev_mod.normalize_kind("bug") == "ticket" and dev_mod.normalize_kind("zzz") == "suggestion"
    st.close()


# ---------------------------------------------------------------- music_play without the player
async def test_music_play_with_the_player_closed_answers_at_once_and_does_not_retry():
    fp = FakePlayer()
    fp.offline = True
    reg = SkillRegistry()
    reg.register(MusicSkill(fp, verify_tries=2))
    r = await reg.call("music_play", {"query": "Song One"})
    assert not r.ok and r.code == "MUSIC_PLAYER_OFFLINE"
    assert "jarvis music open" in r.message and r.data["fix_command"] == "jarvis music open"
    assert fp.calls == []                                  # not a single search / play_track / poll


async def test_music_play_stops_retrying_when_the_player_closes_midway():
    class Dies(FakePlayer):
        async def play_track(self, track):
            await super().play_track(track)
            self.np = None                                 # the song never shows
            self.dead_after = True

        async def probe(self):
            return "not_running" if getattr(self, "dead_after", False) else "running"

    fp = Dies([__import__("jarves.skills.music", fromlist=["Track"]).Track(f"v{i}", "Same Song %d" % i, "A")
               for i in range(3)])
    reg = SkillRegistry()
    reg.register(MusicSkill(fp, verify_tries=2))
    r = await reg.call("music_play", {"query": "Same Song"})
    assert r.code == "MUSIC_PLAYER_OFFLINE"
    assert fp.calls.count("play_track") == 1


async def test_pear_probe_tells_a_closed_app_from_a_frozen_one(tmp_path):
    from jarves.skills.music import PearDesktopPlayer

    def refuse(req):
        raise httpx.ConnectError("refused")

    def freeze(req):
        raise httpx.ReadTimeout("slow")
    assert await PearDesktopPlayer("http://x", tmp_path / "id", transport=httpx.MockTransport(refuse)).probe() == "not_running"
    assert await PearDesktopPlayer("http://x", tmp_path / "id", transport=httpx.MockTransport(freeze)).probe() == "unresponsive"
    ok = PearDesktopPlayer("http://x", tmp_path / "id", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert await ok.probe() == "running"


# ---------------------------------------------------------------- clean-stop plumbing
def test_console_events_become_a_clean_stop_and_hold_windows_back():
    seen, stopped = [], threading.Event()
    h = shutdown.make_console_handler(seen.append, stopped, wait_s=0.05)
    assert h(shutdown.CTRL_CLOSE_EVENT) is True and seen == ["console_closed"]
    assert h(shutdown.CTRL_LOGOFF_EVENT) and h(shutdown.CTRL_SHUTDOWN_EVENT)
    assert seen[1:] == ["windows_logoff", "windows_shutdown"]
    assert h(99) is False                                  # not ours
    assert h(shutdown.CTRL_C_EVENT) is True and h(shutdown.CTRL_C_EVENT) is False   # second Ctrl+C aborts as before


def test_os_ending_event_waits_for_the_core_to_finish():
    stopped = threading.Event()
    h = shutdown.make_console_handler(lambda r: threading.Timer(0.05, stopped.set).start(), stopped, wait_s=3)
    import time
    t = time.monotonic()
    assert h(shutdown.CTRL_SHUTDOWN_EVENT)
    assert 0.03 < time.monotonic() - t < 2.5               # released as soon as stop() is done, not after 3 s


def test_install_registers_and_removes_the_windows_handler():
    calls = []

    class K32:
        def SetConsoleCtrlHandler(self, cb, add):
            calls.append(add)
            return 1

    class Loop:
        def call_soon_threadsafe(self, fn):
            fn()
    undo = shutdown.install(Loop(), lambda r: None, threading.Event(), platform="win32", kernel32=K32())
    undo()
    assert calls == [True, False]


async def test_install_posix_signals_route_to_request_stop():
    import os
    import signal
    import sys
    if sys.platform == "win32":
        return
    got = []
    undo = shutdown.install(asyncio.get_running_loop(), got.append, threading.Event())
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.1)
    finally:
        undo()
    assert got == ["sigterm"]


def test_boot_time_helpers():
    assert shutdown.same_boot(1000, 1050) is True and shutdown.same_boot(1000, 9000) is False
    assert shutdown.same_boot(None, 5) is None
    b = shutdown.boot_time(clock=lambda: 10_000_000.0)
    assert b is None or isinstance(b, int)


def _procs(*specs):
    out = []
    for i, (sid, boot, stopped, stopping) in enumerate(specs):
        out.append(rec(T0 - 1000 + i * 100, "CORE_STARTING", session=sid, **({"boot": boot} if boot else {})))
        if stopping:
            out.append(rec(T0 - 1000 + i * 100 + 50, "CORE_STOPPING", session=sid, reason="tray_quit"))
        if stopped:
            out.append(rec(T0 - 1000 + i * 100 + 60, "CORE_STOPPED", session=sid))
    return out


def run_check(home, lines):
    write_log(home, lines)
    c = check(home)
    v = c.run("test", ai=False)
    c.close()
    return {f["fingerprint"]: f for f in v["findings"]}, v["metrics"]


def test_a_reboot_between_two_starts_is_not_reported_as_a_jarves_crash(home):
    by, m = run_check(home, _procs(("a", 1_000_000, False, False), ("b", 1_900_000, True, True), ("c", 1_900_000, False, False)))
    assert "crash" not in by and "system_restart" in by and by["system_restart"]["severity"] == "low"
    assert m["crashes"] == 0 and m["system_restarts"] == 1


def test_same_boot_without_a_stop_is_still_a_crash_and_a_hung_stop_is_its_own_finding(home):
    by, m = run_check(home, _procs(("a", 1_000_000, False, False), ("b", 1_000_010, False, True), ("c", 1_000_020, True, True)))
    assert "crash" in by and "stop_hung" in by and m["crashes"] == 1 and m["stop_hung"] == 1


def test_old_logs_without_a_boot_field_keep_the_old_verdict(home):
    by, m = run_check(home, _procs(("a", None, False, False), ("b", None, True, True)))
    assert "crash" in by and m["system_restarts"] == 0


# ---------------------------------------------------------------- Core: the marker is written even when a part fails
async def test_core_stop_always_writes_the_clean_stop_marker(core, caplog):
    caplog.set_level("INFO")

    async def boom():
        raise RuntimeError("link broke")
    core.link.stop = boom
    core.request_stop("test_reason")
    assert core.stop_event.is_set() and core.stop_reason == "test_reason"
    await core.stop()
    got = [r for r in caplog.records if getattr(r, "code", "") == "CORE_STOPPED"]
    assert got and got[0].fields["failed_steps"] == ["link"] and got[0].fields["reason"] == "test_reason"
    assert core.stopped_evt.is_set()
    assert "CORE_STOP_STEP_FAILED" in codes(caplog)


async def test_open_dashboard_does_not_wait_for_a_stuck_browser(core, monkeypatch):
    import time
    from jarves import app as app_mod
    monkeypatch.setattr(app_mod, "DASHBOARD_BROWSER_S", 0.2)
    core.open_url = lambda url: time.sleep(1.5)            # webbrowser.open stuck on the shell
    t = time.monotonic()
    url = await core.open_dashboard()
    assert url and time.monotonic() - t < 1.2 and core.dashboard_browser == "pending"
    res = await core.registry.call("open_dashboard", {})
    assert res.ok and res.data.get("browser") == "pending"
    await asyncio.sleep(1.6)


# ---------------------------------------------------------------- slow tools
async def test_remember_runs_off_the_event_loop(home):
    from jarves.memory import MemoryStore
    from jarves.skills.builtin import MemorySkill
    store = MemoryStore(home / "memory.db")
    release = threading.Event()
    real = store.upsert

    def slow(*a, **k):
        release.wait(2)
        return real(*a, **k)
    store.upsert = slow
    reg = SkillRegistry()
    reg.register(MemorySkill(store))
    task = asyncio.ensure_future(reg.call("remember", {"text": "I like tea"}, origin="user"))
    await asyncio.sleep(0.05)
    ticks = 0
    for _ in range(5):                                     # the loop keeps running while the write waits
        await asyncio.sleep(0.01)
        ticks += 1
    release.set()
    assert (await task).ok and ticks == 5
    store.close()
