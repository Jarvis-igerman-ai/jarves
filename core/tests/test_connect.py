"""`jarvis connect` (owner request 20 Oct 2026, Issue #17): one guided command for every external account.

Real HTTP against the local fakes in channels_fakes.py (Telegram Bot API with long-poll getUpdates pairing, Pear
Desktop API Server, Instagram Graph API), the real google_setup wizard against test_google_setup's fake Google and
fake browser, a fake clipboard that implements connect.Clipboard, and a tmp Downloads folder. Waits are
event-driven (a threading.Event set by the fake browser when the t.me link opens; the fake Telegram's `polled`
event); nothing sleeps on a clock and nothing leaves 127.0.0.1."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import time

import pytest

from channels_fakes import (GOOD_TOKEN, IG_TOKEN, FakeClipboard, instagram_server, pear_server, telegram_server)
from jarves import alerts, clilang, connect, dashboard, google_setup
from jarves import features as feat
from jarves import instagram_channel as ig
from jarves import telegram_channel as tg
from jarves.config import Config
from jarves.google_auth import GoogleAuth
from jarves.setup_wizard import ConsoleIO
from jarves.userdata import open_db

from test_dashboard import dash, get, req  # noqa: F401  (fixture + helpers)
from test_google_setup import Browser, Google, dl  # noqa: F401  (fixture + fakes)
from test_setup_wizard import env, wizard  # noqa: F401  (fixture + helper)

ENV_KEYS = ("TELEGRAM_BOT_TOKEN", tg.API_BASE_ENV, ig.API_BASE_ENV, ig.TOKEN_ENV, "HOME_ASSISTANT_TOKEN")
CLOSED = "http://127.0.0.1:1"                      # nothing listens there: "Pear is not running"


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


async def no_sleep(_s: float) -> None:
    return None


def cfg_for(pear_url: str = CLOSED) -> Config:
    c = Config()
    c.music.pear_url = pear_url
    return c


def make(home, lines=None, *, only=None, yes=False, clipboard=None, open_url=None, **kw):
    out: list[str] = []
    conn = connect.Connector(home, kw.pop("cfg", None) or cfg_for(), io=ConsoleIO(list(lines or []), out.append),
                             clipboard=clipboard or FakeClipboard(), open_url=open_url or (lambda u: True), only=only,
                             yes=yes, asleep=no_sleep, pair_polls=kw.pop("pair_polls", 4),
                             pear_probe=kw.pop("pear_probe", lambda url: False), **kw)
    return conn, out


class Link:
    """A fake browser for the t.me deep link: remembers every URL and sets an event when the pairing link opens."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.seen = threading.Event()

    def __call__(self, url: str) -> bool:
        self.opened.append(url)
        if "?start=" in url:
            self.seen.set()
        return True

    @property
    def code(self) -> str:
        return next(u for u in self.opened if "?start=" in u).split("?start=")[1]


def owner_taps_start(srv, link: Link, chat_id: int = 555, name: str = "Igal", stranger: bool = True) -> threading.Thread:
    """The owner (and, first, a stranger with the wrong code) writes to the bot after the link opened."""
    def go() -> None:
        assert link.seen.wait(30)
        if stranger:
            srv.deliver({"update_id": 10, "message": {"chat": {"id": 999, "type": "private", "first_name": "Eve"},
                                                      "from": {"first_name": "Eve"}, "text": "/start not-the-code"}})
        srv.deliver({"update_id": 11, "message": {"chat": {"id": chat_id, "type": "private", "first_name": name},
                                                  "from": {"first_name": name}, "text": f"/start {link.code}"}})
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def tg_server():
    srv = telegram_server(updates=[])
    srv.state["block"] = True                           # getUpdates with timeout > 0 is a real long poll
    os.environ[tg.API_BASE_ENV] = srv.url
    return srv


# ====================================================================== status (no changes)
def test_status_lists_every_account_and_changes_nothing(home, capsys):
    def files():
        return sorted(str(p.relative_to(home)) for p in home.rglob("*") if "logs" not in p.relative_to(home).parts)
    before = files()
    args = argparse.Namespace(only=None, status=True, yes=False, json=False, reminders=None)
    assert connect.cmd_connect(args, cfg_for(), home) == 0
    out = capsys.readouterr().out
    for word in ("Google", "Telegram", "Pear", "Wake word", "Instagram", "Home Assistant", "not connected", "needs attention"):
        assert word in out
    assert "Status only" in out and "jarvis connect" in out
    assert files() == before                                       # no file written (the log folder aside)


def test_status_json_reflects_connected_accounts(home, capsys):
    os.environ["TELEGRAM_BOT_TOKEN"] = GOOD_TOKEN
    tg.write_private_json(tg.pair_file(home), {"bot_username": "jarves_test_bot", "chats": [{"chat_id": 1, "name": "a"}]})
    (home / "pear_client_id.paired").write_text("x", encoding="utf-8")
    (home / "wake").mkdir()
    for i in range(3):
        (home / "wake" / "samples").mkdir(exist_ok=True)
        (home / "wake" / "samples" / f"s{i}.wav").write_bytes(b"RIFF")
    srv = pear_server()
    try:
        cfg = cfg_for(srv.url)
        args = argparse.Namespace(only=None, status=True, yes=False, json=True, reminders=None)
        assert connect.cmd_connect(args, cfg, home) == 0
        data = json.loads(capsys.readouterr().out)
    finally:
        srv.close()
    st = {i["id"]: i["state"] for i in data["items"]}
    assert st == {"openai": "missing", "chatgpt": "missing", "google": "missing", "telegram": "connected", "pear": "connected", "wake": "connected",
                  "instagram": "missing", "home": "missing", "openai-admin": "missing", "whatsapp": "missing"}
    assert data["all_connected"] is False and data["command"] == "jarvis connect"
    assert GOOD_TOKEN not in json.dumps(data)


def test_status_marks_an_expired_google_connection_and_a_missing_pear_as_needing_attention(home):
    (home / "google").mkdir()
    (home / "google" / "token.json").write_text(json.dumps({"needs_reconnect": True}), encoding="utf-8")
    items = {i.id: i for i in connect.status_items(home, cfg_for(), pear_probe=lambda u: False)}
    assert items["google"].state == "attention" and "7" in items["google"].detail_en
    assert items["pear"].state == "attention" and "Plugins > API Server" in items["pear"].fix_en
    assert "127.0.0.1" in items["pear"].fix_he and "26538" in items["pear"].fix_en


# ====================================================================== Telegram
async def test_telegram_token_from_clipboard_and_chat_paired_by_deep_link(home):
    srv, link = tg_server(), Link()
    try:
        clip = FakeClipboard(["", "something else", f"BotFather: Use this token to access the HTTP API:\n{GOOD_TOKEN}\nKeep it safe"])
        conn, out = make(home, ["", "y"], only="telegram", clipboard=clip, open_url=link, pair_poll_timeout=5)
        t = owner_taps_start(srv, link)
        rc = await asyncio.to_thread(conn.run)
        t.join(5)
        assert rc == 0 and conn.results == {"telegram": "connected"}
        # opened BotFather, then the bot's deep link carrying a one-time code
        assert link.opened[0] == "https://t.me/BotFather" and link.opened[1].startswith("https://t.me/jarves_test_bot?start=jv")
        assert clip.writes == ["/newbot"]                      # what to paste into the BotFather chat
        # only the chat that sent the code was paired, never the stranger
        assert tg.pairing(home)["chats"] == [{"chat_id": 555, "name": "Igal"}]
        sent = [r for r in srv.requests if r["path"].endswith("/sendMessage")]
        assert [s["body"]["chat_id"] for s in sent] == [555] and "connected" in sent[0]["body"]["text"]
        # the token was validated (getMe) before it was saved, saved privately, and the clipboard was cleared
        paths = srv.paths()
        assert paths.index(f"/bot{GOOD_TOKEN}/getMe") < paths.index(f"/bot{GOOD_TOKEN}/sendMessage")
        assert f"TELEGRAM_BOT_TOKEN={GOOD_TOKEN}" in (home / "secrets.env").read_text(encoding="utf-8")
        assert clip.cleared == 1 and clip.value == ""
        assert GOOD_TOKEN not in "\n".join(out)
        # the long poll really was used, and the stranger's message was acknowledged (no endless re-serving)
        assert any((r["body"] or {}).get("timeout") == 5 for r in srv.requests if r["path"].endswith("getUpdates"))
        assert srv.state["updates"] == []
        assert "connected" in "\n".join(out) and not clilang.has_hebrew("\n".join(out))
    finally:
        srv.close()


async def test_telegram_declined_clipboard_falls_back_to_hidden_prompt_and_never_reads_the_clipboard(home):
    srv, link = tg_server(), Link()
    try:
        clip = FakeClipboard([GOOD_TOKEN])                      # a token IS on the clipboard, but the owner said no
        conn, out = make(home, ["", "n", GOOD_TOKEN], only="telegram", clipboard=clip, open_url=link)
        t = owner_taps_start(srv, link, stranger=False)
        assert await asyncio.to_thread(conn.run) == 0
        t.join(5)
        assert clip.reads == 0 and clip.cleared == 0           # consent was refused: not even read
        assert tg.pairing(home)["chats"][0]["chat_id"] == 555
    finally:
        srv.close()


async def test_telegram_no_consent_possible_without_a_terminal(home):
    srv = tg_server()
    try:
        clip = FakeClipboard([GOOD_TOKEN])
        conn, out = make(home, [], only="telegram", clipboard=clip)     # end of input: no answer = no consent
        assert await asyncio.to_thread(conn.run) == 0                   # nothing was tried, so nothing failed ...
        assert clip.reads == 0 and conn.results == {"telegram": "attention"}     # ... and the clipboard was not read
        assert not (home / "secrets.env").exists()
    finally:
        srv.close()


async def test_telegram_rejected_token_is_a_real_failure_nothing_saved_clipboard_cleared(home):
    srv = tg_server()
    try:
        bad = "987654321:AAH-this_is_not_the_right_token_0123456789"
        clip = FakeClipboard([bad])
        conn, out = make(home, ["", "y"], only="telegram", clipboard=clip)
        assert await asyncio.to_thread(conn.run) == 1
        assert conn.results == {"telegram": "failed"}
        assert not (home / "secrets.env").exists() and not tg.pairing(home)["chats"]
        assert clip.cleared == 1 and bad not in "\n".join(out)
        assert not any(r["path"].endswith("/sendMessage") for r in srv.requests)
    finally:
        srv.close()


async def test_telegram_token_saved_but_owner_never_pressed_start_needs_attention_not_failure(home):
    srv = telegram_server(updates=[])                    # getUpdates answers at once with nothing
    os.environ[tg.API_BASE_ENV] = srv.url
    try:
        conn, out = make(home, ["", "y"], only="telegram", clipboard=FakeClipboard([GOOD_TOKEN]), pair_polls=2,
                         pair_poll_timeout=0)
        assert await asyncio.to_thread(conn.run) == 0
        assert conn.results == {"telegram": "attention"} and tg.token_present() and not tg.pairing(home)["chats"]
        assert "press Start in the bot" in "\n".join(out)
    finally:
        srv.close()


async def test_telegram_rerun_when_connected_does_nothing_new(home):
    srv, link = tg_server(), Link()
    try:
        conn, _ = make(home, ["", "y"], only="telegram", clipboard=FakeClipboard([GOOD_TOKEN]), open_url=link)
        t = owner_taps_start(srv, link, stranger=False)
        assert await asyncio.to_thread(conn.run) == 0
        t.join(5)
        n_sent, n_open = len([r for r in srv.requests if r["path"].endswith("/sendMessage")]), len(link.opened)
        clip2 = FakeClipboard([])
        conn2, out2 = make(home, [], only="telegram", clipboard=clip2, open_url=link)
        assert await asyncio.to_thread(conn2.run) == 0 and conn2.results == {"telegram": "connected"}
        assert len(link.opened) == n_open and clip2.reads == 0 and clip2.writes == []      # no BotFather, no clipboard
        assert len([r for r in srv.requests if r["path"].endswith("/sendMessage")]) == n_sent
        assert "Already paired" in "\n".join(out2)
    finally:
        srv.close()


def test_token_pattern_finds_only_real_looking_tokens():
    assert connect.TOKEN_RE.search(f"text {GOOD_TOKEN} more").group(0) == GOOD_TOKEN
    for nope in ("hello", "123:short", "12345678:" + "a" * 20, "abc:" + "A" * 40, "my phone 0501234567:" + "x" * 5):
        assert connect.TOKEN_RE.search(nope) is None


# ====================================================================== Google (the existing wizard, reused)
def google_factory(home, g, b, tmp=None):
    auth = GoogleAuth(home, transport=g.transport(), open_browser=b.open_consent)

    def factory(h, cfg, **kw):
        return google_setup.Wizard(h, cfg, auth=auth, sleep=lambda s: None, which=lambda n: None, watch_timeout_s=2,
                                   transport=g.transport(), **kw)
    return factory


def test_google_runs_the_existing_wizard_picks_up_the_download_and_validates_live(home, dl):
    g, b = Google(), Browser(dl)
    clip = FakeClipboard()
    conn, out = make(home, ["", "me@example.com"], only="google", clipboard=clip, open_url=b.open_console,
                     downloads=dl, google_factory=google_factory(home, g, b))
    assert conn.run() == 0 and conn.results == {"google": "connected"}
    assert "/auth/clients" in " ".join(b.opened) and google_setup.scopes_text() in clip.writes
    assert not list(dl.glob("client_secret*.json"))             # taken from Downloads without being asked
    assert GoogleAuth(home).status()["connected"]
    paths = [c.url.path for c in g.api_calls()]
    assert "/calendar/v3/users/me/calendarList" in paths       # validated live before it counted as connected
    text = "\n".join(out)
    assert "== Summary ==" in text and "connected" in text and not clilang.has_hebrew(text)
    assert "ya29" not in text and "GOCSPX" not in text


def test_google_rerun_is_safe_and_just_rechecks(home, dl):
    g, b = Google(), Browser(dl)
    f = google_factory(home, g, b)
    assert make(home, ["", "me@example.com"], only="google", open_url=b.open_console, downloads=dl, google_factory=f)[0].run() == 0
    b2 = Browser(dl, client=None)
    conn, _ = make(home, [], only="google", open_url=b2.open_console, downloads=dl, google_factory=google_factory(home, g, b2))
    assert conn.run() == 0 and conn.results == {"google": "connected"} and b2.opened == []


def test_google_wizard_failure_is_a_failure_and_ctrl_c_is_130(home, dl):
    class Bad:
        def __init__(self, rc=None):
            self.rc = rc

        def run(self):
            if self.rc is None:
                raise KeyboardInterrupt
            return self.rc
    conn, out = make(home, [], only="google", google_factory=lambda h, c, **kw: Bad(1))
    assert conn.run() == 1 and conn.results == {"google": "failed"}
    assert "Run jarvis connect again" in "\n".join(out) or "jarvis connect" in "\n".join(out)
    conn2, out2 = make(home, [], only="google", google_factory=lambda h, c, **kw: Bad(None))
    assert conn2.run() == 130 and "run jarvis connect again" in "\n".join(out2).lower().replace("  ", " ")


# ====================================================================== Pear
def test_pear_not_running_says_exactly_how_to_enable_the_plugin_and_is_not_a_failure(home):
    conn, out = make(home, [], only="pear", yes=True)
    assert conn.run() == 0 and conn.results == {"pear": "attention"}
    text = "\n".join(out)
    assert "Plugins > API Server" in text and "127.0.0.1" in text and "26538" in text and "jarvis music open" in text
    assert not clilang.has_hebrew(text)                                    # English only by default


def test_pear_waits_for_the_app_then_pairs_when_the_owner_clicks_allow(home):
    srv = pear_server()
    try:
        probes = iter([False, False, True])
        conn, out = make(home, [], only="pear", cfg=cfg_for(srv.url), pear_probe=lambda u: next(probes, True))
        assert conn.run() == 0 and conn.results == {"pear": "connected"}
        assert (home / "pear_client_id.paired").exists()
        assert any(p.startswith("/auth/jarves-") for p in srv.paths("POST"))
        assert "Allow" in "\n".join(out)
    finally:
        srv.close()


def test_pear_denied_in_the_dialog_needs_attention(home):
    srv = pear_server()
    srv.state["deny"] = True
    try:
        conn, out = make(home, [], only="pear", cfg=cfg_for(srv.url), pear_probe=lambda u: True)
        assert conn.run() == 0 and conn.results == {"pear": "attention"}
        assert not (home / "pear_client_id.paired").exists()
    finally:
        srv.close()


def test_pear_already_paired_is_left_alone(home):
    srv = pear_server()
    (home / "pear_client_id.paired").write_text("x", encoding="utf-8")
    try:
        conn, _ = make(home, [], only="pear", cfg=cfg_for(srv.url), pear_probe=lambda u: True)
        assert conn.run() == 0 and conn.results == {"pear": "connected"} and srv.requests == []
    finally:
        srv.close()


# ====================================================================== wake word, optional accounts, --yes
def test_wake_calibration_offered_run_and_skipped_with_yes(home):
    calls = []
    conn, _ = make(home, ["y"], only="wake", wake_runner=lambda: calls.append(1) or 0)
    assert conn.run() == 0 and calls == [1] and conn.results == {"wake": "connected"}
    conn2, out2 = make(home, [], only="wake", yes=True, wake_runner=lambda: calls.append(2) or 0)
    assert conn2.run() == 0 and calls == [1] and conn2.results == {"wake": "skipped"}   # needs the owner's voice
    conn3, _ = make(home, ["s"], only="wake", wake_runner=lambda: calls.append(3) or 0)
    assert conn3.run() == 0 and conn3.results == {"wake": "skipped"}
    conn4, _ = make(home, ["y"], only="wake", wake_runner=lambda: 1)
    assert conn4.run() == 0 and conn4.results == {"wake": "attention"}                  # mic problem: not a crash


def test_wake_counts_saved_calibration_a_trained_phrase_or_three_recordings(home):
    assert connect.wake_state(home) == (False, "")
    (home / "wake").mkdir()
    (home / "wake" / "hey_max.npz").write_bytes(b"x")
    assert connect.wake_state(home)[0]


async def test_instagram_is_optional_skipped_by_yes_and_runs_the_real_wizard_when_asked(home):
    srv = instagram_server()
    os.environ[ig.API_BASE_ENV] = srv.url
    try:
        conn, out = make(home, [], only="instagram", yes=True)
        assert conn.run() == 0 and conn.results == {"instagram": "skipped"} and srv.requests == []
        conn2, out2 = make(home, ["y", IG_TOKEN, "y", ""], only="instagram")
        assert await asyncio.to_thread(conn2.run) == 0 and conn2.results == {"instagram": "connected"}
        assert ig.account(home)["username"] == "igal_biz" and IG_TOKEN not in "\n".join(out2)
    finally:
        srv.close()


def test_home_assistant_optional_runner_and_failure_exit_code(home):
    conn, _ = make(home, ["y"], only="home", optional_runners={"home": lambda: 1})
    assert conn.run() == 1 and conn.results == {"home": "failed"}
    conn2, _ = make(home, ["s"], only="home", optional_runners={"home": lambda: 1})
    assert conn2.run() == 0 and conn2.results == {"home": "skipped"}


def test_full_run_with_yes_skips_optional_waits_for_nothing_and_writes_the_summary_file(home):
    srv, link = tg_server(), Link()
    srv.state["block"] = False
    try:
        conn, out = make(home, [], yes=True, clipboard=FakeClipboard([GOOD_TOKEN]), open_url=link, pair_polls=1,
                         pair_poll_timeout=0, google_factory=lambda h, c, **kw: type("W", (), {"run": lambda s: 0})())
        assert conn.run() == 0
        assert conn.results["google"] == "connected" and conn.results["telegram"] == "attention"
        assert conn.results["pear"] == "attention" and conn.results["wake"] == "skipped"
        assert conn.results["instagram"] == conn.results["home"] == "skipped"
        saved = json.loads((home / "connect.json").read_text(encoding="utf-8"))
        assert saved["items"]["pear"]["state"] == "attention" and saved["last_run"] > 0
        table = [l for l in out if l.startswith("  ")]
        assert any("Telegram" in l and "needs attention" in l for l in table)
        assert any("Instagram" in l and "skipped" in l for l in table)
    finally:
        srv.close()


# ====================================================================== the clipboard provider
def test_system_clipboard_read_is_safe_everywhere():
    v = connect.SystemClipboard().read()
    assert v is None or isinstance(v, str)


# ====================================================================== the daily reminder
class Out:
    def __init__(self):
        self.got = []

    def available(self):
        return True

    async def deliver(self, a):
        self.got.append(a)
        return True


@pytest.fixture
def engine(home):
    db = open_db(home)
    e = alerts.AlertEngine(db, outputs={"speech": Out(), "popup": Out()}, clock=lambda: e.now)
    e.now = 1_700_000_000.0 + 12 * 3600        # a UTC afternoon ... local zone is Asia/Jerusalem, 16:00-17:00 (no quiet hours)
    yield e
    db.close()


def nudge(e, home, **kw):
    return connect.maybe_nudge(e.db, e, home=home, clock=lambda: e.now, **kw)


def test_nudge_once_a_day_in_hebrew_by_voice(home, engine):
    a = nudge(engine, home)
    assert a and a["text"] == "לא חיברת עדיין גוגל, להריץ jarvis connect?" and a["channel"] == "speech"
    asyncio.run(engine.flush())
    assert engine.outputs["speech"].got and engine.outputs["speech"].got[0]["text"].endswith("jarvis connect?")
    engine.now += 3600 * 5
    assert nudge(engine, home) is None                       # same day
    engine.now += 3600 * 20
    assert nudge(engine, home)                               # next day


def test_nudge_says_reconnect_when_the_connection_expired(home, engine):
    from test_google import seed
    seed(home)
    tok = json.loads((home / "google" / "token.json").read_text(encoding="utf-8"))
    tok.pop("refresh_token", None)
    tok["needs_reconnect"] = True
    (home / "google" / "token.json").write_text(json.dumps(tok), encoding="utf-8")
    a = nudge(engine, home)
    assert a and a["text"] == connect.NUDGE_TEXT_RECONNECT_HE


def test_nudge_respects_quiet_hours_pause_proactive_setting_autonomy_and_the_off_switches(home, engine):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Jerusalem")
    engine.now = datetime(2026, 10, 20, 23, 30, tzinfo=tz).timestamp()
    assert nudge(engine, home) is None                       # quiet hours 22:00-07:00
    engine.now = datetime(2026, 10, 21, 7, 5, tzinfo=tz).timestamp()
    engine.pause(30, by="test")
    assert nudge(engine, home) is None                       # paused
    engine.resume(by="test")
    engine.enabled = lambda: False
    assert nudge(engine, home) is None                       # proactive alerts switched off
    engine.enabled = lambda: True
    engine.db.set_setting("autonomy.level", "observe_only", actor="test")
    assert nudge(engine, home) is None                       # observe-only autonomy: no suggestions
    engine.db.set_setting("autonomy.level", "suggest_and_ask", actor="test")
    engine.db.set_setting("connect.nudge", False, actor="test")
    assert nudge(engine, home) is None                       # `jarvis connect --reminders off`
    engine.db.set_setting("connect.nudge", True, actor="test")
    f = feat.Features(Config(), home)
    f.set("guided_setup", False, by="user")
    assert nudge(engine, home, features=f) is None           # the guided_setup feature is off
    f.set("guided_setup", True, by="user")
    assert nudge(engine, home, features=f)                   # and when everything allows it, it does remind


def test_nudge_silent_when_google_is_connected(home, engine):
    from test_google import seed
    seed(home)
    assert GoogleAuth(home).status()["connected"]
    assert nudge(engine, home) is None


def test_reminders_cli_switch_persists(home, capsys):
    args = argparse.Namespace(only=None, status=False, yes=False, json=False, reminders="off")
    assert connect.cmd_connect(args, cfg_for(), home) == 0
    db = open_db(home)
    try:
        assert db.setting("connect.nudge") is False
    finally:
        db.close()


# ====================================================================== feature, dashboard, first-run wizard, CLI help
def test_guided_setup_is_registered_on_by_default_low_risk_with_a_guide_and_bundle():
    from jarves import guides
    from jarves import setup_wizard as sw
    sw.load_addons()
    f = feat.CATALOG["guided_setup"]
    assert f.default_enabled and f.risk == "low" and "guided_setup" not in sw.SAFE_OFF
    assert any("guided_setup" in ids for _, _, _, ids in sw.GROUPS)
    assert "guided_setup" in sw.BUNDLE and all(ok for ok, _ in (c() for c in sw.BUNDLE["guided_setup"]))
    assert [g for g in guides.load().values() if "guided_setup" in g.features]
    assert f.targets["windows"]["status"] == "implemented_unverified"


def test_dashboard_connections_checklist_and_run_button(dash, monkeypatch):
    data = get(dash, "/api/connect")
    assert [i["id"] for i in data["items"]] == list(connect.ONLY) and data["command"] == "jarvis connect"
    launched = []
    monkeypatch.setattr(connect, "LAUNCH", lambda: launched.append(1) or True)
    r, body = req(dash, "POST", "/api/connect/run", headers={"X-Jarves": "1"}, body={})
    assert r.status == 200 and json.loads(body)["started"] is True and launched == [1]
    monkeypatch.setattr(connect, "LAUNCH", lambda: False)
    r, body = req(dash, "POST", "/api/connect/run", headers={"X-Jarves": "1"}, body={})
    assert json.loads(body)["started"] is False and "jarvis connect" in json.loads(body)["message_en"]
    js = (dashboard.STATIC / "home.js").read_text(encoding="utf-8")
    assert "Run guided setup" in js and "הפעלת הגדרה מודרכת" in js and "/api/connect/run" in js


def test_first_run_wizard_ends_by_offering_jarvis_connect(env):
    started = []
    w, out = wizard(env, lines=["2"] + [""] * 10 + ["n", "y"], actions={"connect_start": lambda: started.append(1) or 0})
    assert w.run() == 0 and started == [1]
    w2, out2 = wizard(env, answers={}, actions={"connect_start": lambda: started.append(2) or 0})
    assert w2.run() == 0 and started == [1]                  # never from a silent / answers-file run ...
    assert any("jarvis connect" in line for line in out2)    # ... but the summary still points to it


def test_cli_registers_connect_and_root_help_lists_it(capsys):
    from jarves import cli
    with pytest.raises(SystemExit):
        cli.main(["connect", "--help"])
    out = capsys.readouterr().out
    for flag in ("--only", "--status", "--yes", "--reminders"):
        assert flag in out
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "connect" in capsys.readouterr().out
