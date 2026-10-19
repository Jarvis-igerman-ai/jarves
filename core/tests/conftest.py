import asyncio
import json
import socket

import numpy as np
import pytest
from websockets.asyncio.client import connect

from jarves.config import Config
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeSTT:
    def __init__(self, text="מה השעה"):
        self.text = text
        self.loaded_name = "fake"

    async def transcribe(self, audio):
        return self.text


class FakeTTS:
    def __init__(self):
        self.spoken = []

    def check(self):
        return True, "cloud", "fake"

    async def synth(self, text):
        self.spoken.append(text)
        return np.zeros(1600, np.float32), 16000


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def cfg():
    c = Config()
    c.link.port = free_port()
    c.voice.greeting = False      # the greeting has its own test
    c.voice.greeting_delay_s = 0  # the 0.5 s beat before the hello has its own test (test_conversation_quality)
    return c


class Face:
    """Minimal stand-in for the Godot face client (same protocol as godot/core_link.gd)."""

    def __init__(self, ws):
        self.ws = ws
        self.msgs: list[dict] = []

    async def send(self, t, payload=None):
        await self.ws.send(json.dumps({"type": t, "payload": payload or {}}))

    async def wait(self, t, pred=lambda p: True, timeout=5):
        async def go():
            while True:
                m = json.loads(await self.ws.recv())
                self.msgs.append(m)
                if m["type"] == t and pred(m["payload"]):
                    return m["payload"]
        return await asyncio.wait_for(go(), timeout)

    def states(self):
        return [m["payload"]["state"] for m in self.msgs if m["type"] == "face.set_state"]


async def drain_tools(lv):
    """Live Skill calls run in their own task (they never block the event loop): wait for them."""
    await asyncio.gather(*[t for t in list(lv._tasks) if "tool-" in t.get_name()], return_exceptions=True)


async def open_face(core, token=None):
    ws = await connect(f"ws://127.0.0.1:{core.cfg.link.port}")
    await ws.send(json.dumps({"type": "hello", "payload": {"token": token or core.link.token, "client": "godot",
                                                           "protocol": 1}}))
    f = Face(ws)
    await f.wait("hello.ok")
    return f


@pytest.fixture
async def core(home, cfg):
    from jarves.app import Core
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    yield c
    if c.health.core != "stopped":
        await c.stop()
