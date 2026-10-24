"""Issue #71: lazy tool exposure on the client-delegation backend call."""
import json

from jarves.voice.lazy_tools import DISCOVER, GROUPS, TOOL_GROUP, groups_for, lazy_tools, open_group
from jarves.voice.live import live_tools

LAZY_CEILING = 38_000          # chars: tools JSON + rules of the client-delegation call (all built-ins, nothing opened)
# tools that approvals and safety depend on stay visible in every group state
NEVER_HIDDEN = {"schedule_approve", "schedule_preview", "proposal_decide", "folder_grant_request", "feature_set", "forget",
                "forget_all", "remember", "quit_jarves", "end_conversation", "mute_microphone", "hide_yourself",
                "job_delete", "job_control", "workspace_edit", "workspace_undo", "profile_switch", "file_write"}


def _size(core, groups=frozenset()):
    return len(json.dumps(lazy_tools(core.registry, groups), ensure_ascii=False)) + len(core.agent.instructions())


async def test_lazy_prompt_is_smaller_and_under_its_ceiling(core):
    full = len(json.dumps(live_tools(core.registry), ensure_ascii=False)) + len(core.agent.instructions())
    assert _size(core) <= LAZY_CEILING
    assert _size(core) < full - 5_000


async def test_groups_are_real_and_safety_tools_never_hidden(core):
    names = {t.name for t in core.registry.tools()}
    for g, (tools, _, _) in GROUPS.items():
        assert set(tools) <= names | {"health_canary_run", "health_ai_review_run"}, g
    assert not (set(TOOL_GROUP) & NEVER_HIDDEN)
    shown = {t["name"] for t in lazy_tools(core.registry)}
    assert NEVER_HIDDEN & names <= shown


async def test_discover_and_open_group(core):
    base = {t["name"] for t in lazy_tools(core.registry)}
    assert DISCOVER in base and "goal_add" not in base
    opened: set = set()
    assert "goal_add" in open_group(core.registry, "goals", opened)
    assert "goal_add" in {t["name"] for t in lazy_tools(core.registry, opened)}
    assert "unknown group" in open_group(core.registry, "nope", opened)
    every = {t["name"] for t in lazy_tools(core.registry, set(GROUPS))}
    assert DISCOVER not in every and every == {t.name for t in core.registry.tools()}


def test_user_words_open_groups_in_english_and_hebrew():
    assert groups_for("add a goal to run daily") == {"goals"} and groups_for("תוסיף יעד חדש") == {"goals"}
    assert "selfcheck" in groups_for("run the health check") and "selfcheck" in groups_for("תריץ בדיקה עצמית")
    assert "preferences" in groups_for("what do I prefer") and "development" in groups_for("show the draft skill")
    assert groups_for("play some music") == set() and groups_for("") == set()
