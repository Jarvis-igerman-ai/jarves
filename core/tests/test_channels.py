"""V3 P1 channels (Issue #17): Telegram outbound, morning briefing, WhatsApp assisted handoff, Home Assistant.
Real HTTP against local fake servers (tests/channels_fakes.py), the real SkillRegistry permission policy, the real
wizards driven by a scripted ConsoleIO. Nothing leaves 127.0.0.1."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from channels_fakes import GOOD_TOKEN, HA_TOKEN, ha_server, telegram_server, weather_server
from jarves import briefing, homeassistant as ha, telegram_channel as tg, whatsapp as wa
from jarves import features as feat
from jarves.config import Config, secrets_file
from jarves.setup_wizard import ConsoleIO
from jarves.skills import NoArgs, Skill, SkillRegistry, Tool, ToolResult
from jarves.userdata import open_db

ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "HOME_ASSISTANT_TOKEN", tg.API_BASE_ENV, briefing.WEATHER_BASE_ENV)


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

    async def confirm(prompt):
        if log is not None:
            log.append(prompt)
        return approve
    reg.confirm = confirm
    return reg


# ====================================================================== Telegram
async def test_telegram_wizard_pairs_saves_token_privately_and_sends_test(home):
    srv = telegram_server()
    try:
        os.environ[tg.API_BASE_ENV] = srv.url
        out = []
        io = ConsoleIO([GOOD_TOKEN, "", "y", "y"], out.append)
        assert await tg.Wizard(home, io, poll_sleep=0).run() == 0
        assert f"TELEGRAM_BOT_TOKEN={GOOD_TOKEN}" in secrets_file(home).read_text(encoding="utf-8")
        pair = tg.pairing(home)
        assert pair["chats"] == [{"chat_id": 555, "name": "Igal"}] and pair["bot_username"] == "jarves_test_bot"
        assert any(r["path"].endswith("/sendMessage") and r["body"]["chat_id"] == 555 for r in srv.requests)
        assert GOOD_TOKEN not in "\n".join(out)                               # never printed
        assert (home / "telegram" / "pair.json").read_text(encoding="utf-8").find(GOOD_TOKEN) < 0
    finally:
        srv.close()


async def test_telegram_wizard_rejects_bad_token_and_saves_nothing(home):
    srv = telegram_server()
    try:
        os.environ[tg.API_BASE_ENV] = srv.url
        out = []
        bad = "999999999:AAH-wrong_token_for_tests_0123456789abc"
        assert await tg.Wizard(home, ConsoleIO([bad], out.append), poll_sleep=0).run() == 1
        assert not secrets_file(home).exists() and not tg.pair_file(home).exists() and not tg.token_present()
        assert await tg.Wizard(home, ConsoleIO(["not a token"], out.append)).run() == 1
        assert await tg.Wizard(home, ConsoleIO([""], out.append)).run() == 1
    finally:
        srv.close()


async def test_telegram_wizard_pairs_only_private_chats_and_needs_a_message(home):
    group = {"update_id": 2, "message": {"chat": {"id": -100, "type": "supergroup", "title": "g"},
                                         "from": {"first_name": "X"}, "text": "hi"}}
    srv = telegram_server(updates=[group])
    try:
        os.environ[tg.API_BASE_ENV] = srv.url
        io = ConsoleIO([GOOD_TOKEN, ""], lambda s: None)
        assert await tg.Wizard(home, io, polls=2, poll_sleep=0).run() == 1       # a group is never paired
        assert tg.pairing(home)["chats"] == []
        # the owner says "no, that is not me": nothing is paired
        srv.state["updates"] = [{"update_id": 3, "message": {"chat": {"id": 9, "type": "private"},
                                                            "from": {"first_name": "Stranger"}, "text": "x"}}]
        assert await tg.Wizard(home, ConsoleIO(["", "n"], lambda s: None), poll_sleep=0).run() == 1
        assert tg.pairing(home)["chats"] == []
    finally:
        srv.close()


def paired_home(home, srv):
    os.environ[tg.API_BASE_ENV] = srv.url
    os.environ["TELEGRAM_BOT_TOKEN"] = GOOD_TOKEN
    tg.write_private_json(tg.pair_file(home), {"bot_username": "b", "chats": [{"chat_id": 555, "name": "Igal"}]})


async def test_telegram_send_only_after_confirmation(home):
    srv = telegram_server()
    try:
        paired_home(home, srv)
        prompts = []
        reg = reg_with(tg.TelegramSkill(home), approve=False, log=prompts)
        reg.allowed_actions |= tg.ALLOWED_ACTIONS
        d = await reg.call("telegram_draft", {"text": "הכביסה מוכנה"})
        assert d.ok and d.code == "TELEGRAM_DRAFT_READY" and not srv.paths("POST")[1:] and "sendMessage" not in "".join(srv.paths())
        # declined by the user: nothing is sent, from the model AND from the user origin
        for origin in ("model", "user"):
            r = await reg.call("telegram_send", {"draft_id": d.data["draft_id"]}, origin=origin)
            assert not r.ok and r.code == "TOOL_DECLINED"
        assert "sendMessage" not in "".join(srv.paths()) and "הכביסה מוכנה" in prompts[0] and "Igal" in prompts[0]
        # approved: sent exactly once with the exact text to the paired chat only
        reg.confirm = lambda p: asyncio.sleep(0, result=True)
        ok = await reg.call("telegram_send", {"draft_id": d.data["draft_id"]})
        assert ok.ok and ok.code == "TELEGRAM_SENT"
        sent = [r for r in srv.requests if r["path"].endswith("/sendMessage")]
        assert len(sent) == 1 and sent[0]["body"]["chat_id"] == 555 and sent[0]["body"]["text"] == "הכביסה מוכנה"
        again = await reg.call("telegram_send", {"draft_id": d.data["draft_id"]})
        assert not again.ok and again.code == "TELEGRAM_UNKNOWN_DRAFT"          # a draft is single use
    finally:
        srv.close()


async def test_telegram_cannot_send_without_draft_or_to_unknown_chat_or_when_blocked(home):
    srv = telegram_server()
    try:
        reg = reg_with(tg.TelegramSkill(home))
        reg.allowed_actions |= tg.ALLOWED_ACTIONS
        assert (await reg.call("telegram_draft", {"text": "x"})).code == "TELEGRAM_NOT_PAIRED"
        paired_home(home, srv)
        assert (await reg.call("telegram_draft", {"text": "x", "to": "Stranger"})).code == "TELEGRAM_UNKNOWN_CHAT"
        assert (await reg.call("telegram_send", {"draft_id": "nope"})).code == "TELEGRAM_UNKNOWN_DRAFT"
        d = await reg.call("telegram_draft", {"text": "hello"})
        srv.state["blocked"] = True
        r = await reg.call("telegram_send", {"draft_id": d.data["draft_id"]})
        assert not r.ok and r.code == "TELEGRAM_BLOCKED"
    finally:
        srv.close()


async def test_telegram_errors_never_contain_the_token(home):
    os.environ["TELEGRAM_BOT_TOKEN"] = GOOD_TOKEN
    os.environ[tg.API_BASE_ENV] = "http://127.0.0.1:1"                       # nothing listens there
    with pytest.raises(tg.TelegramError) as e:
        await tg.TelegramApi(timeout=1).get_me()
    assert e.value.code == "TELEGRAM_UNREACHABLE" and GOOD_TOKEN not in str(e.value) + e.value.he
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_telegram_feature_probe_and_registration(home):
    f = feat.Features(Config(), home)
    ok, why = f.available("telegram")
    assert not ok and "jarvis telegram start" in why and f.enabled("telegram") is False       # off by default
    os.environ["TELEGRAM_BOT_TOKEN"] = GOOD_TOKEN
    f.invalidate()
    assert not f.available("telegram")[0]                                    # token but no paired chat
    tg.write_private_json(tg.pair_file(home), {"chats": [{"chat_id": 1, "name": "a"}]})
    f.invalidate()
    assert f.available("telegram")[0]
    reg = SkillRegistry()
    reg.gate = f.gate
    tg.register_telegram(reg, home)
    assert reg.get("telegram_send") is None                                  # hidden while the switch is off
    f.set("telegram", True)
    assert reg.get("telegram_send") is not None and reg.get("telegram_draft") is not None


async def test_telegram_dashboard_token_route_validates_then_saves(home):
    from types import SimpleNamespace
    srv = telegram_server()
    routes = {}
    tg.dashboard_routes(lambda m, p, h: routes.update({(m, p): h}))
    ctx = SimpleNamespace(home=home)
    try:
        os.environ[tg.API_BASE_ENV] = srv.url
        post = routes[("POST", r"/api/telegram/token")]
        st, body = await asyncio.to_thread(post, ctx, None, {"token": "junk"})
        assert st == 400 and not tg.token_present()
        st, body = await asyncio.to_thread(post, ctx, None, {"token": "999999999:AAH-wrong_token_for_tests_0123456789abc"})
        assert st == 400 and not tg.token_present()
        st, body = await asyncio.to_thread(post, ctx, None, {"token": GOOD_TOKEN})
        assert st == 200 and body["bot"] == "jarves_test_bot" and tg.token_present() and GOOD_TOKEN not in str(body)
        st, body = routes[("GET", r"/api/telegram")](ctx, None, None)
        assert st == 200 and body["token_saved"] and GOOD_TOKEN not in str(body)
    finally:
        srv.close()


def test_telegram_cli_status_and_logout(home, capsys, monkeypatch):
    import argparse

    from jarves.config import Config as C
    monkeypatch.setenv("JARVES_HOME", str(home))
    p = argparse.ArgumentParser()
    tg.add_cli(p.add_subparsers(dest="cmd"))
    cfg = C()
    assert tg.cmd_telegram(p.parse_args(["telegram", "status"]), cfg, home) == 1
    os.environ["TELEGRAM_BOT_TOKEN"] = GOOD_TOKEN
    tg.write_private_json(tg.pair_file(home), {"chats": [{"chat_id": 1, "name": "a"}]})
    assert tg.cmd_telegram(p.parse_args(["telegram", "status"]), cfg, home) == 0
    assert GOOD_TOKEN not in capsys.readouterr().out
    assert tg.cmd_telegram(p.parse_args(["telegram", "logout"]), cfg, home) == 0
    assert not tg.pair_file(home).exists() and not tg.token_present()


# ====================================================================== WhatsApp
def test_whatsapp_phone_normalisation_and_link():
    assert wa.normalize_phone("054-123-4567") == "972541234567"
    assert wa.normalize_phone("+972 54 123 4567") == "972541234567"
    assert wa.normalize_phone("00972541234567") == "972541234567"
    assert wa.normalize_phone("+1 (415) 555-0100") == "14155550100"
    assert wa.normalize_phone("abc") is None and wa.normalize_phone("12") is None
    assert wa.chat_link("972541234567", "שלום & hi?") == \
        "https://wa.me/972541234567?text=%D7%A9%D7%9C%D7%95%D7%9D%20%26%20hi%3F"


async def test_whatsapp_opens_chat_only_after_approval_and_never_sends(home):
    opened = []
    sk = wa.WhatsAppSkill(home, opener=lambda u: opened.append(u) or True)
    prompts = []
    reg = reg_with(sk, approve=False, log=prompts)
    reg.allowed_actions |= wa.ALLOWED_ACTIONS
    d = await reg.call("whatsapp_draft", {"text": "אני מאחר בעשר דקות", "name": "דנה", "phone": "054-123-4567"})
    assert d.ok and d.code == "WHATSAPP_DRAFT_READY" and opened == []         # drafting opens nothing
    r = await reg.call("whatsapp_open", {"draft_id": d.data["draft_id"]}, origin="user")
    assert not r.ok and r.code == "TOOL_DECLINED" and opened == [] and "דנה" in prompts[0]
    assert "לא שולח" in prompts[0]                                             # the read-back says he does not send
    reg.confirm = lambda p: asyncio.sleep(0, result=True)
    r = await reg.call("whatsapp_open", {"draft_id": d.data["draft_id"]})
    assert r.ok and r.code == "WHATSAPP_CHAT_OPENED" and len(opened) == 1
    assert opened[0].startswith("https://wa.me/972541234567?text=") and "%D7%90%D7%A0%D7%99" in opened[0]
    assert (await reg.call("whatsapp_open", {"draft_id": d.data["draft_id"]})).code == "WHATSAPP_UNKNOWN_DRAFT"


async def test_whatsapp_remembers_numbers_and_asks_when_unknown(home):
    opened = []
    sk = wa.WhatsAppSkill(home, opener=lambda u: opened.append(u) or True)
    reg = reg_with(sk)
    reg.allowed_actions |= wa.ALLOWED_ACTIONS
    assert (await reg.call("whatsapp_draft", {"text": "hi", "name": "דנה"})).code == "WHATSAPP_PHONE_UNKNOWN"
    assert (await reg.call("whatsapp_draft", {"text": "hi", "name": "דנה", "phone": "x1"})).code == "WHATSAPP_BAD_PHONE"
    assert (await reg.call("whatsapp_draft", {"text": "hi", "name": "דנה", "phone": "0541234567"})).ok
    d = await reg.call("whatsapp_draft", {"text": "again", "name": "דנה"})          # number remembered locally
    assert d.ok
    assert (await reg.call("whatsapp_open", {"draft_id": d.data["draft_id"]})).ok and "972541234567" in opened[0]


async def test_whatsapp_open_failure_is_reported(home):
    sk = wa.WhatsAppSkill(home, opener=lambda u: False)
    reg = reg_with(sk)
    reg.allowed_actions |= wa.ALLOWED_ACTIONS
    d = await reg.call("whatsapp_draft", {"text": "hi", "phone": "0541234567"})
    r = await reg.call("whatsapp_open", {"draft_id": d.data["draft_id"]})
    assert not r.ok and r.code == "WHATSAPP_OPEN_FAILED"


def test_whatsapp_has_no_network_or_send_code():
    src = open(wa.__file__, encoding="utf-8").read()
    for banned in ("httpx", "requests", "urllib.request", "selenium", "pyautogui", "subprocess"):
        assert banned not in src, banned


# ====================================================================== Morning briefing
class FakeGoogle(Skill):
    name = "calendar"

    def __init__(self, fail=False):
        self.fail = fail

    def tools(self):
        async def cal(_):
            if self.fail:
                return ToolResult(False, "צריך לחבר מחדש את גוגל", code="GOOGLE_RECONNECT")
            return ToolResult(True, "היום יש לך 2 אירועים ביומן.", {"count": 2, "untrusted_events": [{"t": "09:00"}]})

        async def mail(_):
            return ToolResult(True, "יש 3 מיילים.", {"count": 3, "senders_for_speech": ["דנה", "הבנק"]})
        return [Tool("calendar_list_events", "cal", cal, NoArgs, permission="read", untrusted_output=True),
                Tool("gmail_search", "mail", mail, NoArgs, permission="read", untrusted_output=True)]


class FakeConnect(Skill):
    name = "google_connect"

    def tools(self):
        async def h(_):
            return ToolResult(False, "not connected")
        return [Tool("google_connect_help", "help", h, NoArgs, permission="read")]


def add_reminder(db, name, when):
    db.x("INSERT INTO jobs (id, name, status, kind, spec, tz, action, task, created, updated, next_run) "
         "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (name, name, "active", "once", "x", "Asia/Jerusalem", "reminder", name,
                                           time.time(), time.time(), when))


async def test_briefing_full_with_google_weather_reminders_and_lists(home):
    srv = weather_server()
    try:
        os.environ[briefing.WEATHER_BASE_ENV] = srv.url
        db = open_db(home)
        add_reminder(db, "לקחת כדורים", time.time() + 60)
        add_reminder(db, "בעוד שבוע", time.time() + 7 * 86400)
        db.x("INSERT INTO notes (title, text, tags, created, updated) VALUES (?,?,?,?,?)",
             ("קניות", "חלב, לחם", "רשימה", time.time(), time.time()))
        reg = SkillRegistry()
        reg.register(FakeGoogle())
        briefing.register_briefing(reg, db)
        r = await reg.call("briefing_get", {}, origin="user")
        assert r.ok and r.code == "BRIEFING_OK" and not r.data["partial"]
        secs = {s["id"]: s for s in r.data["sections"]}
        assert secs["calendar"]["count"] == 2 and secs["email"]["count"] == 3
        assert "דנה" in secs["email"]["text"] and "כותרת" not in secs["email"]["text"]
        assert "בין 21 ל-29 מעלות" in secs["weather"]["text"] and "Tel Aviv" in secs["weather"]["text"]
        assert secs["reminders"]["reminders"] == ["לקחת כדורים"] and secs["reminders"]["lists"] == ["קניות"]
        q = [x["path"] for x in srv.requests][0]
        assert "latitude=32.0853" in q and "longitude=34.7818" in q            # only coordinates leave the PC
        db.close()
    finally:
        srv.close()


async def test_briefing_degrades_gracefully_without_google_and_without_network(home):
    os.environ[briefing.WEATHER_BASE_ENV] = "http://127.0.0.1:1"
    db = open_db(home)
    reg = SkillRegistry()
    reg.register(FakeConnect())
    briefing.register_briefing(reg, db)
    r = await reg.call("briefing_get", {}, origin="user")
    secs = {s["id"]: s for s in r.data["sections"]}
    assert r.ok and r.code == "BRIEFING_PARTIAL" and r.data["partial"]
    assert not secs["calendar"]["ok"] and "jarvis google start" in secs["calendar"]["text"]
    assert not secs["email"]["ok"] and secs["email"]["code"] == "GOOGLE_NOT_CONNECTED"
    assert secs["weather"]["code"] == "WEATHER_UNAVAILABLE"
    assert secs["reminders"]["ok"] and "אין תזכורות" in secs["reminders"]["text"]      # local part still works
    db.close()


async def test_briefing_section_failures_do_not_break_the_rest_and_calendar_error_is_reported(home):
    srv = weather_server()
    try:
        os.environ[briefing.WEATHER_BASE_ENV] = srv.url
        db = open_db(home)
        reg = SkillRegistry()
        reg.register(FakeGoogle(fail=True))
        briefing.register_briefing(reg, db)
        r = await reg.call("briefing_get", {"sections": ["calendar", "weather"]}, origin="user")
        secs = {s["id"]: s for s in r.data["sections"]}
        assert set(secs) == {"calendar", "weather"} and not secs["calendar"]["ok"] and secs["weather"]["ok"]
        assert r.ok and r.code == "BRIEFING_PARTIAL"
        db.close()
    finally:
        srv.close()


async def test_briefing_is_untrusted_so_no_side_effect_runs_in_the_same_turn(home):
    db = open_db(home)
    reg = SkillRegistry(allowed_actions={"louder"})
    reg.register(FakeGoogle())

    class Player(Skill):
        name = "player"

        def tools(self):
            async def louder(_):
                return ToolResult(True, "louder")
            return [Tool("louder", "x", louder, NoArgs, permission="action")]
    reg.register(Player())
    os.environ[briefing.WEATHER_BASE_ENV] = "http://127.0.0.1:1"
    briefing.register_briefing(reg, db)
    await reg.call("briefing_get", {}, origin="user")
    assert (await reg.call("louder", {}, origin="model")).code == "TOOL_UNTRUSTED_TURN"
    db.close()


# ====================================================================== Home Assistant
def ha_home(home, srv, risky_extra=None):
    ents = {"light.living": {"name": "Living room light"}, "switch.heater": {"name": "Heater", **(risky_extra or {})},
            "lock.front_door": {"name": "Front door"}}
    ha.write_private_json(ha.config_file(home), {"url": srv.url, "entities": ents})
    os.environ["HOME_ASSISTANT_TOKEN"] = HA_TOKEN


def services(srv):
    return [(r["path"], r["body"]) for r in srv.requests if r["method"] == "POST"]


async def test_home_wizard_picks_allow_list_saves_token_and_flags_risky(home):
    srv = ha_server()
    try:
        out = []
        # url, token, pick devices 1-3 (light, lock, switch sorted by id), mark #1 (light.living) as always ask
        io = ConsoleIO([srv.url, HA_TOKEN, "1-3", "1"], out.append)
        assert await ha.Wizard(home, io).run() == 0
        cfg = ha.load_config(home)
        assert cfg["url"] == srv.url and set(cfg["entities"]) == {"light.living", "light.secret", "lock.front_door"}
        assert cfg["entities"]["light.living"]["risky"] is True
        assert f"HOME_ASSISTANT_TOKEN={HA_TOKEN}" in secrets_file(home).read_text(encoding="utf-8")
        assert HA_TOKEN not in "\n".join(out) and HA_TOKEN not in ha.config_file(home).read_text(encoding="utf-8")
        assert "sensor.temp" not in cfg["entities"]                           # sensors are not controllable devices
    finally:
        srv.close()


async def test_home_wizard_bad_token_bad_url_and_nothing_chosen(home):
    srv = ha_server()
    try:
        assert await ha.Wizard(home, ConsoleIO([srv.url, "wrong"], lambda s: None)).run() == 1
        assert await ha.Wizard(home, ConsoleIO(["ftp://x", HA_TOKEN], lambda s: None)).run() == 1
        assert await ha.Wizard(home, ConsoleIO([srv.url, HA_TOKEN, ""], lambda s: None)).run() == 1
        assert await ha.Wizard(home, ConsoleIO([srv.url, HA_TOKEN, "99"], lambda s: None)).run() == 1
        assert not ha.config_file(home).exists() and not secrets_file(home).exists()
    finally:
        srv.close()


def test_home_parse_picks():
    assert ha.parse_picks("1,3,5-7", 9) == [0, 2, 4, 5, 6]
    assert ha.parse_picks("", 3) == [] and ha.parse_picks("2 2", 3) == [1]
    for bad in ("0", "4", "a", "3-1"):
        with pytest.raises(ValueError):
            ha.parse_picks(bad, 3)


async def test_home_allow_list_and_normal_control(home):
    srv = ha_server()
    try:
        ha_home(home, srv)
        reg = reg_with(ha.HomeSkill(home))
        reg.allowed_actions |= ha.ALLOWED_ACTIONS
        lst = await reg.call("home_list", {})
        assert lst.ok and {d["entity"] for d in lst.data["devices"]} == {"light.living", "switch.heater", "lock.front_door"}
        assert {d["entity"]: d["needs_confirmation"] for d in lst.data["devices"]}["lock.front_door"] is True
        r = await reg.call("home_control", {"entity": "Living room light", "action": "on", "value": 40})
        assert r.ok and r.code == "HOME_DONE"
        assert services(srv) == [("/api/services/light/turn_on", {"entity_id": "light.living", "brightness_pct": 40})]
        # not on the allow-list: refused before ANY request is made
        before = len(srv.requests)
        bad = await reg.call("home_control", {"entity": "light.secret", "action": "on"})
        assert not bad.ok and bad.code == "HOME_NOT_ALLOWED" and len(srv.requests) == before
        assert (await reg.call("home_state", {"entity": "light.secret"})).code == "HOME_NOT_ALLOWED"
        assert (await reg.call("home_control", {"entity": "light.living", "action": "explode"})).code == "HOME_BAD_ACTION"
        assert (await reg.call("home_state", {"entity": "light.living"})).data["state"] == "off"
    finally:
        srv.close()


async def test_home_risky_entities_always_need_confirmation(home):
    srv = ha_server()
    try:
        ha_home(home, srv, risky_extra={"risky": True})                       # heater flagged by the owner
        prompts = []
        reg = reg_with(ha.HomeSkill(home), approve=False, log=prompts)
        reg.allowed_actions |= ha.ALLOWED_ACTIONS
        for ent, act in (("lock.front_door", "unlock"), ("switch.heater", "on")):
            r = await reg.call("home_control", {"entity": ent, "action": act})
            assert not r.ok and r.code == "HOME_NEEDS_CONFIRM"                  # the normal tool refuses them
        assert services(srv) == []
        for origin in ("model", "user"):                                         # declined: nothing is sent
            r = await reg.call("home_control_risky", {"entity": "lock.front_door", "action": "unlock"}, origin=origin)
            assert not r.ok and r.code == "TOOL_DECLINED"
        assert services(srv) == [] and "unlock" in prompts[0] and "Front door" in prompts[0]
        reg.confirm = lambda p: asyncio.sleep(0, result=True)
        r = await reg.call("home_control_risky", {"entity": "lock.front_door", "action": "unlock"})
        assert r.ok and services(srv) == [("/api/services/lock/unlock", {"entity_id": "lock.front_door"})]
        # the confirmed tool is not a back door for ordinary devices
        assert (await reg.call("home_control_risky", {"entity": "light.living", "action": "on"})).code == "HOME_NOT_RISKY"
    finally:
        srv.close()


async def test_home_bad_token_and_unreachable_are_reported_without_leaking_the_token(home):
    srv = ha_server()
    try:
        ha_home(home, srv)
        os.environ["HOME_ASSISTANT_TOKEN"] = "wrong-token"
        reg = reg_with(ha.HomeSkill(home))
        r = await reg.call("home_list", {})
        assert not r.ok and r.code == "HOME_BAD_TOKEN" and "wrong-token" not in r.to_model()
    finally:
        srv.close()
    os.environ["HOME_ASSISTANT_TOKEN"] = HA_TOKEN
    r = await reg.call("home_list", {})
    assert r.code == "HOME_UNREACHABLE" and HA_TOKEN not in r.to_model()


def test_home_feature_default_off_probe_and_gate(home):
    f = feat.Features(Config(), home)
    ok, why = f.available("home_assistant")
    assert not ok and "jarvis home start" in why and not f.enabled("home_assistant")
    srv = ha_server()
    try:
        ha_home(home, srv)
        f.invalidate()
        assert f.available("home_assistant")[0]
        reg = SkillRegistry()
        reg.gate = f.gate
        ha.register_home(reg, home)
        assert reg.get("home_control") is None
        f.set("home_assistant", True)
        assert reg.get("home_control") is not None and reg.get("home_control_risky") is not None
    finally:
        srv.close()


# ====================================================================== catalog invariants for the four add-ons
NEW = ("telegram", "whatsapp_handoff", "morning_briefing", "home_assistant")


def test_new_features_are_registered_grouped_bundled_declared_and_guided():
    from jarves import capabilities as cap
    from jarves import guides
    from jarves import setup_wizard as sw
    sw.load_addons()
    grouped = {i for _, _, _, ids in sw.GROUPS for i in ids}
    owners = {fid for g in guides.load().values() for fid in g.features}
    for fid in NEW:
        assert fid in feat.CATALOG and fid in grouped and fid in sw.BUNDLE and fid in owners, fid
        assert cap.view(fid)["problems"] == [] and cap.view(fid)["declared"], fid
    assert {"telegram", "home_assistant"} <= sw.SAFE_OFF
    assert feat.CATALOG["telegram"].default_enabled is False and feat.CATALOG["home_assistant"].default_enabled is False
    chk = sw.check_addons()
    bad = [r for r in chk["rows"] if r["id"] in NEW and not r["ok"]]
    assert not bad, bad


def test_cli_registers_the_wizards():
    from jarves.cli import main
    for cmd in ("telegram", "home"):
        with pytest.raises(SystemExit) as e:
            main([cmd, "--help"])
        assert e.value.code == 0
