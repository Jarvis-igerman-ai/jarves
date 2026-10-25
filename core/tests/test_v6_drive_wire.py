"""V6 item 9 (#216): drive mode wired into SkillRegistry._authorize, before the approval prompt.
Drive mode never approves anything: an "allow" continues into the normal policy/approval unchanged, a "defer" queues
the request (shown when the owner has stopped, never run), and trust comes from Core state only (origin, the route a
code path set, the untrusted-text taint), never from a model-written field. Offline: fake Skills, tmp paths."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from jarves import drive_mode as dm
from jarves import logs
from jarves.skills import ROUTE, Skill, SkillRegistry, Tool, ToolResult

HERE = Path(__file__).resolve().parent


class SendArgs(BaseModel):
    name: str = Field("", max_length=60)
    text: str = Field("", max_length=400)
    number_source: str = "owner_said"            # a model-written field: must never change trust


class FeatArgs(BaseModel):
    id: str
    on: bool


class DialArgs(BaseModel):
    name: str = ""
    number: str = ""
    number_source: str = "owner_said"


class Fake(Skill):
    name = "fake"

    def __init__(self):
        self.ran: list[str] = []

    def tools(self):
        def h(n, untrusted=False):
            async def run(_a):
                self.ran.append(n)
                return ToolResult(True, f"{n} done", {"untrusted_text": "x"} if untrusted else {})
            return run
        return [
            Tool("gmail_read", "read mail", h("gmail_read", True), permission="read", untrusted_output=True),
            Tool("get_time", "time", h("get_time"), permission="read"),
            Tool("whatsapp_send", "send", h("whatsapp_send"), SendArgs, permission="action", confirm="Send it?"),
            Tool("end_conversation", "hang up", h("end_conversation"), permission="action"),
            Tool("dial_prepare", "dial", h("dial_prepare"), DialArgs, permission="action"),
            Tool("feature_set", "feature", h("feature_set"), FeatArgs, permission="action"),
            Tool("settings_change", "settings", h("settings_change"), permission="action"),
            Tool("note_delete", "delete a note", h("note_delete"), permission="destructive"),
        ]


ALLOWED = {"whatsapp_send", "end_conversation", "dial_prepare", "feature_set", "settings_change"}


def make(tmp_path, *, active, approve=True, contacts=({"label": "my wife", "name": "Dana Levi"},)):
    reg = SkillRegistry(allowed_actions=ALLOWED)
    skill = Fake()
    reg.register(skill)
    asked: list[str] = []

    async def confirm(prompt):
        asked.append(prompt)
        return approve
    reg.confirm = confirm
    state = {"on": active}
    gate = dm.Gate(active=lambda: state["on"], queue=dm.DriveQueue(tmp_path / "drive_queue.json"),
                   contacts=lambda: list(contacts))
    reg.drive = gate
    return reg, skill, asked, gate, state


async def on_route(route, coro_fn):
    tok = ROUTE.set(route)
    try:
        return await coro_fn()
    finally:
        ROUTE.reset(tok)


# ------------------------------------------------------------------ off = zero change
SCENARIOS = [
    ("get_time", {}, "model", "voice", False, True),
    ("whatsapp_send", {"name": "Dana", "text": "hi"}, "model", "voice", False, True),
    ("whatsapp_send", {"name": "Dana", "text": "hi"}, "model", "voice", False, False),
    ("whatsapp_send", {"name": "Dana", "text": "hi"}, "model", "voice", True, True),
    ("settings_change", {}, "model", "text", False, True),
    ("note_delete", {}, "model", "default", False, False),
    ("note_delete", {}, "user", "text", False, True),
    ("end_conversation", {}, "model", "voice", True, True),
    ("dial_prepare", {"number": "112"}, "model", "voice", True, True),
    ("feature_set", {"id": "drive_mode", "on": False}, "model", "voice", False, True),
]


@pytest.mark.parametrize("name,args,origin,route,taint,approve", SCENARIOS)
async def test_drive_off_is_byte_identical(tmp_path, name, args, origin, route, taint, approve):
    out = []
    for wired in (False, True):
        reg, skill, asked, gate, _ = make(tmp_path / str(wired), active=False, approve=approve)
        if not wired:
            reg.drive = None

        async def go():
            if taint:
                await reg.call("gmail_read", {})
            return await reg.call(name, args, origin=origin)
        res = await on_route(route, go)
        out.append((res.to_model(), asked, skill.ran))
        assert not (tmp_path / str(wired) / "drive_queue.json").exists()
    assert out[0] == out[1]


@pytest.mark.parametrize("module", ["test_registry_results.py", "test_swv3_p01.py", "test_whatsapp_injection.py",
                                    "test_helper_approvals.py", "test_review_c.py", "test_security.py"])
def test_existing_registry_tests_pass_with_drive_off_gate(module, tmp_path):
    env = dict(os.environ)
    import jarves
    src = str(Path(jarves.__file__).resolve().parents[1])          # the jarves this run imports (worktree or CI)
    env["PYTHONPATH"] = os.pathsep.join([str(HERE), src, str(HERE.parent), env.get("PYTHONPATH", "")])
    r = subprocess.run([sys.executable, "-m", "pytest", str(HERE / module), "-q", "-p", "_drive_off_plugin",
                        "-p", "no:cacheprovider"], cwd=str(tmp_path), env=env, capture_output=True, text=True,
                       timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    calls = int(r.stdout.rsplit("DRIVE_OFF_GATE_CALLS=", 1)[1].split()[0])
    assert calls > 0                              # the off gate really sat in the call path
    assert not (tmp_path / "unused.json").exists()


# ------------------------------------------------------------------ on
async def test_message_from_an_email_sourced_turn_is_deferred_not_run(tmp_path):
    reg, skill, asked, gate, _ = make(tmp_path, active=True)

    async def go():
        await reg.call("gmail_read", {})                 # long read while driving: deferred, does not run
        return await reg.call("whatsapp_send", {"name": "Dana", "text": "secret body"})
    assert skill.ran == []
    # taint the route the honest way: the registry's own untrusted marker (as a read mail would)
    ROUTE.set("voice")
    reg._untrusted_at = reg.clock()
    res = await reg.call("whatsapp_send", {"name": "Dana", "text": "secret body", "number_source": "owner_said"})
    assert not res.ok and res.code == "DRIVE_DEFERRED" and res.data["drive"] == "deferred"
    assert asked == [] and "whatsapp_send" not in skill.ran
    [item] = gate.queue.items()
    assert item.source == "untrusted" and item.action == "message_send"
    assert "secret" not in item.text and item.text == "whatsapp_send -> Dana"
    res2 = await on_route("voice", go)
    assert res2.code == "DRIVE_DEFERRED" and skill.ran == []


async def test_allowed_answer_runs_and_approvals_still_apply(tmp_path):
    reg, skill, asked, gate, _ = make(tmp_path, active=True, approve=False)
    res = await on_route("voice", lambda: reg.call("get_time", {}))
    assert res.ok and skill.ran == ["get_time"]
    # known contact, owner's own voice turn: drive mode allows, but the normal approval is still asked - and a "no"
    # is still a no (drive mode never approves)
    res = await on_route("voice", lambda: reg.call("whatsapp_send", {"name": "Dana", "text": "on my way"}))
    assert asked == ["Send it?"] and res.code == "TOOL_DECLINED" and "whatsapp_send" not in skill.ran
    assert gate.queue.items() == []


async def test_text_route_is_not_by_voice_and_unknown_contact_waits(tmp_path):
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    res = await on_route("text", lambda: reg.call("whatsapp_send", {"name": "Dana", "text": "x"}))
    assert res.code == "DRIVE_DEFERRED" and asked == []                 # typed, not spoken: by_voice is False
    res = await on_route("voice", lambda: reg.call("whatsapp_send", {"name": "Stranger", "text": "x"}))
    assert res.code == "DRIVE_DEFERRED" and asked == []
    assert [i.text for i in gate.queue.items()] == ["whatsapp_send -> Dana", "whatsapp_send -> Stranger"]


async def test_model_written_source_field_cannot_make_a_tainted_turn_trusted(tmp_path):
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    ROUTE.set("voice")
    reg._untrusted_at = reg.clock()
    res = await reg.call("dial_prepare", {"number": "112", "number_source": "owner_said"})
    assert not res.ok and res.code == "DRIVE_REFUSED" and skill.ran == []
    assert gate.queue.items() == []                       # an emergency from untrusted text is refused, not queued


async def test_emergency_from_the_owner_is_never_blocked(tmp_path):
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    for _ in range(dm.MAX_QUEUE):
        gate.queue.add("x", "filler")
    res = await on_route("voice", lambda: reg.call("dial_prepare", {"name": "משטרה"}))
    assert res.ok and skill.ran == ["dial_prepare"]


@pytest.mark.parametrize("route,taint", [("voice", False), ("voice", True), ("text", False), ("default", False)])
async def test_hang_up_is_always_allowed_by_drive_mode(tmp_path, route, taint):
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    for _ in range(dm.MAX_QUEUE):
        gate.queue.add("x", "filler")

    async def go():
        if taint:
            reg._untrusted_at = reg.clock()
        return await reg.call("end_conversation", {})
    res = await on_route(route, go)
    if taint:
        assert res.code == "TOOL_UNTRUSTED_TURN"         # the normal guard, exactly as with drive mode off
    else:
        assert res.ok and skill.ran == ["end_conversation"]
    assert not str(res.code).startswith("DRIVE")


async def test_model_cannot_turn_drive_mode_off_or_release_the_queue(tmp_path):
    reg, skill, asked, gate, state = make(tmp_path, active=True)
    gate.queue.add("settings", "waiting")
    for route in ("voice", "text", "default"):
        res = await on_route(route, lambda: reg.call("feature_set", {"id": "drive_mode", "on": False}))
        assert res.code == "DRIVE_OWNER_ONLY" and "feature_set" not in skill.ran
    for fid in ("Drive_Mode", " drive_mode ", "DRIVE_MODE"):          # feature_set normalises the id the same way
        res = await on_route("voice", lambda: reg.call("feature_set", {"id": fid, "on": False}))
        assert res.code == "DRIVE_OWNER_ONLY" and "feature_set" not in skill.ran
    assert len(gate.queue.items()) == 1 and state["on"]
    # no registered tool can release / run the queue
    assert not [t for t in reg._tools if "drive" in t]
    # the owner's own words (deterministic parser, origin="user") go on into the normal flow
    res = await on_route("voice", lambda: reg.call("feature_set", {"id": "drive_mode", "on": False}, origin="user"))
    assert res.ok and skill.ran == ["feature_set"]


async def test_policy_exception_fails_closed_for_side_effects(tmp_path, monkeypatch):
    reg, skill, asked, gate, _ = make(tmp_path, active=True)

    def boom(*a, **k):
        raise RuntimeError("policy broke")
    monkeypatch.setattr(dm, "decide", boom)
    for name, args in (("whatsapp_send", {"name": "Dana"}), ("settings_change", {}), ("note_delete", {})):
        res = await on_route("voice", lambda: reg.call(name, args))
        assert not res.ok and res.code == "DRIVE_ERROR"
    assert skill.ran == [] and asked == []
    assert (await on_route("voice", lambda: reg.call("end_conversation", {}))).ok      # hang-up still works
    assert (await on_route("voice", lambda: reg.call("get_time", {}))).ok              # a read has no side effect
    # a broken switch read fails closed too
    gate.active = lambda: (_ for _ in ()).throw(OSError("features.json"))
    assert (await on_route("voice", lambda: reg.call("settings_change", {}))).code == "DRIVE_ERROR"


async def test_queue_is_never_executed_automatically(tmp_path, capsys):
    reg, skill, asked, gate, state = make(tmp_path, active=True)
    for name in ("settings_change", "note_delete"):
        assert (await on_route("voice", lambda: reg.call(name, {}))).code == "DRIVE_DEFERRED"
    assert skill.ran == []
    released = gate.queue.release()                                     # what `jarvis drive stopped` does
    assert [i.action for i in released] == ["settings_change", "note_delete"]
    state["on"] = False
    assert skill.ran == [] and asked == [] and gate.queue.items() == []


async def test_audit_lines_carry_no_text(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(logs, "info", lambda *a, **k: seen.append((a, k)))
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append((a, k)))
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    await on_route("text", lambda: reg.call("whatsapp_send", {"name": "Dana", "text": "PRIVATE-BODY"}))
    drive = [(a, k) for a, k in seen if a and a[0] == "drive"]
    assert drive and "PRIVATE-BODY" not in repr(seen)


def test_trust_and_kind_come_from_core_state_only():
    assert dm.turn_source("model", "voice", False) == "owner"
    assert dm.turn_source("model", "voice", True) == "untrusted"
    assert dm.turn_source("user", "default", False) == "owner"
    assert dm.turn_source("model", "default", False) == "scheduled"
    assert dm.turn_source("model", "plan", False) == "scheduled"
    assert dm.tool_kind("brand_new_tool", "action") == "brand_new_tool"          # unknown -> deferred by decide()
    assert dm.tool_kind("gmail_read", "read", untrusted_output=True) == "long_read"
    assert dm.tool_kind("whatever_read", "read") == "answer"
    assert not dm.decide("brand_new_tool", active=True).allowed


def test_core_and_text_session_wire_the_gate():
    src = (HERE.parent / "src" / "jarves" / "app.py").read_text(encoding="utf-8")
    assert "drive_mode.attach(self.registry, self.features, self.home, self.memory)" in src
    src = (HERE.parent / "src" / "jarves" / "text_session.py").read_text(encoding="utf-8")
    assert "drive_mode.attach(registry, features, home, memory)" in src


# ------------------------------------------------------------------ the phone screen route (issue #311)
async def test_phone_screen_reads_are_allowed_and_never_queue(tmp_path):
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    # the same read on the normal route (a spoken turn) is still deferred: nothing about the voice path changed
    res = await on_route("voice", lambda: reg.call("gmail_read", {}, origin="user"))
    assert not res.ok and len(gate.queue.items()) == 1
    gate.queue.clear()
    skill.ran.clear()

    async def taps():
        out = []
        for _ in range(25):                                   # 25 "I am alone" taps
            out.append(await reg.call("gmail_read", {}, origin="user"))
            out.append(await reg.call("get_time", {}, origin="user"))
        return out
    outs = await on_route(dm.PHONE_SCREEN, taps)
    assert all(o.ok for o in outs), [(o.code, o.message) for o in outs if not o.ok][:2]
    assert gate.queue.items() == [] and not (tmp_path / "drive_queue.json").exists() or gate.queue.items() == []
    assert skill.ran.count("gmail_read") == 25 and asked == []


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("name,args", [
    ("whatsapp_send", {"name": "Dana", "text": "hi"}), ("dial_prepare", {"number": "112"}), ("dial_prepare", {"name": "Dana"}),
    ("end_conversation", {}), ("feature_set", {"id": "drive_mode", "on": False}), ("settings_change", {}),
    ("note_delete", {})])
async def test_phone_screen_refuses_every_non_read_tool_without_queueing(tmp_path, active, name, args):
    reg, skill, asked, gate, _ = make(tmp_path, active=active)
    res = await on_route(dm.PHONE_SCREEN, lambda: reg.call(name, args, origin="user"))
    assert not res.ok and res.code in ("DRIVE_REFUSED", "TOOL_FORBIDDEN")
    assert skill.ran == [] and asked == [] and gate.queue.items() == []


async def test_a_queue_with_real_items_is_not_released_or_changed_by_the_phone_route(tmp_path):
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    await on_route("voice", lambda: reg.call("whatsapp_send", {"name": "Zed", "text": "hi"}, origin="model"))
    before = [(i.id, i.text) for i in gate.queue.items()]
    assert before
    await on_route(dm.PHONE_SCREEN, lambda: reg.call("gmail_read", {}, origin="user"))
    assert [(i.id, i.text) for i in gate.queue.items()] == before and skill.ran == ["gmail_read"]       # only the read ran


async def test_the_model_cannot_pick_the_route(tmp_path):
    # the route comes from skills.ROUTE (Core code) only: no tool argument or model-written field changes it
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    for fake in ("phone_screen", "route=phone_screen"):
        res = await reg.call("whatsapp_send", {"name": "Dana", "text": "hi", "number_source": fake}, origin="model")
        assert not res.ok and ROUTE.get() == "default"
    res = await reg.call("gmail_read", {"route": dm.PHONE_SCREEN}, origin="model")      # an extra field is ignored
    assert not res.ok and len(gate.queue.items()) >= 1 and "gmail_read" not in skill.ran
    import inspect
    assert "PHONE_SCREEN" not in inspect.getsource(SkillRegistry)                       # the registry never sets it itself


async def test_the_phone_bridge_sets_the_route_for_the_briefing_only(tmp_path):
    import asyncio
    import threading
    from types import SimpleNamespace
    from jarves import mobile
    seen: list[str] = []

    class Brief(Skill):
        name = "drive_briefing"

        def tools(self):
            async def run(_a):
                seen.append(ROUTE.get())
                return ToolResult(True, "ok", {"sections": []})
            from jarves.drive_briefing import DriveBriefingArgs
            return [Tool("drive_briefing_get", "b", run, DriveBriefingArgs, permission="read", untrusted_output=True)]
    reg, skill, asked, gate, _ = make(tmp_path, active=True)
    reg.register(Brief())
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    try:
        bridge = mobile.core_bridge(SimpleNamespace(registry=reg), loop)
        got = bridge.drive_briefing("alone")
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert got["ok"] and seen == [dm.PHONE_SCREEN] and ROUTE.get() == "default" and gate.queue.items() == []


async def test_policy_exception_on_the_phone_screen_route_allows_reads_only(tmp_path, monkeypatch):
    """Issue #318: when the Gate itself raises on the read-only phone route, hang-up / stop / cancel do not slip
    through the fail-closed fallback: only a plain read may run there."""
    reg, skill, asked, gate, _ = make(tmp_path, active=True)

    def boom(*a, **k):
        raise RuntimeError("policy broke")
    monkeypatch.setattr(gate, "check", boom)
    res = await on_route(dm.PHONE_SCREEN, lambda: reg.call("end_conversation", {}, origin="user"))
    assert not res.ok and res.code == "DRIVE_ERROR" and skill.ran == []
    assert (await on_route(dm.PHONE_SCREEN, lambda: reg.call("get_time", {}, origin="user"))).ok
    assert (await on_route("voice", lambda: reg.call("end_conversation", {}))).ok      # elsewhere hang-up still works
    assert skill.ran == ["get_time", "end_conversation"] and asked == [] and gate.queue.items() == []


def test_fallback_allows_is_read_only_on_the_phone_screen():
    assert dm.fallback_allows("get_time", "read", dm.PHONE_SCREEN)
    assert not dm.fallback_allows("end_conversation", "action", dm.PHONE_SCREEN)
    assert dm.fallback_allows("end_conversation", "action", "voice")
    assert not dm.fallback_allows("whatsapp_send", "action", "voice")
