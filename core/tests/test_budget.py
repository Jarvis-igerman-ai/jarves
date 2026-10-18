"""CR-01 hard daily budget + CR-04 billing truth. Fully offline: mock httpx transports, scripted SDK models."""
import asyncio

import httpx
import numpy as np
import pytest

from jarves.brain import AgentBrain, BrainUnavailable, billing_routes, billing_summary
from jarves.budget import Budget
from jarves.chatgpt_auth import ChatGPTAuthError
from jarves.chatgpt_brain import ChatGPTBrain
from jarves.config import AIConfig, Config, VoiceConfig
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import ClockSkill
from jarves.voice.tts import FallbackTTS, MeteredTTS, TTSUnavailable, tts_chain

from test_chatgpt import ResponsesServer, completed, msg, signed_in_auth, sse


def fcall(cid):
    return {"type": "function_call", "call_id": cid, "name": "get_time", "namespace": "jarves", "arguments": "{}"}


def plan_brain(tmp_path, handler, limit=200, budget=None, **ai):
    cfg = Config()
    cfg.ai.daily_request_limit = limit
    cfg.ai.chatgpt_model = "gpt-6-luna"
    cfg.ai.web_search = False
    for k, v in ai.items():
        setattr(cfg.ai, k, v)
    tmp_path.mkdir(parents=True, exist_ok=True)
    mem = MemoryStore(tmp_path / "m.db")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    b = ChatGPTBrain(cfg.ai, reg, mem, signed_in_auth(tmp_path), transport=httpx.MockTransport(handler),
                     budget=budget(mem, cfg.ai) if budget else None)
    return b, mem


async def _nosleep(_s):
    return None


# ------------------------------------------------------------------ ChatGPT plan brain
async def test_plan_tool_loop_stops_at_limit(tmp_path):
    srv = ResponsesServer([(200, sse(completed([fcall("c1")]))), (200, sse(completed([fcall("c2")]))),
                           (200, sse(completed([msg("done")])))])
    b, mem = plan_brain(tmp_path, srv.handler, limit=2)
    with pytest.raises(BrainUnavailable) as e:
        await b.reply("x")
    assert e.value.code == "AI_BUDGET" and len(srv.bodies) == 2 and mem.usage_today("ai_requests") == 2
    assert b.budget.remaining("ai_requests") == 0 and b.available()[1] == "AI_BUDGET"


async def test_plan_concurrent_turns_at_limit_1_send_once(tmp_path):
    gate, sent = asyncio.Event(), []

    async def handler(req):
        sent.append(req)
        await gate.wait()
        return httpx.Response(200, content=sse(completed([msg("ok")])), headers={"content-type": "text/event-stream"})

    b, mem = plan_brain(tmp_path, handler, limit=1)
    t1, t2 = asyncio.create_task(b.reply("a")), asyncio.create_task(b.reply("b"))
    await asyncio.sleep(0.05)
    gate.set()
    r = await asyncio.gather(t1, t2, return_exceptions=True)
    assert len(sent) == 1 and mem.usage_today("ai_requests") == 1
    assert sorted(type(x).__name__ for x in r) == ["BrainUnavailable", "str"]
    assert next(x for x in r if isinstance(x, BrainUnavailable)).code == "AI_BUDGET"


async def test_plan_503_retries_each_counted_and_bounded_by_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    srv = ResponsesServer([(503, {"detail": "busy"})] * 3)
    b, mem = plan_brain(tmp_path, srv.handler)
    with pytest.raises(BrainUnavailable):
        await b.reply("x")
    assert len(srv.bodies) == 3 and mem.usage_today("ai_requests") == 3     # failed attempts are counted
    srv2 = ResponsesServer([(503, {"detail": "busy"})] * 3)
    b2, mem2 = plan_brain(tmp_path / "b", srv2.handler, limit=2)
    with pytest.raises(BrainUnavailable) as e:
        await b2.reply("x")
    assert e.value.code == "AI_BUDGET" and len(srv2.bodies) == 2 and mem2.usage_today("ai_requests") == 2


async def test_plan_failed_stream_counted(tmp_path):
    srv = ResponsesServer([(200, sse({"type": "response.failed", "response": {"error": {"code": "server_error"}}}))])
    b, mem = plan_brain(tmp_path, srv.handler)
    with pytest.raises(BrainUnavailable):
        await b.reply("x")
    assert mem.usage_today("ai_requests") == 1 and b.budget.held("ai_requests") == 0


async def test_plan_cancelled_after_send_counted(tmp_path):
    started = asyncio.Event()

    async def handler(req):
        started.set()
        await asyncio.sleep(3600)

    b, mem = plan_brain(tmp_path, handler)
    t = asyncio.create_task(b.reply("x"))
    await started.wait()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert mem.usage_today("ai_requests") == 1 and b.budget.held("ai_requests") == 0


async def test_plan_not_sent_is_not_counted(tmp_path):
    def handler(req):
        raise AssertionError("must not be sent")

    b, mem = plan_brain(tmp_path, handler, limit=1)

    async def no_token():
        raise ChatGPTAuthError("CHATGPT_SIGNED_OUT", "signed out", "login")

    b.auth.access_token = no_token
    with pytest.raises(BrainUnavailable) as e:
        await b.reply("x")
    assert e.value.code == "CHATGPT_SIGNED_OUT"
    assert mem.usage_today("ai_requests") == 0 and b.budget.remaining("ai_requests") == 1

    def refuse(req):
        raise httpx.ConnectError("no route", request=req)

    b2, mem2 = plan_brain(tmp_path / "c", refuse, limit=1)
    with pytest.raises(BrainUnavailable) as e:
        await b2.reply("x")
    assert e.value.code == "AI_OFFLINE" and mem2.usage_today("ai_requests") == 0


async def test_plan_model_cache_prefers_auth_models(tmp_path):
    srv = ResponsesServer([(200, sse(completed([msg("ok")])))])
    b, _ = plan_brain(tmp_path, srv.handler, chatgpt_model="")
    b.auth.models = lambda: [{"slug": "from-cache"}]
    assert await b.reply("x") == "ok" and srv.bodies[0]["model"] == "from-cache"


# ------------------------------------------------------------------ API Agents SDK brain
def agent_brain(tmp_path, steps, limit, monkeypatch):
    from agents.testing.model import ScriptedModel
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cfg = AIConfig(daily_request_limit=limit, web_search=False)
    mem = MemoryStore(tmp_path / "m.db")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    model = ScriptedModel(steps)
    return AgentBrain(cfg, reg, mem, model=model), mem, model


def _agent_steps():
    from agents.testing.model import assistant_message, function_call
    return [[function_call("get_time", {}, call_id="c1")], [function_call("get_time", {}, call_id="c2")],
            [assistant_message("done")]]


async def test_agent_hook_stops_multi_call_run(tmp_path, monkeypatch):
    b, mem, model = agent_brain(tmp_path, _agent_steps(), 2, monkeypatch)
    with pytest.raises(BrainUnavailable) as e:
        await b.reply("x")
    assert e.value.code == "AI_BUDGET" and len(model.calls) == 2 and mem.usage_today("ai_requests") == 2
    assert b.available()[1] == "AI_BUDGET"


async def test_agent_counts_every_model_call(tmp_path, monkeypatch):
    b, mem, model = agent_brain(tmp_path, _agent_steps(), 10, monkeypatch)
    assert await b.reply("x") == "done"
    assert len(model.calls) == 3 and mem.usage_today("ai_requests") == 3
    assert "API key" in b.describe()


async def test_agent_failed_call_counted(tmp_path, monkeypatch):
    b, mem, _ = agent_brain(tmp_path, [RuntimeError("provider blew up")], 10, monkeypatch)
    with pytest.raises(RuntimeError):
        await b.reply("x")
    assert mem.usage_today("ai_requests") == 1 and b.budget.held("ai_requests") == 0


async def test_agent_and_plan_share_budget_concurrently(tmp_path, monkeypatch):
    """Two concurrent turns on one shared Budget at limit 1: exactly one model call is made."""
    from agents.testing.model import assistant_message
    b, mem, model = agent_brain(tmp_path, [[assistant_message("a")], [assistant_message("b")]], 1, monkeypatch)
    r = await asyncio.gather(b.reply("1"), b.reply("2"), return_exceptions=True)
    assert len(model.calls) == 1 and mem.usage_today("ai_requests") == 1
    assert any(isinstance(x, BrainUnavailable) and x.code == "AI_BUDGET" for x in r)


# ------------------------------------------------------------------ cloud speech
class FakeEngine:
    def __init__(self, name, quality, fail=False):
        self.name, self.quality, self.fail, self.calls = name, quality, fail, 0

    def check(self):
        return True, "fake"

    async def synth(self, text):
        self.calls += 1
        if self.fail:
            raise TTSUnavailable("TTS_FAILED", "boom")
        return np.zeros(10, np.float32), 16000


async def test_tts_cloud_metered_then_local_when_over_budget(tmp_path):
    mem = MemoryStore(tmp_path / "m.db")
    budget = Budget(mem, AIConfig(daily_request_limit=1))
    cloud, local = FakeEngine("cloud", "cloud"), FakeEngine("local", "local-fallback")
    t = FallbackTTS([cloud, local], budget=budget)
    assert t.check()[0]
    await t.synth("hi")
    assert t.last_used == "cloud" and mem.usage_today("ai_requests") == 1
    await t.synth("again")
    assert t.last_used == "local" and cloud.calls == 1 and mem.usage_today("ai_requests") == 1


async def test_tts_failed_cloud_attempt_counted(tmp_path):
    mem = MemoryStore(tmp_path / "m.db")
    budget = Budget(mem, AIConfig(daily_request_limit=5))
    t = FallbackTTS([FakeEngine("cloud", "cloud", fail=True), FakeEngine("local", "local-fallback")], budget=budget)
    t.check()
    await t.synth("hi")
    assert t.last_used == "local" and mem.usage_today("ai_requests") == 1


async def test_tts_over_budget_skips_every_cloud_engine(tmp_path):
    mem = MemoryStore(tmp_path / "m.db")
    budget = Budget(mem, AIConfig(daily_request_limit=0))
    c1, c2, local = FakeEngine("c1", "cloud"), FakeEngine("c2", "cloud"), FakeEngine("local", "local-fallback")
    t = FallbackTTS([c1, c2, local], budget=budget)
    t.check()
    await t.synth("hi")
    assert c1.calls == c2.calls == 0 and t.last_used == "local"


def test_tts_chain_wraps_cloud_with_budget(tmp_path):
    budget = Budget(MemoryStore(tmp_path / "m.db"), AIConfig())
    chain = tts_chain(VoiceConfig(), budget=budget)
    assert all(isinstance(e, MeteredTTS) == (e.quality == "cloud") for e in chain)
    assert any(e.quality == "cloud" for e in chain)
    assert not any(isinstance(e, MeteredTTS) for e in tts_chain(VoiceConfig()))


# ------------------------------------------------------------------ CR-04 billing truth
def test_billing_routes_per_connection():
    assert billing_routes(AIConfig(connection="api_key")) == {
        "typed_text": "api_key", "spoken_voice": "api_key", "voice_backend": "api_key", "reply_speech": "api_key"}
    plan = billing_routes(AIConfig(connection="chatgpt"))
    assert plan == {"typed_text": "chatgpt_plan", "spoken_voice": "local", "voice_backend": "chatgpt_plan",
                    "reply_speech": "local"}
    assert "api_key" not in plan.values()                           # never a silent paid route
    assert billing_routes(AIConfig(connection="combined")) == {
        "typed_text": "chatgpt_plan", "spoken_voice": "api_key", "voice_backend": "api_key", "reply_speech": "api_key"}
    assert set(billing_routes(AIConfig(provider="offline")).values()) == {"local"}
    assert billing_routes(AIConfig(connection="combined"), VoiceConfig(mode="off", tts_engine="local")) == {
        "typed_text": "chatgpt_plan", "spoken_voice": "off", "voice_backend": "off", "reply_speech": "local"}


def test_combined_describe_states_who_pays(tmp_path):
    srv = ResponsesServer([])
    b, _ = plan_brain(tmp_path, srv.handler, connection="combined")
    d = b.describe()
    assert ("typed answers: ChatGPT plan; spoken conversation (GPT-Live voice + its backend reasoning/web search): "
            "API key, billed") in d
    p, _ = plan_brain(tmp_path / "p", srv.handler, connection="chatgpt")
    assert "billed" not in p.describe() and "ChatGPT plan" in p.describe()
    assert "billed" in billing_summary(AIConfig(connection="api_key"))
