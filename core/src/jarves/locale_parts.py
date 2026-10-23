"""Dashboard catalog parts: one small JSON file per PR instead of edits in the shared en.js / he.js.

Every PR that added a dashboard text edited the same two sorted files, so two PRs with neighbouring texts always
conflicted (59 of ~150 merges touched them). Now `core/tools/catalog_add.py` writes the new texts to
`locales/parts/<date>-<topic>.json` (`{"dict": {"English": "עברית"}, "rules": [[pattern, hebrew]]}`): a new file per PR
never conflicts. The dashboard server, the self-test and the tests read the base catalog PLUS all parts (this module);
`catalog_add.py --fold` moves the parts into en.js / he.js when a release is cut. English is the identity: a part lists
the Hebrew once and the English key is its own English text.
"""
from __future__ import annotations

import json
from pathlib import Path

LOCALES = Path(__file__).with_name("dashboard_static") / "locales"


def parts_dir(locales: Path | None = None) -> Path:
    return (locales or LOCALES) / "parts"


def parse(src: str) -> tuple[str, dict]:
    head, body = src.replace("\r\n", "\n").split("=\n", 1)
    return head + "=\n", json.loads(body.strip().rstrip(";"))


def load_base(code: str, locales: Path | None = None) -> tuple[str, dict]:
    return parse(((locales or LOCALES) / f"{code}.js").read_text(encoding="utf-8"))


def load_parts(locales: Path | None = None) -> list[tuple[str, dict]]:
    """[(file stem, content)] in file-name order (the date prefix makes that the order they were added)."""
    d = parts_dir(locales)
    return [(p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def apply(code: str, cat: dict, parts: list[tuple[str, dict]]) -> dict:
    """The base catalog with every part added. English: identity; others: the part's translation. A later part wins
    for a text two parts both define; the rules of the parts follow the base rules, in part order."""
    out = {**cat, "dict": dict(cat["dict"])}
    rules = [list(r) for r in cat.get("rules", [])]
    for _, part in parts:
        for k, v in part.get("dict", {}).items():
            out["dict"][k] = k if code == "en" else v
        if code != "en":
            for pat, to in part.get("rules", []):
                for r in rules:
                    if r[0] == pat:
                        r[1] = to
                        break
                else:
                    rules.append([pat, to])
    if rules or "rules" in cat:
        out["rules"] = rules
    return out


def catalog(code: str, locales: Path | None = None) -> dict:
    return apply(code, load_base(code, locales)[1], load_parts(locales))


def parts_stamp(locales: Path | None = None) -> tuple:
    """Changes whenever a part is added, edited or removed (the cache key of the served catalog)."""
    d = parts_dir(locales)
    return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(d.glob("*.json"))) if d.is_dir() else ()


def render_js(code: str, locales: Path | None = None) -> bytes:
    """locales/<code>.js as the browser gets it: the file's own header line, then the merged catalog (same layout)."""
    head, base = load_base(code, locales)
    merged = apply(code, base, load_parts(locales))
    merged["dict"] = dict(sorted(merged["dict"].items(), key=lambda kv: (kv[0].casefold(), kv[0])))
    return (head + json.dumps(merged, ensure_ascii=False, indent=1) + ";\n").encode("utf-8")
