"""Add dashboard catalog texts in one step (SW-V4 K): every English source key goes to locales/en.js (identity) and
its Hebrew to locales/he.js; patterns go to the "rules" of he.js. Keeps the strict-JSON format the locale tests parse.

    python tools/catalog_add.py texts.json [--part NAME]
                                               # {"dict": {"English": "עברית", ...}, "rules": [["^re$", "עברית $1"]]}
                                               # -> locales/parts/<date>-<NAME or file name>.json (a NEW file per PR)
    python tools/catalog_add.py --fold         # release time: move every part into en.js / he.js, delete the parts
    python tools/catalog_add.py --sort         # re-sort and re-format both catalogs (after a hand edit)
    python tools/catalog_add.py --resolve      # in a merge with conflicts in en.js/he.js: merge them text by text

New texts go to a PART file (src/jarves/locale_parts.py reads the base catalogs plus all parts), so two PRs never edit
the same file: even sorted, two texts with the same neighbours in en.js conflicted (59 of ~150 merges touched it).
`--fold` keeps the base files current; a text already in the base or in another part is changed THERE.

The "dict" entries of en.js / he.js are kept SORTED by key. Appending every new text at the end made any two PRs that add texts edit
the same last lines of en.js and he.js, so they always conflicted (10 of 21 PRs on 22-23 Oct touched them, #89);
sorted, a new text lands at its own place. The Hebrew "rules" keep their order (it matters: first match wins).
When the two sides of a merge still clash in these files, `--resolve` merges them per text: both sides' additions,
changes and removals are kept, and only a text that both sides changed differently is reported.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jarves import locale_parts  # noqa: E402

LOCALES = locale_parts.LOCALES


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


def load_merged(code: str) -> tuple[str, dict]:
    """Base catalog plus every part: what the dashboard shows."""
    head, base = load(code)
    return head, locale_parts.apply(code, base, locale_parts.load_parts(LOCALES))


def part_file(parts: list[tuple[str, dict]], name: str) -> str:
    """The part named `name`: the one a PR already started (any date), else a new `<date>-<name>`."""
    stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "texts"
    old = [p for p, _ in parts if p == stem or p.endswith("-" + stem)]
    return old[-1] if old else f"{datetime.now(timezone.utc):%Y-%m-%d}-{stem}"


def add(texts: dict[str, str], rules: list | None = None, part: str = "texts") -> tuple[int, int]:
    """Add Hebrew texts / rules. A text that already lives in en.js / he.js or in another part is changed THERE
    (one home per text); everything new goes to the part file `parts/<date>-<part>.json` (created once, then edited)."""
    he_head, he = load("he")
    en_head, en = load("en")
    parts = dict(locale_parts.load_parts(LOCALES))
    mine_name = part_file(list(parts.items()), part)
    parts.setdefault(mine_name, {"dict": {}})
    touched, base_changed, n, r = {mine_name}, False, 0, 0
    home = {k: p for p, c in parts.items() for k in c.get("dict", {})}
    for k, v in texts.items():
        if not v:
            raise SystemExit(f"empty Hebrew for {k!r}")
        if k in en["dict"] or k in he["dict"]:
            n += he["dict"].get(k) != v or k not in en["dict"]
            en["dict"][k], he["dict"][k], base_changed = k, v, True
        else:
            where = home.get(k, mine_name)
            n += parts[where]["dict"].get(k) != v
            parts[where]["dict"][k] = v
            touched.add(where)
    in_part = {p: name for name, c in parts.items() for p, _ in c.get("rules", [])}
    in_base = {p for p, _ in he.get("rules", [])}
    for p, to in rules or []:
        if p in in_base:
            he["rules"] = [[q, to if q == p else t] for q, t in he["rules"]]
            base_changed = True
        elif p in in_part:
            c = parts[in_part[p]]
            c["rules"] = [[q, to if q == p else t] for q, t in c["rules"]]
            touched.add(in_part[p])
        else:
            parts[mine_name].setdefault("rules", []).append([p, to])
            r += 1
    for name in touched:
        c = parts[name]
        if c["dict"] or c.get("rules"):
            c["dict"] = dict(sorted(c["dict"].items(), key=lambda kv: sort_key(kv[0])))
            _write_part(name + ".json", c)
    if base_changed:
        save("en", en_head, en)
        save("he", he_head, he)
    return n, r


def _write_part(name: str, content: dict) -> None:
    d = locale_parts.parts_dir(LOCALES)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(content, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def fold() -> int:
    """Release time: move every part into the sorted base catalogs and delete the part files; returns the part count."""
    parts = locale_parts.load_parts(LOCALES)
    for code in ("en", "he"):
        head, base = load(code)
        save(code, head, locale_parts.apply(code, base, parts))
    for p in locale_parts.parts_dir(LOCALES).glob("*.json"):
        p.unlink()
    return len(parts)


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
    if sys.argv[1:] == ["--fold"]:
        print("folded %d part file(s) into en.js and he.js" % fold())
        sys.exit(0)
    args = sys.argv[1:]
    name = Path(args[0]).stem
    if "--part" in args:
        name = args[args.index("--part") + 1]
    d = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    print("added/changed %d texts, %d rules" % add(d.get("dict", {}), d.get("rules", []), name))
