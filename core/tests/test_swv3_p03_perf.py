"""SW-V3 P0.3 performance fixes keep behaviour: route-aware prewarm, no idle runtime.json churn, the music probe
off the event loop, lean OpenBLAS threads for the Core. Offline: fakes only, no sound, no network."""
import asyncio
import os

from jarves import cli
from jarves import features as feat
from jarves import runtime as rt_mod
from jarves.brain import AgentBrain

from test_runtime import FakeTray, make_core


def _modules(core, voice_mode, agent):
    core.voice_mode, core.agent = voice_mode, agent
    return core._prewarm_modules()


def test_prewarm_imports_only_what_the_route_uses(home, cfg):
    core = make_core(home, cfg)
    live = _modules(core, "live", object())                       # combined / ChatGPT-plan brain
    assert live[0] == "openai" and "jarves.voice.live" in live
    assert not any(m.startswith("agents") for m in live)          # GPT-Live never uses the Agents SDK
    rt = _modules(core, "realtime", object())
    assert "agents.realtime" in rt and "jarves.voice.realtime" in rt
    api = _modules(core, "live", AgentBrain.__new__(AgentBrain))   # API-key text brain still gets a warm SDK
    assert api[-1] == "agents" and api.index("openai") < api.index("agents")
    assert _modules(core, "push_to_talk", None) == ["openai"]


def test_prewarm_runs_and_logs_the_modules(home, cfg, monkeypatch):
    core = make_core(home, cfg)
    core.voice_mode, core.agent = "live", None
    seen = []
    monkeypatch.setattr("importlib.import_module", lambda name: seen.append(name))
    core._prewarm()
    assert seen == core._prewarm_modules()


async def test_idle_runtime_does_not_rewrite_state_every_second(home, cfg, monkeypatch):
    trays = []

    def factory(ctl):
        trays.append(FakeTray(ctl))
        return trays[-1]
    core = make_core(home, cfg, runtime_mode="background", tray_factory=factory)
    await core.start()
    try:
        saves = []
        real_save = core.runtime.save
        monkeypatch.setattr(core.runtime, "save", lambda view=None: (saves.append(1), real_save(view)))
        await asyncio.sleep(1.2)                 # first pass of the loop records the current view
        saves.clear()
        before = trays[0].refreshes
        await asyncio.sleep(2.3)                 # idle: only "ts" changes
        assert saves == [] and trays[0].refreshes == before
        core.runtime.prefs["alerts_paused"] = True     # a real change still reaches runtime.json and the tray
        await asyncio.sleep(1.3)
        assert saves and trays[0].refreshes == before + 1
        assert rt_mod.read_state(home)["live"]["alerts"]["paused"] is True
    finally:
        await core.stop()


async def test_idle_runtime_heartbeat_still_refreshes_the_file(home, cfg, monkeypatch):
    monkeypatch.setattr("jarves.runtime.service.HEARTBEAT_S", 1.5)
    core = make_core(home, cfg, runtime_mode="background", tray_factory=FakeTray)
    await core.start()
    try:
        t0 = rt_mod.read_state(home)["live"]["ts"]
        await asyncio.sleep(3.3)
        assert rt_mod.read_state(home)["live"]["ts"] > t0
    finally:
        await core.stop()


def test_music_probe_warm_up_is_a_cache_hit_later(home, cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(feat, "_tcp_open", lambda url, timeout=0.3: calls.append(url) or False)
    f = feat.Features(cfg, home, components=lambda: {}, skills=lambda: set())
    f.warm("music")
    assert len(calls) == 1
    ok, why = f.available("music")                  # the event-loop caller gets the warmed result, no connect
    assert (ok, len(calls)) == (False, 1) and "Pear" in why
    f.warm("music")                                 # still fresh: not probed again
    assert len(calls) == 1


async def test_core_start_runs_the_music_probe_off_the_loop(home, cfg, monkeypatch):
    threads = []
    import threading
    monkeypatch.setattr(feat, "_tcp_open", lambda url, timeout=0.3: threads.append(threading.current_thread().name) or False)
    core = make_core(home, cfg)
    await core.start()
    try:
        await asyncio.sleep(0.1)
        assert threads and all(t != threading.main_thread().name for t in threads)
    finally:
        await core.stop()


def test_core_run_keeps_openblas_lean_unless_the_user_chose(monkeypatch):
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    cli._lean_numeric_threads()
    assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "4")
    cli._lean_numeric_threads()
    assert os.environ["OPENBLAS_NUM_THREADS"] == "4"
