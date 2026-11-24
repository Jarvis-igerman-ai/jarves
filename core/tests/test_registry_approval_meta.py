"""The registry contract `approval_meta` relies on (#1514, review R3): it runs once per call, on the same args object
the handler then gets, right before the one ask, whether or not the confirm callback accepts meta (no real services)."""
import pytest
from pydantic import BaseModel, PrivateAttr

from jarves.skills import Skill, SkillRegistry, Tool, ToolResult


class Args(BaseModel):
    value: int
    _bound: str | None = PrivateAttr(None)


@pytest.mark.parametrize("accepts_meta", [True, False])
@pytest.mark.parametrize("approved", [True, False])
async def test_approval_meta_runs_once_on_the_handlers_args_before_the_ask(accepts_meta, approved):
    seen = []

    def meta(a):
        seen.append(("meta", a))
        a._bound = "shown words"
        return {"details": {"what": "shown words"}}

    async def handle(a):
        seen.append(("run", a))
        return ToolResult(True, "done")

    tool = Tool("test_meta", "Test approval meta", handle, args=Args, permission="action",
                confirm="Approve?", approval_meta=meta)

    class TestSkill(Skill):
        name = "test_meta_skill"

        def tools(self):
            return [tool]

    reg = SkillRegistry(allowed_actions={tool.name})
    reg.register(TestSkill())

    async def confirm(prompt, **kw):
        seen.append(("ask", kw.get("details")))
        return approved

    if accepts_meta:
        confirm.accepts_meta = True
    reg.confirm = confirm
    r = await reg.call("test_meta", {"value": 1})
    assert [s[0] for s in seen] == (["meta", "ask", "run"] if approved else ["meta", "ask"])
    if accepts_meta:
        assert seen[1][1] == {"what": "shown words"}
    if approved:
        assert r.ok and seen[2][1] is seen[0][1] and seen[2][1]._bound == "shown words"
    else:
        assert not r.ok


@pytest.mark.parametrize("given,expected", [("Approve 'shown words'?", "Approve 'shown words'?"), ("", "Approve?"),
                                            ("  ", "Approve?"), (7, "Approve?"), (None, "Approve?")])
async def test_approval_meta_prompt_replaces_the_confirm_prompt_only_when_it_is_text(given, expected):
    """#1514: a plain confirm(prompt) callback sees only the prompt, so approval_meta may put the details in it."""
    asked = []

    async def handle(_a):
        return ToolResult(True, "done")

    tool = Tool("test_prompt", "Test approval prompt", handle, args=Args, permission="action", confirm="Approve?",
                approval_meta=lambda a: {"prompt": given})

    class TestSkill(Skill):
        name = "test_prompt_skill"

        def tools(self):
            return [tool]

    reg = SkillRegistry(allowed_actions={tool.name})
    reg.register(TestSkill())

    async def confirm(prompt):
        asked.append(prompt)
        return True

    reg.confirm = confirm
    assert (await reg.call("test_prompt", {"value": 1})).ok and asked == [expected]


async def test_approval_meta_prompt_never_adds_an_ask_to_a_tool_without_one():
    """Review R2: a tool with no confirm prompt is not asked, whatever approval_meta would return."""
    asked, ran = [], []

    async def handle(_a):
        ran.append(1)
        return ToolResult(True, "done")

    tool = Tool("test_no_ask", "Test no ask", handle, args=Args, permission="action",
                approval_meta=lambda a: {"prompt": "Approve?"})

    class TestSkill(Skill):
        name = "test_no_ask_skill"

        def tools(self):
            return [tool]

    reg = SkillRegistry(allowed_actions={tool.name})
    reg.register(TestSkill())

    async def confirm(prompt):
        asked.append(prompt)
        return True

    reg.confirm = confirm
    assert (await reg.call("test_no_ask", {"value": 1})).ok and asked == [] and ran == [1]
