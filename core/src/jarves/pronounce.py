"""User-editable pronunciation dictionary: how names are SPOKEN (cloud TTS text + the live voice persona note).

Lives in the workspace file PRONUNCIATIONS.md (dashboard Files page / the workspace-edit skill). One entry per line:

    צחי = צַ֫חִי          (word = spoken form: vocalized Hebrew with niqqud, or a phonetic respelling)
    Jarvis = ג'ארוויס

Hebrew words match with the common one-letter prefixes (ל ו ב כ מ ה ש, also stacked: ולצחי, מהצחי): the prefix is
stripped, the stem replaced, and the prefix re-attached with niqqud when the spoken form is vocalized.
"""
from __future__ import annotations

import re
from pathlib import Path

NIQQUD = re.compile(r"[֑-ׇ]")
_ENTRY = re.compile(r"^\s*(?:[-*]\s*)?(.+?)\s*(?:=>|->|=|→)\s*(.+?)\s*$")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_WORD = re.compile(r"[֐-׿][֐-׿'’]*|[A-Za-z][A-Za-z'’]*")
PREFIXES = "לובכמהש"
FILE = "PRONUNCIATIONS.md"

DEFAULT_ENTRIES: dict[str, str] = {}   # owner decision 20 Oct: no shipped entries (the plain voice sounded better)
DEFAULT_TEXT = ("# הגייה / Pronunciations\n"
                "<!-- Empty on purpose: the voice sounds best without entries unless a name is mispronounced.\n"
                "One entry per line: word = how to say it (Hebrew with niqqud, or a phonetic respelling). "
                "Prefixes ל ו ב כ מ ה ש are handled automatically. Applies to every spoken reply and the live voice.\n"
                "Example (remove the leading # to enable):\n"
                "# - Dana = דָּנָה\n"
                "-->\n")


def strip_niqqud(s: str) -> str:
    return NIQQUD.sub("", s)


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _COMMENT.sub("", text).splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ENTRY.match(line)
        if m and m.group(1).strip() and m.group(2).strip():
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _key(w: str) -> str:
    return strip_niqqud(w).lower()


def prefix_vowel(letter: str, stem: str) -> str:
    """Prefix letter with its niqqud, given the (vocalized) spoken stem that follows it."""
    first = stem[:1]
    shva_start = len(stem) > 1 and stem[1] == "ְ"
    if len(stem) > 1 and stem[1] in "ֱֲֳ" and letter in "לבכ":   # hataf (אֲ): prefix takes the matching full vowel
        v = {"ֲ": "ַ", "ֱ": "ֶ", "ֳ": "ָ"}[stem[1]]
        return letter + (("ּ" if letter != "ל" else "") + v)
    if letter == "ו":
        return "וּ" if (first in "בומפ" or shva_start) else "וְ"
    if letter == "ל":
        return "לִ" if (first == "י" or shva_start) else "לְ"
    if letter == "ב":
        return "בִּ" if (first == "י" or shva_start) else "בְּ"
    if letter == "כ":
        return "כִּ" if (first == "י" or shva_start) else "כְּ"
    return {"מ": "מִ", "ה": "הַ", "ש": "שֶׁ"}.get(letter, letter)


def attach(prefix: str, spoken: str) -> str:
    """Re-attach stripped prefix letters: niqqud'd when the spoken form is vocalized, plain letters otherwise."""
    if not prefix:
        return spoken
    if not NIQQUD.search(spoken):
        return prefix + spoken
    out = spoken
    for ch in reversed(prefix):                  # right to left: each prefix letter sees the vocalized rest
        out = prefix_vowel(ch, out) + out
    return out


class Dictionary:
    def __init__(self, entries: dict[str, str]) -> None:
        self.entries = dict(entries)
        self.index = {_key(k): v for k, v in entries.items()}

    def lookup(self, word: str) -> str | None:
        k = _key(word)
        if k in self.index:
            return self.index[k]
        if not ("א" <= k[:1] <= "ת"):
            return None
        for n in (1, 2, 3):
            pre, stem = k[:n], k[n:]
            if len(stem) < 2 or any(c not in PREFIXES for c in pre):
                break
            if stem in self.index:
                return attach(pre, self.index[stem])
        return None

    def apply(self, text: str) -> str:
        if not self.index:
            return text
        return _WORD.sub(lambda m: self.lookup(m.group(0)) or m.group(0), text)

    def note(self) -> str:
        """Short pronunciation note for the live voice persona."""
        pairs = "; ".join(f"{k} = {v}" for k, v in self.entries.items() if k != v)
        return ("Pronunciation: say these exactly so, also with prefixes ל/ו/ב/כ/מ/ה/ש (e.g. a prefixed name keeps "
                f"its vowels): {pairs}") if pairs else ""


_path: Path | None = None
_cache: tuple[Path, tuple[int, int], Dictionary] | None = None


def set_path(p: Path | str | None) -> None:
    """Pin the dictionary file (tests / the Core); None = <state_dir>/workspace/PRONUNCIATIONS.md."""
    global _path, _cache
    _path, _cache = (Path(p) if p else None), None


def current_path() -> Path:
    if _path is not None:
        return _path
    from .config import state_dir
    return state_dir() / "workspace" / FILE


def load() -> Dictionary:
    """The current dictionary; re-read only when the file changed. Missing file -> built-in defaults."""
    global _cache
    try:
        p = current_path()
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
        if _cache and _cache[0] == p and _cache[1] == key:
            return _cache[2]
        d = Dictionary(parse(p.read_text(encoding="utf-8", errors="replace")))
        _cache = (p, key, d)
        return d
    except Exception:
        return Dictionary(DEFAULT_ENTRIES)


def apply(text: str) -> str:
    try:
        return load().apply(text)
    except Exception:
        return text
