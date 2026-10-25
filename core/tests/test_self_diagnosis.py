"""Self-diagnosis on failure, slices 1+2 (issue #336): the ask-first gate and the read-only area self-check.
Fakes only: fake ask, fake clock, fake model, seeded fake secrets. No network, no real account."""
from __future__ import annotations

import asyncio
import json
import socket

import pytest

from jarves import features as feat, guides, logs
from jarves.diag import areas, failure_gate as fg
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult, NoArgs

SECRET = "sk-SECRETSECRETSECRET1234567890"
PHONE = "+972541234567"
MAIL = "owner.private@example.com"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, answer=True, enabled=True, lang="en", model=None, wording=False, **kw):
        self.asked, self.delivered, self.collected = [], [], []
        self.answer, self.on = answer, enabled
        self.clock = Clock()
        self.gate = fg.FailureGate(enabled=lambda: self.on, ask=self._ask, deliver=self._deliver, collect=self._collect,
                                   lang=lambda: lang, ask_model=model, wording_enabled=lambda: wording,
                                   clock=self.clock, **kw)

    async def _ask(self, prompt, **meta):
        self.asked.append((prompt, meta))
        return self.answer

    def _deliver(self, text, rep):
        self.delivered.append((text, rep))

    def _collect(self, area, code, data):
        self.collected.append((area, code, data))
        return areas.run(area, code, home=None, failure_data=data)

    async def fail(self, code="ANYDO_AUTH_EXPIRED", tool="anydo_list_tasks", route="text", **data):
        self.gate.notify(tool, ToolResult(False, "failed", data, code=code), route)
        for t in list(self.gate._tasks):
            await t


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------------------------------ the gate
def test_failure_asks_one_short_question_then_runs_the_selfcheck_on_yes():
    async def go():
        r = Rig()
        await r.fail()
        assert len(r.asked) == 1
        prompt, meta = r.asked[0]
        assert len(prompt.splitlines()) <= 2 and meta["risk"] == "low" and meta["voice"] is False   # a card, not aloud
        assert meta["details"] == {"area": "anydo", "code": "ANYDO_AUTH_EXPIRED"}
        assert r.collected and r.delivered and "Self-check" in r.delivered[0][0]
        assert "Any.do" in r.delivered[0][0]
    run(go())


def test_hebrew_question_and_answer():
    async def go():
        r = Rig(lang="he")
        await r.fail()
        assert any("֐" <= ch <= "׿" for ch in r.asked[0][0])
        assert any("֐" <= ch <= "׿" for ch in r.delivered[0][0])
    run(go())


@pytest.mark.parametrize("answer", [False])
def test_no_or_timeout_skips_without_running_anything(answer):
    async def go():
        r = Rig(answer=answer)
        await r.fail()
        assert len(r.asked) == 1 and not r.collected and not r.delivered
    run(go())


def test_voice_failure_is_asked_aloud_other_routes_only_a_card():
    async def go():
        r = Rig()
        await r.fail(code="LIVE_CONNECTION_LOST", tool="", route="voice")
        assert r.asked[0][1]["voice"] is True
        r2 = Rig()
        await r2.fail(route="phone")
        assert r2.asked[0][1]["voice"] is False
    run(go())


def test_same_problem_not_asked_again_inside_the_cooldown_but_after_it():
    async def go():
        r = Rig(answer=False)
        await r.fail()
        await r.fail()
        assert len(r.asked) == 1
        r.clock.t += 599
        await r.fail()
        assert len(r.asked) == 1
        r.clock.t += 2
        await r.fail()
        assert len(r.asked) == 2
    run(go())


def test_at_most_three_questions_an_hour_then_again_after_it():
    async def go():
        r = Rig(answer=False)
        for code in ("ANYDO_AUTH_EXPIRED", "ANYDO_RATE_LIMITED", "GOOGLE_AUTH_EXPIRED", "GOOGLE_OFFLINE"):
            await r.fail(code=code, tool="")
        assert len(r.asked) == 3
        r.clock.t += 3601
        await r.fail(code="GOOGLE_OFFLINE", tool="")
        assert len(r.asked) == 4
    run(go())


def test_off_switch_never_asks():
    async def go():
        r = Rig(enabled=False)
        await r.fail()
        assert not r.asked
        r.on = True
        await r.fail()
        assert len(r.asked) == 1
    run(go())


def test_background_jobs_and_automations_never_ask():
    async def go():
        r = Rig()
        await r.fail(route="default")
        assert not r.asked
    run(go())


def test_only_user_visible_diagnosable_failures_ask():
    async def go():
        r = Rig()
        await r.fail(code="TOOL_BAD_ARGS", tool="anydo_x")           # the model's own mistake
        await r.fail(code="ANYDO_TASK_NOT_FOUND")                    # no guide problem explains it
        await r.fail(code="SOMETHING_ELSE", tool="calc")
        await r.fail(safety_rule="untrusted")                        # refused by a safety rule
        r.gate.notify("anydo_list", ToolResult(True, "ok", code="ok"), "text")
        assert not r.asked
    run(go())


def test_one_question_open_at_a_time():
    async def go():
        r = Rig()
        gate = asyncio.Event()

        async def slow(prompt, **meta):
            r.asked.append(prompt)
            await gate.wait()
            return False
        r.gate._ask = slow
        r.gate.notify("", ToolResult(False, "x", code="ANYDO_AUTH_EXPIRED"), "text")
        r.gate.notify("", ToolResult(False, "x", code="GOOGLE_AUTH_EXPIRED"), "text")
        await asyncio.sleep(0)
        assert len(r.asked) == 1
        gate.set()
        for t in list(r.gate._tasks):
            await t
    run(go())


def test_a_failing_collector_still_ends_with_a_plain_message_and_frees_the_gate():
    async def go():
        r = Rig()

        def boom(*a):
            raise RuntimeError("x")
        r.gate.collect = boom
        await r.fail()
        assert r.delivered and r.gate._busy is False
    run(go())


# ------------------------------------------------------------------------------------------ the registry hook
class _FailSkill(Skill):
    name = "anydo"

    def tools(self):
        async def bad(_a):
            return ToolResult(False, "expired", code="ANYDO_AUTH_EXPIRED")
        return [Tool("anydo_list_tasks", "x", bad, NoArgs, permission="read")]


def test_registry_calls_the_hook_with_tool_result_and_route_and_survives_a_broken_hook():
    async def go():
        reg = SkillRegistry()
        reg.register(_FailSkill())
        seen = []
        reg.on_failure = lambda tool, res, route: seen.append((tool, res.code, route))
        res = await reg.call("anydo_list_tasks", {})
        assert not res.ok and seen == [("anydo_list_tasks", "ANYDO_AUTH_EXPIRED", "default")]

        def broken(*a):
            raise RuntimeError("hook")
        reg.on_failure = broken
        assert (await reg.call("anydo_list_tasks", {})).code == "ANYDO_AUTH_EXPIRED"
    run(go())


# ------------------------------------------------------------------------------------------ the runners
def _seed(home):
    (home / "anydo").mkdir(exist_ok=True)
    (home / "anydo" / "token.json").write_text(json.dumps(
        {"refresh_token": SECRET, "access_token": SECRET, "scopes": ["access_anydo_data"]}), encoding="utf-8")
    (home / "google").mkdir(exist_ok=True)
    (home / "google" / "token.json").write_text(json.dumps(
        {"refresh_token": SECRET, "scopes": [], "last_error": f"invalid_grant for {MAIL}"}), encoding="utf-8")
    (home / "logs").mkdir(exist_ok=True)
    rows = [{"level": "WARNING", "component": "anydo", "code": "ANYDO_AUTH_EXPIRED", "msg": f"token {SECRET} {PHONE}",
             "text": f"message from {MAIL}"},
            {"level": "INFO", "component": "skills", "code": "TOOL_RESULT", "msg": "unrelated"}]
    (home / "logs" / "jarves.log").write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")


def _tree(home):
    return sorted((str(p.relative_to(home)), p.stat().st_size, p.stat().st_mtime_ns) for p in home.rglob("*") if p.is_file())


@pytest.mark.parametrize("area,code", [("anydo", "ANYDO_AUTH_EXPIRED"), ("google", "GOOGLE_AUTH_EXPIRED"),
                                       ("whatsapp", "WA_BRIDGE_DOWN"), ("voice", "LIVE_CONNECTION_LOST")])
def test_runner_matches_the_guide_and_leaks_nothing(home, monkeypatch, area, code):
    _seed(home)
    from jarves import whatsapp_bridge as wb
    monkeypatch.setattr(wb, "status_or_none", lambda h: {"state": "connected", "me": PHONE, "ready": True,
                                                         "last": {"text": f"hi {MAIL}"}})
    rep = areas.run(area, code, home=home, failure_data={"error": SECRET, "from": PHONE, "items": [{"title": MAIL}]})
    blob = json.dumps(rep.to_dict(), ensure_ascii=False)
    for bad in (SECRET, PHONE, MAIL, "SECRETSECRET"):
        assert bad not in blob, (area, bad)
    assert rep.confidence == "high" and rep.guide_id
    assert rep.cause_en and rep.fix_en and rep.cause_he and rep.fix_he
    assert any("֐" <= ch <= "׿" for ch in rep.text("he")) and len(rep.evidence) <= areas.MAX_EVIDENCE
    ev = "\n".join(rep.evidence)
    assert f"error_code: {code}" in ev and "jarves_version" in ev and "response_shape" in ev
    assert '"error": "str"' in ev                              # shape only: key name + type name


def test_runner_collects_state_and_last_log_codes_only(home):
    _seed(home)
    rep = areas.run("anydo", "ANYDO_AUTH_EXPIRED", home=home)
    ev = "\n".join(rep.evidence)
    assert "connected: True" in ev and "scopes_count: 1" in ev
    assert "WARNING anydo ANYDO_AUTH_EXPIRED" in ev and "TOOL_RESULT" not in ev
    g = areas.run("google", "GOOGLE_AUTH_EXPIRED", home=home)
    assert "last_error_code" not in "\n".join(g.evidence)      # free text is never copied


def test_unknown_code_is_honest_and_points_to_the_coding_agent():
    rep = areas.run("anydo", "ANYDO_NEVER_SEEN_BEFORE")
    assert rep.confidence == "low" and not rep.guide_id and "do not recognise" in rep.cause_en
    assert rep.cause_he and rep.fix_he


def test_runner_is_read_only_and_makes_no_outside_network_call(home, monkeypatch):
    _seed(home)
    before = _tree(home)
    real = socket.socket.connect

    def guard(self, addr, *a, **k):
        assert addr[0] in ("127.0.0.1", "::1", "localhost"), addr          # only this PC
        return real(self, addr, *a, **k)
    monkeypatch.setattr(socket.socket, "connect", guard)
    for area in areas.AREAS:
        areas.run(area, "", home=home)
    assert _tree(home) == before


def test_every_guide_code_resolves_to_an_area_and_a_bilingual_entry():
    n = 0
    for g in guides.load().values():
        for p in g.problems:
            for code in p.get("codes", []):
                n += 1
                assert areas.area_for(code) in areas.AREAS, code
                assert areas.match_problem(code)[0] is p
                assert p["he"] and p["fix_he"] and p["en"] and p["fix_en"]
    assert n >= 15


def test_area_for_uses_code_then_tool_name():
    assert areas.area_for("WA_KILLED") == "whatsapp" and areas.area_for("", "gmail_search") == "google"
    assert areas.area_for("X", "calc") is None


# ------------------------------------------------------------------------------------------ model wording
def test_model_sees_only_the_redacted_report_and_its_text_is_redacted():
    async def go():
        seen = []

        async def model(prompt):
            seen.append(prompt)
            return f"Sign in again. (key {SECRET}, {PHONE})"
        _seed_dir = None
        rep = areas.run("anydo", "ANYDO_AUTH_EXPIRED", failure_data={"error": SECRET})
        rep.evidence.append(f"leak: {SECRET} {PHONE} {MAIL}")
        out = await areas.word(rep, "en", model)
        assert rep.ai_worded and SECRET not in out and PHONE not in out
        for bad in (SECRET, PHONE, MAIL):
            assert bad not in seen[0]
    run(go())


def test_wording_failure_or_off_falls_back_to_the_deterministic_text():
    async def go():
        async def broken(prompt):
            raise TimeoutError

        rep = areas.run("anydo", "ANYDO_AUTH_EXPIRED")
        assert await areas.word(rep, "en", broken) == rep.text("en") and not rep.ai_worded
        assert await areas.word(rep, "en", None) == rep.text("en")
        called = []

        async def model(p):
            called.append(p)
            return "ok"
        r = Rig(model=model, wording=False)
        await r.fail()
        assert not called
        r2 = Rig(model=model, wording=True)
        await r2.fail()
        assert called and r2.delivered[0][0].endswith("ok")
    run(go())


# ------------------------------------------------------------------------------------------ features, CLI
def test_switches_exist_default_on_and_are_in_the_guide():
    for fid in ("self_diagnosis_ask", "self_diagnosis_ai_wording"):
        assert fid in feat.CATALOG and feat.CATALOG[fid].default_enabled
        assert fid in guides.load()["diagnosis"].features


def test_cli_diagnose_area_json(home, capsys):
    from jarves import cli
    assert cli.main(["diagnose", "google", "--code", "GOOGLE_AUTH_EXPIRED", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["area"] == "google" and d["confidence"] == "high" and d["guide_id"] == "google_calendar"
