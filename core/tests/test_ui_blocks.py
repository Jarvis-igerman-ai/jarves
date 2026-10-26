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
