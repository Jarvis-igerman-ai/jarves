"""JAR-128: the ten most-seen user-facing error strings say what happened AND what to do, in English and Hebrew.

Hermetic: reads the source files and the dashboard catalog; the api()/fail() test runs the real app.js functions in
node against a stubbed fetch and DOM (no server, no browser, no clock; skipped when node is not installed).
"""
from __future__ import annotations

import json
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


# Runs i18n.js (Hebrew chosen) plus the real api() and fail() bodies cut out of app.js, with stubbed fetch/DOM.
NODE_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const [he, i18n, app] = process.argv.slice(2).map((f) => fs.readFileSync(f, "utf8"));
const cut = (re) => { const m = re.exec(app); if (!m) throw new Error("not found: " + re); return m[0]; };
const api = cut(/async function api\([\s\S]*?\n}\n/), fail = cut(/function fail\(e\)[^\n]*\n[^\n]*\n/);
const noop = () => {};
const doc = { readyState: "loading", addEventListener: noop, documentElement: {}, querySelectorAll: () => [] };
const ctx = { console, URLSearchParams, location: { hash: "" }, document: doc,
  localStorage: { getItem: () => "he", setItem: noop },
  el: (tag, attrs, ...kids) => ({ tag, attrs, kids }), render: (n) => { ctx.out = n; },
  fetch: async () => ({ ok: false, status: 403, statusText: "Forbidden",
    json: async () => ({ error: "only the computer itself can do this: do it in the dashboard on that computer",
                         code: "UI_ACTION_FORBIDDEN", next: "n" }) }) };
ctx.window = ctx; vm.createContext(ctx);
vm.runInContext(he + "\n" + i18n + "\n" + api + "\n" + fail, ctx);
(async () => {
  let err = null;
  try { await vm.runInContext("api('/api/x', { method: 'POST' })", ctx); } catch (e) { err = e; }
  vm.runInContext("fail", ctx)(err);
  const flat = (n) => typeof n === "string" ? n : (n.kids || []).map(flat).join("|");
  process.stdout.write(JSON.stringify({ message: err.message, en: err.en, code: err.code, next: err.next,
                                        page: flat(ctx.out) }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_dashboard_api_error_is_hebrew_with_english_kept_and_fail_shows_the_catalog_text(tmp_path):
    """api() translates data.error once through the catalog, keeps the English on err.en and passes code/next;
    fail() renders that message plus the catalog's next step (the one source the TOP10 test checks)."""
    he_js = tmp_path / "he.js"
    he_js.write_bytes(locale_parts.render_js("he"))      # the served catalog: base plus parts
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(harness), str(he_js), str(STATIC / "i18n.js"), str(STATIC / "app.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    cat = locale_parts.catalog("he")["dict"]
    en = "only the computer itself can do this: do it in the dashboard on that computer"
    assert got["en"] == en and got["message"] == cat[en] != en
    assert got["code"] == "UI_ACTION_FORBIDDEN" and got["next"] == "n"
    assert got["page"] == "לא נטען: " + cat[en] + "|" + cat["Check that JARVES is running (jarvis status), then refresh the page."]


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
