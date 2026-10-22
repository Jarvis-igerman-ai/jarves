"""Setup wizard on an existing profile (keep by default, backup before changes, --yes) and the Hebrew browser offer.
Fake old profile directory; offline, silent, no real browser, no real key."""
import pytest

from jarves import connect
from jarves import setup_wizard as sw
from test_setup_wizard import env, switches, wizard  # noqa: F401  (fixtures + helper)

SECRET = "sk-TESTSECRETVALUE1234567890"


@pytest.fixture
def old(env, monkeypatch):  # noqa: F811
    """A fake V3 profile directory: config.toml, secrets.env, finished setup."""
    (env / "config.toml").write_text('[ai]\nconnection = "api_key"\n', encoding="utf-8")
    (env / "secrets.env").write_text(f"OPENAI_API_KEY={SECRET}\n", encoding="utf-8")
    (env / "setup_done").write_text("api_key", encoding="utf-8")
    items = [connect.Item("google", "גוגל", "Google", False, "missing"),
             connect.Item("telegram", "טלגרם", "Telegram", False, "connected"),
             connect.Item("pear", "Pear", "Pear", False, "connected"),
             connect.Item("wake", "מילה", "Wake word", True, "connected")]
    monkeypatch.setattr(connect, "status_items", lambda home, cfg=None, **kw: items)
    monkeypatch.delenv("JARVES_CLI_LANG", raising=False)
    return env


def snapshot(home):
    return {n: (home / n).read_bytes() for n in ("config.toml", "secrets.env")}


def backups(home):
    d = home / "backups"
    return list(d.glob("jarves-backup-*.zip")) if d.exists() else []


def test_existing_profile_is_detected_and_fresh_one_is_not(old, tmp_path_factory):
    assert sw.existing_profile(old, old / "config.toml")
    fresh = tmp_path_factory.mktemp("fresh")
    assert not sw.existing_profile(fresh, fresh / "config.toml")


def test_interactive_default_keeps_everything_and_asks_only_what_is_missing(old):
    before = snapshot(old)
    connects = []
    w, out = wizard(old, lines=[], actions={"connect_start": lambda: connects.append(1) or 0})
    assert w.run() == 0
    text = "\n".join(out)
    assert "existing settings" in text and "Google: missing" in text and "Telegram: connected" in text
    assert "API key: saved" in text and "AI connection mode: api_key" in text
    assert "Keep my current settings" in text
    head = text.split("Summary")[0]
    for asked in ("Wake phrase", "Add-ons", "Privacy mode", "Monthly API budget", "How should JARVES connect"):
        assert asked not in head, asked
    assert connects == [1]                                      # Google is missing, so the guided connect is offered
    assert snapshot(old) == before                              # nothing overwritten
    assert SECRET not in text
    assert not (old / "features.json").exists()
    assert backups(old) == []                                   # nothing changed, nothing to back up


def test_nothing_missing_means_no_connect_question(old, monkeypatch):
    monkeypatch.setattr(connect, "status_items", lambda home, cfg=None, **kw: [
        connect.Item("google", "גוגל", "Google", False, "connected")])
    connects = []
    w, out = wizard(old, lines=[], actions={"connect_start": lambda: connects.append(1) or 0})
    assert w.run() == 0
    assert connects == []


def test_yes_keeps_everything_non_interactively(old):
    before = snapshot(old)
    w, out = wizard(old, yes=True)
    assert w.keep is True
    assert w.run() == 0
    assert snapshot(old) == before
    assert SECRET not in "\n".join(out)
    assert backups(old) == []
    assert not sw.first_run(old)


def test_yes_on_a_fresh_profile_takes_the_safe_defaults(env):
    w, out = wizard(env, yes=True, lang="en")
    assert w.keep is False and w.run() == 0
    assert all(not switches(env).enabled(fid) for fid in sw.SAFE_OFF)


def test_choosing_change_takes_a_backup_first_and_keeps_values_on_enter(old):
    before = snapshot(old)
    w, out = wizard(old, lines=["n"])                           # "keep?" -> no; everything else Enter (current value)
    assert w.run() == 0
    text = "\n".join(out)
    assert "Backup saved" in text
    assert backups(old)
    assert snapshot(old) == before and SECRET not in text


def test_failed_backup_stops_before_any_change(old, monkeypatch):
    from jarves import backup
    before = snapshot(old)

    def boom(*a, **k):
        raise backup.BackupError("disk full")
    monkeypatch.setattr(backup, "create", boom)
    w, out = wizard(old, lines=["n"])
    assert w.run() == 1
    assert "backup failed" in "\n".join(out) and snapshot(old) == before


def test_answers_file_on_an_existing_profile_backs_up_first(old):
    w, out = wizard(old, answers={"language": "en", "connection": "api_key"})
    assert w.run() == 0
    assert backups(old)


def test_missing_key_is_asked_in_keep_mode_and_is_skippable(old):
    (old / "secrets.env").write_text("", encoding="utf-8")
    asked = []
    w, out = wizard(old, lines=["y", "", ""], actions={"ask_key": lambda h: asked.append(1) or 1})
    assert w.run() == 0
    assert asked == [1]
    assert any("jarvis key" in line for line in out)           # skipped: the note says how to add it later


# ---------------------------------------------------------------------- the '<-' marker and the browser offer
def test_current_choice_marker_follows_the_chosen_value(env):
    w, out = wizard(env, lines=["1"], lang="en")
    assert w.choose("language", "Language", [("he", "עברית"), ("en", "English")], "en") == "he"
    assert "<-" not in "\n".join(out)
    assert "> 2  English" in out and "> 1  עברית" in out        # the menu marks the old value, then the pick is echoed


def test_hebrew_offers_the_browser_first_and_enter_takes_it(env, monkeypatch):
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: pytest.fail("no real browser in tests"))
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    went = []
    w, out = wizard(env, lines=[""], actions={"browser_setup": lambda: went.append(1) or 0})
    assert w.run() == 0
    assert went == [1]
    assert any("recommended for Hebrew" in line for line in out)
    assert not (env / "setup_done").exists()                   # the browser carries on; nothing half-finished is marked done


def test_hebrew_browser_offer_can_be_declined(env, monkeypatch):
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    went = []
    w, out = wizard(env, lines=["c"], actions={"browser_setup": lambda: went.append(1) or 0})
    assert w.run() == 0 and went == []
    assert "הגדרת JARVES" in "\n".join(out)


def test_choosing_hebrew_in_the_language_menu_offers_the_browser(env):
    went = []
    w, out = wizard(env, lines=["1", ""], actions={"browser_setup": lambda: went.append(1) or 0})
    assert w.run() == 0 and went == [1]


def test_browser_that_cannot_start_falls_back_to_the_terminal(env, monkeypatch):
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    w, out = wizard(env, lines=[""], actions={"browser_setup": lambda: sw.CONTINUE_IN_TERMINAL})
    assert w.run() == 0
    assert "הגדרת JARVES" in "\n".join(out)
