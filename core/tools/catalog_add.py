"""Add dashboard catalog texts in one step (SW-V4 K): every English source key goes to locales/en.js (identity) and
its Hebrew to locales/he.js; patterns go to the "rules" of he.js. Keeps the strict-JSON format the locale tests parse.

    python tools/catalog_add.py texts.json     # {"dict": {"English": "עברית", ...}, "rules": [["^re$", "עברית $1"]]}
    python tools/catalog_add.py --sort         # re-sort and re-format both catalogs (after a hand edit)
    python tools/catalog_add.py --resolve      # in a merge with conflicts in en.js/he.js: merge them text by text

The "dict" entries are kept SORTED by key. Appending every new text at the end made any two PRs that add texts edit
the same last lines of en.js and he.js, so they always conflicted (10 of 21 PRs on 22-23 Oct touched them, #89);
sorted, a new text lands at its own place. The Hebrew "rules" keep their order (it matters: first match wins).
When the two sides of a merge still clash in these files, `--resolve` merges them per text: both sides' additions,
changes and removals are kept, and only a text that both sides changed differently is reported.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static" / "locales"


def parse(src: str) -> tuple[str, dict]:
    head, body = src.replace("\r\n", "\n").split("=\n", 1)
    return head + "=\n", json.loads(body.strip().rstrip(";"))


def load(code: str) -> tuple[str, dict]:
    return parse((LOCALES / f"{code}.js").read_text(encoding="utf-8"))


def sort_key(text: str) -> tuple[str, str]:
    return (text.casefold(), text)


def render(head: str, cat: dict) -> str:
    """The one canonical file text: texts sorted, indent 1. A file in any other layout rewrites every line of the
    diff, so it conflicts with every other PR that adds a text (test_dashboard_locales checks it)."""
    cat = {**cat, "dict": dict(sorted(cat["dict"].items(), key=lambda kv: sort_key(kv[0])))}
    return head + json.dumps(cat, ensure_ascii=False, indent=1) + ";\n"


def save(code: str, head: str, cat: dict) -> None:
    (LOCALES / f"{code}.js").write_text(render(head, cat), encoding="utf-8", newline="\n")


_GONE = object()


def merge3(base: dict, ours: dict, theirs: dict) -> tuple[dict, list[str]]:
    """Three-way merge of two catalogs per key ("dict" per text, "rules" per rule, the rest per field): a change made
    on one side wins, a change made the same way on both is kept once; a key both sides changed differently keeps
    OUR value and is listed as a clash."""
    out: dict = {}
    clashes: list[str] = []
    for k in [*ours, *(k for k in theirs if k not in ours), *(k for k in base if k not in ours and k not in theirs)]:
        b, o, t = base.get(k, _GONE), ours.get(k, _GONE), theirs.get(k, _GONE)
        if k == "dict" and all(isinstance(x, dict) for x in (o, t)):
            texts, more = merge3(b if isinstance(b, dict) else {}, o, t)
            out[k], clashes = texts, clashes + more
            continue
        if k == "rules" and all(isinstance(x, list) for x in (o, t)):      # per pattern; ours keep their order
            rules, more = merge3(dict(b) if isinstance(b, list) else {}, dict(o), dict(t))
            out[k], clashes = [[p, to] for p, to in rules.items()], clashes + [f"rule {p}" for p in more]
            continue
        v = t if o == b else o
        if o != b and t != b and o != t:
            clashes.append(k)
        if v is not _GONE:
            out[k] = v
    return out, clashes


def resolve(code: str) -> list[str]:
    """Resolve a merge conflict in locales/<code>.js from git's three stages (base, ours, theirs); returns clashes.
    Stages 2 and 3 exist only while the file is in conflict; a file without a conflict is left alone."""
    stage = {}
    for n in (1, 2, 3):
        r = subprocess.run(["git", "show", f":{n}:./{code}.js"], cwd=LOCALES, capture_output=True, text=True,
                           encoding="utf-8")
        stage[n] = parse(r.stdout) if r.returncode == 0 else None
    if stage[2] is None or stage[3] is None:
        return []
    base = stage[1][1] if stage[1] else {}
    merged, clashes = merge3(base, stage[2][1], stage[3][1])
    (LOCALES / f"{code}.js").write_text(render(stage[2][0], merged), encoding="utf-8", newline="\n")
    if not clashes:
        subprocess.run(["git", "add", f"{code}.js"], cwd=LOCALES, check=True)
    return clashes


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
    if sys.argv[1:] == ["--sort"]:
        for code in ("en", "he"):
            save(code, *load(code))
        print("sorted en.js and he.js")
        sys.exit(0)
    if sys.argv[1:] == ["--resolve"]:
        bad = {code: resolve(code) for code in ("en", "he")}
        for code, keys in bad.items():
            for k in keys:
                print(f"{code}.js: both sides changed {k!r} differently; kept ours, check it, then git add")
        print("resolved en.js and he.js" if not any(bad.values()) else "resolved, with the clashes above")
        sys.exit(1 if any(bad.values()) else 0)
    d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print("added/changed %d texts, %d rules" % add(d.get("dict", {}), d.get("rules", [])))
