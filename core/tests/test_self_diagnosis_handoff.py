"""#336 slice C: hand an unexplained failure to a coding agent after two approvals. Fake Orchestrator, fake asks, seeded fake
secrets. No live agent, no network, no spend."""
from __future__ import annotations

import asyncio

import pytest

from jarves import features as feat
from jarves.diag import areas, failure_gate as fg, handoff as ho
from jarves.diag.diag_ticket import TicketBlocked
from jarves.skills import ToolResult

SECRET = "sk-SECRETSECRETSECRET1234567890"


class FakeOrch:
    def __init__(self, result=None):
        self.calls, self.result = [], result or {"ok": True, "text": "session abc in a new worktree"}

    def start_session(self, provider, project, task, **kw):
        self.calls.append((provider, project, task, kw))
        return self.result


def rep(**kw):
    r = areas.run("anydo", "ANYDO_AUTH_EXPIRED", home=None)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_switch_is_beta_off_and_needs_the_orchestrator():
    f = feat.CATALOG[ho.FEATURE]
    assert f.default_enabled is False and feat.stage_of(ho.FEATURE) == "beta" and "agent_orchestrator" in f.needs


def test_brief_is_redacted_and_tells_the_agent_to_draft_and_never_merge():
    b = ho.brief(rep())
    assert "ANYDO_AUTH_EXPIRED" in b and "DRAFT pull request" in b and "Never merge" in b
    assert "Do not read, print or send tokens" in b


def test_brief_blocks_private_text():
    r = rep()
    r.code = "X"
    r.area = "anydo " + SECRET
    with pytest.raises(TicketBlocked):
        ho.brief(r)


def test_start_uses_the_orchestrators_own_approval_and_never_pre_approves():
    o = FakeOrch()
    out = ho.start(o, rep(), "jarves", "en")
    assert out["ok"] is True
    provider, project, task, kw = o.calls[0]
    assert (provider, project, kw["by"], kw["approved"]) == ("claude", "jarves", "diag", False)   # its own card shows the brief
    assert "DRAFT pull request" in task


def test_no_project_no_agent_blocked_and_refused():
    assert ho.start(FakeOrch(), rep(), "", "en")["code"] == "HANDOFF_NO_PROJECT"
    assert ho.start(None, rep(), "p", "en")["code"] == "HANDOFF_NO_AGENT"
    r = rep()
    r.area = "anydo " + SECRET
    o = FakeOrch()
    assert ho.start(o, r, "p", "en")["code"] == "HANDOFF_BLOCKED" and o.calls == []
    denied = FakeOrch({"ok": False, "code": "NOT_APPROVED", "text": "Not done: it was not approved."})
    out = ho.start(denied, rep(), "p", "en")
    assert out["ok"] is False and "not started" in out["text"]


def test_a_crashing_orchestrator_gives_fixed_words():
    class Boom:
        def start_session(self, *a, **k):
            raise RuntimeError("worktree failed at C:\\Users\\dana\\secret " + SECRET)
    out = ho.start(Boom(), rep(), "p", "en")
    assert out["ok"] is False and SECRET not in out["text"] and "dana" not in out["text"]


# ---------------------------------------------------------------- the gate: a second question, only when the switch is on
class Rig:
    def __init__(self, answers, handoff_on=True, project="jarves"):
        self.asked, self.delivered, self.orch, self.answers = [], [], FakeOrch(), list(answers)
        t = [1000.0]

        async def handoff(r, lang):
            return ho.start(self.orch, r, project, lang)
        self.gate = fg.FailureGate(enabled=lambda: True, ask=self._ask, deliver=lambda text, r: self.delivered.append(text),
                                   collect=lambda a, c, d: areas.run(a, c, home=None, failure_data=d), clock=lambda: t[0],
                                   handoff=handoff, handoff_enabled=lambda: handoff_on)

    async def _ask(self, prompt, **meta):
        self.asked.append((prompt, meta))
        return self.answers.pop(0) if self.answers else False

    async def fail(self):
        self.gate.notify("anydo_list_tasks", ToolResult(False, "failed", {}, code="ANYDO_AUTH_EXPIRED"), "text")
        for t in list(self.gate._tasks):
            await t


def test_two_yes_answers_start_the_agent_with_a_medium_risk_card():
    async def go():
        r = Rig([True, True])
        await r.fail()
        assert len(r.asked) == 2 and r.asked[1][1]["risk"] == "medium" and r.asked[1][1]["voice"] is False
        assert len(r.orch.calls) == 1 and any("coding agent started" in d for d in r.delivered)
    asyncio.run(go())


def test_no_to_the_second_question_starts_nothing():
    async def go():
        r = Rig([True, False])
        await r.fail()
        assert len(r.asked) == 2 and r.orch.calls == []
    asyncio.run(go())


def test_no_to_the_first_question_asks_no_second():
    async def go():
        r = Rig([False, True])
        await r.fail()
        assert len(r.asked) == 1 and r.orch.calls == []
    asyncio.run(go())


def test_switch_off_means_one_question_only():
    async def go():
        r = Rig([True, True], handoff_on=False)
        await r.fail()
        assert len(r.asked) == 1 and r.orch.calls == []
    asyncio.run(go())


def test_no_project_tells_the_owner_how_to_set_it():
    async def go():
        r = Rig([True, True], project="")
        await r.fail()
        assert r.orch.calls == [] and any("diag.handoff_project" in d for d in r.delivered)
    asyncio.run(go())
