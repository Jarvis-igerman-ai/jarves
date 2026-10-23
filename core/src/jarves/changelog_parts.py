"""CHANGELOG fragments: one small file per change instead of a line at the top of CHANGELOG.md.

Every PR added its bullet at the top of the unreleased section, so any two PRs in flight conflicted in CHANGELOG.md
(33 of ~150 merges touched it). Now a PR adds `changelog.d/<YYYY-MM-DD>-<topic>.md` holding its bullet lines
(`- Added: ...`, `- Fixed: ...`); a new file never conflicts. Readers (release notes, the issue registry's "Fixes:"
tracking, the manual test sheet) use `read()`, which puts the fragments, newest first, at the top of the first
"(unreleased)" section. `python core/tools/changelog_build.py --write` folds them into CHANGELOG.md for good (release time).
"""
from __future__ import annotations

import re
from pathlib import Path

FRAGMENT = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*\.md")


def fragments(directory: Path) -> list[tuple[str, str]]:
    """[(file name, bullet text)] newest first (file names start with the date; same day: reverse name order)."""
    if not directory.is_dir():
        return []
    out = []
    for p in sorted(directory.iterdir(), reverse=True):
        if FRAGMENT.fullmatch(p.name) and p.is_file():
            out.append((p.name, p.read_text(encoding="utf-8", errors="replace").strip()))
    return [(n, t) for n, t in out if t]


def merge(text: str, frags: list[tuple[str, str]]) -> str:
    """`text` (CHANGELOG.md) with the fragments inserted right under the first '## ... (unreleased)' heading, else
    under a new '## Unreleased' heading before the first version section."""
    if not frags:
        return text
    block = "\n".join(t for _, t in frags)
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and "unreleased" in ln.lower():
            return "\n".join([*lines[: i + 1], "", block, *lines[i + 1 :]])
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            return "\n".join([*lines[:i], "## Unreleased", "", block, "", *lines[i:]])
    return text.rstrip("\n") + "\n\n## Unreleased\n\n" + block + "\n"


def read(changelog: Path) -> str:
    """CHANGELOG.md with the `changelog.d` fragments next to it merged in."""
    return merge(changelog.read_text(encoding="utf-8", errors="replace"), fragments(changelog.with_name("changelog.d")))
