"""The REAL Godot face (headless, no window, no sound) must survive every message the Core can send - including
malformed ones: unknown kinds, wrong types, huge text, bad JSON, binary frames. A fake Core serves the face.
Runs only when GODOT_EXE points at a Godot 4.7 binary (CI e2e job sets it; locally: the console exe)."""
import asyncio
import json
import os
import socket
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

GODOT = os.environ.get("GODOT_EXE", "")
GODOT_DIR = Path(__file__).resolve().parents[2] / "godot"
pytestmark = pytest.mark.skipif(not GODOT or not Path(GODOT).exists(), reason="set GODOT_EXE to run the real-face test")

STATES = ["idle", "listening", "thinking", "speaking", "happy", "error"]
KINDS = ["thinking", "searching", "reading", "music", "time", "memory", "tool", "connecting", "done"]
EMOTIONS = ["laugh", "cry", "surprised", "love", "sad"]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def messages():
    m = lambda t, p=None: json.dumps({"type": t, "payload": p if p is not None else {}})  # noqa: E731
    out = []
    out.append(m("status", {"core": "ready", "voice_mode": "live", "live_session": False, "wake_listening": True,
                            "components": {"voice": {"status": "ok"}, "wake_word": {"status": "ok"}}}))
    out += [m("face.set_state", {"state": s}) for s in STATES + ["weird", "", None]]
    out.append(m("status", {"live_session": True, "face_state": "listening"}))
    out.append(m("status", {"voice_connecting": True}))
    out.append(m("status", {"mic_muted": True}))
    out.append(m("status", {"components": {"voice": {"status": "degraded", "detail": "x", "action": "y"}}}))
    out.append(m("status", {"components": "not-a-dict", "live_session": "yes"}))
    out += [m("assistant.activity", {"kind": k, "text": "מחפש ברשת…", "detail": "שלמה ארצי – תתארו לכם"}) for k in KINDS]
    out += [m("assistant.activity", p) for p in (
        {"kind": "nonsense", "text": "x"}, {"kind": "music"}, {"text": "no kind"}, {"kind": "tool", "text": 123},
        {"kind": None, "text": None, "detail": None}, {"kind": "music", "text": "a" * 5000, "detail": "b" * 5000}, {})]
    out += [m("face.emotion", {"emotion": e, "ms": 800}) for e in EMOTIONS]
    out += [m("face.emotion", p) for p in (
        {"emotion": "unknown", "ms": 500}, {"emotion": "laugh", "ms": -5}, {"emotion": "cry", "ms": "soon"},
        {"emotion": "laugh", "ms": 10 ** 9}, {"emotion": 7}, {})]
    # camera (WP-J): gaze target and indicator, also malformed
    out += [m("face.set_gaze", p) for p in (
        {"x": 0.4, "y": -0.2, "confidence": 0.9}, {"x": -1, "y": 1, "confidence": 1}, {"x": 99, "y": -99, "confidence": 5},
        {"x": "left", "y": None, "confidence": "high"}, {"x": 0, "y": 0, "confidence": 0}, {})]
    out += [m("camera.indicator", p) for p in (
        {"on": True, "flash": True}, {"on": True}, {"on": "yes", "flash": 1}, {"on": None}, {}, {"on": False})]
    # appearance (WP-M2): every presentation, then malformed modes; a speaking level / emotion reach the avatars too
    for mode in ("avatar2d", "avatar3d", "hidden", "face", "avatar3d"):
        out.append(m("appearance.set", {"mode": mode}))
        out.append(m("face.set_state", {"state": "speaking"}))
        out.append(m("speaker.level", {"level": 0.6}))
        out.append(m("face.emotion", {"emotion": "laugh", "ms": 300}))
    out += [m("appearance.set", p) for p in (
        {"mode": "AVATAR3D"}, {"mode": 3}, {"mode": None}, {"mode": ["face"]}, {"mode": {"a": 1}}, {"mode": "x" * 5000},
        {"mode": True}, {})]
    out.append(m("appearance.set", {"mode": "avatar2d"}))
    out += [m("mic.level", {"level": v}) for v in (0, 0.5, 1, -3, 99, "x", None)]
    out += [m("speaker.level", {"level": v}) for v in (0, 0.7, 5, -1, "loud")]
    out.append(m("assistant.text", {"text": "שלום! " * 800, "source": "live"}))
    out.append(m("user.transcript", {"text": "מה השעה"}))
    out.append(m("error", {"code": "X", "message": "boom", "action": "retry"}))
    out.append(m("confirm.request", {"id": "c1", "prompt": "למחוק הכל?"}))
    out += [m("dashboard.opened", p) for p in (
        {"url": "http://127.0.0.1:5000/", "browser": True}, {"url": "http://127.0.0.1:5000/", "browser": False},
        {"url": None, "browser": "yes"}, {})]
    out.append(m("no.such.type", {"a": 1}))
    out.append("this is not json {{{")
    out.append(json.dumps([1, 2, 3]))
    out.append(m("face.emotion", {"emotion": "laugh", "ms": 400}))        # still alive after all of it
    out.append(m("face.set_state", {"state": "happy"}))
    return out


async def test_face_survives_every_protocol_message(tmp_path):
    port = free_port()
    (tmp_path / "link_token").write_text("t" * 24, encoding="utf-8")
    sent, hello = [], asyncio.Event()

    async def core(ws):
        h = json.loads(await ws.recv())
        assert h["type"] == "hello" and h["payload"]["token"] == "t" * 24
        await ws.send(json.dumps({"type": "hello.ok", "payload": {"protocol": 1}}))
        hello.set()
        await asyncio.sleep(1.0)                                   # let the face build its UI
        for raw in messages():
            await ws.send(raw)
            sent.append(raw)
            await asyncio.sleep(0.04)
        await ws.send(b"\x00\x01\x02binary-frame")
        await asyncio.sleep(1.5)
        try:
            await asyncio.wait_for(ws.recv(), 0.1)
        except Exception:
            pass

    env = {**os.environ, "JARVES_HOME": str(tmp_path)}
    async with serve(core, "127.0.0.1", port):
        proc = await asyncio.create_subprocess_exec(
            GODOT, "--headless", "--path", str(GODOT_DIR), "res://face/face_app.tscn", "--", f"--core-port={port}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
        try:
            await asyncio.wait_for(hello.wait(), 60)
            await asyncio.sleep(0.5 + 0.04 * len(messages()) + 3.5)
            assert proc.returncode is None, "the face process died while receiving messages"
        finally:
            if proc.returncode is None:
                proc.terminate()
            out = (await asyncio.wait_for(proc.stdout.read(), 20)).decode("utf-8", "replace")
            await proc.wait()
    bad = [l for l in out.splitlines() if "SCRIPT ERROR" in l or "Parse Error" in l or l.startswith("ERROR:")]
    assert len(sent) == len(messages())
    assert not bad, "the face logged script errors:\n" + "\n".join(bad[:12])
