"""SW-V4 K: every inline tx("עברית", "English") / L(...) / bi(...) pair in the dashboard scripts becomes a catalog key
(en.js identity, he.js = the inline Hebrew) when it is missing, so a future locale can translate it.

    python tools/catalog_sync.py          # prints what it added
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog_add  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static"
RX = re.compile(r'\b(?:tx|L|bi)\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"')

AX = re.compile(r'\[\s*(?:"[a-z_]+"\s*,\s*)?"([^"]*[֐-׿][^"]*)"\s*,\s*"([^"]*[A-Za-z][^"]*)"')   # [he, en] tables


def main() -> int:
    _, en = catalog_add.load("en")
    _, he = catalog_add.load("he")
    new: dict[str, str] = {}
    for js in sorted(STATIC.glob("*.js")):
        if js.name == "i18n.js":
            continue
        src = js.read_text(encoding="utf-8")
        for h, e in RX.findall(src) + AX.findall(src):
            h, e = json.loads(f'"{h}"'), json.loads(f'"{e}"')
            if e and h and (e not in en["dict"] or e not in he["dict"]):
                new.setdefault(e, he["dict"].get(e) or h)
    if new:
        catalog_add.add(new)
    for k, v in new.items():
        print(f"+ {k!r} -> {v!r}")
    print(f"{len(new)} keys added")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
