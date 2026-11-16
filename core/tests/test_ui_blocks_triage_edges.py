"""JAR-143: edge cases (empty, malformed, huge input) for ui_blocks.py and triage.py. Hermetic: fakes only, no network."""
from __future__ import annotations

import pytest

from jarves import triage
from jarves import ui_blocks as ub
from jarves.skills import TOOL_SINK, SkillRegistry


class Sink:
    def ui_block(self, b):
        pass


async def run(tmp_path, cands, anydo=None):
    reg = SkillRegistry()

    async def titles():
        return anydo
    reg.register(triage.TriageSkill(tmp_path, titles_fn=titles))
    TOOL_SINK.set(Sink())
    return await reg.call("tasks_from_messages", {"candidates": cands})


# ------------------------------------------------------------------ ui_blocks
@pytest.mark.parametrize("raw", [None, "text", 5, [], ["x"], 1.5, True])
def test_non_dict_block_is_invalid(raw):
    with pytest.raises(ValueError, match="UI_BLOCK_INVALID"):
        ub.validate_block(raw)


@pytest.mark.parametrize("raw", [{}, {"kind": ""}, {"kind": "form"}, {"kind": None}, {"v": 2, "kind": "text", "text": "x"}])
def test_unknown_kind_or_version(raw):
    with pytest.raises(ValueError, match="UI_BLOCK_UNKNOWN"):
        ub.validate_block(raw)


def test_validate_blocks_non_list_inputs():
    assert ub.validate_blocks([]) == []
    assert ub.validate_blocks(None) == []
    assert ub.validate_blocks("junk") == []
    one = ub.validate_blocks({"kind": "text", "text": "hi"})          # a lone dict is wrapped
    assert len(one) == 1 and one[0]["text"] == "hi"


def test_validate_blocks_keeps_at_most_four():
    raws = [{"kind": "text", "text": f"t{n}"} for n in range(9)]
    good = ub.validate_blocks(raws)
    assert [g["text"] for g in good] == ["t0", "t1", "t2", "t3"]


@pytest.mark.parametrize("text", ["", "   ", "​‮\x00", "\n\t"])
def test_text_block_empty_after_cleaning_is_rejected(text):
    with pytest.raises(ValueError):
        ub.validate_block({"kind": "text", "text": text})


def test_text_block_limit_is_1000_chars():
    assert ub.validate_block({"kind": "text", "text": "x" * 1000})["text"] == "x" * 1000
    with pytest.raises(ValueError):
        ub.validate_block({"kind": "text", "text": "x" * 1001})


def test_multibyte_payload_over_16k_is_too_big_not_a_crash():
    items = [{"label": "ש" * 120, "detail": "ש" * 200} for _ in range(30)]       # ~19 KB as UTF-8
    with pytest.raises(ValueError, match="UI_BLOCK_TOO_BIG"):
        ub.validate_block({"kind": "checklist", "items": items})


def test_checklist_limits():
    assert len(ub.validate_block({"kind": "checklist", "items": [{"label": "a"}] * 30})["items"]) == 30
    for raw in ({"kind": "checklist", "items": [{"label": "a"}] * 31}, {"kind": "checklist", "items": []},
                {"kind": "checklist", "items": [{"label": ""}]},
                {"kind": "checklist", "items": [{"label": "a"}], "actions": [{"label": "a"}] * 5}):
        with pytest.raises(ValueError):
            ub.validate_block(raw)


def test_table_malformed_shapes():
    base = {"kind": "table", "columns": [{"label": "A"}], "rows": [{"cells": ["1"]}]}
    assert ub.validate_block(base)["rows"][0]["cells"] == ["1"]
    for patch in ({"columns": []}, {"rows": []}, {"rows": [{"cells": []}]}, {"rows": [{"cells": ["1", "2"]}]},
                  {"rows": [{"cells": ["x" * 101]}]}, {"columns": [{"label": "A"}] * 7}, {"rows": "nope"}, {"columns": None}):
        with pytest.raises(ValueError):
            ub.validate_block({**base, **patch})


def test_choices_bounds():
    def opt(n):
        return [{"label": f"o{i}"} for i in range(n)]
    assert len(ub.validate_block({"kind": "choices", "options": opt(2)})["options"]) == 2
    assert len(ub.validate_block({"kind": "choices", "options": opt(6)})["options"]) == 6
    for n in (0, 1, 7):
        with pytest.raises(ValueError):
            ub.validate_block({"kind": "choices", "options": opt(n)})


def test_extra_fields_and_none_values_are_refused():
    with pytest.raises(ValueError):
        ub.validate_block({"kind": "text", "text": "x", "html": "<b>"})
    with pytest.raises(ValueError):
        ub.validate_block({"kind": "text", "text": None})


def test_clean_handles_non_strings_and_empty():
    assert ub.clean("") == "" and ub.clean(12) == "12"
    assert ub.clean("‮‭  \x00 ") == ""


def test_numbered_handles_empty_and_partial_blocks():
    assert ub.numbered({}) == ""
    assert ub.numbered({"kind": "table"}) == ""
    assert "type your choice" in ub.numbered({"kind": "choices"})
    assert ub.numbered({"kind": "text"}) == ""
    assert ub.numbered({"kind": "checklist", "items": [{"id": "i1"}]}) == "1) "


@pytest.mark.parametrize("line", ["", None, "   ", "hello", "1;2", "1,,2", "-1", "1.5", "100", "one"])
def test_parse_numbers_non_selections(line):
    assert ub.parse_numbers(line, {"items": [{"id": "i1"}, {"id": "i2"}]}) is None


def test_parse_numbers_range_and_empty_block():
    assert ub.parse_numbers("all", {}) == []
    with pytest.raises(ValueError):
        ub.parse_numbers("1", {})
    with pytest.raises(ValueError):
        ub.parse_numbers("99", {"items": [{"id": "i1"}]})


def test_blockstore_take_rejects_malformed_arguments():
    for args in ((None, ["i1"]), ("blk", "i1"), ("blk", []), ("blk", ["../x"]), ("blk", ["i1"] * 2), ("blk", ["i1"], "x"),
                 ("blk", ["i1"] * 61), (5, ["i1"])):
        s = ub.BlockStore()
        with pytest.raises(ub.ActionError) as e:
            s.take(*args)
        assert e.value.code in ("UI_ACTION_INVALID", "UI_ACTION_UNKNOWN")


def test_blockstore_evicts_oldest_beyond_cap():
    s = ub.BlockStore()
    ids = []
    for n in range(ub.MAX_RECORDS + 5):
        b = {"id": f"blk_{n}", "kind": "checklist", "on": "anydo_create_tasks", "items": [{"id": "i1", "label": "a"}]}
        s.add(b)
        ids.append(b["id"])
    assert s.state(ids[0]) == "expired" and s.state(ids[-1]) == "open"


def test_blockstore_rate_limit_is_per_minute():
    now = [1000.0]
    s = ub.BlockStore(clock=lambda: now[0])
    for _ in range(ub.ACTION_RATE):
        with pytest.raises(ub.ActionError):
            s.take("nope", ["i1"])
    with pytest.raises(ub.ActionError) as e:
        s.take("nope", ["i1"])
    assert e.value.code == "UI_ACTION_RATE" and e.value.status == 429
    now[0] += 61
    with pytest.raises(ub.ActionError) as e2:
        s.take("nope", ["i1"])
    assert e2.value.code == "UI_ACTION_UNKNOWN"


def test_action_text_flattens_newlines_and_truncates():
    t = ub.Taken("blk_1", "anydo_create_tasks", ["i1"], ["line1\nline2 " + "x" * 500], False)
    row = [x for x in ub.action_text(t, None).splitlines() if x.startswith("1) ")][0]
    assert "line1 line2" in row and len(row) <= 124


# ------------------------------------------------------------------ triage
def test_norm_and_similar_edge_inputs():
    assert triage.norm("") == "" and triage.norm("   ") == "" and triage.norm("!!!") == ""
    assert triage.similar("", "") is False and triage.similar("a", "") is False and triage.similar("!!", "!!") is False
    assert triage.similar("Ｃall Ｄana", "call dana")                    # NFKC full-width


def test_skipped_store_without_home_is_a_noop(tmp_path):
    s = triage.SkippedStore(None)
    assert s.add([("a", "")]) == 1 and s.list() == [] and s.has("a") is False
    assert s.restore("all") == 0


@pytest.mark.parametrize("content", ["", "not json", "[]", "null", '{"items": []}', '{"items": {"a": "x", "b": null}}'])
def test_skipped_store_survives_corrupt_file(tmp_path, content):
    (tmp_path / triage.FILE).write_text(content, encoding="utf-8")
    s = triage.SkippedStore(tmp_path)
    assert s.list() == [] and s.has("anything") is False
    assert s.add([("t", "")]) == 1 and s.has("t") is True


def test_skipped_store_restore_short_id_is_refused(tmp_path):
    s = triage.SkippedStore(tmp_path)
    s.add([("t", "")])
    assert s.restore("") == 0 and s.restore("ab") == 0 and len(s.list()) == 1
    assert s.restore("all") == 1 and s.list() == []


def test_skipped_store_ref_changes_fingerprint(tmp_path):
    s = triage.SkippedStore(tmp_path)
    s.add([("t", "m1")])
    assert s.has("t", "m1") and not s.has("t", "m2") and not s.has("t")


def test_skipped_store_expiry_boundary(tmp_path):
    now = [1_800_000_000.0]
    s = triage.SkippedStore(tmp_path, clock=lambda: now[0])
    s.add([("t", "")])
    now[0] += (triage.EXPIRY_DAYS - 1) * 86400
    assert s.has("t")
    now[0] += 2 * 86400
    assert not s.has("t") and s.list() == []


def test_ok_due_edges():
    assert triage._ok_due(None) == "" and triage._ok_due("") == "" and triage._ok_due("2026-13-45") == ""
    assert triage._ok_due("9999-12-31") == "9999-12-31"


async def test_zero_candidates_refused_by_schema(tmp_path):
    res = await run(tmp_path, [])
    assert not res.ok and "TOOL_BAD_ARGS" in res.message


async def test_malformed_candidate_fields_refused_by_schema(tmp_path):
    for bad in ({"title": 5}, {"title": "a", "source": "sms"}, {"title": "a", "person": "p" * 41}, {"title": "a", "due": "x" * 11},
                {"title": "a", "when": "x" * 17}, {}):
        res = await run(tmp_path, [bad])
        assert not res.ok and "TOOL_BAD_ARGS" in res.message


async def test_bad_when_is_dropped_not_the_whole_card(tmp_path):
    res = await run(tmp_path, [{"title": "a", "when": "yesterday-ish"}, {"title": "b", "when": "2026-10-26 14:10"}])
    assert res.ok and [c["when"] for c in res.ui_blocks[0]["cards"]] == ["", ""]


async def test_title_boundary_120_and_whitespace_collapse(tmp_path):
    res = await run(tmp_path, [{"title": "x" * 120}, {"title": "  a \n\t b   c  "}])
    assert [c["title"] for c in res.ui_blocks[0]["cards"]] == ["x" * 120, "a b c"]


async def test_only_punctuation_titles_are_dropped(tmp_path):
    res = await run(tmp_path, [{"title": "!!! ???"}, {"title": "---"}])
    assert not res.ok and res.code == "TRIAGE_NONE" and res.data["dropped"] == 2


async def test_existing_anydo_titles_with_blanks_do_not_flag(tmp_path):
    res = await run(tmp_path, [{"title": "pay rent"}], anydo=["", "   ", "!!"])
    assert res.ok and res.data["flagged_duplicates"] == 0 and res.data["any_do_checked"] is True


async def test_huge_existing_list_still_flags_duplicate(tmp_path):
    existing = [f"unrelated chore number {n}" for n in range(1000)] + ["Pay the rent"]
    res = await run(tmp_path, [{"title": "pay the rent"}], anydo=existing)
    assert res.data["flagged_duplicates"] == 1


async def test_hebrew_with_niqqud_dedupes(tmp_path):
    res = await run(tmp_path, [{"title": "לְהִתְקַשֵּׁר לדנה"}], anydo=["להתקשר לדנה"])
    assert res.data["flagged_duplicates"] == 1


async def test_previously_skipped_is_none_with_count(tmp_path):
    triage.SkippedStore(tmp_path).add([("x task", "m1")])
    again = await run(tmp_path, [{"title": "x task", "ref": "m1"}])
    assert not again.ok and again.code == "TRIAGE_NONE" and again.data["already_skipped"] == 1
