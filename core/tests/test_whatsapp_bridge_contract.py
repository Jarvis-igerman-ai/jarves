"""SW-V4 F: BridgeProtocol v1 contract (bridge/whatsapp/PROTOCOL.md) against the local fake bridge.

The same HTTP/WebSocket contract the Go bridge implements: auth (user vs admin token, Host check), read gate (groups
hidden, read allow-list), messages (Hebrew / English / Arabic, emoji, 4,000 characters, LID vs phone ids, same-name
contacts, media as metadata), mode + watch window, kill file within 2 s, auto-kill on account.warning / logged_out,
retention purge on a deterministic clock, keep-nothing, events without text, the encrypted store on disk.

Owner mode: with JARVES_WA_REAL=<state dir of a REAL linked bridge> the read-only `test_real_*` checks below exist and
run against it (never in CI; nothing is sent). Without it they are not defined at all (no skips)."""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest

from jarves import whatsapp_policy as pol
from jarves.whatsapp_bridge import BridgeClient, BridgeError, health_view
from whatsapp_fixtures import (BOB, DANA, DANA2, GROUP, LAYLA, LIDUSER, bridge, clock, home, seed,  # noqa: F401
                               wait_until)


def raw(b, method, path, token=None, host=None, **kw):
    h = {"Authorization": f"Bearer {token if token is not None else b.token}"}
    if host:
        h["Host"] = host
    return httpx.request(method, f"http://127.0.0.1:{b.port}{path}", headers=h, timeout=5, trust_env=False, **kw)


def test_endpoint_file_tokens_and_auth(bridge, home):
    ep = json.loads((home / "whatsapp_bridge" / "endpoint.json").read_text())
    assert ep["port"] == bridge.port and ep["engine"] == "fake"
    assert bridge.token.startswith("wab_") and bridge.admin_token.startswith("waa_")
    assert len(bytes.fromhex((home / "whatsapp_bridge" / "approval.key").read_text())) == 32
    assert raw(bridge, "GET", "/status", token="nope").status_code == 401
    r = raw(bridge, "GET", "/status", host="evil.example:80")
    assert r.status_code == 403 and r.json()["error"]["code"] == "WA_BAD_HOST"       # DNS rebinding guard
    assert raw(bridge, "GET", "/status").json()["mode"] == "read"
    # the user token never reaches an admin endpoint
    for path, body in (("/mode", {"mode": "send"}), ("/allow", {"list": "send", "op": "add", "chat": DANA}),
                       ("/kill", {"on": False}), ("/link", {}), ("/unlink", {}), ("/purge", {}), ("/retention", {"days": 1}),
                       ("/read_scope", {"groups": True}), ("/shutdown", {})):
        r = raw(bridge, "POST", path, json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "WA_FORBIDDEN", path
    assert raw(bridge, "GET", "/policy").status_code == 403
    assert bridge.policy["mode"] == "read"


def test_status_has_no_names_numbers_or_text(bridge):
    st = BridgeClient(bridge.dir.parent).status()
    blob = json.dumps(st, ensure_ascii=False)
    for needle in ("דנה", "Bob", "972541112222", "מגיע", "Meeting"):
        assert needle not in blob
    assert st["counts"]["unread"] >= 4 and st["device_name"] == "JARVES bridge" and st["key_protection"] in ("dpapi", "file")


def test_read_gate_groups_hidden_and_allow_list(bridge, home):
    c = BridgeClient(home)
    ids = {x["id"] for x in c.chats()}
    assert GROUP not in ids and {DANA, DANA2, BOB, LAYLA, LIDUSER} <= ids
    with pytest.raises(BridgeError) as e:
        c.messages(GROUP)
    assert e.value.code == "WA_CHAT_NOT_FOUND" and e.value.status == 404
    ad = BridgeClient(home, admin=True)
    ad.call("POST", "/read_scope", body={"all_direct": False})
    assert {x["id"] for x in c.chats()} == set()
    ad.call("POST", "/allow", body={"list": "read", "op": "add", "chat": "+972 54-111-2222"})
    assert {x["id"] for x in c.chats()} == {DANA}
    ad.call("POST", "/allow", body={"list": "read", "op": "add", "chat": GROUP})
    assert GROUP not in {x["id"] for x in c.chats()}                        # a group also needs groups=true
    ad.call("POST", "/read_scope", body={"groups": True})
    assert GROUP in {x["id"] for x in c.chats()}


def test_messages_languages_media_lid_and_long_text(bridge, home):
    c = BridgeClient(home)
    long = "א" * 2000 + "B" * 2000
    bridge.deliver(LAYLA, long)
    ms = c.messages(LAYLA, limit=10)["messages"]
    assert ms[0]["text"] == "مرحبا، كيف حالك؟" and ms[-1]["text"] == long and len(ms[-1]["text"]) == 4000
    dana = c.messages(DANA)["messages"]
    assert dana[0]["text"].endswith("🙂") and dana[-1]["from_me"] is True
    img = [m for m in c.messages(BOB)["messages"] if m["kind"] == "image"][0]
    assert img["text"] == "" and img["media"] == {"mime": "image/jpeg", "size": 120000, "caption": "the whiteboard"}
    lid = [x for x in c.chats() if x["id"] == LIDUSER][0]
    assert lid["number"] == "" and pol.is_user(LIDUSER)
    names = [x["name"] for x in c.chats() if x["name"].startswith("דנה")]
    assert sorted(names) == ["דנה כהן", "דנה לוי"]                          # same first name: both listed
    assert {m["text"] for m in c.search("מטען")} == {"תביא את המטען"}
    assert c.search("whiteboard")[0]["kind"] == "image"                      # captions are searchable


def test_seen_is_local_only(bridge, home):
    c = BridgeClient(home)
    before = [x for x in c.chats() if x["id"] == DANA][0]["unread"]
    c.seen(DANA)
    assert before and [x for x in c.chats() if x["id"] == DANA][0]["unread"] == 0
    assert bridge.outbox == []                                                # nothing goes to WhatsApp


def test_mode_off_refuses_reads_and_watch_window(bridge, home, clock):
    ad, c = BridgeClient(home, admin=True), BridgeClient(home)
    with pytest.raises(BridgeError) as e:
        ad.call("POST", "/mode", body={"mode": "send"})
    assert e.value.code == "WA_WATCH_WINDOW" and e.value.extra["days_left"] == 14
    clock.advance(13 * 86400)
    with pytest.raises(BridgeError):
        ad.call("POST", "/mode", body={"mode": "send"})
    bridge.warn("account_at_risk")                              # a warning restarts the watch (and kills)
    bridge.release()
    ad.call("POST", "/mode", body={"mode": "read"})
    clock.advance(14 * 86400)
    assert ad.call("POST", "/mode", body={"mode": "send"})["mode"] == "send"
    ad.call("POST", "/mode", body={"mode": "off"})
    with pytest.raises(BridgeError) as e:
        c.chats()
    assert e.value.code == "WA_MODE_OFF"
    assert ad.call("POST", "/mode", body={"mode": "send", "override_watch": True})["mode"] == "send"


def test_kill_file_stops_within_two_seconds_and_release(bridge, home):
    c = BridgeClient(home)
    assert c.status()["connection"] == "connected"
    t0 = time.monotonic()
    (home / "whatsapp_bridge" / "KILL").write_text("owner_test\n")
    wait_until(lambda: c.status()["killed"], timeout=2.0)
    assert time.monotonic() - t0 < 2.0
    st = c.status()
    assert st["connection"] == "disconnected" and st["kill_reason"] == "owner_test"
    assert health_view(st)[0] == "unavailable"
    assert c.chats()                                            # reads of stored data keep working
    BridgeClient(home, admin=True).call("POST", "/kill", body={"on": False})
    assert not (home / "whatsapp_bridge" / "KILL").exists() and c.status()["connection"] == "connected"


def test_auto_kill_on_warning_and_logout(bridge, home):
    c = BridgeClient(home)
    bridge.warn("account_at_risk")
    st = c.status()
    assert st["killed"] and st["account_warning"]["code"] == "account_at_risk"
    assert (home / "whatsapp_bridge" / "KILL").read_text().startswith("account_warning")
    assert "account warning" in health_view(st)[1]
    bridge.release()
    bridge.logged_out()
    st = c.status()
    assert st["killed"] and st["connection"] == "logged_out"


def test_retention_purge_keep_nothing_and_store_on_disk(bridge, home, clock):
    c, ad = BridgeClient(home), BridgeClient(home, admin=True)
    raw_store = (home / "whatsapp_bridge" / "store.enc").read_bytes()
    assert raw_store[:4] == b"JWS1"
    for plain in ("מגיע".encode(), b"Meeting moved", b"972541112222", "דנה".encode()):
        assert plain not in raw_store                          # encrypted at rest
    clock.advance(13 * 86400)
    assert c.messages(DANA)["messages"]
    clock.advance(2 * 86400)                                   # past 14 days: purged on the next read
    assert c.messages(DANA)["messages"] == []
    assert [x for x in c.chats() if x["id"] == DANA][0]["has_history"] is True     # the chat history flag stays
    bridge.deliver(BOB, "fresh one")
    assert ad.call("POST", "/purge", body={"older_than_days": None})["deleted"] >= 1
    ad.call("POST", "/retention", body={"days": 3, "keep_nothing": True})
    bridge.deliver(BOB, "PLANTEDLIVEONLY")
    assert b"PLANTEDLIVEONLY" not in bridge.store.load()      # keep nothing: never written, not even encrypted
    with pytest.raises(BridgeError):
        ad.call("POST", "/retention", body={"days": 0})


def test_events_websocket_carries_no_text(bridge, home):
    from websockets.asyncio.client import connect

    async def go():
        async with connect(f"ws://127.0.0.1:{bridge.port}/events",
                           additional_headers={"Authorization": f"Bearer {bridge.token}"}, proxy=None) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
            assert hello["type"] == "hello" and hello["status"]["mode"] == "read"
            await asyncio.to_thread(bridge.deliver, DANA, "PLANTEDSECRETTEXT")
            await asyncio.to_thread(bridge.deliver, GROUP, "group text")          # unreadable chat: no event
            await asyncio.to_thread(bridge.set_connection, "disconnected")
            evs = [json.loads(await asyncio.wait_for(ws.recv(), 10)) for _ in range(2)]
            return evs
    evs = asyncio.run(go())
    assert [e["type"] for e in evs] == ["message.new", "connection.state"]
    assert evs[0]["chat"] == DANA and "PLANTEDSECRETTEXT" not in json.dumps(evs)
    assert evs[0]["seq"] < evs[1]["seq"]


def test_events_refuse_a_wrong_token(bridge):
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    async def go():
        async with connect(f"ws://127.0.0.1:{bridge.port}/events", additional_headers={"Authorization": "Bearer x"},
                           proxy=None):
            pass
    with pytest.raises(InvalidStatus):
        asyncio.run(go())


def test_link_flow_with_qr_and_pairing_code(home, clock):
    from tools.fake_whatsapp_bridge import FakeBridge
    b = FakeBridge(home / "whatsapp_bridge", clock=clock, linked=False)
    b.start()
    try:
        ad = BridgeClient(home, admin=True)
        st = BridgeClient(home).status()
        assert st["connection"] == "not_linked" and health_view(st)[0] == "degraded"
        r = ad.call("POST", "/link")
        assert r["state"] == "waiting" and r["qr"] and r["qr_text"] and not r["pairing_code"]
        r = ad.call("POST", "/link", body={"phone": "+972541112222"})
        assert len(r["pairing_code"]) == 9
        b.scan()
        assert ad.call("GET", "/link")["state"] == "linked" and BridgeClient(home).status()["connection"] == "connected"
        ad.call("POST", "/unlink")
        assert not (home / "whatsapp_bridge" / "store.enc").exists() or BridgeClient(home).status()["linked"] is False
    finally:
        b.stop()


def test_restart_reconnects_without_qr_and_keeps_messages(bridge, home, clock):
    from tools.fake_whatsapp_bridge import FakeBridge
    bridge.stop()
    b2 = FakeBridge(home / "whatsapp_bridge", clock=clock, hour=lambda: 12)
    b2.start()
    try:
        st = BridgeClient(home).status()
        assert st["linked"] and st["connection"] == "connected"
        assert BridgeClient(home).messages(DANA)["messages"]
    finally:
        b2.stop()


def test_bridge_down_is_a_clear_error(home):
    with pytest.raises(BridgeError) as e:
        BridgeClient(home).status()
    assert e.value.code == "WA_BRIDGE_DOWN"
    assert health_view(None)[0] == "unavailable"


REAL = os.environ.get("JARVES_WA_REAL", "").strip()
if REAL:                       # owner mode only (never in CI): read-only checks against the real linked bridge
    from pathlib import Path as _P

    def test_real_status_and_read_gate():
        home = _P(REAL).parent
        st = BridgeClient(home).status()
        assert st["engine"] == "whatsmeow" and st["linked"] and st["device_name"] == "JARVES bridge"
        assert all(not c["is_group"] for c in BridgeClient(home).chats())

    def test_real_user_token_cannot_change_mode():
        home = _P(REAL).parent
        with pytest.raises(BridgeError) as e:
            BridgeClient(home).call("POST", "/mode", body={"mode": "send"})
        assert e.value.code == "WA_FORBIDDEN"
