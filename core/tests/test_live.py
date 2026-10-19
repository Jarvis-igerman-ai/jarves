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
