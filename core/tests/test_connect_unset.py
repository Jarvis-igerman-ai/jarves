"""`jarvis connect` lists only what is NOT set up, remembers skips, tests the connected ones read-only.

Everything is local: httpx.MockTransport fakes for the checks, injected runners for the interactive steps, a fake
browser/clipboard; nothing opens a browser or touches the network."""
from __future__ import annotations

import argparse
import json
import os
import threading
import time

import httpx
import pytest

from channels_fakes import GOOD_TOKEN, FakeClipboard
from jarves import clilang, connect, connect_checks as cc, dashboard
from jarves import instagram_channel as ig
from jarves import telegram_channel as tg
from jarves.config import Config
from jarves.setup_wizard import ConsoleIO

from test_connect import cfg_for, make, no_sleep  # noqa: F401
from test_dashboard import dash, get, req  # noqa: F401  (fixture + helpers)

KEYS = ("TELEGRAM_BOT_TOKEN", tg.API_BASE_ENV, ig.API_BASE_ENV, ig.TOKEN_ENV, "HOME_ASSISTANT_TOKEN",
        "OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "JARVES_CLI_LANG")


@pytest.fixture(autouse=True)
def clean():
    old = {k: os.environ.get(k) for k in KEYS}
    for k in KEYS:
        os.environ.pop(k, None)
    cc.clear_cache()
    yield
    cc.clear_cache()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def pair_telegram(home):
    os.environ["TELEGRAM_BOT_TOKEN"] = GOOD_TOKEN
    tg.write_private_json(tg.pair_file(home), {"bot_username": "jarves_test_bot", "chats": [{"chat_id": 1, "name": "a"}]})


def mock(status=200, body=None, boom=False):
    seen = []

    def handler(req_: httpx.Request) -> httpx.Response:
        seen.append((req_.method, str(req_.url)))
        if boom:
            raise httpx.ConnectError("down")
        return httpx.Response(status, json=body if body is not None else {})
    t = httpx.MockTransport(handler)
    t.seen = seen
    return t


# ====================================================================== unset only, skips remembered
def test_default_run_walks_only_unset_and_skips_are_remembered_forever(home):
    pair_telegram(home)
    conn, out = make(home, ["s"] * 20, check_first=False)
    assert conn.run() == 0
    assert "telegram" not in conn.results and set(conn.results) == set(connect.ONLY) - {"telegram"}
    assert all(v == "skipped" for v in conn.results.values())
    assert set(connect.never_list(home)) == set(connect.ONLY) - {"telegram"}
    conn2, out2 = make(home, [], check_first=False)                 # no input at all: nothing may be asked
    assert conn2.run() == 0 and conn2.results == {}
    assert any("Nothing left to set up" in line for line in out2)
    assert not any("==" in line for line in out2)                   # no integration was even mentioned


def test_nothing_left_line_is_hebrew_when_the_ui_is_hebrew(home, monkeypatch):
    connect.set_never(home, list(connect.ONLY))
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    conn, out = make(home, [], check_first=False)
    assert conn.run() == 0
    assert any("אין מה להגדיר" in line for line in out) and clilang.current() == "he"


def test_all_shows_the_three_states_and_changes_nothing(home, capsys):
    pair_telegram(home)
    connect.set_never(home, ["wake"])
    before = sorted(p.name for p in home.rglob("*") if "logs" not in p.parts)
    conn, out = make(home, [], show_all=True)
    assert conn.run() == 0
    text = "\n".join(out)
    row = {w: next(line for line in out if w in line) for w in ("Telegram", "Wake word", "Instagram")}
    assert "connected" in row["Telegram"] and "skipped" in row["Wake word"] and "not set" in row["Instagram"]
    assert "OpenAI API key" in text and "ChatGPT plan" in text
    assert sorted(p.name for p in home.rglob("*") if "logs" not in p.parts) == before
    args = argparse.Namespace(only=None, name=None, status=False, all=True, json=True, yes=False, reminders=None)
    assert connect.cmd_connect(args, cfg_for(), home) == 0
    data = {i["id"]: i["status"] for i in json.loads(capsys.readouterr().out)["items"]}
    assert data["telegram"] == "connected" and data["wake"] == "skipped" and data["instagram"] == "not_set"


def test_named_integration_always_runs_and_unskips(home):
    connect.set_never(home, ["wake", "home"])
    calls = []
    conn, _ = make(home, ["y"], only="wake", wake_runner=lambda: calls.append(1) or 0, check_first=False)
    assert conn.run() == 0 and calls == [1] and conn.results == {"wake": "connected"}
    assert connect.never_list(home) == ["home"] and connect.state_map(home)["wake"] == "connected"
    assert connect.state_map(home)["home"] == "skipped"


def test_reset_skipped_clears_the_list(home):
    connect.set_never(home, ["wake", "home"])
    conn, out = make(home, [], reset_skips=True, check_first=False, show_all=True)
    assert conn.run() == 0 and connect.never_list(home) == [] and connect.state_map(home) == {}
    args = argparse.Namespace(only=None, name=None, status=False, all=True, json=False, yes=True, reminders=None,
                              reset_skips=True, check=False, no_check=True)
    connect.set_never(home, ["wake"])
    assert connect.cmd_connect(args, cfg_for(), home) == 0 and connect.never_list(home) == []


def test_a_mid_step_skip_word_is_remembered(home):
    conn, _ = make(home, ["", "s"], only="instagram", check_first=False,
                   optional_runners={"instagram": lambda: 0})
    # gate answered Enter, then the step asks nothing (runner returns 0): connected, not skipped
    assert conn.run() == 0 and conn.results == {"instagram": "connected"}
    conn2, _ = make(home, ["s"], only="home", check_first=False)
    conn2.run()
    assert "home" in connect.never_list(home)


def test_unknown_name_is_refused_and_cli_flags_exist(capsys):
    args = argparse.Namespace(only=None, name="nope", status=False, all=False, json=False, yes=False, reminders=None)
    assert connect.cmd_connect(args, cfg_for(), __import__("pathlib").Path(os.getcwd())) == 2
    from jarves import cli
    with pytest.raises(SystemExit):
        cli.main(["connect", "--help"])
    out = capsys.readouterr().out
    for flag in ("--all", "--check", "--reset-skipped", "--only", "item"):
        assert flag in out


# ====================================================================== the registry
def test_other_modules_can_register_an_integration(home):
    seen = []
    connect.register(connect.Integration(
        "zz_phone", "גישה מהטלפון", "Phone access", True,
        status=lambda it, h, c: setattr(it, "state", "connected") if (h / "phone.ok").exists() else None,
        step=lambda conn, it: (seen.append(it.id), conn.results.__setitem__("zz_phone", "connected")),
        check=lambda c: cc.CheckResult(cc.WORKING, "tailscale up")))
    try:
        assert "zz_phone" in connect.ONLY
        assert [i.id for i in connect.status_items(home, cfg_for()) if i.id == "zz_phone"]
        conn, _ = make(home, [""] * 20, only="zz_phone", check_first=False)
        assert conn.run() == 0 and seen == ["zz_phone"]
        (home / "phone.ok").write_text("x")
        assert cc.run_checks(cc.CheckCtx(home, cfg_for()), ["zz_phone"], fresh=True)["zz_phone"].state == cc.WORKING
        conn2, out = make(home, [], show_all=True)
        conn2.run()
        assert any("Phone access" in line and "connected" in line for line in out)
    finally:
        for d in (connect.TITLES, connect.EXTRA_STATUS, connect.EXTRA_STEPS, cc.CHECKS):
            d.pop("zz_phone", None)
        connect.ONLY.remove("zz_phone")


# ====================================================================== the read-only checks
def test_openai_key_working_broken_unreachable_and_never_posts(home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ok, bad, off = mock(200, {"data": []}), mock(401), mock(boom=True)
    c = lambda t: cc.CheckCtx(home, cfg_for(), transports={"openai": t})            # noqa: E731
    assert cc.check_openai(c(ok)).state == cc.WORKING and ok.seen == [("GET", "https://api.openai.com/v1/models")]
    r = cc.check_openai(c(bad))
    assert r.state == cc.BROKEN and r.fix == "jarvis key"
    assert cc.check_openai(c(off)).state == cc.UNREACHABLE
    monkeypatch.delenv("OPENAI_API_KEY")
    assert cc.check_openai(c(ok)).state == cc.BROKEN


def test_chatgpt_valid_token_is_not_refreshed_and_signed_out_is_broken(home):
    from jarves.chatgpt_auth import PLAN_SCOPE, ChatGPTAuth
    trap = mock(500)
    c = cc.CheckCtx(home, cfg_for(), transports={"chatgpt": trap})
    r = cc.check_chatgpt(c)
    assert r.state == cc.BROKEN and r.fix == "jarvis chatgpt login"
    ChatGPTAuth(home)._save({"refresh_token": "r", "access_token": "a", "scopes": [PLAN_SCOPE],
                             "expires_at": time.time() + 3600, "email": "o@x"})
    assert cc.check_chatgpt(c).state == cc.WORKING and trap.seen == []             # the real token was left alone


def test_telegram_and_instagram_checks_use_read_only_calls(home):
    pair_telegram(home)
    t = mock(200, {"ok": True, "result": {"username": "jarves_test_bot"}})
    assert cc.check_telegram(cc.CheckCtx(home, cfg_for(), transports={"telegram": t})).state == cc.WORKING
    assert all("getMe" in u for _, u in t.seen)                                    # never sendMessage / getUpdates
    assert cc.check_telegram(cc.CheckCtx(home, cfg_for(), transports={"telegram": mock(401)})).state == cc.BROKEN
    assert cc.check_telegram(cc.CheckCtx(home, cfg_for(), transports={"telegram": mock(boom=True)})).state == cc.UNREACHABLE
    os.environ[ig.TOKEN_ENV] = "igtok"
    i = mock(200, {"user_id": "1", "username": "biz"})
    assert cc.check_instagram(cc.CheckCtx(home, cfg_for(), transports={"instagram": i})).state == cc.WORKING
    assert i.seen[0][0] == "GET"
    assert cc.check_instagram(cc.CheckCtx(home, cfg_for(), transports={"instagram": mock(401, {"error": {"code": 190}})})
                              ).fix == "jarvis instagram start"


def test_pear_and_wake_checks(home):
    c = cc.CheckCtx(home, cfg_for(), pear_probe=lambda u: False)
    r = cc.check_pear(c)
    assert r.state == cc.UNREACHABLE and "Pear" in r.detail
    c = cc.CheckCtx(home, cfg_for(), pear_probe=lambda u: True)
    assert cc.check_pear(c).state == cc.BROKEN
    (home / "pear_client_id.paired").write_text("x")
    assert cc.check_pear(c).state == cc.WORKING

    def boom(_v):
        raise RuntimeError("no model")
    assert cc.check_wake(cc.CheckCtx(home, cfg_for(), wake_loader=boom)).state == cc.BROKEN
    assert cc.check_wake(cc.CheckCtx(home, cfg_for(), wake_loader=lambda v: object())).state == cc.WORKING


def test_checks_run_concurrently_time_out_and_are_cached(home):
    gate, calls = threading.Event(), []
    cc.register_check("zz_slow", lambda c: (gate.wait(5), cc.CheckResult(cc.WORKING))[1])
    cc.register_check("zz_fast", lambda c: (calls.append(1), cc.CheckResult(cc.WORKING, "fast"))[1])
    try:
        t0 = time.monotonic()
        res = cc.run_checks(cc.CheckCtx(home, cfg_for()), ["zz_slow", "zz_fast"], timeout=0.3)
        assert time.monotonic() - t0 < 2
        assert res["zz_slow"].state == cc.UNREACHABLE and res["zz_fast"].state == cc.WORKING
        cc.run_checks(cc.CheckCtx(home, cfg_for()), ["zz_fast"])
        assert calls == [1]                                                         # second call came from the cache
        cc.run_checks(cc.CheckCtx(home, cfg_for()), ["zz_fast"], fresh=True)
        assert calls == [1, 1]
        cc.clock = lambda: time.monotonic() + 3600            # expired
        cc.run_checks(cc.CheckCtx(home, cfg_for()), ["zz_fast"])
        assert len(calls) == 3
    finally:
        gate.set()
        cc.CHECKS.pop("zz_slow", None)
        cc.CHECKS.pop("zz_fast", None)
        cc.clock = time.monotonic


def test_a_crashing_probe_never_breaks_the_run(home):
    cc.register_check("zz_crash", lambda c: 1 / 0)
    try:
        assert cc.run_checks(cc.CheckCtx(home, cfg_for()), ["zz_crash"])["zz_crash"].state == cc.UNREACHABLE
    finally:
        cc.CHECKS.pop("zz_crash")


# ====================================================================== --check and the reconnect offer
def test_check_flag_tests_connected_only_and_writes_nothing(home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    before = sorted(p.name for p in home.rglob("*") if "logs" not in p.parts)
    conn, out = make(home, [], check_only=True, check_transports={"openai": mock(401)})
    assert conn.run() == 1                                                          # a broken one -> exit 1
    text = "\n".join(out)
    assert "OpenAI API key: broken" in text and "-> jarvis key" in text and "Telegram" not in text
    assert sorted(p.name for p in home.rglob("*") if "logs" not in p.parts) == before
    conn2, out2 = make(home, [], check_only=True, check_transports={"openai": mock(200, {"data": []})})
    cc.clear_cache()
    assert conn2.run() == 0 and any("working" in line for line in out2)


def test_plain_run_offers_to_reconnect_failing_ones_before_the_unset_ones(home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reran = []
    conn, out = make(home, ["y"] + ["s"] * 20, check_transports={"openai": mock(401)},
                     optional_runners={"openai": lambda: reran.append(1) or 0})
    assert conn.run() == 0 and reran == [1] and conn.results["openai"] == "connected"
    first = next(line for line in out if line.startswith("== "))
    assert "OpenAI API key" in first                                                # ahead of the unset ones
    assert connect.state_map(home)["openai"] == "connected"


def test_yes_reports_failures_but_never_reconnects_unattended(home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reran = []
    from types import SimpleNamespace
    conn, out = make(home, [], yes=True, check_transports={"openai": mock(401)},
                     optional_runners={"openai": lambda: reran.append(1) or 0},
                     google_factory=lambda *a, **k: SimpleNamespace(run=lambda: 0))   # the real wizard waits for a download
    conn.run()
    assert reran == [] and any("broken" in line for line in out)


# ====================================================================== dashboard
def test_dashboard_three_states_skip_unskip_and_test_again(dash, home, monkeypatch):
    monkeypatch.setattr(connect, "CHECK_TRANSPORTS", {"telegram": mock(200, {"ok": True, "result": {"username": "b"}})})
    pair_telegram(home)
    data = get(dash, "/api/connect")
    assert all(i["status"] in ("connected", "skipped", "not_set") for i in data["items"])
    h = {"X-Jarves": "1"}
    r, body = req(dash, "POST", "/api/connect/skip", headers=h, body={"id": "wake"})
    assert r.status == 200 and {i["id"]: i["status"] for i in json.loads(body)["items"]}["wake"] == "skipped"
    r, body = req(dash, "POST", "/api/connect/skip", headers=h, body={"id": "wake", "skip": False})
    assert {i["id"]: i["status"] for i in json.loads(body)["items"]}["wake"] == "not_set"
    r, _ = req(dash, "POST", "/api/connect/skip", headers=h, body={"id": "nope"})
    assert r.status == 404
    r, body = req(dash, "POST", "/api/connect/check", headers=h, body={})
    assert r.status == 200 and "items" in json.loads(body)
    js = (dashboard.STATIC / "home.js").read_text(encoding="utf-8")
    for needle in ("/api/connect/skip", "/api/connect/check", "Test again", "בדיקה חוזרת", "Un-skip", "ביטול דילוג"):
        assert needle in js


def test_dashboard_run_one_integration_launches_its_console(dash, monkeypatch):
    seen = []
    monkeypatch.setattr(connect, "LAUNCH", lambda *a: seen.append(a) or True)
    r, body = req(dash, "POST", "/api/connect/run", headers={"X-Jarves": "1"}, body={"id": "pear"})
    assert r.status == 200 and seen == [("pear",)] and "jarvis connect pear" in json.loads(body)["message_en"]


# ====================================================================== WhatsApp bridge + openai-admin
def test_whatsapp_check_is_read_only_status_and_never_links(home, monkeypatch):
    from jarves import whatsapp_bridge as wb
    assert "whatsapp" in connect.ONLY and "openai-admin" in connect.ONLY
    seen = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append((r.method, r.url.path))
        return httpx.Response(200, json={"linked": True, "connection": "connected", "mode": "read", "counts": {"unread": 2}})
    monkeypatch.setattr(wb.BridgeClient, "_base", lambda self: "http://127.0.0.1:9")
    monkeypatch.setattr(wb.BridgeClient, "_token", lambda self: "t")
    c = cc.CheckCtx(home, cfg_for(), transports={"whatsapp": httpx.MockTransport(handler)})
    assert cc.run_checks(c, ["whatsapp"], fresh=True)["whatsapp"].state == cc.WORKING and seen == [("GET", "/status")]
    down = cc.CheckCtx(home, cfg_for())
    r = cc.run_checks(down, ["whatsapp"], fresh=True)["whatsapp"]          # no bridge endpoint file: not running
    assert r.state == cc.UNREACHABLE and r.fix == "jarvis whatsapp start"
    monkeypatch.setattr(wb, "bridge_command", lambda h: None)               # no bridge program: say so, never fail silently
    conn, out = make(home, [""], only="whatsapp", check_first=False)
    assert conn.run() == 0 and conn.results == {"whatsapp": "attention"}
    assert any("not installed" in line and "build_whatsapp_bridge" in line for line in out)
    it = next(i for i in connect.status_items(home, cfg_for()) if i.id == "whatsapp")
    assert it.state != "connected" and "not installed" in it.detail_en and "reinstall JARVES" in it.fix_en
