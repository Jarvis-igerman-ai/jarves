"""changelog.d fragments: one file per change, merged under the unreleased heading by every reader, folded at release."""
from __future__ import annotations

import sys
from pathlib import Path

from jarves import changelog_parts as cp

ROOT = Path(__file__).resolve().parents[2]
CL = "# Changelog\n\nintro\n\n## 4.1.0 (unreleased)\n\n- Added: old one.\n\n## 4.0.0\n\n- Added: shipped.\n"


def _tool():
    sys.path.insert(0, str(ROOT / "core" / "tools"))
    import changelog_build
    return changelog_build


def _write(tmp_path, files):
    d = tmp_path / "changelog.d"
    d.mkdir()
    (tmp_path / "CHANGELOG.md").write_text(CL, encoding="utf-8")
    for n, t in files.items():
        (d / n).write_text(t, encoding="utf-8")
    return d


def test_fragments_go_newest_first_under_the_unreleased_heading(tmp_path):
    _write(tmp_path, {"2026-10-01-a.md": "- Added: a.\n", "2026-10-02-b.md": "- Fixed: b.\n", "README.md": "- Added: no\n",
                      "notes.md": "- Added: not a fragment\n"})
    text = cp.read(tmp_path / "CHANGELOG.md")
    assert text.index("- Fixed: b.") < text.index("- Added: a.") < text.index("- Added: old one.") < text.index("## 4.0.0")
    assert "not a fragment" not in text and "- Added: no" not in text
    assert text.index("## 4.1.0 (unreleased)") < text.index("- Fixed: b.")


def test_no_unreleased_heading_makes_one_and_no_fragments_changes_nothing(tmp_path):
    assert cp.merge("## 4.0.0\n\n- x\n", [("2026-10-01-a.md", "- Added: a.")]).startswith("## Unreleased\n\n- Added: a.\n\n## 4.0.0")
    assert cp.merge(CL, []) == CL
    assert cp.read(_write(tmp_path, {}).with_name("CHANGELOG.md")) == CL


def test_release_notes_and_the_fixes_tracking_see_the_fragments(tmp_path, monkeypatch):
    from jarves import issue_registry, release_notes
    _write(tmp_path, {"2026-10-01-a.md": "- Fixes: JI-1a2b3c4d\n- Added: shown in the notes.\n"})
    monkeypatch.chdir(tmp_path)
    text = release_notes.find_changelog()
    assert "shown in the notes" in text
    top = release_notes.parse(text)[0]
    assert top["status"] and any("shown in the notes" in i["text"] for i in top["items"])
    monkeypatch.setattr(issue_registry, "__file__", str(tmp_path / "pkg" / "x.py"), raising=False)
    assert "JI-1a2b3c4d" in issue_registry.changelog_text()


def test_write_folds_and_deletes_the_fragments(tmp_path, monkeypatch):
    tool = _tool()
    d = _write(tmp_path, {"2026-10-01-a.md": "- Added: a.\n"})
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    assert tool.main(["--write"]) == 0
    assert "- Added: a." in (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") and not list(d.glob("2026-*"))


def test_the_repository_fragments_are_well_formed():
    assert _tool().problems(ROOT / "changelog.d") == []


def test_check_rejects_a_bad_name_and_a_bad_line(tmp_path):
    d = _write(tmp_path, {"mine.md": "- Added: x\n", "2026-10-01-ok.md": "just prose\n"})
    bad = _tool().problems(d)
    assert len(bad) == 2 and "2026-10-01-ok.md:1" in bad[0] and "mine.md" in bad[1]
