"""SW-V4 F: sending through the bridge - the approvals flow end to end against the fake bridge.

Registry (model origin) -> whatsapp_compose -> whatsapp_send -> ONE approvals request whose popup payload carries the
recipient's name, the number and the FULL text -> the owner's answer -> a signed single-use approval id minted only
for that approved grant -> the bridge's /send (which checks it again). Deny = nothing sent; read mode = refused before
any popup; the 11th message in an hour is refused by the bridge; night = a typed code; the first message to a person
is screen-only (no spoken yes); the kill switch stops a send that waits for its approval."""
from __future__ import annotations

import asyncio

import pytest

from jarves import approvals
from jarves import whatsapp_policy as pol
from jarves.skills import SkillRegistry
from jarves.whatsapp_bridge import WhatsAppBridgeSkill, mint_approval
from whatsapp_fixtures import BOB, DANA, GROUP, bridge, clock, home, set_send_mode  # noqa: F401

LONG = ("שלום דנה, " + "זה טקסט ארוך מאוד שצריך להופיע במלואו בחלון האישור. " * 25).strip()


class Owner:
    """An approvals channel standing in for the popup: records what was shown, answers like the owner would."""
    name = "cli"

    def __init__(self, svc, answer=True, hold: asyncio.Event | None = None):
        self.svc, self.answer, self.hold, self.shown = svc, answer, hold, []

    def available(self, a):
        return True

    def open(self, a):
        self.shown.append(a)
        if self.hold is not None:
            async def later():
                await self.hold.wait()
                self.svc.answer(a.id, self.answer, channel="cli", typed=a.typed_code)
            return later()                       # awaited by ask() -> answers when released
        self.svc.answer(a.id, self.answer, channel="cli", typed=a.typed_code)

    def close(self, a):
        pass


@pytest.fixture
def rig(bridge, home, clock):
    svc = approvals.ApprovalService(home, timeout_s=30, rate_limit=(1000, 60.0), clock=clock)
    owner = Owner(svc)
    svc.channels.append(owner)
    approvals.attach(svc)
    reg = SkillRegistry()

    async def confirm(prompt, **meta):
        return await svc.ask(prompt, **meta)
    confirm.accepts_meta = True
    reg.confirm = confirm
    skill = WhatsAppBridgeSkill(home, clock=clock)
    reg.register(skill)
    reg.allowed_actions |= {"whatsapp_compose", "whatsapp_show"}
    set_send_mode(bridge, DANA, BOB)
    yield reg, owner, svc
    approvals.detach(svc)


async def compose_send(reg, who, text):
    r = await reg.call("whatsapp_compose", {"who": who, "text": text})
    assert r.ok, r.code
    return await reg.call("whatsapp_send", {"draft_id": r.data["draft_id"]})


async def test_send_after_approval_shows_name_number_and_full_text(rig, bridge):
    reg, owner, _ = rig
    res = await compose_send(reg, "לדנה כהן", LONG)
    assert res.ok and res.code == "WA_SENT"
    assert bridge.outbox[-1] == {**bridge.outbox[-1], "to": DANA, "text": LONG}
    a = owner.shown[-1]
    assert a.kind == "message" and a.risk == "high"
    assert a.details["recipient"] == "דנה כהן" and a.details["number"] == "+972541112222"
    assert a.details["text"] == LONG and len(LONG) > 1000                      # the FULL text, no ellipsis
    assert LONG in a.prompt
    assert "first message" in a.details["warning"] and a.voice_ok is False     # first message: on screen only
    assert a.spoken_in("he") == "לשלוח הודעת וואטסאפ לדנה?"                    # spoken: kind + first name only


async def test_second_message_to_a_person_may_be_answered_by_voice(rig, bridge, clock):
    reg, owner, _ = rig
    assert (await compose_send(reg, "Bob", "first")).ok
    clock.advance(20)
    assert (await compose_send(reg, "Bob", "second")).ok
    assert owner.shown[-1].voice_ok is True and "warning" not in owner.shown[-1].details


async def test_deny_sends_nothing(rig, bridge):
    reg, owner, _ = rig
    owner.answer = False
    res = await compose_send(reg, "דנה כהן", "לא לשלוח")
    assert not res.ok and res.code == "TOOL_DECLINED" and bridge.outbox == []


async def test_eleventh_message_in_an_hour_is_refused_by_the_bridge(rig, bridge, clock):
    reg, _, _ = rig
    for i in range(10):
        assert (await compose_send(reg, "Bob", f"msg {i}")).ok, i
        clock.advance(20)
    res = await compose_send(reg, "Bob", "msg 11")
    assert not res.ok and res.code == "WA_RATE_LIMITED" and res.data["limit"] == "per_hour"
    assert len(bridge.outbox) == 10


async def test_read_mode_refuses_before_any_popup(rig, bridge):
    reg, owner, _ = rig
    bridge.policy["mode"] = "read"
    r = await reg.call("whatsapp_compose", {"who": "דנה כהן", "text": "x"})
    assert not r.ok and r.code == "WA_MODE_READ" and "whatsapp_draft" in r.data["fallback"]
    assert owner.shown == [] and bridge.outbox == []


async def test_not_allow_listed_group_and_unknown_are_refused(rig, bridge):
    reg, owner, _ = rig
    bridge.policy["send_allow"] = [BOB]
    assert (await reg.call("whatsapp_compose", {"who": "דנה כהן", "text": "x"})).code == "WA_NOT_ALLOWED"
    bridge.policy["read_scope"]["groups"] = True
    bridge.policy["read_allow"] = [GROUP]
    assert (await reg.call("whatsapp_compose", {"who": "משפחה", "text": "x"})).code == "WA_GROUP_REFUSED"
    assert (await reg.call("whatsapp_compose", {"who": "Nobody Here", "text": "x"})).code == "WA_UNKNOWN_CONTACT"
    amb = await reg.call("whatsapp_compose", {"who": "דנה", "text": "x"})
    assert amb.code == "WA_AMBIGUOUS" and len(amb.data["untrusted_candidates"]) == 2          # never guesses
    assert owner.shown == [] and bridge.outbox == []


async def test_night_needs_a_typed_code(rig, bridge, monkeypatch):
    reg, owner, _ = rig
    monkeypatch.setattr(pol, "local_hour", lambda ts=None: 2)
    bridge.hour = lambda: 2
    res = await compose_send(reg, "Bob", "late night")
    a = owner.shown[-1]
    assert a.typed_code and not a.voice_ok and "23:00-07:00" in a.details["warning"]
    assert res.ok and bridge.outbox[-1]["text"] == "late night"


async def test_send_without_owner_grant_is_refused_and_forged_core_sends_fail(rig, bridge, home, clock):
    reg, owner, svc = rig
    skill = reg.skills["whatsapp_bridge"]
    r = await reg.call("whatsapp_compose", {"who": "Bob", "text": "sneaky"})
    did = r.data["draft_id"]
    # a handler called directly (no approval asked) cannot mint an approval id
    res = await skill.tools()[-1].handler(type("A", (), {"draft_id": did})())
    assert not res.ok and res.code == "WA_APPROVAL_MISSING" and bridge.outbox == []
    # the bridge refuses an approval minted for another text (a compromised Core swapping the text after approval)
    tok = mint_approval(home, BOB, "approved text", now=clock())
    from jarves.whatsapp_bridge import BridgeClient, BridgeError
    with pytest.raises(BridgeError) as e:
        BridgeClient(home).send(BOB, "swapped text", tok)
    assert e.value.code == "WA_APPROVAL_MISMATCH" and bridge.outbox == []


async def test_kill_switch_stops_a_send_waiting_for_approval(bridge, home, clock):
    svc = approvals.ApprovalService(home, timeout_s=30, rate_limit=(1000, 60.0), clock=clock)
    release = asyncio.Event()
    owner = Owner(svc, hold=release)
    svc.channels.append(owner)
    approvals.attach(svc)
    try:
        reg = SkillRegistry()

        async def confirm(prompt, **meta):
            return await svc.ask(prompt, **meta)
        confirm.accepts_meta = True
        reg.confirm = confirm
        reg.register(WhatsAppBridgeSkill(home, clock=clock))
        reg.allowed_actions |= {"whatsapp_compose"}
        set_send_mode(bridge, BOB)
        r = await reg.call("whatsapp_compose", {"who": "Bob", "text": "queued"})
        task = asyncio.create_task(reg.call("whatsapp_send", {"draft_id": r.data["draft_id"]}))
        await asyncio.to_thread(lambda: __import__("whatsapp_fixtures").wait_until(lambda: owner.shown))
        from jarves import whatsapp_admin
        await asyncio.to_thread(whatsapp_admin.kill, home)
        release.set()                                     # the owner approves AFTER the kill
        res = await task
        assert not res.ok and res.code == "WA_KILLED" and bridge.outbox == []
    finally:
        approvals.detach(svc)
