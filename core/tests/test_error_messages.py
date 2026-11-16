"""JAR-128: the ten most-seen user-facing error strings say what happened AND what to do, in English and Hebrew.

Hermetic: reads the source files and the dashboard catalog; the api()/fail() test runs the real app.js functions in
node against a stubbed fetch and DOM (no server, no browser, no clock; skipped when node is not installed).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jarves import chat_engine, locale_parts

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


# Runs i18n.js (Hebrew chosen) plus the real api() and fail() bodies cut out of app.js, with stubbed fetch/DOM,
# and the real chatview.js WORDS with its word() helper and failed-reply fallback line.
NODE_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const [he, i18n, app, chat] = process.argv.slice(2).map((f) => fs.readFileSync(f, "utf8").replace(/\r\n/g, "\n"));   // Windows CRLF checkout
const cut = (re, src = app) => { const m = re.exec(src); if (!m) throw new Error("not found: " + re); return m[0]; };
const api = cut(/async function api\([\s\S]*?\n}\n/), fail = cut(/const tc = [^\n]*\nfunction fail\(e\)[^\n]*\n[^\n]*\n/);
const noop = () => {};
const doc = { readyState: "loading", addEventListener: noop, documentElement: {}, querySelectorAll: () => [] };
const ctx = { console, URLSearchParams, location: { hash: "" }, document: doc,
  localStorage: { getItem: () => "he", setItem: noop },
  el: (tag, attrs, ...kids) => ({ tag, attrs, kids }), render: (n) => { ctx.out = n; },
  fetch: async () => ({ ok: false, status: 403, statusText: "Forbidden",
    json: async () => ({ error: "only the computer itself can do this: do it in the dashboard on that computer",
                         code: "UI_ACTION_FORBIDDEN", next: "n" }) }) };
ctx.window = ctx; vm.createContext(ctx);
vm.runInContext(he + "\n" + i18n + "\n" + api + "\n" + fail + "\n" + chat, ctx);
const word = cut(/const word = [^\n]*;/, chat), reply = cut(/const text = e\.text \|\| [^\n]*;/, chat);
const said = (e) => vm.runInContext(`(() => { const WORDS = JarvesChat.WORDS; ${word} ${reply} return text; })()`,
  Object.assign(ctx, { e }));
(async () => {
  let err = null;
  try { await vm.runInContext("api('/api/x', { method: 'POST' })", ctx); } catch (e) { err = e; }
  vm.runInContext("fail", ctx)(err);
  const flat = (n) => typeof n === "string" ? n : (n.kids || []).map(flat).join("|");
  const page = flat(ctx.out), JL = ctx.JL;
  ctx.JL = undefined; vm.runInContext("fail", ctx)(new Error("x")); const bare = flat(ctx.out); ctx.JL = JL;   // i18n.js missing
  process.stdout.write(JSON.stringify({ message: err.message, en: err.en, code: err.code, next: err.next,
                                        page, bare, stopping: said({ ok: false, code: "CHAT_STOPPING" }),
                                        other: said({ ok: false, code: "NOT_A_CODE" }) }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _run_node(tmp_path):
    if not shutil.which("node"):
        if os.environ.get("CI"):
            pytest.fail("node is required in CI: the dashboard error tests must run there")
        pytest.skip("node is not installed")
    he_js = tmp_path / "he.js"
    he_js.write_bytes(locale_parts.render_js("he"))      # the served catalog: base plus parts
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(harness), str(he_js)] + [str(STATIC / f) for f in ("i18n.js", "app.js", "chatview.js")],
                       capture_output=True, encoding="utf-8", timeout=60)      # utf-8: Windows would decode as cp1252
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_dashboard_api_error_is_hebrew_with_english_kept_and_fail_shows_the_catalog_text(tmp_path):
    """api() translates data.error once through the catalog, keeps the English on err.en and passes code/next;
    fail() renders that message plus the catalog's next step (the one source the TOP10 test checks)."""
    got = _run_node(tmp_path)
    cat = locale_parts.catalog("he")["dict"]
    en = "only the computer itself can do this: do it in the dashboard on that computer"
    assert got["en"] == en and got["message"] == cat[en] != en
    assert got["code"] == "UI_ACTION_FORBIDDEN" and got["next"] == "n"
    assert got["page"] == "לא נטען: " + cat[en] + "|" + cat["Check that JARVES is running (jarvis status), then refresh the page."]
    assert got["bare"] == "Could not load: x|Check that JARVES is running (jarvis status), then refresh the page."


def test_chat_failed_reply_shows_hebrew_words_for_stopping_and_the_fallback(tmp_path):
    """A failed chat turn goes through chatview's real word(): CHAT_STOPPING gets its words, an unknown code the fallback."""
    got = _run_node(tmp_path)
    assert got["stopping"] == "ג'רביס נעצר או מופעל מחדש. המתינו כמה שניות ושלחו שוב."
    assert got["other"] == "משהו השתבש. נסו שוב; אם זה חוזר, פתחו את דף הבריאות או הריצו jarvis logs."


# matching on e.message breaks in Hebrew mode (api() translates it): callers match the English source e.en instead
MESSAGE_LOGIC = re.compile(r"\.message\s*(===|!==|==|!=|\.(test|match|includes|indexOf|startsWith|endsWith|search)\b)"
                           r"|switch\s*\([^)]*\.message|\.(test|match|exec|includes|indexOf|startsWith)\((?![^)]*\.en\b)[^)]*\.message")


def test_no_dashboard_caller_matches_on_the_translated_error_message():
    hits = [f"{f.name}:{n}" for f in sorted(STATIC.glob("*.js")) if f.name != "i18n.js"
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if MESSAGE_LOGIC.search(line)]
    assert not hits, f"match on e.en (English) or e.code, not e.message: {hits}"


def test_chat_too_long_texts_match_the_server_limit():
    """The 4000 in the catalog key and in chatview.js WORDS is MAX_TEXT; a new limit must update those texts."""
    assert chat_engine.MAX_TEXT == 4000
    assert f"message too long: {chat_engine.MAX_TEXT} characters at most" in "".join(locale_parts.catalog("he")["dict"])
    assert f"({chat_engine.MAX_TEXT} characters at most)" in (STATIC / "chatview.js").read_text(encoding="utf-8")


def test_every_chat_engine_error_code_has_friendly_words():
    """chat_engine codes the chat view can receive must map to a sentence, never a raw server string."""
    engine = (SRC / "chat_engine.py").read_text(encoding="utf-8")
    view = (STATIC / "chatview.js").read_text(encoding="utf-8")
    for code in ("CHAT_STOPPING", "CHAT_BUSY", "CHAT_UNAVAILABLE", "CHAT_TOO_LONG", "UI_ACTION_FORBIDDEN"):
        assert f'"code": "{code}"' in engine, code
        assert re.search(rf"\b{code}: \[", view), f"chatview.js WORDS lacks {code}"
