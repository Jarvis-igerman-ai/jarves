"""Decide whether a Dependabot PR may be auto-merged: patch and minor bumps yes, major (and anything unclear) no.

    python core/tools/dependabot_gate.py --title "Bump x from 1.2.3 to 1.2.4" [--body TEXT]    # prints patch|minor|major|unknown|group

Exit code 0 = eligible for the `automerge` label (.github/workflows/automerge.yml then puts it in the merge queue
only when ALL checks are green), 1 = not eligible. Pure text parsing: no network, no checkout of the PR's code. A grouped
update ("Bump the pip group ... with 3 updates") is eligible only when the body lists no major bump. Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys

VERSION = r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
SINGLE = re.compile(rf"\bfrom {VERSION}\S* to {VERSION}\S*", re.I)
PAIR = re.compile(rf"from `?{VERSION}\S*?`? to `?{VERSION}", re.I)


def bump_kind(old: tuple, new: tuple) -> str:
    o = [int(x or 0) for x in old]
    n = [int(x or 0) for x in new]
    if n[0] != o[0]:
        return "major" if n[0] > o[0] else "unknown"
    if n[1] != o[1]:
        # 0.x versions: a minor step is breaking by semver convention
        return "major" if o[0] == 0 and n[1] > o[1] else ("minor" if n[1] > o[1] else "unknown")
    return "patch" if n[2] > o[2] else "unknown"


def classify(title: str, body: str = "") -> str:
    """patch, minor, major, group (every bump in the body is patch/minor) or unknown."""
    m = SINGLE.search(title)
    if m:
        g = m.groups()
        return bump_kind(g[:3], g[3:])
    if re.search(r"\bwith \d+ updates?\b|\bgroup\b", title, re.I):
        kinds = {bump_kind(p[:3], p[3:]) for p in PAIR.findall(body)} or {"unknown"}
        return "group" if kinds <= {"patch", "minor"} else ("major" if "major" in kinds else "unknown")
    return "unknown"


def eligible(kind: str) -> bool:
    return kind in ("patch", "minor", "group")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dependabot_gate.py")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    a = ap.parse_args(argv)
    kind = classify(a.title, a.body)
    print(kind)
    return 0 if eligible(kind) else 1


if __name__ == "__main__":
    sys.exit(main())
