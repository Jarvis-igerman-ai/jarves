"""JAR-128: the ten most-seen user-facing error strings say what happened AND what to do, in English and Hebrew.

Hermetic: reads the source files and the dashboard catalog only (no server, no browser, no clock).
Audit and evidence: docs/reports/2026-11-16-error-messages-audit.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarves import locale_parts

SRC = Path(__file__).resolve().parents[1] / "src" / "jarves"
STATIC = SRC / "dashboard_static"

# English source text -> the file that must still emit it (catches a reworded source losing its Hebrew)
TOP10 = {
    "Check that JARVES is running (jarvis status), then refresh the page.": "dashboard_static/app.js",
    "JARVES is stopping or restarting. Wait a few seconds and send again.": "dashboard_static/chatview.js",
    "The message is too long (4000 characters at most). Shorten it or split it into several messages.":
        "dashboard_static/chatview.js",
    "Something went wrong. Try again; if it repeats, open the Health page or run jarvis logs.":
        "dashboard_static/chatview.js",
    "only the computer itself can do this: do it in the dashboard on that computer": "dashboard.py",
    "JARVES is not running; start it first (jarvis start)": "runtime/__init__.py",
    "too many requests: wait a minute": "mobile_drive.py",
    "still loading, retry in a few seconds": "swr.py",
    "the briefing is not available right now: try again in a minute": "mobile_drive.py",
    "chat is not running here: start `jarvis web` or JARVES": "chat_engine.py",
}
# an action cue: an imperative next step or a command to run
ACTION = re.compile(r"\b(check|wait|try|retry|run|start|shorten|split|open|refresh|send|do it)\b|jarvis \w+", re.I)
HE_ACTION = re.compile(r"(בדקו|המתינו|נסו|הריצו|הפעילו|קצרו|חלקו|פתחו|רעננו|שלחו|עשו)")


@pytest.mark.parametrize("en", sorted(TOP10))
def test_top_error_says_what_to_do_in_both_languages(en):
    assert en in (SRC / TOP10[en]).read_text(encoding="utf-8"), f"{en!r} no longer in {TOP10[en]}"
    assert ACTION.search(en), f"English has no next step: {en!r}"
    he = locale_parts.catalog("he")["dict"].get(en)
    assert he, f"no Hebrew catalog entry for {en!r} (add it with core/tools/catalog_add.py)"
    assert HE_ACTION.search(he), f"Hebrew has no next step: {he!r}"


def test_dashboard_api_errors_are_shown_in_the_page_language():
    """Server errors reach 'Failed: ' at many sites; the shared api() helper translates them once via the catalog."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "new Error(window.JL ? JL.t(en) : en)" in app
    assert "err.en = en" in app                       # the English source is kept for bug reports


def test_every_chat_engine_error_code_has_friendly_words():
    """chat_engine codes the chat view can receive must map to a sentence, never a raw server string."""
    engine = (SRC / "chat_engine.py").read_text(encoding="utf-8")
    view = (STATIC / "chatview.js").read_text(encoding="utf-8")
    for code in ("CHAT_STOPPING", "CHAT_BUSY", "CHAT_UNAVAILABLE", "CHAT_TOO_LONG", "UI_ACTION_FORBIDDEN"):
        assert f'"code": "{code}"' in engine, code
        assert re.search(rf"\b{code}: \[", view), f"chatview.js WORDS lacks {code}"
