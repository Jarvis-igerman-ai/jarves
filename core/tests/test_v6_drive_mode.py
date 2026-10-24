"""V6 item 2 (#209) drive-mode policy: pure decisions, the when-stopped queue, the CLI and the catalog row.
No call, message or link is made; everything runs on tmp paths."""
from __future__ import annotations

import json

import pytest

from jarves import cli, drive_mode as dm
from jarves import features as feat


def test_off_by_default_and_allows_everything():
    assert feat.CATALOG["drive_mode"].default_enabled is False
    v = dm.decide("settings_change", active=False)
    assert v.allowed and v.code == "DRIVE_OFF" and not v.needs_approval


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("action", sorted(dm.ALWAYS))
@pytest.mark.parametrize("source", ["owner", "email", "web"])
def test_emergency_and_hangup_are_never_blocked(action, active, source):
    v = dm.decide(action, active=active, known_contact=False, source=source, by_voice=False)
    assert v.allowed and v.always


def test_emergency_works_with_a_full_queue(tmp_path):
    q = dm.DriveQueue(tmp_path / "q.json")
    for i in range(dm.MAX_QUEUE):
        assert q.add("settings", f"x{i}")
    v, item = dm.handle("hang_up", "", q, active=True)
    assert v.allowed and item is None
    v, item = dm.handle("settings", "one more", q, active=True)
    assert v.code == "DRIVE_QUEUE_FULL" and not v.allowed and item is None


@pytest.mark.parametrize("action", sorted(dm.ALLOWED))
def test_safe_actions_allowed_while_driving(action):
    v = dm.decide(action, active=True)
    assert v.allowed and v.code == "DRIVE_OK"
    assert v.needs_approval == (action == "navigate_link")      # a link is never opened without the owner


@pytest.mark.parametrize("action", ["call", "message_send"])
def test_call_and_message_rules(action):
    ok = dm.decide(action, active=True, known_contact=True)
    assert ok.allowed and ok.needs_approval and ok.confirm == "spoken_short"      # approvals unchanged
    assert dm.decide(action, active=True, known_contact=False).decision == "defer"
    assert dm.decide(action, active=True, known_contact=True, by_voice=False).decision == "defer"
    for src in dm.UNTRUSTED:
        assert dm.decide(action, active=True, known_contact=True, source=src).code == "DRIVE_UNTRUSTED"


@pytest.mark.parametrize("action", ["settings_change", "open_browser", "file_edit", "purchase", "read_email_aloud", "???"])
def test_everything_else_defers(action):
    v = dm.decide(action, active=True)
    assert v.decision == "defer" and not v.needs_approval


def test_queue_roundtrip_ttl_and_release(tmp_path):
    q = dm.DriveQueue(tmp_path / "sub" / "q.json")
    v, item = dm.handle("open_browser", "  look   up\nthe menu " + "x" * 500, q, active=True)
    assert v.decision == "defer" and item and item.id == 1 and len(item.text) == dm.MAX_TEXT and "\n" not in item.text
    assert q.add("file_edit", "b", now=1000.0) is not None
    assert [i.id for i in q.items()] == [1]                       # the 1970 entry expired
    assert [i.id for i in q.release()] == [1]
    assert q.items() == [] and q.clear() == 0


def test_corrupt_queue_file_is_empty_not_a_crash(tmp_path):
    p = tmp_path / "q.json"
    p.write_text("{not json", encoding="utf-8")
    q = dm.DriveQueue(p)
    assert q.items() == [] and q.add("a", "b").id == 1


def test_shorten_cuts_at_a_word():
    s = dm.shorten("word " * 200)
    assert len(s) <= dm.MAX_SPOKEN_CHARS + 3 and s.endswith("...")
    assert dm.shorten("short answer") == "short answer"


def test_texts_exist_in_both_languages():
    for code, (he, en) in dm.T.items():
        assert he and en, code
        assert dm.say(code, "he", n=2) and dm.say(code, "en", n=2)


def test_cli_on_off_status_queue(home, capsys):
    assert cli.main(["drive", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"drive_mode": "off", "waiting": 0}
    assert cli.main(["drive", "on"]) == 0
    capsys.readouterr()
    assert cli.main(["drive", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["drive_mode"] == "on"
    dm.queue_for(home).add("open_browser", "menu")
    assert cli.main(["drive", "queue"]) == 0
    assert "open_browser" in capsys.readouterr().out
    assert cli.main(["drive", "stopped", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["text"] == "menu"
    assert cli.main(["drive", "off"]) == 0
    capsys.readouterr()
    cli.main(["drive", "status", "--json"])
    assert json.loads(capsys.readouterr().out)["drive_mode"] == "off"
    assert cli.main(["drive", "clear"]) == 0


def test_catalog_row_is_honest_and_has_no_skill_or_tool():
    f = feat.CATALOG["drive_mode"]
    assert f.targets["windows"]["status"] == "implemented_unverified"
    assert f.risk == "low" and not f.skills and not f.tools
