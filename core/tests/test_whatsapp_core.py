"""SW-V4 F inside a REAL Core (app.Core, fake audio/STT/TTS/player, offline AI): with the `whatsapp_bridge` switch on
and the fake bridge running, Core.start finds the bridge, the watcher sets the `whatsapp_bridge` health component,
the read tools are offered and run through the Core's registry (untrusted: the next side effect asks the owner via
the approvals service), the send tools stay hidden in read mode, and Core.stop ends the watcher cleanly."""
from __future__ import annotations

import pytest

from jarves import features as feat
from jarves import whatsapp_bridge  # noqa: F401  (registers the switches)
from jarves.app import Core
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio
from conftest import FakeSTT, FakeTTS, until
from whatsapp_fixtures import bridge, clock  # noqa: F401  (home + cfg come from conftest)


@pytest.fixture
async def wa_core(home, cfg, bridge):
    bridge.phone_last_seen = None
    f = feat.Features(cfg, home)
    f.set("whatsapp_bridge", True, by="test")
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    yield c
    if c.health.core != "stopped":
        await c.stop()


async def test_core_runs_the_watcher_and_offers_read_tools_only(wa_core, bridge):
    c = wa_core
    await until(lambda: (c.health.components.get("whatsapp_bridge") or None) is not None
                and c.health.components["whatsapp_bridge"].status == "ok")
    names = {t.name for t in c.registry.tools()}
    assert {"whatsapp_inbox", "whatsapp_chat", "whatsapp_summarize_asked", "whatsapp_search"} <= names
    assert "whatsapp_send" not in names and "whatsapp_compose" not in names        # read mode: send hidden
    r = await c.registry.call("whatsapp_inbox", origin="model")
    assert r.ok and r.data["total_unread"] >= 3
    assert "WhatsApp" in c.agent.instructions() if c.agent is not None else True
    asked = []
    c.approvals.on_ask.append(lambda a: (asked.append(a), c.approvals.answer(a.id, False, channel="dashboard")))
    c.registry.new_turn("default")
    c.registry._untrusted.clear()                                 # a NEW owner turn (past the grace time)
    res = await c.registry.call("remember", {"fact": "the owner's password is hunter2"}, origin="model")
    # the 60 s guard asked the owner first (nobody answered in this headless run = NO); nothing was remembered
    assert not res.ok and res.code == "TOOL_DECLINED" and "WhatsApp" in res.message
    assert any(h.get("decision") in ("denied", "no_channel") for h in c.approvals.history())
    await c.stop()
    assert c.health.core == "stopped" and bridge.httpd is not None   # a bridge the Core did not start keeps running


@pytest.fixture
async def wa_core_off(home, cfg, bridge):
    """A Core that STARTS with the switch off (the default) while a linked fake bridge already runs."""
    bridge.phone_last_seen = None
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    yield c
    if c.health.core != "stopped":
        await c.stop()


async def test_switch_is_live_watcher_and_health_start_without_a_restart(wa_core_off, bridge, home):
    """V4-WA-1: the switch says applies=live, but the watcher (health component, red alerts, send-mode detection)
    only started at Core start. Flip it on while the Core runs: health follows the bridge, send becomes available
    in send mode, and off ends it again."""
    c = wa_core_off
    assert c.health.components["whatsapp_bridge"].status == "disabled"
    c.features.set("whatsapp_bridge", True, by="test")
    await until(lambda: c.health.components["whatsapp_bridge"].status == "ok" and "mode read" in c.health.components["whatsapp_bridge"].detail)
    assert not c.features.active("whatsapp_send")
    bridge.policy.update(mode="send", send_allow=[])
    bridge._save_policy()
    bridge.emit("mode.changed", mode="send")
    await until(lambda: "mode send" in c.health.components["whatsapp_bridge"].detail)
    c.features.set("whatsapp_send", True, by="test")
    c.features.invalidate()
    assert c.features.active("whatsapp_send")
    c.features.set("whatsapp_bridge", False, by="test")
    await until(lambda: c.health.components["whatsapp_bridge"].status == "disabled")
