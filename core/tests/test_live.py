"""GPT-Live adapter: session config and delegated function-call round trip (offline, fake connection)."""
import json

from conftest import drain_tools

from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import ClockSkill, MemorySkill
from jarves.voice.live import LiveVoice


class FakeConn:
    def __init__(self):
        self.sent = []
        outer = self

        class Item:
            async def create(self, item, event_id=None):
                outer.sent.append(("response.item.create", item))

        class Resp:
            item = Item()

            async def create(self, event_id=None):
                outer.sent.append(("response.create", None))
        self.response = Resp()


def test_reply_audio_interval_survives_delayed_transcript_flush(monkeypatch):
    import struct
    from jarves.voice import live
    lv = LiveVoice(SkillRegistry(), lambda: "")
    clock = {"mono": 100.0, "wall": 1_800_000_000.0}
    monkeypatch.setattr(live.time, "monotonic", lambda: clock["mono"])
    monkeypatch.setattr(live.time, "time", lambda: clock["wall"])
    pcm = struct.pack("<h", 4000) * 2400     # 100 ms of generated speech energy, no physical audio device
    lv._play(pcm)
    clock.update(mono=105.0, wall=1_800_000_005.0)
    lv._play(pcm)
    clock.update(mono=120.0, wall=1_800_000_020.0)     # transcript flushed well after the actual speech
    timing = lv._take_reply_wait()
    assert timing["audio_started"] == 1_800_000_000.0
    assert abs(timing["audio_ended"] - 1_800_000_005.1) < 0.001
    assert lv._take_reply_wait() == {}


def test_reply_audio_onset_accounts_for_already_queued_speech(monkeypatch):
    import struct
    from jarves.voice import live
    lv = LiveVoice(SkillRegistry(), lambda: "")
    monkeypatch.setattr(live.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(live.time, "time", lambda: 1_800_000_000.0)
    lv._speaking_until = 103.0
    lv._play(struct.pack("<h", 4000) * 2400)
    timing = lv._take_reply_wait()
    assert timing["audio_started"] == 1_800_000_003.0
    assert abs(timing["audio_ended"] - 1_800_000_003.1) < 0.001


async def test_interrupt_does_not_reuse_abandoned_audio_timing(monkeypatch):
    import struct
    from jarves.voice import live
    lv = LiveVoice(SkillRegistry(), lambda: "")
    monkeypatch.setattr(live.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(live.time, "time", lambda: 1_800_000_000.0)
    lv._play(struct.pack("<h", 4000) * 2400)
    lv._reply_wait_s = 2.0
    await lv.interrupt()
    assert lv._take_reply_wait() == {}
    monkeypatch.setattr(live.time, "monotonic", lambda: 110.0)
    monkeypatch.setattr(live.time, "time", lambda: 1_800_000_010.0)
    lv._play(struct.pack("<h", 4000) * 2400)
    assert lv._take_reply_wait()["audio_started"] == 1_800_000_010.0


def test_session_reset_clears_unflushed_audio_timing():
    lv = LiveVoice(SkillRegistry(), lambda: "")
    lv._reply_audio_started, lv._reply_audio_ended = 1.0, 2.0
    lv._reply_wait_s, lv._wait_ref = 3.0, 4.0
    lv._reset_session()
    assert lv._take_reply_wait() == {} and lv._wait_ref == 0.0


async def test_session_config_uses_current_models(tmp_path):
    reg = SkillRegistry()
    reg.register(ClockSkill())
    lv = LiveVoice(reg, lambda: "backend prompt")
    cfg = lv.session_config()
    assert cfg["model"] == "gpt-live-1" and cfg["audio"]["format"] == {"type": "audio/pcm", "rate": 24000}
    d = cfg["delegation"]
    assert d["type"] == "responses" and d["responses"]["model"] == "gpt-6-luna"
    names = [t.get("name", t["type"]) for t in d["responses"]["tools"]]
    assert "get_time" in names and "web_search" in names


async def test_delegated_function_call_runs_skill_and_resumes(tmp_path):
    mem = MemoryStore(tmp_path / "m.db")
    reg = SkillRegistry()
    reg.register(MemorySkill(mem))
    states, transcripts = [], []

    async def on_state(s):
        states.append(s)

    async def on_tr(role, text):
        transcripts.append((role, text))
    lv = LiveVoice(reg, lambda: "", on_state=on_state, on_transcript=on_tr)
    lv.conn = FakeConn()
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "תזכור שאני גר בחיפה"})
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {"type": "response.created"}})
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "remember", "call_id": "c1",
                 "arguments": json.dumps({"text": "אני גר בחיפה"})}}})
    await drain_tools(lv)
    assert mem.search("חיפה")
    kind, item = lv.conn.sent[0]
    assert kind == "response.item.create" and item["call_id"] == "c1" and json.loads(item["output"])["ok"]
    assert lv.conn.sent[1][0] == "response.create"
    await lv.handle_event({"type": "session.output_transcript.delta", "delta": "זכרתי"})
    assert transcripts == [("user", "תזכור שאני גר בחיפה")] and "thinking" in states
    await lv.handle_event({"type": "session.usage.updated", "usage": {"seconds": 42.5}})
    assert lv.usage_s == 42.5
