"""SW-V4 F: the send policy is enforced BY THE BRIDGE, even with a compromised Core.

1. The shared table bridge/whatsapp/testdata/policy_table.json (also run by `go test ./internal/policy`) against
   jarves.whatsapp_policy, and every row again through the fake bridge's real HTTP /send (a "compromised Core" =
   raw HTTP with the user token: no approval id, a reused one, a non-allow-listed recipient, rate exceeded, read mode).
2. Mode, allow-lists and kill release cannot be changed from the model path: no tool does it, the tools' client has
   the user token only (403 from the bridge), and the dashboard refuses a non-local principal."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jarves import whatsapp_policy as pol
from whatsapp_fixtures import DANA, LIDUSER, bridge, clock, home, set_send_mode  # noqa: F401

TABLE = json.loads((Path(__file__).resolve().parents[2] / "bridge" / "whatsapp" / "testdata" / "policy_table.json")
                   .read_text(encoding="utf-8"))
NONCE = "0" * 31 + "1"


def merged(row: dict) -> tuple[dict, dict]:
    st = {**TABLE["defaults"]["state"], **row.get("state", {})}
    rq = {**TABLE["defaults"]["request"], **row.get("request", {})}
    rq["approval"] = {**TABLE["defaults"]["request"]["approval"], **row.get("request", {}).get("approval", {})}
    if isinstance(rq["text"], dict):
        rq["text"] = rq["text"]["repeat"] * rq["text"]["n"]
    return st, rq


def token(key: bytes, rq: dict, now: float) -> str:
    a = rq["approval"]
    kind, exp = a["kind"], now + a.get("exp_in", 120)
    if kind == "none":
        return ""
    if kind == "garbage":
        return "not-a-token"
    to = LIDUSER if kind == "wrong_to" and rq["to"] != LIDUSER else rq["to"]
    text = rq["text"] + "!" if kind == "wrong_text" else rq["text"]
    t = pol.sign_approval(key if kind != "bad_sig" else b"x" * 32, to, text, exp=exp, night=a.get("night", False),
                          nonce=NONCE)
    return t


def expect_of(row) -> tuple[str, str]:
    e = row["expect"]
    return ("OK", "") if e == "OK" else (e["code"], e.get("limit", ""))


@pytest.mark.parametrize("row", TABLE["rows"], ids=[r["name"] for r in TABLE["rows"]])
def test_shared_policy_table(row):
    key, now = bytes.fromhex(TABLE["key_hex"]), float(TABLE["now"])
    st, rq = merged(row)
    s = pol.SendState(mode=st["mode"], killed=st["killed"], send_allow=set(st["send_allow"]), history=set(st["history"]),
                      sent=[(now - age, to) for age, to in st["sent"]],
                      used_nonces={NONCE: now + 300} if rq["approval"]["kind"] == "reused" else {})
    ref, appr = pol.check_send(s, rq["to"], rq["text"], token(key, rq, now), key, now=now, local_hour=st["local_hour"])
    code, limit = expect_of(row)
    if code == "OK":
        assert ref is None and appr is not None, (row["name"], ref and ref.code)
    else:
        assert ref is not None and ref.code == code and ref.limit == limit, (row["name"], ref and ref.code)


@pytest.mark.parametrize("row", TABLE["rows"], ids=[r["name"] for r in TABLE["rows"]])
def test_bridge_enforces_every_row_over_http(row, bridge, clock):
    """The fake bridge's /send (the Core's only path) gives the same answer; a refusal never sends."""
    st, rq = merged(row)
    now = clock()
    bridge.hour = lambda: st["local_hour"]
    bridge.policy.update(mode=st["mode"], send_allow=list(st["send_allow"]))
    for jid in st["history"]:
        if jid not in bridge.chats:
            bridge.add_chat(jid, "x")
    for jid in list(bridge.chats):
        bridge.chats[jid]["has_history"] = jid in st["history"]
    for jid in (rq["to"],):
        if jid not in bridge.chats and pol.is_user(jid):
            bridge.add_chat(jid, "x", history=jid in st["history"])
    bridge.sent = [(now - age, to) for age, to in st["sent"]]
    bridge.used = {NONCE: now + 300} if rq["approval"]["kind"] == "reused" else {}
    if st["killed"]:
        bridge.kill("test")
    r = httpx.post(f"http://127.0.0.1:{bridge.port}/send", headers={"Authorization": f"Bearer {bridge.token}"},
                   json={"to": rq["to"], "text": rq["text"], "approval_id": token(bridge.key, rq, now)},
                   timeout=5, trust_env=False)
    code, limit = expect_of(row)
    if code == "OK":
        assert r.status_code == 200, (row["name"], r.text)
        assert bridge.outbox[-1]["text"] == rq["text"]
    else:
        body = r.json()["error"]
        assert body["code"] == code and body.get("limit", "") == limit, (row["name"], body)
        assert r.status_code == pol.STATUS[code]
        assert bridge.outbox == []


def test_reused_approval_id_is_refused_by_the_bridge(bridge, clock):
    set_send_mode(bridge, DANA)
    tok = pol.sign_approval(bridge.key, DANA, "שלום", exp=clock() + 60)
    post = lambda: httpx.post(f"http://127.0.0.1:{bridge.port}/send", headers={"Authorization": f"Bearer {bridge.token}"},  # noqa: E731
                              json={"to": DANA, "text": "שלום", "approval_id": tok}, timeout=5, trust_env=False)
    assert post().status_code == 200
    clock.advance(20)
    r = post()
    assert r.status_code == 403 and r.json()["error"]["code"] == "WA_APPROVAL_REUSED"
    assert len(bridge.outbox) == 1
    audit = (bridge.dir / "audit.jsonl").read_text(encoding="utf-8")
    assert "שלום" not in audit and "972541112222" not in audit and "WA_APPROVAL_REUSED" in audit


def test_text_cleaning_reports_hidden_characters():
    text, removed = pol.clean_text("pay‮​now⁦ ok\x07")
    assert text == "paynow ok" and removed == ["U+0007", "U+200B", "U+202E", "U+2066"]
    assert pol.clean_text("שלום 🙂\nשורה")[0] == "שלום 🙂\nשורה"


def test_ids():
    assert pol.number_to_jid("054-111-2222") == DANA and pol.number_to_jid("+972541112222") == DANA
    assert pol.number_to_jid("hello") is None and pol.jid_number(DANA) == "+972541112222"
    assert pol.jid_number(LIDUSER) == "" and pol.is_group("1@g.us") and not pol.is_user("1@g.us")
    assert len(pol.chat_hash(DANA)) == 16


# ---------------------------------------------------------------------- the model path cannot change the policy
def test_no_tool_changes_mode_allow_lists_or_kill(home):
    from jarves.skills import SkillRegistry
    from jarves.whatsapp_bridge import WhatsAppBridgeSkill
    reg = SkillRegistry()
    skill = WhatsAppBridgeSkill(home)
    reg.register(skill)
    names = {t.name for t in skill.tools()}
    assert names == {"whatsapp_status", "whatsapp_inbox", "whatsapp_chat", "whatsapp_summarize_asked",
                     "whatsapp_search", "whatsapp_find_chat", "whatsapp_show", "whatsapp_compose",
                     "whatsapp_send"}
    for t in skill.tools():
        props = set(t.args.model_json_schema().get("properties", {}))
        assert not props & {"mode", "list", "op", "allow", "on", "logout", "override_watch", "days"}, t.name
    assert skill.client.admin is False                    # the tools' client holds the USER token only


def test_tool_client_gets_403_on_admin_endpoints(bridge, home):
    from jarves.whatsapp_bridge import BridgeError, WhatsAppBridgeSkill
    c = WhatsAppBridgeSkill(home).client
    for path, body in (("/mode", {"mode": "send", "override_watch": True}),
                       ("/allow", {"list": "send", "op": "add", "chat": DANA}), ("/kill", {"on": False})):
        with pytest.raises(BridgeError) as e:
            c.call("POST", path, body=body)
        assert e.value.code == "WA_FORBIDDEN"
    assert bridge.policy["mode"] == "read" and bridge.policy["send_allow"] == []


def test_voice_feature_switch_cannot_flip_the_bridge_mode(bridge, home):
    """feature_set (the model/voice path to switches) only flips JARVES's own switch; the bridge mode stays."""
    from jarves import features as feat
    from jarves.config import Config
    f = feat.Features(Config(), home)
    f.set("whatsapp_send", True, by="voice")
    assert bridge.policy["mode"] == "read"
    assert feat.CATALOG["whatsapp_send"].risk == "high" and feat.CATALOG["whatsapp_bridge"].risk == "high"


class _Ctx:
    def __init__(self, home, kind):
        self.home, self.principal = home, {"kind": kind, "id": "x"}


def test_dashboard_writes_refused_for_a_paired_phone(bridge, home):
    from jarves import whatsapp_admin
    routes = {}
    whatsapp_admin.dashboard_routes(lambda m, p, fn: routes.__setitem__((m, p), fn))
    phone = _Ctx(home, "device")
    for name, body in (("mode", {"mode": "send", "override_watch": True}), ("allow", {"list": "send", "chat": DANA}),
                       ("kill", {"on": False}), ("unlink", {}), ("purge", {"confirm": True}), ("retention", {"days": 1})):
        status, out = routes[("POST", rf"/api/whatsapp/{name}")](phone, None, body)
        assert status == 403, name
    assert routes[("GET", r"/api/whatsapp/messages")](phone, None, None, {"chat": [DANA]})[0] == 403
    assert bridge.policy["mode"] == "read" and bridge.policy["send_allow"] == []
    # a remote phone's write is also outside the dashboard's own device allow-list
    from jarves.dashboard_auth import _DEVICE_WRITE_OK
    assert not _DEVICE_WRITE_OK.fullmatch("/api/whatsapp/mode")
    local = _Ctx(home, "local")
    status, out = routes[("POST", r"/api/whatsapp/allow")](local, None, {"list": "send", "op": "add", "chat": DANA})
    assert status == 200 and bridge.policy["send_allow"] == [DANA]
    status, out = routes[("POST", r"/api/whatsapp/mode")](local, None, {"mode": "off"})
    assert status == 200 and bridge.policy["mode"] == "off"
