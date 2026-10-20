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

from typing import Callable

from . import hebrew

DEFAULT = "en"
# locale -> translator for our fixed English texts (None = no translation: the dashboard keeps the English)
TRANSLATORS: dict[str, Callable[[object], str | None]] = {
    "he": hebrew.he,
}


def text(locale: str, s) -> str | None:
    """`s` in `locale`, or None (English, an unknown locale or no translation: the caller keeps the English)."""
    fn = TRANSLATORS.get(locale)
    return fn(s) if fn else None


def siblings(key: str, s) -> dict:
    """{"<key>_he": ..., ...} for one fixed English text (only the locales that translate it)."""
    out = {}
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
