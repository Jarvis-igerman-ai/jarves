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
from types import SimpleNamespace

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
const [he, i18n, app, chat, browser, m4] = process.argv.slice(2).map((f) => fs.readFileSync(f, "utf8").replace(/\r\n/g, "\n"));   // Windows CRLF checkout
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
// the real catch branches of browser.js (live view busy) and m4.js (voice cost confirm), given a Hebrew e.message
const busy = cut(/if \(\/busy\/i\.test\([^\n]*\n\s*img\.style\.display = "none"; end\(e\.message\);/, browser);
const costCatch = cut(/if \(!approve && [^\n]*\n\s*vMsg\.textContent = [^\n]*;/, m4);
const branch = (code, e) => vm.runInContext(`(() => { const out = { asked: null };
  const clockEl = {}, img = { style: {} }, vMsg = {}, approve = false, v = {};
  const end = (t) => { out.ended = t; }, confirm = (t) => { out.asked = t; return true; }, choose = () => "retried";
  const r = (() => { ${code} })(); out.clock = clockEl.textContent; out.msg = vMsg.textContent; out.r = r; return out; })()`,
  Object.assign(ctx, { e }));
const heErr = (msg, en) => Object.assign(new Error(msg), { en });
(async () => {
  let err = null;
  try { await vm.runInContext("api('/api/x', { method: 'POST' })", ctx); } catch (e) { err = e; }
  vm.runInContext("fail", ctx)(err);
  const flat = (n) => typeof n === "string" ? n : (n.kids || []).map(flat).join("|");
  const page = flat(ctx.out), JL = ctx.JL;
  ctx.JL = undefined; vm.runInContext("fail", ctx)(new Error("x")); const bare = flat(ctx.out); ctx.JL = JL;   // i18n.js missing
  process.stdout.write(JSON.stringify({ message: err.message, en: err.en, code: err.code, next: err.next,
                                        page, bare, stopping: said({ ok: false, code: "CHAT_STOPPING" }),
                                        other: said({ ok: false, code: "NOT_A_CODE" }),
                                        busy: branch(busy, heErr("הדף עסוק", "the page is busy")),
                                        notBusy: branch(busy, heErr("נכשל", "grant expired")),
                                        cost: branch(costCatch, heErr("נדרש אישור", "approve the cost before switching")),
                                        noCost: branch(costCatch, heErr("לא ידוע", "unknown voice")),
                                        words: Object.fromEntries(JSON.parse(process.env.CHAT_CODES || "[]").map(
                                          (c) => [c, said({ ok: false, code: c })])) }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


# chat_engine codes the chat view can receive: each must reach the user as a sentence, never a raw server string
CHAT_CODES = ("CHAT_STOPPING", "CHAT_BUSY", "CHAT_UNAVAILABLE", "CHAT_TOO_LONG", "CHAT_FEATURE_OFF", "UI_ACTION_FORBIDDEN")


def _run_node(tmp_path):
    if not shutil.which("node"):
        if os.environ.get("CI"):
            pytest.fail("node is required in CI: the dashboard error tests must run there")
        pytest.skip("node is not installed")
    he_js = tmp_path / "he.js"
    he_js.write_bytes(locale_parts.render_js("he"))      # the served catalog: base plus parts
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(harness), str(he_js)] + [str(STATIC / f) for f in ("i18n.js", "app.js", "chatview.js", "browser.js", "m4.js")],
                       capture_output=True, encoding="utf-8", timeout=60,
                       env={**os.environ, "CHAT_CODES": json.dumps(CHAT_CODES)})      # utf-8: Windows would decode as cp1252
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


def test_browser_busy_and_voice_cost_branches_match_the_english_while_showing_hebrew(tmp_path):
    """Hebrew mode: the real browser.js busy branch and m4.js cost-confirm branch decide on e.en, show Hebrew text."""
    got = _run_node(tmp_path)
    assert got["busy"].get("clock") == "הדף עסוק, מנסה שוב" and "ended" not in got["busy"]
    assert got["notBusy"]["ended"] == "נכשל" and "clock" not in got["notBusy"]
    assert got["cost"]["asked"] == "נדרש אישור\n\nלאשר את השינוי?" and got["cost"]["r"] == "retried"
    assert got["noCost"]["asked"] is None and got["noCost"]["msg"] == "לא שונה: לא ידוע"


# secondary guard: matching on e.message breaks in Hebrew mode (api() translates it): callers match the English source e.en instead
MESSAGE_LOGIC = re.compile(r"\.message\s*(===|!==|==|!=|\.(test|match|includes|indexOf|startsWith|endsWith|search)\b)"
                           r"|switch\s*\([^)]*\.message|\.(test|match|exec|includes|indexOf|startsWith)\((?![^)]*\.en\b)[^)]*\.message")


def test_no_dashboard_caller_matches_on_the_translated_error_message():
    """All dashboard scripts, the phone app (mobile/) included: none decides on the translated e.message."""
    hits = [f"{f.relative_to(STATIC)}:{n}" for f in sorted(STATIC.rglob("*.js"))
            if f.name != "i18n.js" and "locales" not in f.parts
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if MESSAGE_LOGIC.search(line)]
    assert not hits, f"match on e.en (English) or e.code, not e.message: {hits}"


def test_no_client_matches_the_reworded_server_texts():
    """JAR-128 reworded server errors; no script (dashboard or phone) may still compare against the old exact text."""
    old = ('"only the computer itself can do this"', '"the briefing is not available right now"')
    hits = [f"{f.relative_to(STATIC)}: {o}" for f in sorted(STATIC.rglob("*.js")) if "locales" not in f.parts
            for o in old if o in f.read_text(encoding="utf-8")]
    assert not hits, hits


def _chat(tmp_path, stopping=False):
    core = SimpleNamespace(home=str(tmp_path), approvals=None, stopping=stopping)
    return chat_engine.WebChat(core, loop=None)     # the refusals below return before any turn reaches the loop


def test_too_long_chat_message_is_refused_with_the_limit_and_a_next_step(tmp_path):
    """Behavioural: submit() refuses MAX_TEXT+1 characters with 413 CHAT_TOO_LONG; the text names the limit, says what
    to do, and has Hebrew; the chat view words for that code name the same limit."""
    status, body = _chat(tmp_path).submit("x" * (chat_engine.MAX_TEXT + 1))
    assert status == 413 and body["code"] == "CHAT_TOO_LONG"
    assert str(chat_engine.MAX_TEXT) in body["error"] and ACTION.search(body["error"])
    assert HE_ACTION.search(locale_parts.catalog("he")["dict"][body["error"]])
    assert f"({chat_engine.MAX_TEXT} characters at most)" in (STATIC / "chatview.js").read_text(encoding="utf-8")


def test_stopping_chat_returns_the_code_the_view_has_words_for(tmp_path):
    status, body = _chat(tmp_path, stopping=True).submit("hello")
    assert status == 503 and body["code"] == "CHAT_STOPPING" and body["code"] in CHAT_CODES


def test_every_chat_error_code_reaches_the_user_as_words_not_the_fallback(tmp_path):
    """Runs chatview.js's real word() for each code (Hebrew mode): each gets its own sentence, not the generic fallback."""
    words = _run_node(tmp_path)["words"]
    fallback = "משהו השתבש. נסו שוב; אם זה חוזר, פתחו את דף הבריאות או הריצו jarvis logs."
    for code in CHAT_CODES:
        assert words.get(code) and words[code] != fallback, f"chatview.js WORDS lacks {code}"
