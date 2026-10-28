"""Offline sync, Core side (V7 A5): the merge is a deterministic total order (commutative, associative, idempotent), deletes are tombstones,
the phone's input is validated, and several fake devices converge to the same state in any order."""
from __future__ import annotations

import itertools
import random

import pytest

from jarves import offline_sync as osync
from jarves.userdata import UserDB


def item(iid="n1", version=1, updated=1000.0, deleted=False, data=None, kind="note"):
    return {"kind": kind, "id": iid, "version": version, "updated": updated, "deleted": deleted, "data": data if data is not None else {"t": "x"}}


@pytest.fixture()
def store():
    return osync.OfflineStore(UserDB(":memory:"), clock=lambda: 5_000_000.0)


def rnd(rng):
    return osync.clean(item(version=rng.randint(1, 4), updated=float(rng.randint(1, 4)), deleted=rng.random() < 0.3, data={"v": rng.randint(0, 3)}))


def test_the_merge_rule_is_commutative_associative_and_idempotent():
    rng = random.Random(7)
    for _ in range(400):
        a, b, c = rnd(rng), rnd(rng), rnd(rng)
        assert osync.merge(a, b) == osync.merge(b, a)
        assert osync.merge(osync.merge(a, b), c) == osync.merge(a, osync.merge(b, c))
        assert osync.merge(a, a) == a
    assert osync.merge(None, None) is None


def test_higher_version_then_later_time_then_tombstone_decide():
    assert osync.merge(item(version=2, data={"t": "new"}), item(version=1, updated=9999.0))["data"] == {"t": "new"}
    assert osync.merge(item(updated=5.0, data={"t": "late"}), item(updated=4.0))["data"] == {"t": "late"}
    assert osync.merge(item(deleted=True), item())["deleted"] is True


def test_a_delete_is_a_tombstone_and_an_old_copy_cannot_bring_the_item_back(store):
    store.sync([item(version=1, data={"t": "secret"})])
    out = store.sync([item(version=2, updated=2000.0, deleted=True)])
    assert out["applied"] == 1
    old = store.sync([item(version=1, data={"t": "secret"})])                       # another device still has version 1
    assert old["applied"] == 0 and old["items"][0]["deleted"] is True and old["items"][0]["data"] == {}
    assert store.db.row("SELECT data FROM offline_items")["data"] == "{}"           # the content is really gone


def test_syncing_twice_changes_nothing_and_does_not_echo_what_was_just_sent(store):
    first = store.sync([item()])
    assert first["applied"] == 1 and first["items"] == []
    again = store.sync([item()], cursor=0)
    assert again["applied"] == 0 and again["items"] == []


def test_a_second_device_receives_changes_after_its_cursor_only(store):
    store.sync([item("a"), item("b")])
    pull = store.sync([], cursor=0)
    assert {i["id"] for i in pull["items"]} == {"a", "b"}
    store.sync([item("c")])
    nxt = store.sync([], cursor=pull["cursor"])
    assert [i["id"] for i in nxt["items"]] == ["c"]
    assert store.sync([], cursor=nxt["cursor"])["items"] == []


def test_devices_converge_whatever_the_order_of_their_syncs():
    rng = random.Random(3)
    for _ in range(40):
        batches = [[rnd(rng) | {"id": rng.choice(["a", "b"])} for _ in range(3)] for _ in range(3)]
        finals = set()
        for order in itertools.permutations(range(3)):
            s = osync.OfflineStore(UserDB(":memory:"), clock=lambda: 1.0)
            for i in order:
                s.sync(batches[i])
            finals.add(tuple((r["kind"], r["id"], r["version"], r["updated"], r["deleted"], r["data"]) for r in
                             s.db.rows("SELECT kind,id,version,updated,deleted,data FROM offline_items ORDER BY id")))
        assert len(finals) == 1


@pytest.mark.parametrize("bad", [
    "not an object", item(kind="spy"), item(iid="../x"), item(iid=""), item(iid="a" * 65), item(version=0), item(version=True),
    item(version="1"), item(updated=-1.0), item(updated="now"), {**item(), "deleted": "yes"}, {**item(), "data": [1]},
    item(data={"t": "x" * 5000}),
])
def test_bad_items_are_refused_and_nothing_is_applied(store, bad):
    with pytest.raises(osync.BadItem):
        store.sync([item("fine"), bad])
    assert store.db.rows("SELECT * FROM offline_items") == []


def test_request_and_store_limits(store):
    with pytest.raises(osync.BadItem):
        store.sync([item(f"i{n}") for n in range(osync.MAX_ITEMS_PER_SYNC + 1)])
    with pytest.raises(osync.BadItem):
        store.sync([], cursor=-1)
    with pytest.raises(osync.BadItem):
        store.sync("x")
    for n in range(0, 25):
        store.sync([item(f"k{n}")])
    assert len(store.sync([], cursor=0)["items"]) == 25


def test_a_big_pull_is_paged_by_the_cursor(store):
    for n in range(osync.MAX_ITEMS_PER_SYNC + 20):
        store.db.x("INSERT INTO offline_items VALUES ('note',?,1,1.0,0,'{}',?,1.0)", (f"p{n}", n + 1))
    first = store.sync([], cursor=0)
    assert len(first["items"]) == osync.MAX_ITEMS_PER_SYNC
    second = store.sync([], cursor=first["cursor"])
    assert len(second["items"]) == 20 and second["cursor"] == store.cursor()


def test_old_tombstones_are_purged_but_live_items_and_young_tombstones_stay(store):
    store.db.x("INSERT INTO offline_items VALUES ('note','old',2,1.0,1,'{}',1,1.0)")
    store.db.x("INSERT INTO offline_items VALUES ('note','young',2,1.0,1,'{}',2,?)", (5_000_000.0 - 10,))
    store.db.x("INSERT INTO offline_items VALUES ('note','live',1,1.0,0,'{}',3,1.0)")
    assert store.purge_tombstones() == 1
    assert {r["id"] for r in store.db.rows("SELECT id FROM offline_items")} == {"young", "live"}
