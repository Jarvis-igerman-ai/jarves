"""Home setup cards (#457): cards for open items only, answers remembered; clock-free (explicit `now`), hermetic."""
from __future__ import annotations

from jarves import connect as cn
from jarves import features as feat
from jarves import home_setup as hs


def item(fid, state="missing"):
    return cn.Item(fid, "t-he", "t-en", False, state=state, detail_he="d", detail_en="d")


ALL = [item("google"), item("telegram"), item("pear"), item("wake"), item("instagram"), item("home")]


def test_off_by_default_and_beta():
    assert feat.CATALOG[hs.FEATURE].default_enabled is False
    assert feat.stage_of(hs.FEATURE) == "beta"


def test_only_the_four_open_items_in_order(tmp_path):
    c = hs.cards(tmp_path, None, now=1000.0, items=ALL)
    assert [x["id"] for x in c] == ["google", "telegram", "pear", "wake"]
    assert all(x["steps_en"] and x["steps_he"] and x["guide"] for x in c)


def test_connected_item_has_no_card(tmp_path):
    items = [item("google", "connected"), item("telegram"), item("pear", "attention"), item("wake", "connected")]
    assert [x["id"] for x in hs.cards(tmp_path, None, now=1.0, items=items)] == ["telegram", "pear"]


def test_later_hides_three_days_then_returns(tmp_path):
    r = hs.answer(tmp_path, None, "telegram", "later", now=1000.0)
    assert r["hidden"] is True
    assert "telegram" not in [x["id"] for x in hs.cards(tmp_path, None, now=1000.0 + hs.LATER_S - 1, items=ALL)]
    assert "telegram" in [x["id"] for x in hs.cards(tmp_path, None, now=1000.0 + hs.LATER_S + 1, items=ALL)]


def test_failed_counts_and_keeps_card(tmp_path):
    hs.answer(tmp_path, None, "pear", "failed", now=1.0)
    hs.answer(tmp_path, None, "pear", "failed", now=2.0)
    pear = [x for x in hs.cards(tmp_path, None, now=3.0, items=ALL) if x["id"] == "pear"][0]
    assert pear["failed"] == 2


def test_works_needs_a_real_connection(tmp_path):
    r = hs.answer(tmp_path, None, "wake", "works", now=1.0, check=lambda fid: False)
    assert r["answer"] == "failed" and r["hidden"] is False and r["message_en"]
    r = hs.answer(tmp_path, None, "wake", "works", now=2.0, check=lambda fid: True)
    assert r["hidden"] is True


def test_skipped_in_connect_stays_hidden(tmp_path):
    cn.set_never(tmp_path, ["google"])
    assert "google" not in [x["id"] for x in hs.cards(tmp_path, None, now=1.0, items=ALL)]


def test_bad_input_rejected(tmp_path):
    import pytest
    for fid, a in (("instagram", "later"), ("google", "maybe")):
        with pytest.raises(ValueError):
            hs.answer(tmp_path, None, fid, a, now=1.0)


def test_no_secret_field_anywhere():
    for he, en in hs.STEPS.values():
        assert "password" not in en.lower()
    assert "paste it only in the connect window" in hs.STEPS["telegram"][1]
