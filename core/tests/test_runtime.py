"""SW-V2 M1 always-on runtime: window close/show/hide without stopping the Core, four separate states, tray menu,
single instance, autostart adapters, sleep/resume and unplugged-mic recovery. Offline: fakes only, no sound."""
import asyncio
import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import FakeSTT, FakeTTS, open_face, until
from jarves import cli
from jarves import runtime as rt_mod
from jarves.app import Core
from jarves.runtime import autostart as au
from jarves.runtime.lock import AlreadyRunning, InstanceLock, held, read_holder
from jarves.runtime.power import ResumeWatcher, stall_guard
from jarves.runtime.tray import menu_entries, status_line
from jarves.skills.music import FakePlayer
from jarves.voice.audio import AudioUnavailable, SimAudio
from jarves.voice.wakeword import FakeDetector

CHUNK = 2560


class FakeProc:
    """Stands in for the Godot process."""

    def __init__(self):
        self.returncode = None
        self.pid = 4242
        self._done = asyncio.Event()
        self.terminated = False

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def exit(self, rc=0):
        self.returncode = rc
        self._done.set()

    def terminate(self):
        self.terminated = True
        self.exit(1)


class Launcher:
    def __init__(self):
        self.procs: list[FakeProc] = []

    async def __call__(self, port):
        p = FakeProc()
        self.procs.append(p)
        return p


class FakeTray:
    def __init__(self, ctl):
        self.ctl, self.started, self.stopped, self.notes, self.refreshes = ctl, False, False, [], 0

    def start(self):
        self.started = True

    def refresh(self):
        self.refreshes += 1

    def notify(self, text):
        self.notes.append(text)

    def stop(self):
        self.stopped = True


class FakeReg(dict):
    def get(self, name):
        return dict.get(self, name)

    def set(self, name, value):
        self[name] = value

    def delete(self, name):
        return self.pop(name, None) is not None


def make_core(home, cfg, **kw):
    kw.setdefault("viewer_launcher", Launcher())
    kw.setdefault("autostart", au.WindowsAutostart(FakeReg(), ["pythonw.exe", "-m", "jarves", "run", "--background"]))
    return Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
                use_real_audio=False, **kw)


async def cli_client(core):
    from websockets.asyncio.client import connect
    ws = await connect(f"ws://127.0.0.1:{core.cfg.link.port}")
    await ws.send(json.dumps({"type": "hello", "payload": {"token": core.link.token, "client": "cli"}}))
    from conftest import Face
    f = Face(ws)
    await f.wait("hello.ok")
    return f


# ---------------------------------------------------------------------- window close: the regression
async def test_window_close_keeps_core_running_and_remembers_hidden(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    try:
        face = await open_face(core)
        await face.send("desktop.close")
        await face.wait("desktop.closed")
        await face.ws.close()
        await until(lambda: core.runtime.display_state() == "hidden")
        assert not core.stop_event.is_set() and not core.stopping
        assert rt_mod.read_state(home)["prefs"]["display_hidden"] is True
        # the Core still answers: a CLI status works, the voice/wake paths are untouched
        c = await cli_client(core)
        await c.send("status.request")
        st = await c.wait("status")
        assert st["runtime"]["core"]["state"] == "running" and st["runtime"]["display"]["state"] == "hidden"
        await c.ws.close()
    finally:
        await core.stop()


async def test_window_close_quits_when_background_core_is_switched_off(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    try:
        core.features.set("background_core", False, by="test")
        face = await open_face(core)
        await face.send("desktop.close")
        await face.wait("desktop.closed")
        await asyncio.wait_for(core.stop_event.wait(), 30)      # the old behaviour, still available
        await face.ws.close()
    finally:
        await core.stop()


# ---------------------------------------------------------------------- show / hide by voice
async def test_show_and_hide_yourself_tools_never_stop_the_core(home, cfg):
    launcher = Launcher()
    core = make_core(home, cfg, viewer_launcher=launcher)
    await core.start()
    try:
        names = {t.name for t in core.registry.tools()}
        assert {"show_yourself", "hide_yourself"} <= names
        r = await core.registry.call("show_yourself", {})
        assert r.ok and len(launcher.procs) == 1
        r2 = await core.registry.call("show_yourself", {})          # no second window while one is opening
        assert r2.ok and len(launcher.procs) == 1
        assert core.runtime.display_state() == "starting"
        face = await open_face(core)                                 # the window connected
        assert core.runtime.display_state() == "shown"
        r3 = await core.registry.call("show_yourself", {})
        assert r3.ok and "כבר" in r3.message and len(launcher.procs) == 1
        hide = asyncio.create_task(core.registry.call("hide_yourself", {}))
        await face.wait("display.close")                             # the face is asked to close itself
        launcher.procs[0].exit(0)                                    # ... and does
        r4 = await hide
        assert r4.ok and not launcher.procs[0].terminated
        await face.ws.close()
        await until(lambda: core.runtime.display_state() == "hidden")
        assert not core.stop_event.is_set() and core.health.core != "stopped"
    finally:
        await core.stop()


async def test_hide_terminates_a_window_that_does_not_close(home, cfg):
    launcher = Launcher()
    core = make_core(home, cfg, viewer_launcher=launcher)
    await core.start()
    try:
        await core.runtime.show("test")
        t0 = time.monotonic()
        await core.runtime.viewer.close(grace_s=0.2)
        assert launcher.procs[0].terminated and time.monotonic() - t0 < 2
        assert core.runtime.display_state() == "hidden"
    finally:
        await core.stop()


async def test_display_switch_off_hides_the_tools(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    try:
        core.features.set("display_control", False, by="test")
        assert "show_yourself" not in {t.name for t in core.registry.tools()}
        r = await core.registry.call("show_yourself", {})
        assert not r.ok and r.code == "FEATURE_OFF"
    finally:
        await core.stop()


async def test_viewer_crash_is_reported_and_core_survives(home, cfg):
    launcher = Launcher()
    core = make_core(home, cfg, viewer_launcher=launcher)
    await core.start()
    try:
        await core.runtime.show("test")
        launcher.procs[0].exit(3)                     # Godot crashed
        await until(lambda: core.runtime.display_state() == "crashed")
        assert core.health.components["display"].status == "degraded"
        assert core.runtime.viewer.crashes == 1 and not core.stop_event.is_set()
        ok, _ = await core.runtime.show("voice")      # can be opened again
        assert ok and len(launcher.procs) == 2
    finally:
        await core.stop()


async def test_core_stop_closes_the_window_it_opened(home, cfg):
    launcher = Launcher()
    core = make_core(home, cfg, viewer_launcher=launcher)
    await core.start()
    await core.runtime.show("test")
    await core.stop()
    assert launcher.procs[0].returncode is not None
    assert rt_mod.read_state(home)["live"]["core"]["state"] == "stopped"


# ---------------------------------------------------------------------- the four separate states
async def test_four_states_are_independent_and_persist(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    try:
        c = await cli_client(core)
        await c.send("runtime.set", {"mic_muted": True})
        r = await c.wait("runtime.state")
        v = r["runtime"]
        assert v["mic"]["muted"] and not v["alerts"]["paused"] and v["core"]["state"] == "running"
        assert core.dev.mic_muted
        await c.send("runtime.set", {"alerts_paused": True})
        v = (await c.wait("runtime.state"))["runtime"]
        assert v["alerts"]["paused"] and v["mic"]["muted"] and v["display"]["state"] == "hidden"
        assert rt_mod.alerts_paused(home) is True
        await c.send("runtime.set", {"display": "hide"})
        v = (await c.wait("runtime.state"))["runtime"]
        assert v["mic"]["muted"] and v["alerts"]["paused"] and v["core"]["state"] == "running"
        await c.ws.close()
    finally:
        await core.stop()
    assert rt_mod.alerts_paused(home) is True                        # read from runtime.json with no Core
    # a restart (new login, crash, update) keeps the user's choices
    cfg2 = cfg.model_copy(deep=True)
    core2 = make_core(home, cfg2)
    await core2.start()
    try:
        assert core2.dev.mic_muted and core2.runtime.prefs["alerts_paused"]
        assert core2.snapshot()["runtime"]["mic"]["muted"]
    finally:
        await core2.stop()


async def test_face_cannot_send_runtime_set(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    try:
        face = await open_face(core)
        await face.send("runtime.set", {"mic_muted": True})
        err = await face.wait("error")
        assert err["code"] == "LINK_FORBIDDEN" and not core.dev.mic_muted
        await face.ws.close()
    finally:
        await core.stop()


# ---------------------------------------------------------------------- tray
def test_tray_menu_has_the_spec_items_and_states():
    view = {"core": {"state": "running"}, "display": {"state": "hidden", "available": True},
            "mic": {"muted": True}, "alerts": {"paused": False}, "voice": {"wake_listening": True},
            "dashboard": {"enabled": True}, "health": "ready"}
    items = menu_entries(view)
    assert [i.id for i in items] == ["dashboard", "toggle_display", "toggle_mute", "toggle_alerts", "talk", "status",
                                     "diagnostics", "quit"]
    assert items[1].text == "Show JARVES" and items[2].checked is True and items[3].checked is False
    assert "mic muted" in status_line(view) and "window hidden" in status_line(view)
    view["display"]["state"] = "shown"
    assert menu_entries(view)[1].text == "Hide JARVES"


async def test_tray_actions_reach_the_core_from_its_own_thread(home, cfg):
    launcher = Launcher()
    core = make_core(home, cfg, viewer_launcher=launcher, runtime_mode="background", tray_factory=FakeTray)
    await core.start()
    try:
        tray = core.runtime.tray
        assert tray.started and core.runtime.view()["tray"]["state"] == "shown"
        th = threading.Thread(target=tray.ctl.act, args=("toggle_mute",))
        th.start()
        th.join()
        await until(lambda: core.dev.mic_muted)
        threading.Thread(target=tray.ctl.act, args=("toggle_alerts",)).start()
        await until(lambda: core.runtime.prefs["alerts_paused"])
        threading.Thread(target=tray.ctl.act, args=("toggle_display",)).start()
        await until(lambda: len(launcher.procs) == 1)
        threading.Thread(target=tray.ctl.act, args=("status",)).start()
        await until(lambda: tray.notes)
        assert "mic muted" in tray.notes[0]
        threading.Thread(target=tray.ctl.act, args=("quit",)).start()
        await asyncio.wait_for(core.stop_event.wait(), 30)
    finally:
        await core.stop()
    assert tray.stopped


def test_pystray_backend_builds_without_running():
    import pystray  # noqa: F401  (the `tray` extra is installed everywhere: uv sync --all-extras)
    from jarves.runtime.tray import Controller, PystrayTray, icon_image
    view = {"core": {"state": "running"}, "display": {"state": "shown"}, "mic": {"muted": True},
            "alerts": {"paused": True}}
    acts = []
    t = PystrayTray(Controller(lambda: view, acts.append))
    assert icon_image(view).size == (64, 64)
    labels = [i.text for i in t.icon.menu.items if i.text]
    assert "Dashboard" in labels and "Quit JARVES" in labels and any(l.startswith("Status:") for l in labels)


# ---------------------------------------------------------------------- single instance
def test_instance_lock_refuses_a_second_core(tmp_path):
    a = InstanceLock(tmp_path)
    assert a.acquire("background")
    assert held(tmp_path) and read_holder(tmp_path)["mode"] == "background"
    with pytest.raises(AlreadyRunning) as e:
        InstanceLock(tmp_path).acquire()
    assert str(e.value.holder.get("pid")) in str(e.value)
    a.release()
    assert not held(tmp_path)
    b = InstanceLock(tmp_path)
    assert b.acquire()
    b.release()


def test_instance_lock_across_processes(tmp_path):
    code = ("import sys,time; sys.path.insert(0, sys.argv[2]); from jarves.runtime.lock import InstanceLock; "
            "InstanceLock(__import__('pathlib').Path(sys.argv[1])).acquire('test'); print('locked', flush=True); "
            "time.sleep(30)")
    import jarves
    src = str(__import__("pathlib").Path(jarves.__file__).parents[1])
    p = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), src], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "locked"
        with pytest.raises(AlreadyRunning):
            InstanceLock(tmp_path).acquire()
    finally:
        p.kill()
        p.wait()
    InstanceLock(tmp_path).acquire()          # the OS released it with the dead process


def test_cli_run_exits_3_when_a_core_already_runs(home, cfg):
    lock = InstanceLock(home)
    lock.acquire("background")
    try:
        args = SimpleNamespace(background=True, desktop=False, show=False, log_level=None, module=None, hide=None)
        assert cli.cmd_run(args, cfg, home) == cli.EXIT_ALREADY_RUNNING
    finally:
        lock.release()


# ---------------------------------------------------------------------- autostart adapters
def test_windows_autostart_uses_run_key_and_windowless_python():
    reg = FakeReg()
    a = au.WindowsAutostart(reg, [r"C:\venv\Scripts\pythonw.exe", "-m", "jarves", "run", "--background"])
    assert not a.enabled()
    where = a.enable()
    assert a.enabled() and "CurrentVersion\\Run" in where
    assert reg[au.VALUE] == r"C:\venv\Scripts\pythonw.exe -m jarves run --background"
    assert a.describe()["current_install"] is True
    assert a.disable() and not a.enabled() and not a.disable()


def test_default_command_is_background_without_console():
    cmd = au.core_command()
    assert cmd[1:] == ["-m", "jarves", "run", "--background"]
    if sys.platform == "win32":
        assert cmd[0].lower().endswith("pythonw.exe")


def test_systemd_user_service_file(tmp_path):
    calls = []
    a = au.SystemdUserAutostart(tmp_path, ["/home/pi/jarves/.venv/bin/python", "-m", "jarves", "run", "--background"],
                                runner=lambda args: calls.append(args) or 1)     # no user systemd: fallback link
    a.enable()
    text = (tmp_path / au.UNIT).read_text()
    assert "ExecStart=/home/pi/jarves/.venv/bin/python -m jarves run --background" in text
    assert "RestartPreventExitStatus=3" in text and "WantedBy=default.target" in text and "User=" not in text
    assert a.enabled() and ["enable", au.UNIT] in calls
    assert a.disable() and not a.enabled() and not (tmp_path / au.UNIT).exists()


async def test_autostart_switch_is_applied_only_when_the_user_set_it(home, cfg):
    reg = FakeReg()
    adapter = au.WindowsAutostart(reg, ["pythonw.exe", "-m", "jarves", "run", "--background"])
    core = make_core(home, cfg, autostart=adapter)
    await core.start()
    try:
        assert not reg                                     # default off: nothing written
        core.features.set("autostart", True, by="dashboard")
        assert adapter.enabled()
        core.features.set("autostart", False, by="dashboard")
        assert not adapter.enabled()
    finally:
        await core.stop()


# ---------------------------------------------------------------------- sleep / resume, unplugged mic
async def test_resume_watcher_detects_a_wall_clock_jump():
    now = [1000.0]
    seen = []

    async def fake_sleep(s):
        now[0] += s
        if len(seen) == 0 and now[0] > 1010:
            now[0] += 600                                  # the laptop slept 10 minutes
        await asyncio.sleep(0)

    async def on_resume(slept):
        seen.append(slept)

    w = ResumeWatcher(on_resume, interval_s=5, gap_s=30, clock=lambda: now[0], sleep=fake_sleep)
    task = asyncio.create_task(w.run())
    await until(lambda: seen)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert seen[0] >= 590 and w.resumes == 1


async def test_resume_ends_a_dead_conversation_and_rechecks_audio(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    stopped = []

    class DeadLive:
        async def stop(self):
            stopped.append(True)
    try:
        core.live = DeadLive()
        checks = []
        orig = core.dev.check_audio
        core.dev.check_audio = lambda: checks.append(1) or orig()
        await core.runtime._on_resume(900)
        assert stopped == [True] and core.live is None and checks
        assert core.runtime.view()["voice"]["cloud_connection_open"] is False
    finally:
        await core.stop()


async def test_stall_guard_raises_when_the_mic_goes_silent():
    async def mic():
        yield b"\0" * CHUNK
        await asyncio.Event().wait()                     # unplugged: PortAudio stops calling back

    got = []
    with pytest.raises(AudioUnavailable) as e:
        async for c in stall_guard(mic(), 0.2):
            got.append(c)
    assert e.value.code == "AUDIO_MIC_STALLED" and len(got) == 1


async def test_wake_loop_reopens_the_mic_after_a_stall(home, cfg):
    opened = []

    class StallingMic:
        def __init__(self):
            opened.append(self)

        async def mic_chunks(self, rate, chunk_bytes):
            yield bytes(chunk_bytes)
            if len(opened) == 1:
                await asyncio.Event().wait()             # first device vanishes
            while True:
                yield bytes(chunk_bytes)
                await asyncio.sleep(0.01)

        def close(self):
            pass

    cfg.runtime.mic_stall_s = 0.3
    cfg.voice.wake_enabled = True
    core = make_core(home, cfg, wake_detector=FakeDetector(), wake_audio=StallingMic)
    await core.start()
    try:
        def codes():
            return [json.loads(line).get("code") for line in
                    (home / "logs" / "jarves.log").read_text(encoding="utf-8").splitlines()]
        await until(lambda: "AUDIO_MIC_STALLED" in codes())
        await until(lambda: "AUDIO_RECOVERED" in codes())     # devices re-scanned, audio re-checked
        await until(lambda: len(opened) >= 2)       # re-opened after the 5 s back-off
        await until(lambda: core.snapshot()["wake_listening"])
        assert core.health.components["audio"].status == "ok"
    finally:
        await core.stop()


# ---------------------------------------------------------------------- idle = local only
async def test_hidden_background_idle_opens_no_cloud_voice(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    made = []

    def live_factory(*a, **k):
        made.append(1)
        raise AssertionError("no voice session may open while idle")

    def quiet():
        while True:
            yield bytes(CHUNK)

    class Mic:
        async def mic_chunks(self, rate, chunk_bytes):
            for c in quiet():
                yield c
                await asyncio.sleep(0.01)

        def close(self):
            pass

    cfg.voice.wake_enabled = True
    cfg.voice.greeting = True
    tts = FakeTTS()
    core = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=tts, console_logs=False,
                use_real_audio=False, live_factory=live_factory, wake_detector=FakeDetector(), wake_audio=Mic,
                runtime_mode="background", tray_factory=FakeTray, viewer_launcher=Launcher(),
                autostart=au.WindowsAutostart(FakeReg(), ["x"]))
    await core.start()
    try:
        await until(lambda: core.snapshot()["wake_listening"])
        await asyncio.sleep(1.0)
        v = core.snapshot()["runtime"]
        assert v["display"]["state"] == "hidden" and v["voice"]["wake_listening"]
        assert v["voice"]["cloud_connection_open"] is False and not made and not tts.spoken
        assert core.voice_mode == "live"           # cloud voice is configured, yet idle stays local
    finally:
        await core.stop()


# ---------------------------------------------------------------------- desktop mode (`jarvis`)
async def test_desktop_mode_window_close_keeps_core_and_adopts_window(home, cfg):
    procs = []

    async def launch(port):
        p = FakeProc()
        procs.append(p)
        return p

    core = make_core(home, cfg, runtime_mode="desktop", tray_factory=FakeTray)
    core.prepare_desktop = lambda: asyncio.sleep(0)
    task = asyncio.create_task(cli._run_desktop(core, launch))
    await until(lambda: core.runtime is not None and core.runtime.viewer.proc is procs[0])
    procs[0].exit(0)                                   # the user closed the window
    await until(lambda: core.runtime.display_state() == "hidden")
    assert not task.done() and not core.stopping
    ok, _ = await core.runtime.show("test")            # "show yourself" opens it again
    assert ok
    core.stop_event.set()                              # Quit in the tray
    await asyncio.wait_for(task, 30)
    assert core.health.core == "stopped"


# ---------------------------------------------------------------------- dashboard Home API
async def test_dashboard_runtime_api_live_and_stopped(home, cfg):
    from jarves.dashboard import Ctx
    core = make_core(home, cfg)
    await core.start()
    ctx = Ctx(home, cfg)
    try:
        st, body = await asyncio.to_thread(rt_mod.api_runtime, ctx, None, None)
        assert st == 200 and body["live"] and body["runtime"]["core"]["state"] == "running"
        assert body["platform_status"]["tray_icon"]["linux_arm64_pi"]["status"] == "not_applicable"
        st, body = await asyncio.to_thread(rt_mod.api_runtime_set, ctx, None, {"alerts_paused": True})
        assert st == 200 and body["runtime"]["alerts"]["paused"]
        st, body = await asyncio.to_thread(rt_mod.api_runtime_set, ctx, None, {"display": "sideways"})
        assert st == 400
    finally:
        await core.stop()
    st, body = await asyncio.to_thread(rt_mod.api_runtime, ctx, None, None)
    assert not body["live"] and body["runtime"]["core"]["state"] == "stopped" and body["runtime"]["prefs"]["alerts_paused"]
    st, body = await asyncio.to_thread(rt_mod.api_runtime_set, ctx, None, {"display": "show"})
    assert st == 409                                   # a window needs the running Core
    # mute / pause are saved choices: they can be changed while the Core is stopped and apply at the next start
    st, body = await asyncio.to_thread(rt_mod.api_runtime_set, ctx, None, {"mic_muted": True, "alerts_paused": False})
    assert st == 200 and body["live"] is False and body["saved"]["mic_muted"] and not body["saved"]["alerts_paused"]
    st, body = await asyncio.to_thread(rt_mod.api_runtime, ctx, None, None)
    assert body["runtime"]["prefs"]["mic_muted"] and not body["runtime"]["prefs"]["alerts_paused"]
    assert rt_mod.read_state(home)["prefs"]["mic_muted"] and rt_mod.alerts_paused(home) is False
    ctx.close()


def test_every_m1_feature_has_truthful_platform_status():
    from jarves.features import CATALOG
    for fid in ("background_core", "display_control", "tray_icon", "autostart"):
        targets = CATALOG[fid].targets
        assert set(targets) == {"windows", "linux_arm64_pi"}
        pi = targets["linux_arm64_pi"]
        assert pi["status"] != "verified_on_device"                 # no Pi exists yet
        if pi["status"] in ("implemented_unverified", "planned", "blocked"):
            assert pi["blocker"] and pi["roadmap_issue"].startswith("https://github.com/igalgerman/jarves/issues/")
