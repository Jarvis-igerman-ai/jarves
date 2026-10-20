"""Instagram DM channel (#17): real HTTP against a local fake Graph API; real SkillRegistry confirmation policy; real
webhook receiver over 127.0.0.1; the real wizard driven by a scripted ConsoleIO."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request

import pytest

from channels_fakes import IG_SECRET, IG_TOKEN, instagram_server
from jarves import features as feat
from jarves import instagram_channel as ig
from jarves.config import Config, secrets_file
from jarves.setup_wizard import ConsoleIO
from jarves.skills import SkillRegistry

ENV_KEYS = (ig.TOKEN_ENV, ig.SECRET_ENV_NAME, ig.VERIFY_ENV, ig.API_BASE_ENV)


@pytest.fixture(autouse=True)
def clean_env():
    old = {k: os.environ.get(k) for k in ENV_KEYS}
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def home(tmp_path):
    return tmp_path


def reg_with(skill, approve=True, log=None):
    reg = SkillRegistry()
    reg.register(skill)
    reg.allowed_actions |= ig.ALLOWED_ACTIONS

    async def confirm(prompt):
        if log is not None:
            log.append(prompt)
        return approve
    reg.confirm = confirm
    return reg


def connect(home, srv):
    os.environ[ig.TOKEN_ENV] = IG_TOKEN
    os.environ[ig.API_BASE_ENV] = srv.url
    ig.write_private_json(ig.account_file(home), {"user_id": "9001", "username": "igal_biz"})


def dm(mid, sender="555", text="hi", age_s=60):
    return {"id": mid, "from": {"id": sender, "username": "anna"}, "message": text,
            "created_time": time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(time.time() - age_s))}


def audit_events(home):
    p = ig.idir(home) / "audit.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


async def test_wizard_saves_token_only_in_secrets_env(home):
    srv = instagram_server()
    shown = []
    try:
        os.environ[ig.API_BASE_ENV] = srv.url
        io = ConsoleIO(lines=[IG_TOKEN, "y", IG_SECRET], write=shown.append)
        assert await ig.Wizard(home, io).run() == 0
        env = secrets_file(home).read_text(encoding="utf-8")
        assert f"INSTAGRAM_ACCESS_TOKEN={IG_TOKEN}" in env and "INSTAGRAM_VERIFY_TOKEN=" in env
        assert IG_TOKEN not in ig.account_file(home).read_text(encoding="utf-8")
        assert ig.account(home)["username"] == "igal_biz" and ig.status(home)["verified"]
        assert IG_TOKEN not in "".join(shown) and IG_SECRET not in "".join(shown)
    finally:
        srv.close()


async def test_wizard_rejects_bad_token_and_personal_accounts(home):
    srv = instagram_server()
    try:
        os.environ[ig.API_BASE_ENV] = srv.url
        assert await ig.Wizard(home, ConsoleIO(lines=["wrong-token"], write=lambda s: None)).run() == 1
        assert not secrets_file(home).exists() and not ig.token_present()
        srv.state["me"] = {"user_id": "1", "username": "x", "account_type": "PERSONAL"}
        assert await ig.Wizard(home, ConsoleIO(lines=[IG_TOKEN], write=lambda s: None)).run() == 1
        assert not ig.token_present()
    finally:
        srv.close()


async def test_inbox_reads_aloud_dedupes_and_ignores_own_messages(home):
    srv = instagram_server([dm("m1", text="מה השעה?"), dm("m2", sender="9001", text="my own"), dm("m1", text="מה השעה?")])
    try:
        connect(home, srv)
        reg = reg_with(ig.InstagramSkill(home))
        r = await reg.call("instagram_inbox", {})
        assert r.ok and r.code == "INSTAGRAM_INBOX" and len(r.data["messages"]) == 1
        assert "מה השעה?" in r.message and "UNTRUSTED" in r.data["rule"] and r.data["messages"][0]["can_reply"]
        again = await reg.call("instagram_inbox", {})
        assert again.code == "INSTAGRAM_EMPTY"                       # already read aloud
    finally:
        srv.close()


async def test_send_only_after_confirmation_with_exact_text(home):
    srv = instagram_server([dm("m1", text="Are you open today?")])
    try:
        connect(home, srv)
        prompts = []
        reg = reg_with(ig.InstagramSkill(home), approve=False, log=prompts)
        await reg.call("instagram_inbox", {})
        d = await reg.call("instagram_draft", {"message_id": "m1", "text": "Yes, until 6pm"})
        assert d.ok and d.code == "INSTAGRAM_DRAFT_READY" and "/me/messages" not in srv.paths()
        for origin in ("model", "user"):
            r = await reg.call("instagram_send", {"draft_id": d.data["draft_id"]}, origin=origin)
            assert not r.ok and r.code == "TOOL_DECLINED"
        assert "/me/messages" not in srv.paths()                     # declined: nothing left the PC
        assert len(prompts) == 2 and all("Yes, until 6pm" in p and "anna" in p for p in prompts)

        async def yes_(_p):
            return True
        reg.confirm = yes_
        ok = await reg.call("instagram_send", {"draft_id": d.data["draft_id"]})
        assert ok.ok and ok.code == "INSTAGRAM_SENT"
        sent = [r for r in srv.requests if r["path"] == "/me/messages"]
        assert len(sent) == 1 and sent[0]["body"] == {"recipient": {"id": "555"}, "message": {"text": "Yes, until 6pm"}}
        assert (await reg.call("instagram_send", {"draft_id": d.data["draft_id"]})).code == "INSTAGRAM_UNKNOWN_DRAFT"
        assert [e["event"] for e in audit_events(home)] == ["draft", "sent"]
        assert ig.Inbox(home).get("m1")["status"] == "replied"
    finally:
        srv.close()


async def test_edit_needs_a_new_draft_and_unknown_message_is_refused(home):
    srv = instagram_server([dm("m1")])
    try:
        connect(home, srv)
        reg = reg_with(ig.InstagramSkill(home))
        await reg.call("instagram_inbox", {})
        assert (await reg.call("instagram_draft", {"message_id": "nope", "text": "x"})).code == "INSTAGRAM_UNKNOWN_MESSAGE"
        assert (await reg.call("instagram_send", {"draft_id": "forged"})).code == "INSTAGRAM_UNKNOWN_DRAFT"
        a = await reg.call("instagram_draft", {"message_id": "m1", "text": "one"})
        b = await reg.call("instagram_draft", {"message_id": "m1", "text": "two"})
        assert a.data["draft_id"] != b.data["draft_id"]
        await reg.call("instagram_send", {"draft_id": b.data["draft_id"]})
        sent = [r["body"]["message"]["text"] for r in srv.requests if r["path"] == "/me/messages"]
        assert sent == ["two"]                                       # only the confirmed draft, never "one"
    finally:
        srv.close()


async def test_window_guard_refuses_at_draft_and_at_send(home):
    srv = instagram_server([dm("old", text="old msg", age_s=25 * 3600), dm("new", sender="777", text="fresh", age_s=24 * 3600 - 120)])
    try:
        connect(home, srv)
        now = [time.time()]
        reg = reg_with(ig.InstagramSkill(home, clock=lambda: now[0]))
        await reg.call("instagram_inbox", {})
        r = await reg.call("instagram_draft", {"message_id": "old", "text": "late reply"})
        assert not r.ok and r.code == "INSTAGRAM_WINDOW_CLOSED"
        d = await reg.call("instagram_draft", {"message_id": "new", "text": "ok"})
        assert d.ok
        now[0] += 300                                                 # the window closes before the owner says yes
        late = await reg.call("instagram_send", {"draft_id": d.data["draft_id"]})
        assert not late.ok and late.code == "INSTAGRAM_WINDOW_CLOSED" and "/me/messages" not in srv.paths()
        assert [e["event"] for e in audit_events(home)] == ["refused_window", "draft", "refused_window"]
    finally:
        srv.close()


async def test_api_side_errors_are_reported_without_secrets(home):
    srv = instagram_server([dm("m1")])
    try:
        connect(home, srv)
        reg = reg_with(ig.InstagramSkill(home))
        await reg.call("instagram_inbox", {})
        srv.state["window_closed"] = True
        d = await reg.call("instagram_draft", {"message_id": "m1", "text": "x"})
        r = await reg.call("instagram_send", {"draft_id": d.data["draft_id"]})
        assert not r.ok and r.code == "INSTAGRAM_WINDOW_CLOSED"
        assert audit_events(home)[-1]["event"] == "send_failed"
        os.environ[ig.TOKEN_ENV] = "wrong-secret-token"
        with pytest.raises(ig.InstagramError) as e:
            await ig.InstagramApi().me()
        assert e.value.code == "INSTAGRAM_BAD_TOKEN" and "wrong-secret-token" not in str(e.value) + e.value.he
    finally:
        srv.close()
    os.environ[ig.TOKEN_ENV] = IG_TOKEN
    os.environ[ig.API_BASE_ENV] = "http://127.0.0.1:9"
    with pytest.raises(ig.InstagramError) as e:
        await ig.InstagramApi(timeout=1).me()
    assert e.value.code == "INSTAGRAM_UNREACHABLE" and IG_TOKEN not in str(e.value) + e.value.he


def _http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def test_webhook_receiver_verifies_handshake_and_signature(home):
    os.environ[ig.SECRET_ENV_NAME] = IG_SECRET
    os.environ[ig.VERIFY_ENV] = "verify-me"
    ig.write_private_json(ig.account_file(home), {"user_id": "9001"})
    httpd = ig.make_server(home, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    body = json.dumps({"object": "instagram", "entry": [{"id": "9001", "messaging": [
        {"sender": {"id": "555"}, "recipient": {"id": "9001"}, "timestamp": int(time.time() * 1000),
         "message": {"mid": "w1", "text": "hello via webhook"}},
        {"sender": {"id": "9001"}, "message": {"mid": "w2", "text": "echo", "is_echo": True}}]}]}).encode()
    sig = "sha256=" + hmac.new(IG_SECRET.encode(), body, hashlib.sha256).hexdigest()
    try:
        ok = await asyncio.to_thread(_http, f"{base}/?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=12345")
        assert ok == (200, "12345")
        assert (await asyncio.to_thread(_http, f"{base}/?hub.mode=subscribe&hub.verify_token=bad&hub.challenge=1"))[0] == 403
        assert (await asyncio.to_thread(_http, base, body))[0] == 403                                  # unsigned
        assert (await asyncio.to_thread(_http, base, body, {"X-Hub-Signature-256": "sha256=" + "0" * 64}))[0] == 403
        assert ig.Inbox(home).get("w1") is None
        assert (await asyncio.to_thread(_http, base, body, {"X-Hub-Signature-256": sig}))[0] == 200
        assert (await asyncio.to_thread(_http, base, body, {"X-Hub-Signature-256": sig}))[0] == 200    # replay: deduped
        m = ig.Inbox(home).get("w1")
        assert m and m["text"] == "hello via webhook" and ig.Inbox(home).get("w2") is None
        assert len(ig.Inbox(home).new()) == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_feature_probe_off_by_default_and_registration(home):
    f = feat.Features(Config(), home)
    ok, why = f.available("instagram")
    assert not ok and "jarvis instagram start" in why and f.enabled("instagram") is False
    os.environ[ig.TOKEN_ENV] = IG_TOKEN
    f.invalidate()
    assert not f.available("instagram")[0]
    ig.write_private_json(ig.account_file(home), {"user_id": "9001"})
    f.invalidate()
    assert f.available("instagram")[0]
    reg = SkillRegistry()
    reg.gate = f.gate
    ig.register_instagram(reg, home)
    assert reg.get("instagram_send") is None and reg.get("instagram_inbox") is None     # hidden while the switch is off
    f.set("instagram", True)
    assert reg.get("instagram_send") is not None and reg.get("instagram_draft") is not None


def test_catalog_group_bundle_guide_and_secret_registration():
    from jarves import capabilities as cap
    from jarves import guides
    from jarves import setup_wizard as sw
    from jarves.config import SECRET_ENV
    sw.load_addons()
    owners = {fid for g in guides.load().values() for fid in g.features}
    assert "instagram" in feat.CATALOG and "instagram" in {i for _, _, _, ids in sw.GROUPS for i in ids}
    assert "instagram" in sw.BUNDLE and "instagram" in owners and "instagram" in sw.SAFE_OFF
    assert cap.view("instagram")["problems"] == [] and cap.view("instagram")["declared"]
    assert feat.CATALOG["instagram"].default_enabled is False
    assert not [r for r in sw.check_addons()["rows"] if r["id"] == "instagram" and not r["ok"]]
    assert {ig.TOKEN_ENV, ig.SECRET_ENV_NAME, ig.VERIFY_ENV} <= set(SECRET_ENV)


def test_cli_status_logout_and_help(home, capsys):
    import argparse

    from jarves.cli import main
    p = argparse.ArgumentParser()
    ig.add_cli(p.add_subparsers(dest="cmd"))
    cfg = Config()
    assert ig.cmd_instagram(p.parse_args(["instagram", "status"]), cfg, home) == 1
    os.environ[ig.TOKEN_ENV] = IG_TOKEN
    ig.write_private_json(ig.account_file(home), {"user_id": "9001", "username": "igal_biz"})
    assert ig.cmd_instagram(p.parse_args(["instagram", "status"]), cfg, home) == 0
    assert IG_TOKEN not in capsys.readouterr().out
    assert ig.cmd_instagram(p.parse_args(["instagram", "logout"]), cfg, home) == 0
    assert not ig.account_file(home).exists() and not ig.token_present()
    with pytest.raises(SystemExit) as e:
        main(["instagram", "--help"])
    assert e.value.code == 0
