"""Offline basics, Core side (V7 A5, #393): the PC keeps a copy of the phone's offline items (notes, reminders, timers) and merges the
phone's changes with its own, so the phone can work in airplane mode and catch up later.

Conflict rule (the same on both sides, so the phone can run the identical merge):
  1. the higher per-item `version` wins;
  2. equal versions: the later `updated` (epoch ms, the writer's clock) wins;
  3. still equal: a tombstone (`deleted`) wins over a live item;
  4. still equal: the smaller canonical JSON wins (any fixed rule works; this makes the merge deterministic).
That is a total order on versions of one item, so the merge is commutative, associative and idempotent: syncing twice, in any order,
from any number of devices, gives every device the same final state. A delete is a tombstone (deleted=True, version+1), never a row removal,
so an old copy on another device cannot bring the item back; tombstones are purged only after PURGE_AFTER_S.

Nothing here talks to the network or to a model. The route in `mobile.py` (later slice) only calls [`OfflineStore.sync`].
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .userdata import UserDB

KINDS = ("note", "reminder", "timer")
MAX_ITEMS_PER_SYNC = 200
MAX_DATA_CHARS = 4000
MAX_ITEMS_TOTAL = 5000
PURGE_AFTER_S = 30 * 24 * 3600
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class BadItem(ValueError):
    """An item that is not well formed; nothing in the request is applied."""


def clean(raw: Any) -> dict:
    """One item in its canonical shape, or BadItem. The phone is not trusted: every field is checked."""
    if not isinstance(raw, dict):
        raise BadItem("item must be an object")
    kind, iid = raw.get("kind"), raw.get("id")
    if kind not in KINDS:
        raise BadItem("unknown kind")
    if not isinstance(iid, str) or not _ID.match(iid):
        raise BadItem("bad id")
    version, updated = raw.get("version"), raw.get("updated")
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 1_000_000:
        raise BadItem("bad version")
    if isinstance(updated, bool) or not isinstance(updated, (int, float)) or not 0 <= updated <= 4_102_444_800_000:
        raise BadItem("bad updated")
    deleted = raw.get("deleted", False)
    if not isinstance(deleted, bool):
        raise BadItem("bad deleted")
    data = raw.get("data", {})
    if not isinstance(data, dict):
        raise BadItem("data must be an object")
    if deleted:
        data = {}                                        # a tombstone carries no content (privacy: a deleted note is really gone)
    if len(json.dumps(data, ensure_ascii=False, sort_keys=True)) > MAX_DATA_CHARS:
        raise BadItem("data too large")
    return {"kind": kind, "id": iid, "version": version, "updated": float(updated), "deleted": deleted, "data": data}


def _canon(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def wins(a: dict, b: dict) -> bool:
    """True when version [a] beats version [b] of the same item."""
    if a["version"] != b["version"]:
        return a["version"] > b["version"]
    if a["updated"] != b["updated"]:
        return a["updated"] > b["updated"]
    if a["deleted"] != b["deleted"]:
        return a["deleted"]
    return _canon(a) < _canon(b)


def merge(a: dict | None, b: dict | None) -> dict | None:
    """The surviving version of two copies of one item (either may be missing)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if wins(a, b) else b


class OfflineStore:
    def __init__(self, db: UserDB, *, clock=time.time) -> None:
        self.db, self.clock = db, clock
        db.x("CREATE TABLE IF NOT EXISTS offline_items ("
             "kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, updated REAL NOT NULL, deleted INTEGER NOT NULL, "
             "data TEXT NOT NULL, seq INTEGER NOT NULL, seen REAL NOT NULL, PRIMARY KEY (kind, id))")
        db.x("CREATE INDEX IF NOT EXISTS offline_seq ON offline_items (seq)")

    def _get(self, kind: str, iid: str) -> dict | None:
        r = self.db.row("SELECT * FROM offline_items WHERE kind=? AND id=?", (kind, iid))
        return None if r is None else self._view(r)

    @staticmethod
    def _view(r: dict) -> dict:
        return {"kind": r["kind"], "id": r["id"], "version": r["version"], "updated": r["updated"],
                "deleted": bool(r["deleted"]), "data": json.loads(r["data"])}

    def _next_seq(self) -> int:
        r = self.db.row("SELECT COALESCE(MAX(seq), 0) AS m FROM offline_items")
        return int(r["m"]) + 1

    def cursor(self) -> int:
        return self._next_seq() - 1

    def sync(self, items: list, cursor: int = 0) -> dict:
        """Apply the phone's items, then return everything that changed on the PC after [cursor] (including what was just applied from
        another device, but not an item whose stored version is exactly what the phone sent). All-or-nothing: a bad item rejects the request."""
        if not isinstance(items, list) or len(items) > MAX_ITEMS_PER_SYNC:
            raise BadItem("too many items")
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise BadItem("bad cursor")
        incoming = [clean(i) for i in items]
        sent = {(i["kind"], i["id"]): i for i in incoming}
        applied = 0
        for it in incoming:
            have = self._get(it["kind"], it["id"])
            if have is None and self.db.row("SELECT COUNT(*) AS n FROM offline_items")["n"] >= MAX_ITEMS_TOTAL:
                raise BadItem("the PC holds too many offline items")
            if have is not None and not wins(it, have):
                continue                                  # the PC's copy survives (or is identical): nothing to write
            if have is not None and _canon(have) == _canon(it):
                continue
            self.db.x("INSERT INTO offline_items (kind,id,version,updated,deleted,data,seq,seen) VALUES (?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(kind,id) DO UPDATE SET version=excluded.version, updated=excluded.updated, deleted=excluded.deleted, "
                      "data=excluded.data, seq=excluded.seq, seen=excluded.seen",
                      (it["kind"], it["id"], it["version"], it["updated"], int(it["deleted"]),
                       json.dumps(it["data"], ensure_ascii=False, sort_keys=True), self._next_seq(), self.clock()))
            applied += 1
        out, last, rows = [], cursor, self.db.rows("SELECT * FROM offline_items WHERE seq > ? ORDER BY seq LIMIT ?", (cursor, MAX_ITEMS_PER_SYNC))
        for r in rows:
            last = r["seq"]
            v = self._view(r)
            if _canon(sent.get((v["kind"], v["id"]), {})) != _canon(v):       # do not echo back exactly what the phone just sent
                out.append(v)
        return {"items": out, "cursor": last if len(rows) >= MAX_ITEMS_PER_SYNC else self.cursor(), "applied": applied}

    def purge_tombstones(self) -> int:
        """Forget deletes older than PURGE_AFTER_S. A device that was offline for longer must do a full re-sync (cursor 0) first."""
        cur = self.db.x("DELETE FROM offline_items WHERE deleted=1 AND seen < ?", (self.clock() - PURGE_AFTER_S,))
        return cur.rowcount
