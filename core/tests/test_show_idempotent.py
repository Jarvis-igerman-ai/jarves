"""`show` is idempotent (#17): at most one face process, a second show while one is starting/open is a no-op,
a show while a window is closing waits for it, and concurrent shows (tray + CLI + voice + dashboard) are safe.
Real child processes: a fake face executable in a tmp dir, launched by the real Viewer / launch_godot."""
import asyncio
import os
import sys

import pytest

from jarves import cli
from jarves.app import Core
from jarves.runtime import autostart as au
from jarves.runtime import viewer as viewer_mod
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio

from conftest import FakeSTT, FakeTTS
from test_runtime import FakeReg, until


class Faces:
    """The fake face: appends its pid to launches.txt, lives until stop_<pid> (or stop_all) appears."""

    def __init__(self, tmp_path):
        self.dir = tmp_path
        self.log = tmp_path / "launches.txt"
        script = tmp_path / "fake_face.py"
        script.write_text(
            "import os, pathlib, time\n"
            f"d = pathlib.Path(r'{tmp_path}')\n"
            "pid = os.getpid()\n"
            "(d / 'launches.txt').open('a').write(str(pid) + '\\n')\n"
            "while not (d / f'stop_{pid}').exists() and not (d / 'stop_all').exists(): time.sleep(0.02)\n",
            encoding="utf-8")
        if sys.platform == "win32":
            self.exe = tmp_path / "fake_face.cmd"
            self.exe.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        else:
            self.exe = tmp_path / "fake_face.sh"
            self.exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            self.exe.chmod(0o755)

    def pids(self):
        return self.log.read_text().split() if self.log.exists() else []

    def stop(self, pid=None):
        (self.dir / (f"stop_{pid}" if pid else "stop_all")).write_text("x")


@pytest.fixture
def faces(tmp_path, monkeypatch):
    f = Faces(tmp_path)
    monkeypatch.setenv("GODOT_EXE", str(f.exe))
    monkeypatch.delenv("JARVES_FACE_EXE", raising=False)
    monkeypatch.setattr(viewer_mod, "installed_face", lambda: tmp_path / "no-such-face.exe")
    yield f
    f.stop()


def make_core(home, cfg, **kw):
    kw.setdefault("runtime_mode", "background")
    return Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
                use_real_audio=False, autostart=au.WindowsAutostart(FakeReg(), ["x"]), **kw)


async def test_concurrent_show_spawns_exactly_one_real_face_process(home, cfg, faces):
    core = make_core(home, cfg)
    await core.start()
    try:
        rt = core.runtime
        results = await asyncio.gather(*(rt.show(f"caller{i}") for i in range(5)))
        assert all(ok for ok, _ in results)
        assert sorted(d for _, d in results) == ["already opening"] * 4 + ["opening"]
        await until(lambda: len(faces.pids()) == 1, timeout=10)
        # CLI / dashboard route and the tray route, concurrently, while the window is still starting
        await asyncio.gather(*(rt.apply({"display": "show"}, by="cli") for _ in range(3)))
        assert len(faces.pids()) == 1
        assert rt.display_state() == "starting"
    finally:
        faces.stop()
        await core.stop()


async def test_show_during_desktop_mode_loading_adopts_the_loading_window(home, cfg, faces):
    """`jarvis` opens the loading window before the Core exists; a show before it connects as a face must not open
    a second window (the loading screen is a cli client, so link.face is None)."""
    core = make_core(home, cfg, runtime_mode="desktop")
    core.prepare_desktop = lambda: asyncio.sleep(0)
    proc = await viewer_mod.launch_godot(core.cfg.link.port)
    await until(lambda: len(faces.pids()) == 1, timeout=10)
    core.preopened_viewer = proc
    await core.start()
    try:
        rt = core.runtime
        assert rt.viewer.proc is proc and rt.display_state() == "starting"
        results = await asyncio.gather(*(rt.show(f"c{i}") for i in range(5)))
        assert {d for _, d in results} == {"already opening"}
        await asyncio.sleep(0.3)
        assert len(faces.pids()) == 1
    finally:
        faces.stop()
        await core.stop()


async def test_show_after_the_user_closed_the_window_waits_for_it_then_opens_exactly_one(home, cfg, faces):
    core = make_core(home, cfg)
    await core.start()
    try:
        rt = core.runtime
        await rt.show("first")
        await until(lambda: len(faces.pids()) == 1, timeout=10)
        first = faces.pids()[0]
        assert rt.window_closed() is True               # desktop.close arrived; the process lingers a moment
        assert rt.viewer.closing
        shows = asyncio.gather(*(rt.show(f"c{i}") for i in range(5)))
        await asyncio.sleep(0.2)
        assert faces.pids() == [first]                   # nothing new while the old one is still alive
        faces.stop(first)                                # the old window ends
        results = await shows
        assert all(ok for ok, _ in results)
        await until(lambda: len(faces.pids()) == 2, timeout=10)
        await asyncio.sleep(0.3)
        assert len(faces.pids()) == 2 and rt.viewer.running and not rt.viewer.closing
        assert rt.prefs["display_hidden"] is False
    finally:
        faces.stop()
        await core.stop()


async def test_show_racing_hide_never_leaves_two_windows(home, cfg, faces):
    core = make_core(home, cfg)
    await core.start()
    try:
        rt = core.runtime
        await rt.show("first")
        await until(lambda: len(faces.pids()) == 1, timeout=10)
        first = faces.pids()[0]
        hide = asyncio.create_task(rt.hide("tray"))
        await asyncio.sleep(0.1)
        show = asyncio.create_task(rt.show("cli"))        # queued behind the hide, never concurrent with it
        await asyncio.sleep(0.1)
        assert not show.done() and faces.pids() == [first]
        faces.stop(first)
        await hide
        ok, _ = await show
        assert ok
        await until(lambda: len(faces.pids()) == 2, timeout=10)
        await asyncio.sleep(0.3)
        assert len(faces.pids()) == 2 and rt.viewer.running
    finally:
        faces.stop()
        await core.stop()
