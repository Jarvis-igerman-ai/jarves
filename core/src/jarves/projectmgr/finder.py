"""Find a project folder on THIS computer from spoken words ("the folder work, project xy"). Local disk only: a bounded
walk of the usual places (or `projects.search_roots`) for git folders whose name matches; nothing leaves the PC."""
from __future__ import annotations

import os
import re
from pathlib import Path

SKIP = {"node_modules", ".git", ".venv", "venv", "__pycache__", "AppData", "Library", ".cache", ".npm", ".cargo",
        "site-packages", "dist", "build", ".jarves", "Windows", "Program Files", "Program Files (x86)", "$Recycle.Bin"}
COMMON = ("", "work", "Work", "projects", "Projects", "src", "code", "dev", "repos", "git", "source/repos",
          "Documents", "Desktop", "OneDrive")
MAX_DEPTH, MAX_DIRS = 4, 6000


def norm(s: str) -> str:
    return re.sub(r"[\s_\-.]+", "", str(s or "").casefold())


def default_roots() -> list[Path]:
    home = Path.home()
    seen, out = set(), []
    for c in COMMON:
        p = home / c if c else home
        if p.is_dir() and str(p) not in seen:
            seen.add(str(p))
            out.append(p)
    return out


def find(name: str, hint: str = "", roots: list[str] | None = None) -> list[str]:
    """Git folders named like `name` (spaces, dashes, case ignored); with `hint` (e.g. "work") the ones whose path
    contains it come first. Bounded: depth 4 below each root, 6000 folders at most."""
    want, h = norm(name), norm(hint)
    if not want:
        return []
    bases = [Path(os.path.expanduser(r)) for r in roots] if roots else default_roots()
    hits: list[str] = []
    seen = 0
    for base in bases:
        stack = [(base, 0)]
        while stack and seen < MAX_DIRS:
            d, depth = stack.pop()
            seen += 1
            try:
                entries = [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for e in entries:
                if e.name in SKIP or e.name.startswith("."):
                    continue
                if norm(e.name) == want and os.path.isdir(os.path.join(e.path, ".git")) or (
                        norm(e.name) == want and os.path.isfile(os.path.join(e.path, ".git"))):
                    if e.path not in hits:
                        hits.append(e.path)
                elif depth + 1 < MAX_DEPTH:
                    stack.append((Path(e.path), depth + 1))
    if h:
        hits.sort(key=lambda p: (h not in norm(p), len(p)))
    return hits[:8]
