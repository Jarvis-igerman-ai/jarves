"""#444 S1: UI block schema v1 (text + checklist), the ui_block channel next to display, XSS payloads as text. Offline."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarves import ui_blocks as ub
from jarves.skills import TOOL_SINK, SkillRegistry, ToolResult

STATIC = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static"


def cl(**kw):
    return {"v": 1, "kind": "checklist", "items": [{"label": "milk"}, {"label": "eggs", "detail": "12"}], **kw}


def test_checklist_gets_server_ids_and_clean_text():
    b = ub.validate_block(cl(title="Shop‮", actions=[{"label": "Go"}]))
    assert b["id"].startswith("blk_") and [i["id"] for i in b["items"]] == ["i1", "i2"] and b["actions"][0]["id"] == "a1"
    assert b["title"] == "Shop" and b["v"] == 1


@pytest.mark.parametrize("raw", [
    {"v": 2, "kind": "text", "text": "x"}, {"v": 1, "kind": "cards"}, {"v": 1, "kind": "form"}, "x", None,
    {"v": 1, "kind": "text", "text": ""}, {"v": 1, "kind": "text", "text": "x" * 1001},
    {"v": 1, "kind": "text", "text": "x", "html": "<b>"}, {"v": 1, "kind": "text", "text": "x", "id": "mine"},
    cl(items=[]), cl(items=[{"label": "x"}] * 31), cl(items=[{"label": "x" * 121}]), cl(items=[{"label": "a", "detail": "d" * 201}]),
    cl(items=[{"label": "a", "id": "free"}]), cl(items=[{"label": 5}]), cl(title="t" * 81), cl(dir="up"), cl(actions=[{"label": "a"}] * 5),
    cl(items=[{"label": "a", "checked": "yes please"}]),
])
def test_bad_blocks_rejected(raw):
    with pytest.raises(ValueError):
        ub.validate_block(raw)


def test_message_limits_drop_but_keep_good():
    good = [{"v": 1, "kind": "text", "text": "ok"}] * 6
    assert len(ub.validate_blocks(good + [{"kind": "nope"}])) == 4
    assert ub.validate_blocks([{"kind": "nope"}]) == []


def test_oversize_total_rejected():
    big = cl(items=[{"label": "é" * 120, "detail": "é" * 200}] * 30)      # ~19 KB in utf-8
    with pytest.raises(ValueError):
        ub.validate_block(big)


def test_numbered_fallback_is_plain():
    assert ub.numbered(ub.validate_block(cl(title="T"))) == "T\n1) milk\n2) eggs - 12"


async def test_ui_show_goes_to_the_sink_not_to_the_model():
    got = []

    class Sink:
        def __call__(self, name, ok, code):
            pass

        def ui_block(self, block):
            got.append(block)
    reg = SkillRegistry()
    reg.register(ub.UiBlocksSkill())
    TOOL_SINK.set(Sink())
    res = await reg.call("ui_show", {"blocks": [cl(), {"kind": "bogus"}]})
    assert res.ok and len(got) == 1 and got[0]["kind"] == "checklist"
    out = res.to_model()
    assert "milk" not in out and "ui_blocks" not in out and json.loads(out)["shown_to_owner"] is True
    bad = await reg.call("ui_show", {"blocks": [{"kind": "bogus"}]})
    assert not bad.ok and bad.code == "UI_BLOCK_INVALID" and len(got) == 1


def test_toolresult_default_has_no_blocks():
    assert ToolResult(True, "x").ui_blocks == []


def test_renderer_has_no_html_injection_apis():
    src = (STATIC / "chatview.js").read_text(encoding="utf-8")
    assert not re.search(r"innerHTML|insertAdjacentHTML|outerHTML|\beval\(|new Function|\bFunction\(", src)


def test_webchat_hides_blocks_from_phones():
    from jarves.chat_engine import WebChat
    src = Path(__file__).resolve().parents[1].joinpath("src/jarves/chat_engine.py").read_text(encoding="utf-8")
    assert '("display", "ui_block")' in src and WebChat is not None


def test_ui_show_is_offered_only_when_the_words_ask_for_a_list():
    reg = SkillRegistry()
    reg.register(ub.UiBlocksSkill())
    assert [t.name for t in reg.tools()] == []
    reg.open_lazy("what is the weather")
    assert reg.tools() == []
    for words in ("show me a checklist", "תן לי רשימה לסימון"):
        reg.open_lazy(words)
        assert [t.name for t in reg.tools()] == ["ui_show"]
    reg.open_lazy("hello")
    assert reg.tools() == []


# ---------------------------------------------------------------- #365: the assistant picks the format (beta, OFF)
def _gated(on: bool) -> SkillRegistry:
    """A registry whose feature gate is the real Features.gate rule for smart_format (switch state given)."""
    from jarves import features as feat
    reg = SkillRegistry()
    reg.register(ub.UiBlocksSkill())
    reg.gate = lambda skill, tool: None if feat.Features.owner(None, skill, tool) != ub.FORMAT_FEATURE or on else "off"
    return reg


def test_smart_format_is_a_beta_feature_off_by_default_that_owns_only_ui_format():
    from jarves import features as feat
    f = feat.CATALOG[ub.FORMAT_FEATURE]
    assert f.default_enabled is False and feat.stage_of(ub.FORMAT_FEATURE) == "beta"
    assert f.tools == ["ui_format"] and f.skills == []         # ui_show stays as it is (no switch)


@pytest.mark.parametrize("words", ["compare the two plans", "what are the pros and cons of renting", "Python vs Go?",
                                   "which one is better for a Pi", "step by step how do I pair the phone",
                                   "תשווה בין שתי התוכניות", "מה עדיף, רכבת או אוטובוס", "מה השלבים להתקנה"])
def test_ui_format_opens_on_the_shape_of_the_question_only_when_switched_on(words):
    off, on = _gated(False), _gated(True)
    for reg in (off, on):
        reg.open_lazy(words)
    assert [t.name for t in off.tools()] == []
    assert [t.name for t in on.tools()] == ["ui_format"]


@pytest.mark.parametrize("words", ["what is the weather", "good morning", "play some music", "תזכיר לי מחר", "vsync is off",
                                   "", "a\nb"])
def test_ui_format_stays_closed_on_ordinary_words(words):
    reg = _gated(True)
    reg.open_lazy(words)
    assert reg.tools() == []


@pytest.mark.parametrize("words", ["compare them in a table", "show me a checklist of steps to pack", "תשווה בטבלה"])
def test_one_block_tool_per_turn_when_the_words_also_ask_for_a_list(words):
    reg = _gated(True)
    reg.open_lazy(words)
    assert [t.name for t in reg.tools()] == ["ui_show"]


async def test_real_core_offers_ui_format_only_after_the_switch(core, monkeypatch):
    monkeypatch.setenv("JARVES_BETA_FEATURES", "1")
    reg = core.registry
    reg.open_lazy("compare the two plans")
    assert "ui_format" not in [t.name for t in reg.tools()]                    # OFF by default
    core.features.set(ub.FORMAT_FEATURE, True)
    reg.open_lazy("compare the two plans")
    names = [t.name for t in reg.tools()]
    assert "ui_format" in names and "ui_show" not in names
    reg.open_lazy("what time is it")
    assert "ui_format" not in [t.name for t in reg.tools()]


async def test_ui_format_is_the_same_validated_read_only_sink_as_ui_show():
    reg = _gated(True)
    t = reg._tools["ui_format"][1]
    assert t.permission == "read" and t.args is reg._tools["ui_show"][1].args and len(t.description) <= 330
    got = []

    class Sink:
        def __call__(self, name, ok, code):
            pass

        def ui_block(self, block):
            got.append(block)
    TOOL_SINK.set(Sink())
    res = await reg.call("ui_format", {"blocks": [{"kind": "table", "columns": [{"label": "Plan"}, {"label": "Cost"}],
                                                   "rows": [{"cells": ["Basic", "tenshekel"]}]}, {"kind": "bogus"}]})
    assert res.ok and [b["kind"] for b in got] == ["table"]
    assert "tenshekel" not in res.to_model() and json.loads(res.to_model())["shown_to_owner"] is True
    cards = await reg.call("ui_format", {"blocks": [{"kind": "cards", "cards": [{"title": "x"}], "on": "anydo_create_tasks"}]})
    assert not cards.ok and cards.code == "UI_BLOCK_INVALID" and len(got) == 1     # cards stay app-made only


# ---------------------------------------------------------------- S4: table and choices
def test_table_gets_server_ids_pads_rows_and_cleans():
    b = ub.validate_block({"v": 1, "kind": "table", "title": "Plan", "columns": [{"label": "Day"}, {"label": "Task‮"}],
                           "rows": [{"cells": ["Mon", "call"]}, {"cells": ["Tue"]}]})
    assert [c["id"] for c in b["columns"]] == ["c1", "c2"] and b["columns"][1]["label"] == "Task"
    assert b["rows"][1]["cells"] == ["Tue", ""]
    assert ub.numbered(b).splitlines()[1:] == ["Day | Task", "Mon | call", "Tue | "]


@pytest.mark.parametrize("raw", [
    {"v": 1, "kind": "table", "columns": [], "rows": [{"cells": ["a"]}]},
    {"v": 1, "kind": "table", "columns": [{"label": "a"}] * 7, "rows": [{"cells": ["a"]}]},
    {"v": 1, "kind": "table", "columns": [{"label": "a"}], "rows": []},
    {"v": 1, "kind": "table", "columns": [{"label": "a"}], "rows": [{"cells": ["a", "b"]}]},
    {"v": 1, "kind": "table", "columns": [{"label": "a"}], "rows": [{"cells": ["x" * 101]}]},
    {"v": 1, "kind": "table", "columns": [{"label": "a"}], "rows": [{"cells": ["a"]}] * 31},
    {"v": 1, "kind": "table", "columns": [{"label": "a"}], "rows": [{"cells": ["a"]}], "on": "anydo_create_tasks"},
    {"v": 1, "kind": "choices", "options": [{"label": "only one"}]},
    {"v": 1, "kind": "choices", "options": [{"label": "a"}] * 7},
    {"v": 1, "kind": "choices", "options": [{"label": "a"}, {"label": "b" * 61}]},
    {"v": 1, "kind": "choices", "options": [{"label": "a"}, {"label": "b"}], "on": "anydo_create_tasks"},
    {"v": 1, "kind": "checklist", "items": [{"label": "a"}], "options": [{"label": "a"}, {"label": "b"}]},
])
def test_table_and_choices_reject_matrix(raw):
    with pytest.raises(ValueError):
        ub.validate_block(raw)


def test_choices_ids_and_numbered():
    b = ub.validate_block({"v": 1, "kind": "choices", "title": "Which?", "options": [{"label": "Tea"}, {"label": "Coffee"}]})
    assert [o["id"] for o in b["options"]] == ["o1", "o2"]
    assert ub.numbered(b).splitlines()[:3] == ["Which?", "1) Tea", "2) Coffee"]
    assert ub.BlockStore().add(b) is None          # no next step: nothing to record or click


def test_context_taint_outlives_the_untrusted_turn_then_decays():
    """#479: third-party text read in an earlier turn still taints blocks the model builds in the next few turns."""
    from jarves.skills import CONTEXT_TAINT_TURNS, SkillRegistry
    reg = SkillRegistry()
    reg._untrusted_at = reg.clock()
    assert reg.context_tainted()
    for _ in range(CONTEXT_TAINT_TURNS):
        reg.new_turn()
        reg._untrusted.clear()                       # the per-turn action guard is lifted by the owner turn
        assert reg.context_tainted()
    reg.new_turn()
    assert not reg.context_tainted()


def test_clean_strips_zero_width_and_direction_marks():   # #485
    assert ub.clean("pa\u200By\u200E\u200F\u2060\u061C\uFEFFroll ") == "payroll"
