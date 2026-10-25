"""Hotfix (#329/#332): after WhatsApp moved a contact to LID addressing the same person was two chats: the old phone
chat (saved name, history up to the move) and a new LID chat (today's messages, short name). find_chat matched only the
stale one, so "all messages from her today" found nothing. Chats sharing a lid are one chat; search reads every id."""
from __future__ import annotations

import time

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


def test_find_chat_name_resolves_to_the_chat_with_todays_messages():
    hits = wab.resolve(wab.merge_aliases([OLD, NEW, OTHER]), "wife")
    assert [c["id"] for c in hits] == [NEW["id"]]


class Client:
    def __init__(self):
        self.asked = []

    def chats(self, since=None, limit=100):
        return [dict(OLD), dict(NEW), dict(OTHER)]

    def messages(self, chat, since=None, limit=20):
        self.asked.append(chat)
        ts = NOW - 30 if chat == NEW["id"] else NOW - 2 * 86400
        return {"chat": chat, "messages": [{"id": chat, "chat": chat, "ts": ts, "from_me": False, "text": "x"}]}


@pytest.mark.asyncio
async def test_search_who_since_reads_the_new_lid_chat(tmp_path):
    s = WhatsAppBridgeSkill(tmp_path)
    s.client = Client()
    t = next(t for t in s.tools() if t.name == "whatsapp_search")
    r = await t.handler(t.args(who="wife", since="today", query="all"))
    assert r.ok and NEW["id"] in s.client.asked
    assert len(r.data["untrusted_messages"]) >= 1
