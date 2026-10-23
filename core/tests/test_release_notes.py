"""Release notes: CHANGELOG parsing, HTML escaping, the API, the CLI and the installer wiring. Offline, no sensors."""
from __future__ import annotations

from pathlib import Path

from jarves import cli, release_notes as rn

TEXT = """# Changelog

## 4.1.0 (unreleased)

Intro line.

- Added: first thing with `<b>` tag
  continued line.
- Fixed (issue 5): a bug.

## 4.0.0 (release candidate, not yet released)

- Changed: old thing.
"""
ROOT = Path(__file__).resolve().parents[2]


def test_parse_sections_and_kinds():
    s = rn.parse(TEXT)
    assert [x["version"] for x in s] == ["4.1.0", "4.0.0"]
    assert s[0]["status"] == "unreleased" and s[0]["intro"] == "Intro line."
    assert s[0]["items"][0] == {"kind": "Added", "scope": "", "text": "first thing with `<b>` tag continued line."}
    assert s[0]["items"][1]["kind"] == "Fixed" and s[0]["items"][1]["scope"] == "issue 5"


def test_pick_maps_a_beta_to_its_stable_version():
    s = rn.parse(TEXT)
    assert rn.pick(s, "4.1.0-beta.7")["version"] == "4.1.0"
    assert rn.pick(s, None)["version"] == "4.1.0"
    assert rn.pick(s, "9.9.9") is None


def test_html_is_escaped_and_hebrew_is_rtl():
    out = rn.render_html(rn.parse(TEXT), "4.1.0")
    assert "<b>" not in out and "&lt;b&gt;" in out and "4.0.0" in out
    he = rn.render_html(rn.parse(TEXT), "4.1.0", "he")
    assert "dir='rtl'" in he and "נוסף" in he


def test_api_lists_versions_and_rejects_unknown(monkeypatch):
    monkeypatch.setattr(rn, "find_changelog", lambda: TEXT)
    st, d = rn.api_release_notes(None, None, None, {})
    assert st == 200 and [v["version"] for v in d["versions"]] == ["4.1.0", "4.0.0"] and d["selected"]
    assert rn.api_release_notes(None, None, None, {"version": ["0.0.1"]})[0] == 404


def test_cli_writes_the_page_and_opens_it(home, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(rn.webbrowser, "open", lambda u: opened.append(u))
    assert cli.main(["release-notes", "--open"]) == 0
    assert len(list((home / "release-notes").glob("release-notes-*.html"))) == 1 and opened
    assert cli.main(["release-notes", "--version", "0.0.1"]) == 1
    assert cli.main(["release-notes", "--list"]) == 0


def test_real_changelog_has_the_current_version():
    from jarves import __version__
    assert rn.pick(rn.parse((ROOT / "CHANGELOG.md").read_text(encoding="utf-8")), __version__)


def test_installer_ships_and_opens_the_notes():
    iss = (ROOT / "packaging" / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    assert "release-notes --open" in iss and "skipifsilent" in iss
    assert "CHANGELOG.md" in (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
