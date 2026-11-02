"""End-to-end Core tests over the real localhost WebSocket with a fake Godot client and simulated devices."""
import asyncio
import json
import uuid

import pytest
import websockets
from websockets.asyncio.client import connect

from conftest import Face, FakeSTT, FakeTTS, open_face
from jarves.app import Core
from jarves.skills.music import FakePlayer, Track
from jarves.voice.audio import SimAudio


async def test_bad_token_is_rejected(core):
    ws = await connect(f"ws://127.0.0.1:{core.cfg.link.port}")
    await ws.send(json.dumps({"type": "hello", "payload": {"token": "wrong"}}))
    with pytest.raises(websockets.ConnectionClosed) as e:
        await ws.recv()
    assert e.value.rcvd.code == 4401
    assert core.health.components["face_link"].status == "unavailable"


async def test_no_hello_times_out(core):
    ws = await connect(f"ws://127.0.0.1:{core.cfg.link.port}")
    with pytest.raises(websockets.ConnectionClosed) as e:
        await asyncio.wait_for(ws.recv(), 30)
    assert e.value.rcvd.code == 4400


async def test_typed_question_full_path(core):
    f = await open_face(core)
    assert core.health.components["face_link"].status == "ok"
    await f.send("user.text", {"text": "מה השעה?"})
    reply = await f.wait("assistant.text")
    assert reply["source"] == "command" and reply["text"].startswith("השעה")
    await f.wait("face.set_state", lambda p: p["state"] == "idle")
    assert f.states()[-4:] == ["thinking", "speaking", "happy", "idle"]
    assert core._tts.spoken[-1] == reply["text"]


async def test_push_to_talk_music_flow(core):
    core._player.catalog = [Track("sa1", "יושבים על הגג", "שלמה ארצי")]
    core.stt.text = "נגן את שלמה ארצי"
    f = await open_face(core)
    await f.send("talk.down")
    await f.wait("face.set_state", lambda p: p["state"] == "listening")
    assert core.dev.audio.capturing
    await f.send("talk.up")
    assert (await f.wait("user.transcript"))["text"] == "נגן את שלמה ארצי"
    r = await f.wait("assistant.text")
    assert r["ok"] and "יושבים על הגג" in r["text"]
    assert core._player.np.video_id == "sa1" and not core._player.np.paused


async def test_mute_blocks_listening(core):
    f = await open_face(core)
    await f.send("mic.set_mute", {"muted": True})
    await f.wait("status", lambda p: p["mic_muted"])
    await f.send("talk.down")
    err = await f.wait("error")
    assert err["code"] == "MIC_MUTED" and not core.dev.audio.capturing
    assert "not a hardware privacy switch" in core.health.components["mic"].detail


async def test_face_disconnect_and_reconnect(core):
    f = await open_face(core)
    await f.ws.close()
    for _ in range(900):
        if core.health.components["face_link"].status == "unavailable":
            break
        await asyncio.sleep(0.05)
    assert core.health.components["face_link"].detail == "Godot face disconnected"
    f2 = await open_face(core)
    st = await f2.wait("face.set_state")          # current truth is re-sent on reconnect
    assert st["state"] == core.dev.face_state
    assert core.health.components["face_link"].status == "ok"


async def test_confirmation_for_destructive_tool(core):
    core.memory.add("secret plan")
    f = await open_face(core)
    await f.send("user.text", {"text": "תשכח את הכל"})
    req = await f.wait("confirm.request")
    await f.send("confirm.reply", {"id": req["id"], "approved": False})
    r = await f.wait("assistant.text")
    assert not r["ok"] and core.memory.count() == 1
    await f.send("user.text", {"text": "תשכח את הכל"})
    req = await f.wait("confirm.request")
    await f.send("confirm.reply", {"id": req["id"], "approved": True})
    r = await f.wait("assistant.text")
    assert r["ok"] and core.memory.count() == 0


async def test_unknown_and_malformed_messages(core):
    f = await open_face(core)
    await f.ws.send("{not json")
    assert (await f.wait("error"))["code"] == "LINK_BAD_MESSAGE"
    await f.send("does.not.exist")
    assert (await f.wait("error"))["code"] == "LINK_UNKNOWN_TYPE"
    await f.send("status.request")
    st = await f.wait("status")
    assert st["core"] in ("ready", "degraded") and "face_link" in st["components"]


async def test_missing_audio_device_is_recoverable(home, cfg):
    c = Core(cfg, home, audio=SimAudio(present=False), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(),
             console_logs=False, use_real_audio=False)
    await c.start()
    try:
        assert c.health.components["audio"].status == "unavailable"
        f = await open_face(c)
        await f.send("talk.down")
        assert (await f.wait("error"))["code"] == "AUDIO_MIC_UNAVAILABLE"
        await f.send("user.text", {"text": "מה השעה"})     # text still works
        assert (await f.wait("assistant.text"))["ok"]
    finally:
        await c.stop()


async def test_memory_survives_restart(home, cfg):
    kw = dict(audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
              use_real_audio=False)
    c = Core(cfg, home, **kw)
    await c.start()
    f = await open_face(c)
    await f.send("user.text", {"text": "תזכור שהחתול שלי נקרא מיצי"})
    await f.wait("assistant.text")
    await c.stop()
    c2 = Core(cfg, home, **kw)
    await c2.start()
    try:
        f = await open_face(c2)
        await f.send("user.text", {"text": "מה אתה זוכר"})
        assert "מיצי" in (await f.wait("assistant.text"))["text"]
    finally:
        await c2.stop()


async def test_logs_have_correlation_ids_and_no_private_text(core, home):
    f = await open_face(core)
    # A long unique marker: a bare "4321" can occur by chance in a timestamp/id/port in the log (flake #481), so a hit
    # on this token is always a real leak of the private text.
    secret = "zq" + uuid.uuid4().hex
    await f.send("user.text", {"text": f"תזכור שהקוד של הכספת {secret}"})
    await f.wait("assistant.text")
    raw = (home / "logs" / "jarves.log").read_text(encoding="utf-8")
    lines = [json.loads(l) for l in raw.splitlines()]
    assert secret not in raw
    reply = [l for l in lines if l["code"] == "REPLY"][-1]
    tool = [l for l in lines if l["code"] == "TOOL_RESULT"][-1]
    assert reply["cid"] == tool["cid"] != "-"


async def test_live_mode_toggle_and_budget(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    started = []

    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, **kw):
            self.on_state, self.on_usage = on_state, on_usage

        async def start(self):
            started.append(1)
            await self.on_state("listening")

        async def stop(self):
            self.on_usage(90)
            await self.on_state("idle")
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, live_factory=FakeLive)
    await c.start()
    try:
        assert c.voice_mode == "live"
        f = await open_face(c)
        await f.send("talk.down")
        await f.wait("face.set_state", lambda p: p["state"] == "listening")
        assert c.live is not None
        await f.send("talk.down")                    # tap again ends the conversation
        await f.wait("face.set_state", lambda p: p["state"] == "idle")
        assert c.live is None and c.memory.usage_today("voice_seconds") == 90
        c.cfg.ai.daily_realtime_minutes = 1           # 90 s already used > 60 s budget
        await f.send("talk.down")
        assert (await f.wait("error"))["code"] == "VOICE_DAILY_LIMIT" and len(started) == 1
    finally:
        await c.stop()


async def test_cli_shutdown_and_status(core):
    ws = await connect(f"ws://127.0.0.1:{core.cfg.link.port}")
    await ws.send(json.dumps({"type": "hello", "payload": {"token": core.link.token, "client": "cli"}}))
    await ws.send(json.dumps({"type": "core.shutdown", "payload": {}}))
    await asyncio.wait_for(core.stop_event.wait(), 30)


async def test_greeting_on_first_face_connect(home, cfg):
    cfg.voice.greeting = True
    tts = FakeTTS()
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=tts, console_logs=False,
             use_real_audio=False)
    await c.start()
    try:
        f = await open_face(c)
        g = await f.wait("assistant.text", lambda p: p.get("code") == "GREETING")
        assert g["text"] and "רווח" not in g["text"]
        await f.wait("face.set_state", lambda p: p["state"] == "idle")
        assert len(tts.spoken) == 1 and "ג'ארוויס" in tts.spoken[0]      # said hello ONCE, Hebrew-written name
        assert "JARVES" not in tts.spoken[0]                          # 19 Oct: the Latin name was mispronounced
        await f.ws.close()
        f2 = await open_face(c)                                       # reconnect: no second greeting
        await f2.send("status.request")
        await f2.wait("status")
        assert not any(m["type"] == "assistant.text" for m in f2.msgs)
    finally:
        await c.stop()


async def test_silent_mic_is_reported_not_ignored(home, cfg):
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(text=""), tts=FakeTTS(),
             console_logs=False, use_real_audio=False)
    await c.start()
    try:
        f = await open_face(c)
        await f.send("talk.down")
        await f.wait("face.set_state", lambda p: p["state"] == "listening")
        await f.send("talk.up")
        err = await f.wait("error")
        assert err["code"] == "MIC_SILENT" and c.snapshot()["components"]["mic"]["status"] == "degraded"
    finally:
        await c.stop()


async def test_remote_close_frees_live_slot_and_restarts(home, cfg, monkeypatch):
    """CR-02 at Core level: a server-side close clears core.live, marks voice degraded, and Talk starts anew."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    sessions = []

    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, on_end, on_health, budget):
            self.on_state, self.on_end, self.running = on_state, on_end, False
            sessions.append(self)

        async def start(self):
            self.running = True
            await self.on_state("listening")

        async def stop(self):
            self.running = False

        async def remote_close(self):
            self.running = False
            await self.on_end("remote_closed")
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, live_factory=FakeLive)
    await c.start()
    try:
        f = await open_face(c)
        await f.send("talk.down")
        await f.wait("face.set_state", lambda p: p["state"] == "listening")
        await sessions[0].remote_close()
        assert c.live is None and c.snapshot()["components"]["voice"]["status"] == "degraded"
        assert (await f.wait("error"))["code"] == "LIVE_ENDED"
        await f.send("talk.down")                                  # not stuck: a new session starts
        await f.wait("face.set_state", lambda p: p["state"] == "listening")
        assert len(sessions) == 2 and c.live is sessions[1]
    finally:
        await c.stop()


async def test_shutdown_during_pending_confirmation_denies(home, cfg):
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    f = await open_face(c)
    c.memory.add("a")
    c.memory.add("b")
    await f.send("user.text", {"text": "תשכח את הכל"})
    await f.wait("confirm.request")
    await c.stop()                                                  # nobody answered; shutdown must deny
    from jarves.memory import MemoryStore
    assert MemoryStore(home / "memory.db").count() == 2          # nothing deleted


async def test_cli_talk_starts_live_without_wake_word(home, cfg, monkeypatch):
    """`jarvis talk` (talk.start, role cli): live conversation with no face and no wake word; muted -> refused."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    started = []

    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, **kw):
            self.on_state, self.on_usage = on_state, on_usage

        async def start(self):
            started.append(1)
            await self.on_state("listening")

        async def stop(self):
            self.on_usage(1)
            await self.on_state("idle")
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, live_factory=FakeLive)
    await c.start()
    try:
        ws = await connect(f"ws://127.0.0.1:{c.cfg.link.port}")
        await ws.send(json.dumps({"type": "hello", "payload": {"token": c.link.token, "client": "cli"}}))
        cli = Face(ws)
        await cli.wait("hello.ok")
        await cli.send("talk.start")
        r = await cli.wait("talk.state")
        assert r["ok"] and r["live"] and c.live is not None and len(started) == 1
        await cli.send("talk.stop")
        r = await cli.wait("talk.state", lambda p: not p["live"])
        assert r["ok"] and c.live is None
        c.dev.set_mic_muted(True)
        await cli.send("talk.start")
        r = await cli.wait("talk.state", lambda p: p.get("code") == "MIC_MUTED")
        assert not r["ok"] and len(started) == 1
    finally:
        await c.stop()
