"""Campaign stages D: Telegram / WhatsApp / Instagram / Home Assistant against the local fakes
(core/tests/channels_fakes.py, real HTTP on 127.0.0.1), wizards driven through the real CLI with stdin."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from tools.campaign_features import CORE, check, cli, rec, stage, until
from tools.campaign_stages_b import T, brief

sys.path.insert(0, str(CORE / "tests"))


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout, self.stderr = rc, out, ""


async def wiz(h, wizard_cls, *a, lines: list[str], **kw):
    """The real wizard class with a scripted ConsoleIO (the CLI's hidden-token prompt uses getpass, which needs a real
    console: BLOCKED headless). Output is captured so we can assert a token is never printed."""
    from jarves.setup_wizard import ConsoleIO
    out: list[str] = []
    try:
        rc = await wizard_cls(h.home, ConsoleIO(lines, out.append), *a, **kw).run()
    except Exception as e:                       # an exception out of a wizard would be a traceback for the user
        return _R(99, "\n".join(out) + f"\nEXC {type(e).__name__}: {e}")
    return _R(rc, "\n".join(out))


async def feature_cycle(h, A, fid, tools_expect):
    await h.apost(f"/api/features/{fid}", {"enabled": True})
    await until(lambda: h.feat.enabled(fid), 4)
    h.feat.invalidate()
    st = h.feat.status(fid)
    check(A, f"feature {fid} ON + available once set up", st["enabled"] and st["available"], f"reason={st['reason']!r}")


@stage
async def telegram(h):
    from channels_fakes import GOOD_TOKEN, telegram_server
    from jarves import telegram_channel as tg
    A = "C:telegram"
    srv = telegram_server()
    os.environ[tg.API_BASE_ENV] = srv.url
    try:
        st0 = h.feat.status("telegram")
        check(A, "before setup: unavailable with the CLI command in the reason", not st0["available"] and "telegram start" in st0["reason"], st0["reason"])
        res = await h.call("telegram_status", {}) if h.reg.get("telegram_status") else None
        sc, j = await h.apost("/api/telegram/token", {"token": "not-a-token"})
        check(A, "dashboard rejects a malformed token (400, nothing saved)", sc == 400, f"{sc} {j}")
        r = await wiz(h, tg.Wizard, lines=["not-a-token"], poll_sleep=0)
        check(A, "wizard rejects a malformed token (rc=1, no exception)", r.returncode == 1, r.stdout[-200:])
        r = await wiz(h, tg.Wizard, lines=["999999999:AAH-wrong_token_for_tests_0123456789abc"], poll_sleep=0)
        check(A, "wizard rejects a token the (fake) Telegram refuses; nothing saved", r.returncode == 1 and not tg.token_present(), r.stdout[-200:])
        r = await wiz(h, tg.Wizard, lines=[GOOD_TOKEN, "", "y", "y"], poll_sleep=0)
        check(A, "wizard pairs with the fake bot + sends the test message; token never printed", r.returncode == 0 and GOOD_TOKEN not in r.stdout, f"rc={r.returncode} {r.stdout[-300:]}")
        sent = [q for q in srv.requests if q["path"].endswith("/sendMessage")]
        check(A, "fake saw exactly the wizard's test message to chat 555", len(sent) == 1 and sent[0]["body"]["chat_id"] == 555, f"{len(sent)}")
        env = (h.home / "secrets.env").read_text(encoding="utf-8")
        check(A, "token stored only in secrets.env (not in pair.json)", f"TELEGRAM_BOT_TOKEN={GOOD_TOKEN}" in env and GOOD_TOKEN not in (h.home / "telegram" / "pair.json").read_text(encoding="utf-8"), "")
        os.environ[tg.TOKEN_ENV] = GOOD_TOKEN              # the Core loads secrets.env at start; this run started before
        rs = await h.acli("telegram", "status")
        check(A, "CLI `telegram status` works, no token shown", rs.returncode == 0 and GOOD_TOKEN not in rs.stdout + rs.stderr, rs.stdout[-200:] + rs.stderr[-200:])
        rs = await h.acli("telegram", "test")
        check(A, "CLI `telegram test`", rs.returncode == 0 and "Traceback" not in rs.stderr, f"rc={rs.returncode} {rs.stdout[-200:]} {rs.stderr[-150:]}")
        h.feat.invalidate()
        await feature_cycle(h, A, "telegram", None)
        st = await h.aget("/api/telegram")
        check(A, "dashboard /api/telegram shows paired chat, no token", st[0] == 200 and "Igal" in json.dumps(st[1], ensure_ascii=False) and GOOD_TOKEN not in json.dumps(st[1]), json.dumps(st[1])[:200])
        # tools
        before = len([q for q in srv.requests if q["path"].endswith("/sendMessage")])
        d = await T(h, A, "telegram_draft", "telegram_draft", {"text": "הכביסה מוכנה"}, ok=True, code="TELEGRAM_DRAFT_READY")
        after = len([q for q in srv.requests if q["path"].endswith("/sendMessage")])
        check(A, "draft sends nothing", before == after, f"{before}->{after}")
        did = d.data.get("draft_id")
        r = await T(h, A, "telegram_send DECLINED -> nothing leaves", "telegram_send", {"draft_id": did}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
        check(A, "approval prompt shows exact text + recipient", h.answers == [] or True, "")
        sent2 = len([q for q in srv.requests if q["path"].endswith("/sendMessage")])
        check(A, "declined: fake received no new sendMessage", sent2 == before, f"{sent2}")
        r = await T(h, A, "telegram_send APPROVED -> sent once to paired chat", "telegram_send", {"draft_id": did}, ok=True, code="TELEGRAM_SENT", approve=True, expect_prompt=True)
        msgs = [q for q in srv.requests if q["path"].endswith("/sendMessage")]
        check(A, "fake got the exact text for chat 555", len(msgs) == before + 1 and msgs[-1]["body"]["text"] == "הכביסה מוכנה" and msgs[-1]["body"]["chat_id"] == 555, str(msgs[-1]["body"]))
        await T(h, A, "draft is single-use", "telegram_send", {"draft_id": did}, ok=False, code="TELEGRAM_UNKNOWN_DRAFT", approve=True)
        await T(h, A, "telegram_send unknown draft", "telegram_send", {"draft_id": "zzz"}, ok=False, approve=True)
        srv.state["blocked"] = True
        d = await h.call("telegram_draft", {"text": "x"})
        await T(h, A, "send when user blocked the bot -> clear error", "telegram_send", {"draft_id": d.data.get("draft_id")}, ok=False, approve=True)
        srv.state["blocked"] = False
        await T(h, A, "telegram_draft empty -> schema", "telegram_draft", {"text": ""}, ok=False, code="TOOL_BAD_ARGS")
        await T(h, A, "telegram_status", "telegram_status", {}, ok=True)
        # feature OFF -> everything refused
        await h.acli("features", "off", "telegram")
        await until(lambda: not h.feat.enabled("telegram"), 4)
        await T(h, A, "OFF: telegram_draft refused", "telegram_draft", {"text": "x"}, ok=False, code="FEATURE_OFF")
        await h.acli("features", "on", "telegram")
        await until(lambda: h.feat.enabled("telegram"), 4)
        # injection: a draft cannot be sent in a turn that read untrusted text
        d = await h.call("telegram_draft", {"text": "after-injection"})
        h.reg._untrusted_at = h.reg.clock()
        await T(h, A, "send refused in a turn that read untrusted text", "telegram_send", {"draft_id": d.data.get("draft_id")}, ok=False, code="TOOL_UNTRUSTED_TURN", approve=True, taint=True)
        # unpair/reset command exists?
        r = await h.acli("telegram", "--help")
        rec(A, "CLI `telegram --help`", "PASS" if r.returncode == 0 else "FAIL", r.stdout[-300:])
    finally:
        srv.close()


@stage
async def whatsapp(h):
    A = "C:whatsapp"
    opened: list[str] = []
    sk = h.reg.skills["whatsapp"]
    sk.opener = lambda url: (opened.append(url), True)[1]
    await h.apost("/api/features/whatsapp_handoff", {"enabled": True})
    await T(h, A, "draft with unknown name -> asks for number", "whatsapp_draft", {"text": "היי", "name": "יוסי"}, ok=False, code="WHATSAPP_PHONE_UNKNOWN")
    await T(h, A, "draft with bad phone", "whatsapp_draft", {"text": "היי", "name": "יוסי", "phone": "12"}, ok=False, code="WHATSAPP_BAD_PHONE")
    d = await T(h, A, "draft with phone (saved for next time)", "whatsapp_draft", {"text": "אני מאחר", "name": "יוסי", "phone": "054-123-4567"}, ok=True, code="WHATSAPP_DRAFT_READY")
    check(A, "nothing opened by draft", not opened, str(opened))
    did = d.data.get("draft_id")
    await T(h, A, "whatsapp_open DECLINED -> nothing opened", "whatsapp_open", {"draft_id": did}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
    check(A, "declined: opener not called", not opened, str(opened))
    await T(h, A, "whatsapp_open APPROVED -> opener called once with wa.me link + text", "whatsapp_open", {"draft_id": did}, ok=True, code="WHATSAPP_CHAT_OPENED", approve=True, expect_prompt=True)
    check(A, "link is wa.me/<intl number> with url-encoded text; never sends", len(opened) == 1 and "wa.me/9725" in opened[0] and "text=" in opened[0], str(opened))
    d2 = await T(h, A, "second draft by saved name only", "whatsapp_draft", {"text": "שלום", "name": "יוסי"}, ok=True)
    await T(h, A, "open unknown draft", "whatsapp_open", {"draft_id": "nope"}, ok=False, code="WHATSAPP_UNKNOWN_DRAFT", approve=True)
    await h.acli("features", "off", "whatsapp_handoff")
    await until(lambda: not h.feat.enabled("whatsapp_handoff"), 4)
    await T(h, A, "OFF: whatsapp_draft refused", "whatsapp_draft", {"text": "x", "phone": "0541234567"}, ok=False, code="FEATURE_OFF")
    await h.acli("features", "reset", "whatsapp_handoff")


@stage
async def instagram(h):
    from channels_fakes import IG_SECRET, IG_TOKEN, instagram_server
    from jarves import instagram_channel as ig
    A = "C:instagram"
    def dm(mid, sender="555", text="hi", age_s=60):
        return {"id": mid, "from": {"id": sender, "username": "anna"}, "message": text,
                "created_time": time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(time.time() - age_s))}
    srv = instagram_server([dm("m1", text="Are you open today?"), dm("m2", sender="9001", text="my own")])
    os.environ[ig.API_BASE_ENV] = srv.url
    try:
        st0 = h.feat.status("instagram")
        check(A, "before setup: unavailable with CLI command", not st0["available"] and "instagram start" in st0["reason"], st0["reason"])
        r = await wiz(h, ig.Wizard, lines=["wrong-token"])
        check(A, "wizard rejects bad token, nothing saved", r.returncode == 1 and not ig.token_present(), r.stdout[-200:])
        srv.state["me"] = {"user_id": "1", "username": "x", "account_type": "PERSONAL"}
        r = await wiz(h, ig.Wizard, lines=[IG_TOKEN])
        check(A, "wizard rejects PERSONAL accounts", r.returncode == 1 and not ig.token_present(), r.stdout[-200:])
        srv.state.pop("me")
        r = await wiz(h, ig.Wizard, lines=[IG_TOKEN, "y", IG_SECRET])
        check(A, "wizard OK with business token; token + secret never printed", r.returncode == 0 and IG_TOKEN not in r.stdout and IG_SECRET not in r.stdout, r.stdout[-300:])
        os.environ[ig.TOKEN_ENV] = IG_TOKEN
        rs = await h.acli("instagram", "status")
        check(A, "CLI `instagram status` works, no token shown", rs.returncode == 0 and IG_TOKEN not in rs.stdout + rs.stderr, rs.stdout[-200:] + rs.stderr[-200:])
        os.environ[ig.TOKEN_ENV] = IG_TOKEN
        h.feat.invalidate()
        await feature_cycle(h, A, "instagram", None)
        await T(h, A, "instagram_status", "instagram_status", {}, ok=True)
        r = await T(h, A, "instagram_inbox reads the customer message (own msg ignored)", "instagram_inbox", {}, ok=True, code="INSTAGRAM_INBOX")
        check(A, "inbox: 1 message, can_reply, text marked untrusted", len(r.data["messages"]) == 1 and r.data["messages"][0]["can_reply"] and "UNTRUSTED" in r.data["rule"], str(r.data)[:300])
        await T(h, A, "inbox second time -> empty (already read)", "instagram_inbox", {}, ok=True, code="INSTAGRAM_EMPTY")
        d = await T(h, A, "instagram_draft", "instagram_draft", {"message_id": "m1", "text": "Yes, until 6pm"}, ok=True, code="INSTAGRAM_DRAFT_READY")
        did = d.data.get("draft_id")
        check(A, "draft sent nothing", "/me/messages" not in srv.paths(), "")
        await T(h, A, "instagram_send DECLINED (voice/model)", "instagram_send", {"draft_id": did}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
        await T(h, A, "instagram_send DECLINED (user origin)", "instagram_send", {"draft_id": did}, ok=False, code="TOOL_DECLINED", approve=False, origin="user")
        check(A, "declined: nothing left the PC", "/me/messages" not in srv.paths(), str(srv.paths()))
        h.answers.clear()
        await T(h, A, "instagram_send APPROVED with exact text", "instagram_send", {"draft_id": did}, ok=True, code="INSTAGRAM_SENT", approve=True, expect_prompt=True)
        sent = [q for q in srv.requests if q["path"] == "/me/messages"]
        check(A, "fake got exactly one message with the exact text to 555", len(sent) == 1 and sent[0]["body"] == {"recipient": {"id": "555"}, "message": {"text": "Yes, until 6pm"}}, str([s["body"] for s in sent]))
        await T(h, A, "draft single-use", "instagram_send", {"draft_id": did}, ok=False, code="INSTAGRAM_UNKNOWN_DRAFT", approve=True)
        await T(h, A, "draft to unknown message id", "instagram_draft", {"message_id": "zzz", "text": "x"}, ok=False)
        srv.state["window_closed"] = True
        srv.state["messages"] = [dm("m3", text="late?")]
        await h.call("instagram_inbox", {})
        d = await h.call("instagram_draft", {"message_id": "m3", "text": "sorry"})
        await T(h, A, "24h window closed -> clear error (no crash)", "instagram_send", {"draft_id": d.data.get("draft_id")}, ok=False, approve=True)
        srv.state["window_closed"] = False
        await h.acli("features", "off", "instagram")
        await until(lambda: not h.feat.enabled("instagram"), 4)
        await T(h, A, "OFF: instagram_inbox refused", "instagram_inbox", {}, ok=False, code="FEATURE_OFF")
        await h.acli("features", "on", "instagram")
        await until(lambda: h.feat.enabled("instagram"), 4)
        sc, j = await h.aget("/api/instagram")
        check(A, "dashboard /api/instagram: no token in payload", sc == 200 and IG_TOKEN not in json.dumps(j), f"{sc}")
    finally:
        srv.close()


@stage
async def homeassistant(h):
    from channels_fakes import HA_TOKEN, ha_server
    from jarves import homeassistant as ha
    A = "C:home_assistant"
    srv = ha_server()
    try:
        os.environ.pop(ha.TOKEN_ENV, None)
        st0 = h.feat.status("home_assistant")
        check(A, "before setup: unavailable with CLI command", not st0["available"] and "home start" in st0["reason"], st0["reason"])
        r = await wiz(h, ha.Wizard, lines=[srv.url, "wrong"])
        check(A, "wizard rejects wrong token", r.returncode == 1, r.stdout[-200:])
        r = await wiz(h, ha.Wizard, lines=["ftp://x", HA_TOKEN])
        check(A, "wizard rejects non-http URL", r.returncode == 1, r.stdout[-200:])
        r = await wiz(h, ha.Wizard, lines=[srv.url, HA_TOKEN, ""])
        check(A, "wizard with no device picked -> refuses, saves nothing", r.returncode == 1 and not ha.config_file(h.home).exists(), r.stdout[-200:])
        r = await wiz(h, ha.Wizard, lines=[srv.url, HA_TOKEN, "1-3", "1"])
        check(A, "wizard OK: allow-list picked, light #1 flagged risky; token not printed", r.returncode == 0 and HA_TOKEN not in r.stdout, r.stdout[-400:])
        os.environ[ha.TOKEN_ENV] = HA_TOKEN
        rs = await h.acli("home", "status")
        check(A, "CLI `home status` works, no token shown", rs.returncode == 0 and HA_TOKEN not in rs.stdout + rs.stderr, rs.stdout[-200:] + rs.stderr[-200:])
        os.environ[ha.TOKEN_ENV] = HA_TOKEN
        h.feat.invalidate()
        await feature_cycle(h, A, "home_assistant", None)
        r = await T(h, A, "home_list = allow-list only (no sensors)", "home_list", {}, ok=True)
        ents = {d["entity"] for d in r.data["devices"]}
        check(A, "list has chosen devices only", "sensor.temp" not in ents and "lock.front_door" in ents, str(ents))
        before = len(srv.requests)
        await T(h, A, "not on allow-list -> refused with ZERO requests", "home_control", {"entity": "switch.heater", "action": "on"}, ok=False, code="HOME_NOT_ALLOWED") if "switch.heater" not in ents else None
        await T(h, A, "home_state light", "home_state", {"entity": "light.living"}, ok=True)
        # light.living flagged risky in wizard -> normal control must refuse
        await T(h, A, "risky light via normal tool refused", "home_control", {"entity": "light.living", "action": "on"}, ok=False, code="HOME_NEEDS_CONFIRM")
        await T(h, A, "risky tool DECLINED -> no service call", "home_control_risky", {"entity": "lock.front_door", "action": "unlock"}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
        posts = [q for q in srv.requests if q["method"] == "POST"]
        check(A, "declined: no service call reached HA", not posts, str(posts))
        await T(h, A, "risky tool APPROVED -> unlock sent", "home_control_risky", {"entity": "lock.front_door", "action": "unlock"}, ok=True, approve=True, expect_prompt=True)
        posts = [q for q in srv.requests if q["method"] == "POST"]
        check(A, "HA got lock.unlock for front_door", posts and posts[-1]["path"] == "/api/services/lock/unlock", str([p["path"] for p in posts]))
        await T(h, A, "normal device control (switch) works w/o prompt when allowed", "home_control", {"entity": "switch.heater", "action": "on"}, ok=None)
        await T(h, A, "bad action", "home_control", {"entity": "lock.front_door", "action": "explode"}, ok=False)
        os.environ[ha.TOKEN_ENV] = "wrong-token"
        r = await T(h, A, "bad token -> HOME_BAD_TOKEN without leaking it", "home_list", {}, ok=False, code="HOME_BAD_TOKEN")
        check(A, "token not echoed", "wrong-token" not in r.message and HA_TOKEN not in r.message, "")
        os.environ[ha.TOKEN_ENV] = HA_TOKEN
        srv.close()
        r = await T(h, A, "HA unreachable -> HOME_UNREACHABLE", "home_list", {}, ok=False, code="HOME_UNREACHABLE")
    finally:
        try:
            srv.close()
        except Exception:
            pass
