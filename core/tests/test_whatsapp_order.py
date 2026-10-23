"""Issue #17: message ORDER. Conversations are presented oldest to newest with a time label that carries the date when
the window spans days; ties break by id; a busy window keeps ALL messages (and says when the bridge cut the earliest);
the inbox is most-recent-chat-first and says so; search is newest first, dated and labeled; the rule/description text
tells the model which order it receives. Offline: a stub client that returns the bridge's messages in a hostile order,
and the fake bridge for the end-to-end paths."""
from __future__ import annotations

import json
import random
from datetime import datetime

from jarves import dashboard
from jarves import whatsapp_bridge as wab
from jarves.config import Config
from jarves.whatsapp_bridge import WhatsAppBridgeSkill
from test_dashboard import req  # noqa: F401
from whatsapp_fixtures import BOB, DANA, bridge, clock, home  # noqa: F401

NOON = datetime(2026, 9, 22, 12, 0).timestamp()         # local noon: labels do not depend on the machine's zone


def at(day: int, hh: int, mm: int = 0, ss: int = 0) -> float:
    return datetime(2026, 9, day, hh, mm, ss).timestamp()


class Stub:
    """A client whose /messages answers in whatever order the test wants (as a buggy or future bridge might)."""
    def __init__(self, msgs, chats=None, hits=None, order=None):
        self.msgs, self.order, self.calls = msgs, order or (lambda ms: ms), []
        self._chats = chats or [{"id": DANA, "name": "דנה כהן", "unread": 1, "last_ts": NOON, "number": "+972541112222"}]
        self.hits = hits or []

    def chats(self, since=None, limit=100):
        return self._chats

    def messages(self, chat, since=None, limit=20):
        self.calls.append((chat, since, limit))
        ms = [m for m in self.msgs if since is None or m["ts"] > since]
        ms = sorted(ms, key=lambda m: (m["ts"], m["id"]))[-limit:]      # what a correct bridge does ...
        return {"chat": self._chats[0], "messages": self.order(ms)}    # ... then the test scrambles it

    def search(self, q, limit=20):
        return self.hits

    def seen(self, chat):
        return {}

    def status(self):
        return {}


def msg(i, ts, text=None, from_me=False, **kw):
    return {"id": f"M{i:05d}", "chat": DANA, "from_me": from_me, "sender_name": "דנה כהן", "ts": ts, "kind": "text",
            "text": text or f"msg {i}", "media": None, "edited": False, "deleted": False, **kw}


def skill(home, stub, now=NOON):
    return WhatsAppBridgeSkill(home, client=stub, clock=lambda: now)


def tool(sk, name):
    return next(t for t in sk.tools() if t.name == name)


async def run(sk, name, **args):
    t = tool(sk, name)
    return await t.handler(t.args(**args))


async def test_chat_is_chronological_even_if_the_bridge_answers_newest_first(home):
    ms = [msg(i, at(22, 9, i)) for i in range(1, 6)]
    r = await run(skill(home, Stub(ms, order=lambda x: list(reversed(x)))), "whatsapp_chat", who="דנה כהן", limit=5)
    got = [m["text"] for m in r.data["untrusted_messages"]]
    assert got == [f"msg {i}" for i in range(1, 6)]
    assert r.data["order"] == "oldest_first" and "OLDEST FIRST" in r.data["rule"]
    # spoken: the last three, oldest to newest, and it says latest N of M
    assert r.message.index("msg 3") < r.message.index("msg 4") < r.message.index("msg 5") and "msg 2" not in r.message
    assert "3 ההודעות האחרונות מתוך 5" in r.message


async def test_chat_shuffled_and_latest_n_of_m(home):
    ms = [msg(i, at(22, 8) + i * 60) for i in range(1, 41)]
    rnd = random.Random(7)
    r = await run(skill(home, Stub(ms, order=lambda x: rnd.sample(x, len(x)))), "whatsapp_chat", who="דנה כהן", limit=4)
    assert [m["text"] for m in r.data["untrusted_messages"]] == [f"msg {i}" for i in range(37, 41)]
    assert (r.data["shown"], r.data["total"]) == (4, 40) and r.data["total_is_minimum"] is False
    assert "מתוך 40" in r.message


async def test_same_second_ties_are_stable_by_id(home):
    t = at(22, 9, 0, 5)
    ms = [msg(3, t, "third"), msg(1, t, "first"), msg(2, t, "second")]
    r = await run(skill(home, Stub(ms, order=lambda x: list(reversed(x)))), "whatsapp_chat", who="דנה כהן", limit=5)
    assert [m["text"] for m in r.data["untrusted_messages"]] == ["first", "second", "third"]
    assert wab.chrono(list(reversed(ms))) == wab.chrono(ms)


async def test_window_crossing_midnight_shows_dates(home):
    ms = [msg(1, at(21, 22, 10)), msg(2, at(21, 23, 55)), msg(3, at(22, 0, 5)), msg(4, at(22, 9, 5))]
    r = await run(skill(home, Stub(ms, order=lambda x: list(reversed(x)))), "whatsapp_chat", who="דנה כהן", limit=5)
    v = r.data["untrusted_messages"]
    assert [m["when"] for m in v] == ["yesterday 22:10", "yesterday 23:55", "00:05", "09:05"]
    assert v[0]["date"] == "2026-09-21" and v[3]["date"] == "2026-09-22"
    assert "אתמול" in r.message or "00:05" in r.message
    # a single day that is today keeps the plain clock
    r2 = await run(skill(home, Stub(ms[2:])), "whatsapp_chat", who="דנה כהן", limit=5)
    assert [m["when"] for m in r2.data["untrusted_messages"]] == ["00:05", "09:05"]


def test_when_label_forms():
    assert wab.when_label(at(22, 9, 5), NOON) == "09:05"
    assert wab.when_label(at(21, 22, 10), NOON) == "yesterday 22:10"
    assert wab.when_label(at(21, 22, 10), NOON, True) == "אתמול 22:10"
    assert wab.when_label(at(18, 9, 5), NOON) == "Fri 09:05"           # 2026-09-18 is a Friday
    assert wab.when_label(at(10, 9, 5), NOON) == "10 Sep 2026 09:05"
    assert wab._secs(NOON * 1000) == NOON                              # milliseconds are scaled down


async def test_busy_day_keeps_all_messages_and_the_earliest(home):
    ms = [msg(i, at(22, 6) + i * 20) for i in range(1, 121)]           # 120 > the old limit of 30
    stub = Stub(ms, order=lambda x: list(reversed(x)))
    r = await run(skill(home, stub), "whatsapp_summarize_asked", who=["דנה כהן"], since_hours=24)
    blk = r.data["untrusted_conversations"][0]
    assert [m["text"] for m in blk["messages"]] == [f"msg {i}" for i in range(1, 121)]
    assert "earlier_not_shown" not in blk and stub.calls[0][2] == wab.CAP_MESSAGES
    assert r.data["order"] == "oldest_first" and "OLDEST FIRST" in r.data["rule"]


async def test_over_the_cap_says_earlier_messages_were_cut(home):
    ms = [msg(i, at(22, 6) + i) for i in range(1, wab.CAP_MESSAGES + 51)]
    r = await run(skill(home, Stub(ms)), "whatsapp_summarize_asked", who=["דנה כהן"], since_hours=24)
    blk = r.data["untrusted_conversations"][0]
    assert len(blk["messages"]) == wab.CAP_MESSAGES and "earlier_not_shown" in blk
    assert blk["messages"][-1]["text"] == f"msg {wab.CAP_MESSAGES + 50}"             # the NEWEST are kept
    assert "earlier_not_shown" in r.data["rule"]


async def test_summary_budget_never_blanks_the_newest_messages(home):
    ms = [msg(i, at(22, 6) + i * 60, "x" * 900) for i in range(1, 31)]
    r = await run(skill(home, Stub(ms)), "whatsapp_summarize_asked", who=["דנה כהן"], since_hours=24)
    v = r.data["untrusted_conversations"][0]["messages"]
    assert all(m["text"] for m in v) and len(v[-1]["text"]) > 0 and sum(len(m["text"]) for m in v) <= 12000


async def test_multi_day_summary_has_dates(home):
    ms = [msg(1, at(20, 9)), msg(2, at(21, 22, 10)), msg(3, at(22, 8))]
    r = await run(skill(home, Stub(ms)), "whatsapp_summarize_asked", who=["דנה כהן"], since_hours=72)
    assert [m["when"] for m in r.data["untrusted_conversations"][0]["messages"]] == ["Sun 09:00", "yesterday 22:10", "08:00"]


async def test_inbox_is_most_recent_first_and_says_so(home):
    chats = [{"id": DANA, "name": "Old", "unread": 1, "last_ts": at(22, 8)},
             {"id": BOB, "name": "New", "unread": 2, "last_ts": at(22, 11)},
             {"id": "x@s.whatsapp.net", "name": "Mid", "unread": 3, "last_ts": at(21, 20)}]
    r = await run(skill(home, Stub([], chats=chats)), "whatsapp_inbox")
    assert [c["name"] for c in r.data["untrusted_chats"]] == ["New", "Old", "Mid"]
    assert r.data["order"] == "most_recent_first" and "MOST RECENT FIRST" in r.data["rule"]
    assert "מהאחרונה לישנה" in r.message and r.data["untrusted_chats"][2]["last"] == "yesterday 20:00"


async def test_search_is_newest_first_dated_and_labeled(home):
    hits = [msg(1, at(20, 9), "pizza old"), msg(3, at(22, 8), "pizza new"), msg(2, at(21, 8), "pizza mid")]
    r = await run(skill(home, Stub([], hits=hits)), "whatsapp_search", query="pizza")
    v = r.data["untrusted_messages"]
    assert [m["text"] for m in v] == ["pizza new", "pizza mid", "pizza old"]
    assert [m["when"] for m in v] == ["08:00", "yesterday 08:00", "Sun 09:00"]
    assert r.data["order"] == "newest_first" and "NEWEST FIRST" in r.data["rule"] and "מהחדשה לישנה" in r.message


def test_tool_descriptions_and_rules_state_the_order(home):
    d = {t.name: t.description for t in skill(home, Stub([])).tools()}
    assert "oldest first" in d["whatsapp_chat"] and "oldest first" in d["whatsapp_summarize_asked"]
    assert "most recent first" in d["whatsapp_inbox"] and "newest first" in d["whatsapp_search"]
    assert "oldest first" in wab.BACKEND_RULE and "newest first" in wab.BACKEND_RULE


# ---- end to end with the fake bridge (real HTTP): the fake sorts like the Go store, including ties
async def test_fake_bridge_round_trip_is_chronological(bridge, home, clock):
    t = clock() - 5000
    bridge.deliver(DANA, "later", ts=t + 20)
    bridge.deliver(DANA, "tie b", ts=t + 10)
    bridge.deliver(DANA, "earlier", ts=t)
    sk = WhatsAppBridgeSkill(home, clock=clock)
    r = await run(sk, "whatsapp_chat", who="+972541112222", limit=20)
    texts = [m["text"] for m in r.data["untrusted_messages"]]
    assert texts.index("earlier") < texts.index("tie b") < texts.index("later")


def test_dashboard_messages_route_is_chronological_and_dated(bridge, home):
    bridge.phone_last_seen = None
    d = dashboard.Dashboard(home, Config()).start()
    try:
        r, body = req(d, "GET", f"/api/whatsapp/messages?chat={DANA.replace('@', '~')}")
        m = json.loads(body)
        assert r.status == 200 and m["order"] == "oldest_first"
        stamps = [(x["date"], x["time"]) for x in m["messages"]]
        assert stamps == sorted(stamps) and all(x["date"] for x in m["messages"])
        assert all(x["when"] != x["time"] for x in m["messages"])      # the seeded day is not "today": dated labels
        r, body = req(d, "GET", "/api/whatsapp")
        lasts = [c.get("last_ts", 0) for c in json.loads(body)["chats"]]
        assert lasts == sorted(lasts, reverse=True)
    finally:
        d.stop()
