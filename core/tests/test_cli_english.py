"""CLI language (owner request 20 Oct 2026, Issue #17): the wizards and commands print ENGLISH ONLY by default;
JARVES_CLI_LANG / the `ui.cli_language` setting choose 'he' or 'both'. And every `jarvis connect` step can be skipped
('skip now' or 'never ask again'; --skip a,b; s / skip inside a step)."""
from __future__ import annotations

import argparse
import asyncio
import os

import pytest

from channels_fakes import GOOD_TOKEN, FakeClipboard, telegram_server
from jarves import alerts, clilang, connect, google_setup
from jarves import telegram_channel as tg
from jarves.config import Config
from jarves.setup_wizard import ConsoleIO
from jarves.userdata import open_db

from test_connect import Link, cfg_for, make, no_sleep, owner_taps_start, tg_server  # noqa: F401
from test_connect import clean_env  # noqa: F401  (autouse fixture of that module)
from test_connect import engine, nudge  # noqa: F401


@pytest.fixture(autouse=True)
def lang_env(monkeypatch):
    monkeypatch.delenv("JARVES_CLI_LANG", raising=False)
    clilang.set_home(None)
    yield
    clilang.set_home(None)


def status_out(home, capsys) -> str:
    args = argparse.Namespace(only=None, status=True, yes=False, json=False, reminders=None)
    assert connect.cmd_connect(args, cfg_for(), home) == 0
    return capsys.readouterr().out


# ---------------------------------------------------------------- the switch itself
def test_default_is_english_and_env_beats_setting(home, monkeypatch):
    assert clilang.current() == "en"
    assert clilang.fmt("שלום", "hello") == "hello" and clilang.fmt("שלום", "") == "שלום"      # never drop a text
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    assert clilang.fmt("שלום", "hello") == "שלום"
    monkeypatch.setenv("JARVES_CLI_LANG", "both")
    assert clilang.fmt("שלום", "hello") == "שלום\nhello" and clilang.pick("כן", "yes") == "כן / yes"
    monkeypatch.setenv("JARVES_CLI_LANG", "nonsense")
    assert clilang.current() == "en"


def test_setting_from_the_dashboard_store_is_used(home):
    from jarves.memory import MemoryStore
    from jarves.skills.settings import Settings
    mem = MemoryStore(home / "memory.db")
    try:
        Settings(mem).set("ui.cli_language", "both")
    finally:
        mem.close() if hasattr(mem, "close") else None
    clilang.set_home(home)
    assert clilang.current() == "both"
    os.environ["JARVES_CLI_LANG"] = "he"
    try:
        assert clilang.current() == "he"
    finally:
        del os.environ["JARVES_CLI_LANG"]


def test_inline_pairs_are_split_per_line(monkeypatch):
    text = "\nמכשירים / Devices:\nplain english line\nנשמר. / Saved."
    assert clilang.bi(text) == "\nDevices:\nplain english line\nSaved."
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    assert clilang.bi(text) == "\nמכשירים\nplain english line\nנשמר."
    monkeypatch.setenv("JARVES_CLI_LANG", "both")
    assert clilang.bi(text) == text


# ---------------------------------------------------------------- no Hebrew with default settings
def test_connect_status_is_english_only_and_he_both_still_work(home, capsys, monkeypatch):
    out = status_out(home, capsys)
    assert "Google" in out and "not connected" in out and not clilang.has_hebrew(out)
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    assert clilang.has_hebrew(status_out(home, capsys))
    monkeypatch.setenv("JARVES_CLI_LANG", "both")
    both = status_out(home, capsys)
    assert clilang.has_hebrew(both) and "not connected" in both


async def run_telegram(home, monkeypatch, lang):
    if lang:
        monkeypatch.setenv("JARVES_CLI_LANG", lang)
    srv, link = tg_server(), Link()
    try:
        out: list[str] = []
        wz = tg.Wizard(home, ConsoleIO([GOOD_TOKEN], out.append), transport=None, polls=4, poll_timeout=5,
                       sleep=no_sleep, auto_pair=True, on_link=link)
        t = owner_taps_start(srv, link, stranger=False)
        rc = await wz.run()
        t.join(5)
        assert rc == 0
        return "\n".join(out)
    finally:
        srv.close()


async def test_telegram_wizard_is_english_only_by_default(home, monkeypatch):
    text = await run_telegram(home, monkeypatch, None)
    assert "Saved on this computer only" in text and not clilang.has_hebrew(text)
    assert GOOD_TOKEN not in text


async def test_telegram_wizard_he_and_both(home, monkeypatch):
    he = await run_telegram(home, monkeypatch, "he")
    assert clilang.has_hebrew(he) and "Saved on this computer only" not in he
    import shutil
    for f in home.glob("*telegram*"):
        shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink()
    (home / "secrets.env").unlink(missing_ok=True)
    os.environ.pop(tg.TOKEN_ENV, None)
    both = await run_telegram(home, monkeypatch, "both")
    assert clilang.has_hebrew(both) and "Saved on this computer only" in both


def test_google_status_command_is_english_only(home, capsys, monkeypatch):
    from jarves import cli
    rc = cli.main(["google", "status"])
    out = capsys.readouterr().out
    assert rc in (0, 1) and out.strip() and not clilang.has_hebrew(out)


def test_features_listing_is_english_only_and_he_keeps_the_hint(home, capsys, monkeypatch):
    from jarves import cli
    assert cli.main(["features"]) == 0
    out = capsys.readouterr().out
    assert "ACTIVE" in out and not clilang.has_hebrew(out) and "run: jarvis google start" in out
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    assert cli.main(["features"]) == 0
    assert clilang.has_hebrew(capsys.readouterr().out)


def test_wake_test_help_is_english_only(capsys):
    from jarves import cli
    with pytest.raises(SystemExit):
        cli.main(["wake", "test", "--help"])
    out = capsys.readouterr().out
    assert "usage" in out.lower() and not clilang.has_hebrew(out)


# ---------------------------------------------------------------- skip: continue / skip now / never
def test_skip_now_moves_on_and_status_says_skipped(home):
    conn, out = make(home, ["s"], only="pear")
    assert conn.run() == 0 and conn.results == {"pear": "skipped"}
    text = "\n".join(out)
    assert "[C]ontinue / [S]kip (not offered again) (Enter = continue)" in text
    assert "(run jarvis connect pear)" in text
    assert "skipped" in text and not clilang.has_hebrew(text)
    assert connect.never_list(home) == ["pear"]                # a skip is remembered: never offered again


def test_enter_continues(home):
    conn, out = make(home, [""], only="pear")
    assert conn.run() == 0 and conn.results == {"pear": "attention"}      # Pear is not running: the step ran


def test_never_is_remembered_and_hidden_until_only_or_reset(home):
    conn, out = make(home, ["n"], only="instagram")
    assert conn.run() == 0 and conn.results == {"instagram": "skipped"}
    assert connect.never_list(home) == ["instagram"]
    db = open_db(home)
    try:
        assert db.setting("connect.skipped") == ["instagram"]
    finally:
        db.close()
    # a full run no longer mentions it
    conn2, out2 = make(home, [], yes=True, pear_probe=lambda u: False,
                       google_factory=lambda h, c, **kw: type("W", (), {"run": lambda s: 0})())
    srv = tg_server()
    srv.close()
    conn2.run()
    assert "instagram" not in conn2.results and "Instagram" not in "\n".join(out2)
    assert "Home Assistant" in "\n".join(out2)
    # --status still lists it, as skipped, with the way back
    conn3, out3 = make(home, [], status_only=True)
    conn3.run()
    assert any("Instagram" in l and "skipped" in l for l in out3)
    # --only brings it back (and clears the memory)
    conn4, _ = make(home, [""], only="instagram", optional_runners={"instagram": lambda: 0})
    conn4.run()
    assert connect.never_list(home) == []
    # --reset-skips
    connect.set_never(home, ["home", "wake"])
    conn5, out5 = make(home, [], only="pear", yes=True, reset_skips=True)
    conn5.run()
    assert connect.never_list(home) == []


def test_skip_flag_skips_named_items_for_this_run_only(home):
    conn, out = make(home, [], yes=True, skip=["telegram", "instagram", "pear"],
                     google_factory=lambda h, c, **kw: type("W", (), {"run": lambda s: 0})())
    assert conn.run() == 0
    assert conn.results["telegram"] == conn.results["pear"] == conn.results["instagram"] == "skipped"
    assert conn.results["google"] == "connected"
    text = "\n".join(out)
    assert "(run jarvis connect telegram)" in text
    assert connect.never_list(home) == []


def test_skip_flag_via_cli_rejects_unknown_item(home, capsys):
    args = argparse.Namespace(only=None, status=False, yes=True, json=False, reminders=None, skip="nope,telegram")
    assert connect.cmd_connect(args, cfg_for(), home) == 2


async def test_s_inside_a_step_abandons_it_cleanly_before_anything_is_saved(home):
    srv = tg_server()
    try:
        # gate: continue; clipboard consent: no; token prompt: 's'
        conn, out = make(home, ["", "n", "s"], only="telegram", clipboard=FakeClipboard([]), open_url=lambda u: True)
        assert await asyncio.to_thread(conn.run) == 0
        assert conn.results == {"telegram": "skipped"}
        assert not tg.token_present() and not (home / "secrets.env").exists()
        assert "Traceback" not in "\n".join(out)
        # 'skip' typed at the consent question also works
        conn2, _ = make(home, ["", "skip"], only="telegram", clipboard=FakeClipboard([]), open_url=lambda u: True)
        assert await asyncio.to_thread(conn2.run) == 0 and conn2.results == {"telegram": "skipped"}
    finally:
        srv.close()


def test_skip_inside_a_step_continues_with_the_next_item(home):
    class Skipper:
        def __init__(self, *a, **k) -> None:
            self.ask = k["ask"]

        def run(self) -> int:
            self.ask("Something: ")
            return 0
    conn, out = make(home, ["", "skip", "s"], yes=False, google_factory=lambda h, c, **kw: Skipper(ask=kw["ask"]),
                     skip=["openai", "chatgpt", "openai_admin", "telegram", "pear", "instagram", "home"],
                     wake_runner=lambda: 0)
    assert conn.run() == 0
    assert conn.results["google"] == "skipped"
    assert conn.results["wake"] == "skipped"                  # the next item was reached and could be skipped too


def test_daily_reminder_ignores_never_items(home, engine):
    db = open_db(home)
    try:
        db.set_setting("connect.skipped", ["google"])
    finally:
        db.close()
    assert nudge(engine, home) is None
    connect.set_never(home, [])
    assert nudge(engine, home) is not None
