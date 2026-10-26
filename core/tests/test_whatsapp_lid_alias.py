"""Hotfix (#329/#332): after WhatsApp moved a contact to LID addressing the same person was two chats: the old phone
chat (saved name, history up to the move) and a new LID chat (today's messages, short name). find_chat matched only the
stale one, so "all messages from her today" found nothing. Chats sharing a lid are one chat; search reads every id."""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from jarves import whatsapp_bridge as wab
from jarves.whatsapp_bridge import WhatsAppBridgeSkill

NOW = time.time()
OLD = {"id": "972500000001@s.whatsapp.net", "lid": "5551@lid", "name": "my perfect wife", "number": "972500000001",
       "last_ts": NOW - 2 * 86400, "unread": 0, "has_history": True}
NEW = {"id": "5551@lid", "lid": "5551@lid", "name": "Vi", "number": "972500000001", "last_ts": NOW - 60,
       "unread": 3, "has_history": True}
OTHER = {"id": "972500000002@s.whatsapp.net", "lid": "", "name": "Bob", "number": "972500000002", "last_ts": NOW}


def test_merge_aliases_one_chat_newest_id_best_name():
    out = wab.merge_aliases([OLD, OTHER, NEW])
    assert len(out) == 2
    m = next(c for c in out if c.get("alias_ids"))
    assert m["id"] == NEW["id"] and m["name"] == "my perfect wife" and m["unread"] == 3
    assert set(m["alias_ids"]) == {OLD["id"], NEW["id"]}
    assert wab.merge_aliases([OTHER]) == [OTHER]


def test_merge_never_joins_different_people():
    a = {"id": "1@s.whatsapp.net", "lid": "", "name": "A", "number": "1"}
    b = {"id": "2@s.whatsapp.net", "lid": None, "name": "B", "number": "2"}
    g1 = {"id": "1@g.us", "lid": "9@lid", "is_group": True, "name": "G1"}
    g2 = {"id": "2@g.us", "lid": "9@lid", "is_group": True, "name": "G2"}
    x = {"id": "3@s.whatsapp.net", "lid": "7@lid", "name": "X", "number": "3"}
    y = {"id": "4@s.whatsapp.net", "lid": "7@lid", "name": "Y", "number": "4"}
    out = wab.merge_aliases([a, b, g1, g2, x, y])
    assert len(out) == 6 and not any(c.get("alias_ids") for c in out)


def test_find_chat_name_resolves_to_the_chat_with_todays_messages():
    hits = wab.resolve(wab.merge_aliases([OLD, NEW, OTHER]), "wife")
    assert [c["id"] for c in hits] == [NEW["id"]]


class Client:
    def __init__(self, new_ts=None):
        self.asked = []
        self.new_ts = NOW - 30 if new_ts is None else new_ts

    def chats(self, since=None, limit=100):
        return [dict(OLD), dict(NEW), dict(OTHER)]

    def messages(self, chat, since=None, limit=20):
        self.asked.append(chat)
        ts = self.new_ts if chat == NEW["id"] else NOW - 2 * 86400
        return {"chat": chat, "messages": [{"id": chat, "chat": chat, "ts": ts, "from_me": False, "text": "x"}]}


def _local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()   # naive = the local zone, as the product's day boundary uses


# Frozen clocks, independent of the real time of day and zone: shortly after local midnight, noon, just before the next
# midnight, and around the Israel DST end (25 Oct 2026, a 25-hour day). The "today" message is always after local
# midnight of the frozen day and before the frozen now (#441: a real now-30s message fell on yesterday after 00:00).
@pytest.mark.asyncio
@pytest.mark.parametrize("tz", ["", "UTC", "Asia/Jerusalem"] if hasattr(time, "tzset") else [""])   # Windows: no tzset
@pytest.mark.parametrize("now_at", [(2026, 10, 24, 0, 1), (2026, 10, 24, 12, 0), (2026, 10, 24, 23, 59),
                                    (2026, 10, 25, 0, 1), (2026, 10, 25, 12, 0), (2026, 10, 26, 0, 1)])
async def test_search_who_since_reads_the_new_lid_chat(tmp_path, monkeypatch, tz, now_at):
    if tz:
        monkeypatch.setenv("TZ", tz)
        time.tzset()
    try:
        now = _local(*now_at)
        midnight = _local(*now_at[:3], 0)
        s = WhatsAppBridgeSkill(tmp_path, clock=lambda: now)
        s.client = Client(new_ts=midnight + (now - midnight) / 2)     # today, before the frozen now
        t = next(t for t in s.tools() if t.name == "whatsapp_search")
        r = await t.handler(t.args(who="wife", since="today", query="all"))
        assert r.ok and NEW["id"] in s.client.asked
        assert len(r.data["untrusted_messages"]) >= 1
    finally:
        if tz:
            monkeypatch.undo()
            time.tzset()
