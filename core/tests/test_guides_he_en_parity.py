"""EN/HE key parity for every guide toml (JAR-162): each *_he has a non-empty *_en and vice versa, in the top
table and in every [[steps]]/[[problems]] entry; each `say` pair has two non-empty strings."""
import tomllib
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "src" / "jarves" / "guides" / "data"
FILES = sorted(DATA.glob("*.toml"))


def _pair_gaps(table: dict, where: str) -> list[str]:
    out = []
    for k, v in table.items():
        for a, b in (("_he", "_en"), ("_en", "_he")):
            if k.endswith(a):
                other = k[: -len(a)] + b
                if not (isinstance(v, str) and v.strip()) or not (isinstance(table.get(other), str) and table[other].strip()):
                    out.append(f"{where}: {k} / {other}")
    if ("he" in table) != ("en" in table):
        out.append(f"{where}: he/en")
    for k in ("he", "en", "fix_he", "fix_en"):
        if k in table and not (isinstance(table[k], str) and table[k].strip()):
            out.append(f"{where}: empty {k}")
    if ("fix_he" in table) != ("fix_en" in table):
        out.append(f"{where}: fix_he/fix_en")
    return out


def test_guide_files_found():
    assert len(FILES) > 20


def test_guides_he_en_key_parity():
    gaps = []
    for f in FILES:
        d = tomllib.loads(f.read_text(encoding="utf-8"))
        gaps += _pair_gaps(d, f.name)
        for sect in ("steps", "problems"):
            for i, t in enumerate(d.get(sect, [])):
                gaps += _pair_gaps(t, f"{f.name} {sect}[{i}]")
        for i, s in enumerate(d.get("say", [])):
            if not (isinstance(s, list) and len(s) == 2 and all(isinstance(x, str) and x.strip() for x in s)):
                gaps.append(f"{f.name} say[{i}]")
    assert not gaps, "EN/HE drift:\n" + "\n".join(gaps)


def test_parity_checker_detects_drift():
    assert _pair_gaps({"title_he": "x"}, "t") and _pair_gaps({"he": "x"}, "t") and not _pair_gaps({"a_he": "x", "a_en": "y"}, "t")
