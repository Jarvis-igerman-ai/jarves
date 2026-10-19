"""Throw-away demo of the privacy strip + "What does he see?" preview for screenshots and hands-on checks: a real Core
(virtual audio, no mic, no speaker, no network) with a FAKE camera that shows a generated test pattern (never a real
camera image), the camera switch on, and the dashboard. Prints the dashboard URL; runs until Ctrl-C / --seconds.

    uv run python tools/privacy_strip_demo.py [--seconds 300] [--face]   (--face also opens the Godot face window)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def pattern(h: int = 240, w: int = 320) -> np.ndarray:
    bars = [(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192), (192, 0, 0), (0, 0, 192)]
    px = np.zeros((h, w, 3), np.uint8)
    for i, c in enumerate(bars):
        px[:, i * w // len(bars):(i + 1) * w // len(bars)] = c
    px[h // 2 - 20:h // 2 + 20, 40:80] = 255
    return px


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp)
        os.environ["JARVES_HOME"] = str(home)
        os.environ.pop("OPENAI_API_KEY", None)
        from conftest import FakeSTT, FakeTTS
        from jarves.app import Core
        from jarves.camera.backends import FakeCamera, Probe
        from jarves.config import Config
        from jarves.skills.music import FakePlayer
        from jarves.voice.audio import SimAudio
        cfg = Config()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cfg.link.port = s.getsockname()[1]
        s.close()
        cfg.voice.greeting = False
        core = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(),
                    console_logs=False, use_real_audio=False)
        await core.start()
        svc = core.camera
        svc._probe_fn = lambda: Probe(True, "fake", "fake camera (test pattern)")
        svc._factory = lambda p: FakeCamera([pattern()])
        svc.detector, svc._models = None, None
        svc.probe(fresh=True)
        svc._report()
        core.features.invalidate()
        url = await core.open_dashboard()
        print("DASHBOARD", url, flush=True)
        print("CORE_PORT", cfg.link.port, "HOME", home, flush=True)
        try:
            await asyncio.wait_for(core.stop_event.wait(), a.seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            await core.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
