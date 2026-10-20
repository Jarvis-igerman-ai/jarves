"""Locales for the SERVER-supplied fixed texts the dashboard shows (SW-V3 P0.2c, issue #17).

English is the default and the source of truth: every module keeps its English text and the API adds a
`<field>_<locale>` sibling next to it for each registered locale that has a translation (about_he, label_he ...).
The dashboard (dashboard_static/i18n.js `txh` / `txo`) shows the sibling of the chosen locale and falls back to
the English field, which its own client catalog may still translate.

Adding a server locale = one module with `text(english) -> str | None` (see hebrew.py, the first one) and one entry
in TRANSLATORS below. The client side is separate: one catalog file in dashboard_static/locales/ plus its <script>
tag in index.html. Only OUR OWN fixed texts are translated; user content, quoted speech, logs, file names, commands
and code never get a sibling.
"""
from __future__ import annotations

import re
from typing import Callable

from . import hebrew

DEFAULT = "en"
# locale -> translator for our fixed English texts (None = no translation: the dashboard keeps the English)
TRANSLATORS: dict[str, Callable[[object], str | None]] = {
    "he": hebrew.he,
}


# a model-facing hint inside an English reason: '...; tell the user: "<Hebrew sentence to say>"' (the voice says it)
_TELL = re.compile(r';?\s*tell the user: "([^"]*)"')
_HEB = re.compile(r"[\u0590-\u05FF]")


def display_en(s) -> str | None:
    """The English DISPLAY form of a fixed text when it differs from the source (SW-V4 K, D-11): the spoken Hebrew hint
    becomes the bare command ('...; run: jarvis google start'). None when the source is already plain English."""
    if not isinstance(s, str) or not _HEB.search(s):
        return None

    def sub(m: re.Match) -> str:
        if not _HEB.search(m.group(1)):
            return m.group(0)
        cmd = re.search(r"jarvis [a-z_]+(?: [a-z_-]+)*", m.group(1))
        return f"; run: {cmd.group(0)}" if cmd else ""
    out = _TELL.sub(sub, s)
    return out if out != s else None


def text(locale: str, s) -> str | None:
    """`s` in `locale`, or None (English, an unknown locale or no translation: the caller keeps the English)."""
    fn = TRANSLATORS.get(locale)
    return fn(s) if fn else None


def siblings(key: str, s) -> dict:
    """{"<key>_he": ..., ...} for one fixed English text (only the locales that translate it)."""
    out = {}
    en = display_en(s)
    if en:
        out[f"{key}_en"] = en
    for loc, fn in TRANSLATORS.items():
        t = fn(s)
        if t:
            out[f"{key}_{loc}"] = t
    return out


def attach(obj: dict, *keys: str) -> dict:
    """Add `<key>_<locale>` next to each translatable string field of `obj` (in place; returns obj)."""
    for k in keys:
        obj.update(siblings(k, obj.get(k)))
    return obj


def attach_list(obj: dict, key: str) -> dict:
    """`<key>_<locale>` = a parallel list for a list of fixed strings (None entries fall back to English)."""
    if isinstance(obj.get(key), list):
        for loc, fn in TRANSLATORS.items():
            obj[f"{key}_{loc}"] = [fn(x) for x in obj[key]]
    return obj


def list_siblings(key: str, items) -> dict:
    """{"<key>_he": [...]} parallel lists for a list of fixed English texts."""
    return {f"{key}_{loc}": [fn(x) for x in items] for loc, fn in TRANSLATORS.items()}


def issue(row: dict) -> dict:
    """title_<locale> / detail_<locale> for a stored issue; quoted user text inside is kept verbatim."""
    return attach(row, "title", "detail")
