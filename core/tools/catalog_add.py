"""Add dashboard catalog texts in one step (SW-V4 K): every English source key goes to locales/en.js (identity) and
its Hebrew to locales/he.js; patterns go to the "rules" of he.js. Keeps the strict-JSON format the locale tests parse.

    python tools/catalog_add.py texts.json     # {"dict": {"English": "עברית", ...}, "rules": [["^re$", "עברית $1"]]}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static" / "locales"


def load(code: str) -> tuple[str, dict]:
    src = (LOCALES / f"{code}.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    head, body = src.split("=\n", 1)
    return head + "=\n", json.loads(body.strip().rstrip(";"))


def save(code: str, head: str, cat: dict) -> None:
    (LOCALES / f"{code}.js").write_text(head + json.dumps(cat, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8",
                                        newline="\n")


def add(texts: dict[str, str], rules: list | None = None) -> tuple[int, int]:
    he_head, he = load("he")
    en_head, en = load("en")
    n = 0
    for k, v in texts.items():
        if not v:
            raise SystemExit(f"empty Hebrew for {k!r}")
        if he["dict"].get(k) != v or k not in en["dict"]:
            n += 1
        en["dict"][k] = k
        he["dict"][k] = v
    r = 0
    have = {p for p, _ in he.get("rules", [])}
    for p, to in rules or []:
        if p in have:
            he["rules"] = [[q, to if q == p else t] for q, t in he["rules"]]
        else:
            he.setdefault("rules", []).append([p, to])
            r += 1
    save("en", en_head, en)
    save("he", he_head, he)
    return n, r


if __name__ == "__main__":
    d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print("added/changed %d texts, %d rules" % add(d.get("dict", {}), d.get("rules", [])))
