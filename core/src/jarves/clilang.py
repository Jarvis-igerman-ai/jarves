"""The ONE language switch for what the CLI wizards and commands PRINT (owner request 20 Oct 2026, Issue #17).

'en' (default) = English only; 'he' = Hebrew only; 'both' = Hebrew line then English line (the old behaviour).
Source order: env JARVES_CLI_LANG, then the setting `ui.cli_language` (memory.db override, editable in the
dashboard Settings), then 'en'. It does NOT touch the voice, the dashboard language, the guides or log files.

Every bilingual print goes through `fmt(he, en)` / `say(he, en)`. A text that exists in one language only is
passed with the other empty: it is shown whenever its own language is selected; in 'en' an empty English side
falls back to the Hebrew text (never silently dropped)."""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Callable

SETTING = "ui.cli_language"
VALUES = ("en", "he", "both")
DEFAULT = "en"
_HEB = re.compile(r"[֐-׿]")
_home: Path | None = None
_cache: dict[str, str] = {}


def set_home(home: Path | str | None) -> None:
    """Tell the module where memory.db lives (called once by the CLI entry point)."""
    global _home
    _home = Path(home) if home else None
    _cache.clear()


def _from_setting() -> str | None:
    if _home is None:
        return None
    if "v" not in _cache:
        v = ""
        p = _home / "memory.db"
        if p.exists():
            try:
                con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=1)
                try:
                    r = con.execute("SELECT value FROM settings WHERE key = ?", ("override:" + SETTING,)).fetchone()
                    v = (r[0] if r else "") or ""
                finally:
                    con.close()
            except sqlite3.Error:
                v = ""
        _cache["v"] = v
    return _cache["v"] or None


def current() -> str:
    for v in (os.environ.get("JARVES_CLI_LANG"), _from_setting()):
        v = (v or "").strip().lower()
        if v in VALUES:
            return v
    return DEFAULT


def fmt(he: str, en: str = "") -> str:
    mode = current()
    if mode == "he":
        return he or en
    if mode == "both":
        return "\n".join(p for p in (he, en) if p)
    return en or he


def pick(he: str, en: str) -> str:
    """One language for a prompt fragment ('both' shows the Hebrew first, joined by ' / ')."""
    mode = current()
    if mode == "he":
        return he or en
    if mode == "both":
        return f"{he} / {en}" if he and en else (he or en)
    return en or he


def bi(text: str) -> str:
    """Pick a language inside an inline 'עברית / English' string, line by line. A line is split at a ' / ' whose left
    side holds Hebrew and whose right side holds none; every other line is left as it is."""
    mode = current()
    if mode == "both":
        return text
    lines = []
    for line in text.split("\n"):
        pos = -1
        while True:
            pos = line.find(" / ", pos + 1)
            if pos < 0:
                break
            left, right = line[:pos], line[pos + 3:]
            if _HEB.search(left) and not _HEB.search(right):
                lead = left[:len(left) - len(left.lstrip())]
                line = lead + (left.strip() if mode == "he" else right)
                break
        lines.append(line)
    return "\n".join(lines)


_TELL = re.compile(r';?\s*tell the user: "([^"]*)"')


def reason(text: str) -> str:
    """A feature `reason` for the CLI: the model-facing 'tell the user: "<Hebrew>"' hint becomes the bare command
    ('run: jarvis google start') unless the CLI language shows Hebrew."""
    if current() != "en":
        return text

    def sub(m: re.Match) -> str:
        if not _HEB.search(m.group(1)):
            return m.group(0)
        cmd = re.search(r"jarvis [a-z_]+(?: [a-z_-]+)*", m.group(1))
        return f"; run: {cmd.group(0)}" if cmd else ""
    return _TELL.sub(sub, text)


def say(he: str, en: str = "", out: Callable[[str], None] | None = None) -> None:
    (out or (lambda s: print(s, flush=True)))(fmt(he, en))


def has_hebrew(text: str) -> bool:
    return bool(_HEB.search(text))
