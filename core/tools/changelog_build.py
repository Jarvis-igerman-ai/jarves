"""Fold the CHANGELOG fragments (changelog.d/*.md) into CHANGELOG.md.

    python core/tools/changelog_build.py            # show what would be folded
    python core/tools/changelog_build.py --write    # release time: insert them under the unreleased heading, delete them
    python core/tools/changelog_build.py --check    # fragment file names and bullet lines are well formed (CI / prepush)

A PR adds `changelog.d/<YYYY-MM-DD>-<topic>.md` with its `- Added: ...` lines instead of editing CHANGELOG.md, so two PRs
never conflict there (jarves.changelog_parts explains the format and the readers).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "src"))
from jarves import changelog_parts  # noqa: E402

KINDS = ("Added", "Changed", "Fixed", "Fixes", "Docs", "Version", "Removed", "Security", "Deprecated")
BULLET = re.compile(r"- (?:" + "|".join(KINDS) + r")(?: \([a-z ]+\))?: \S")


def problems(directory: Path) -> list[str]:
    out = []
    if directory.is_dir():
        for p in sorted(directory.iterdir()):
            if p.name == "README.md" or p.is_dir():
                continue
            if not changelog_parts.FRAGMENT.fullmatch(p.name):
                out.append(f"{p.name}: name it <YYYY-MM-DD>-<topic>.md")
                continue
            for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if ln.strip() and not (BULLET.match(ln) or ln.startswith("  ")):    # indented = continuation of a bullet
                    out.append(f"{p.name}:{n}: a line starts with '- {'|'.join(KINDS)}: ' (or is indented)")
    return out


def main(argv: list[str]) -> int:
    root = ROOT
    d, cl = root / "changelog.d", root / "CHANGELOG.md"
    if argv == ["--check"]:
        bad = problems(d)
        print("\n".join(bad) or "changelog.d is well formed")
        return 1 if bad else 0
    frags = changelog_parts.fragments(d)
    if argv == ["--write"]:
        cl.write_text(changelog_parts.merge(cl.read_text(encoding="utf-8"), frags), encoding="utf-8", newline="\n")
        for name, _ in frags:
            (d / name).unlink()
        print(f"folded {len(frags)} fragment(s) into CHANGELOG.md")
        return 0
    for name, text in frags:
        print(f"== {name}\n{text}")
    print(f"{len(frags)} fragment(s) not yet folded")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
