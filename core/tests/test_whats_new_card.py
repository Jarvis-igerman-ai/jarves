"""'What changed since your version' card (#167): only the releases between the previous and the current one, dismiss
remembered, settings untouched. Hermetic: tmp home, explicit versions and an inline CHANGELOG; no clock, no network."""
from __future__ import annotations

import argparse
import json

from jarves import features as feat
from jarves import release_notes
from jarves import self_aware
from jarves import updates
from jarves import whats_new_card as wn

CL = """# Changelog

## 4.6.0 (beta)
- Added: Phone pairing by QR code.
- Fixed (voice): The wake word no longer triggers on TV.
- Docs: developer-workflow notes, nothing changes for users.

## 4.5.2
- Fixed: Music resumes after a call.

## 4.5.1
- Added: Calendar reminders.

## 4.5.0
- Added: Something old.
"""
SECS = release_notes.parse(CL)


def test_off_by_default_and_beta():
    assert feat.CATALOG[wn.FEATURE].default_enabled is False
    assert feat.stage_of(wn.FEATURE) == "beta"


def test_changes_cover_only_releases_after_the_previous_one():
    ch = wn.changes(SECS, "4.5.1", "4.6.0")
    assert ch["versions"] == ["4.6.0", "4.5.2"]
    texts = [i["text"] for i in ch["items"]]
    assert texts == ["Phone pairing by QR code.", "The wake word no longer triggers on TV.", "Music resumes after a call."]
    assert not any("Calendar" in t or "developer" in t for t in texts)
    assert ch["more"] == 0


def test_limit_and_more_count():
    ch = wn.changes(SECS, "4.4.0", "4.6.0", limit=2)
    assert len(ch["items"]) == 2 and ch["more"] == 3


def test_fresh_install_shows_no_card_then_update_shows_one(tmp_path):
    assert wn.card(tmp_path, "4.5.1", SECS) is None                     # first look: nothing to compare with
    c = wn.card(tmp_path, "4.6.0-beta.2", SECS)                         # updated
    assert c and c["from"] == "4.5.1" and c["to"] == "4.6.0"
    assert [i["text"] for i in c["items"]][-1] == "Music resumes after a call."


def test_beta_builds_of_one_release_show_no_card(tmp_path):
    wn.card(tmp_path, "4.6.0-beta.1", SECS)
    assert wn.card(tmp_path, "4.6.0-beta.2", SECS) is None


def test_dismiss_until_next_release(tmp_path):
    wn.card(tmp_path, "4.5.0", SECS)
    assert wn.card(tmp_path, "4.5.2", SECS)
    assert wn.dismiss(tmp_path, "4.5.2") == {"dismissed": "4.5.2"}
    assert wn.card(tmp_path, "4.5.2", SECS) is None
    c = wn.card(tmp_path, "4.6.0", SECS)
    assert c and c["from"] == "4.5.2"


def test_two_updates_without_dismiss_keep_the_older_start(tmp_path):
    wn.card(tmp_path, "4.5.0", SECS)
    wn.card(tmp_path, "4.5.1", SECS)
    c = wn.card(tmp_path, "4.6.0", SECS)
    assert c["from"] == "4.5.0" and "4.5.1" in [release_notes.stable_of(v) for v in c["versions"]]


def test_rollback_record_gives_the_previous_version_on_first_look(tmp_path):
    pdir = updates._updates_dir(tmp_path) / updates.PREVIOUS_DIR
    pdir.mkdir(parents=True)
    (pdir / updates.PREVIOUS_FILE).write_text(json.dumps({"version": "4.5.1", "to": "4.6.0-beta.3"}), encoding="utf-8")
    c = wn.card(tmp_path, "4.6.0-beta.3", SECS)
    assert c and c["from"] == "4.5.1"


def test_only_version_strings_are_stored(tmp_path):
    wn.card(tmp_path, "4.5.0", SECS)
    wn.card(tmp_path, "4.5.2", SECS)
    assert wn.read_state(tmp_path) == {"last": "4.5.2", "from": "4.5.0"}


def test_cli_since_and_dismiss(tmp_path, monkeypatch):
    monkeypatch.setattr(release_notes, "sections", lambda text=None: SECS)
    monkeypatch.setattr(wn, "__version__", "4.6.0")
    out: list[str] = []
    a = argparse.Namespace(version=None, he=False, since="4.5.1", dismiss=False)
    assert self_aware.cmd_whatsnew(a, None, tmp_path, out=out.append) == 0
    assert out[0] == "What changed since 4.5.1:" and any("QR code" in x for x in out)
    out.clear()
    a = argparse.Namespace(version=None, he=True, since=None, dismiss=True)
    assert self_aware.cmd_whatsnew(a, None, tmp_path, out=out.append) == 0
    assert wn.read_state(tmp_path)["dismissed"]
    ok, lines = wn.since_lines("nope", secs=SECS)
    assert ok is False
