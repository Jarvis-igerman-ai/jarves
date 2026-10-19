import asyncio

import pytest

from jarves.brain import match_intent
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import MemorySkill
from jarves.skills.local_contacts import LocalContactsSkill


def run(coro):
    return asyncio.run(coro)


def registry(memory):
    reg = SkillRegistry()
    reg.register(MemorySkill(memory))
    reg.register(LocalContactsSkill(memory))
    reg.allowed_actions.add("contact_remember")
    return reg


def test_relationship_survives_restart_and_ordinary_forget(tmp_path):
    path = tmp_path / "memory.db"
    mem = MemoryStore(path)
    saved = run(registry(mem).call("contact_remember", {"label": "my wife", "email": "spouse@example.com"}))
    mid = saved.data["contact"]["memory_id"]
    mem.close()
    mem = MemoryStore(path)
    reg = registry(mem)
    try:
        found = run(reg.call("contact_lookup", {"query": "my wife"}))
        assert found.data["contacts"][0]["email"] == "spouse@example.com"
        assert not found.data["ambiguous"]
        assert run(reg.call("forget", {"id": mid}, origin="user")).ok
        assert run(reg.call("contact_lookup", {"query": "my wife"})).data["contacts"] == []
    finally:
        mem.close()


def test_conflict_no_fuzzy_dedupe_or_silent_overwrite(tmp_path):
    mem = MemoryStore(tmp_path / "memory.db")
    reg = registry(mem)
    try:
        args = {"label": "my wife", "email": "spouse1@example.com"}
        assert run(reg.call("contact_remember", args)).ok
        assert run(reg.call("contact_remember", args)).ok
        assert mem.count() == 1
        result = run(reg.call("contact_remember", {**args, "email": "spouse2@example.com"}))
        assert not result.ok and result.code == "CONTACT_CONFLICT"
        assert mem.count() == 1
        assert run(reg.call("contact_remember", {"label": "my sister", "email": "sister@example.com"})).ok
        assert run(reg.call("contact_lookup", {"query": "example.com"})).data["ambiguous"]
        assert mem.clear() == 2  # ordinary privacy controls include contact memories
    finally:
        mem.close()


@pytest.mark.parametrize("data", [
    {"label": " ", "email": "a@example.com"},
    {"label": "wife", "email": "a@example.com\r\nBcc: x@example.com"},
    {"label": "wife", "email": "a@example.com,other@example.com"},
    {"label": "wife", "email": "a\x00@example.com"},
    {"label": "wife\ninstruction", "email": "a@example.com"},
])
def test_invalid_contact_not_stored(tmp_path, data):
    mem = MemoryStore(tmp_path / "memory.db")
    try:
        result = run(registry(mem).call("contact_remember", data))
        assert not result.ok and result.code == "TOOL_BAD_ARGS"
        assert mem.count() == 0
    finally:
        mem.close()


def test_lookup_older_contact_not_limited_to_recent_prompt(tmp_path):
    mem = MemoryStore(tmp_path / "memory.db")
    reg = registry(mem)
    try:
        run(reg.call("contact_remember", {"label": "my wife", "email": "spouse@example.com"}))
        for n in range(25):
            mem.add(f"later ordinary fact {n}")
        assert len(run(reg.call("contact_lookup", {"query": "my wife"})).data["contacts"]) == 1
    finally:
        mem.close()


@pytest.mark.parametrize("phrase,label", [
    ("remember my wife's email is spouse@example.com", "my wife"),
    ("remember that my wife email is spouse@example.com", "my wife"),
    ("תזכור שהמייל של אשתי הוא spouse@example.com", "אשתי"),
])
def test_explicit_contact_intent(phrase, label):
    intent = match_intent(phrase)
    assert intent.tool == "contact_remember"
    assert intent.args == {"label": label, "email": "spouse@example.com"}


def test_contact_cli_local_only_and_confirmed_forget(tmp_path, monkeypatch, capsys):
    from jarves import cli
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    assert cli.main(["contacts", "remember", "my wife", "--email", "spouse@example.com"]) == 0
    assert cli.main(["contacts", "lookup", "my wife"]) == 0
    assert "spouse@example.com" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert cli.main(["contacts", "forget", "1"]) == 1
    mem = MemoryStore(tmp_path / "memory.db")
    assert mem.count() == 1
    mem.close()
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert cli.main(["contacts", "forget", "1"]) == 0


@pytest.mark.parametrize("argv", [["contacts", "--help"], ["calendar", "share", "--help"], ["mail", "draft", "--help"]])
def test_new_cli_paths_discoverable(argv):
    from jarves.cli import main
    with pytest.raises(SystemExit) as result:
        main(argv)
    assert result.value.code == 0


async def test_real_core_registers_local_contacts(tmp_path, cfg):
    from conftest import FakeSTT, FakeTTS
    from jarves.app import Core
    from jarves.voice.audio import SimAudio
    from jarves.skills.music import FakePlayer
    core = Core(cfg, home=tmp_path, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(),
                use_real_audio=False, console_logs=False)
    await core.start()
    try:
        assert core.registry.get("contact_lookup") is not None
        assert core.registry.get("contact_remember") is not None
    finally:
        await core.stop()


@pytest.mark.parametrize("answer,expected", [("no", 1), ("yes", 0)])
def test_calendar_cli_guests_use_same_confirmation(tmp_path, monkeypatch, answer, expected):
    import httpx
    from jarves import cli, features
    from jarves.config import Config
    from jarves.google_auth import ALL_SCOPES, CALENDAR_WRITE
    from jarves.skills import google
    from test_calendar_write import FakeCalendar, seed
    seed(tmp_path, (*ALL_SCOPES, CALENDAR_WRITE))
    f = features.Features(Config(), tmp_path)
    f.set("calendar_write", True)
    api = FakeCalendar()
    original = google.register_google
    monkeypatch.setattr(google, "register_google", lambda registry, home: original(registry, home, transport=httpx.MockTransport(api.handler)))
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or answer)
    assert cli.main(["calendar", "create", "--title", "Family dinner", "--day", "tomorrow", "--guest", "spouse@example.com"]) == expected
    assert prompts and "spouse@example.com" in prompts[0]
    assert len(api.writes) == (1 if answer == "yes" else 0)
    if api.writes:
        assert api.writes[0][2]["attendees"] == [{"email": "spouse@example.com"}]
        assert api.calls[-1].url.params["sendUpdates"] == "all"


def test_contacts_cli_missing_scope_is_actionable(tmp_path, monkeypatch, capsys):
    from jarves import cli
    from jarves.google_auth import ALL_SCOPES
    from test_calendar_write import seed
    seed(tmp_path, ALL_SCOPES)
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    assert cli.main(["contacts", "search", "Dana"]) == 1
    assert "Google Contacts unavailable" in capsys.readouterr().out


@pytest.mark.parametrize("send,answer,sent", [(False, "yes", False), (True, "no", False), (True, "yes", True)])
def test_mail_cli_link_draft_and_separate_send_confirmation(tmp_path, monkeypatch, send, answer, sent):
    import httpx
    from jarves import cli, features
    from jarves.config import Config
    from jarves.skills import google
    from test_google import FakeGoogle, seed
    seed(tmp_path)
    features.Features(Config(), tmp_path).set("gmail_send", True)
    fake = FakeGoogle()
    original = google.register_google
    monkeypatch.setattr(google, "register_google", lambda registry, home: original(registry, home, transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or answer)
    argv = ["mail", "draft", "--to", "spouse@example.com", "--subject", "Event", "--body",
            "https://www.google.com/calendar/event?eid=fixture"]
    result = cli.main(argv + (["--send"] if send else []))
    assert result == (0 if not send or sent else 1)
    assert bool(fake.sent) is sent
    assert bool(prompts) is send
    if prompts:
        assert "spouse@example.com" in prompts[0] and "calendar/event" in prompts[0]
