"""JAR-121: Hebrew texts must not carry runs of 3+ bare English words (untranslated leftovers).

Scans the server Hebrew (`hebrew.TEXT`, which includes `hebrew_parts/*.json`) and the dashboard Hebrew catalog
(he.js plus `locales/parts/*.json`, dict values and rule targets). CLI commands (`jarvis ...`, text in backticks)
are ids, not prose, and are skipped. Hermetic: reads repo files only."""
from __future__ import annotations

import re

from jarves import hebrew, locale_parts

WORD = r"[A-Za-z][A-Za-z']*"
RUN = re.compile(rf"(?<![\w{{$%/\\-]){WORD}(?:[ ]+{WORD}){{2,}}(?![\w}}/\\])")
BACKTICKS = re.compile(r"`[^`]*`")
COMMAND_START = ("jarvis ",)


def english_runs(text: str) -> list[str]:
    """Runs of 3+ consecutive bare English words in `text`, ignoring commands in backticks and `jarvis ...` commands."""
    if not isinstance(text, str):
        return []
    return [m for m in RUN.findall(BACKTICKS.sub(" ", text)) if not m.startswith(COMMAND_START)]


def test_detector_finds_untranslated_prose_and_skips_commands():
    assert english_runs('בחר More info ואז "Windows protected your PC"') == ["Windows protected your PC"]
    assert english_runs("הריצו: jarvis features on model_lab") == []
    assert english_runs("להתקנה: `python -m playwright install ffmpeg`") == []
    assert english_runs("וואטסאפ דרך Any.do ו-GitHub") == []
    assert english_runs(None) == []


def test_hebrew_texts_have_no_runs_of_three_english_words():
    found = []
    for en, he_text in hebrew.TEXT.items():
        found += [("server", en, run) for run in english_runs(he_text)]
    cat = locale_parts.catalog("he")
    for en, he_text in cat["dict"].items():
        found += [("dashboard", en, run) for run in english_runs(he_text)]
    for pattern, he_text in cat.get("rules", []):
        found += [("dashboard rule", pattern, run) for run in english_runs(he_text)]
    assert not found, "\n".join(f"{where}: {run!r} in the Hebrew of {en[:80]!r}" for where, en, run in found)
