"""#1514: a preference confirm carries the wording it was shown (dashboard and voice), like the phone op does.

The order the issue names: wording A is shown, the owner says yes, an edit to B commits, then the confirm lands.
That late confirm must leave B proposed; a confirm for the current wording still confirms it.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from jarves import dashboard
from jarves import preferences as prefs_mod
from jarves.config import Config
from jarves.skills import SkillRegistry
from jarves.userdata import open_db

A, B = "short answers in the morning", "long answers in the evening"


def _setup(home, text=A):
    ctx = dashboard.Ctx(home, Config())
    p = prefs_mod._ctx(ctx)
    return ctx, p, p.suggest(text, "I said so", "voice")["item"]["id"]


def _op(ctx, **body):
    return prefs_mod.api_pref_op(ctx, None, {"op": "confirm", **body})


# ---------------------------------------------------------------------- dashboard
def test_dashboard_confirm_for_old_wording_after_an_edit_leaves_the_new_wording_proposed(home):
    ctx, p, pid = _setup(home)
    shown = prefs_mod.api_prefs(ctx, None, None)[1]["proposed"][0]["text"]     # what pillars.js renders and sends
    p.edit(pid, B, by="phone", repropose=True)                                  # the edit commits first
    st, out = _op(ctx, id=pid, text=shown)
    assert st == 409 and out["code"] == "PREF_CONFLICT"
    assert p.get(pid)["state"] == "proposed" and p.get(pid)["text"] == B and p.for_prompt() == []
    shown = prefs_mod.api_prefs(ctx, None, None)[1]["proposed"][0]["text"]     # the page reloads: B is shown
    assert _op(ctx, id=pid, text=shown)[0] == 200 and p.for_prompt() == [B]


def test_dashboard_confirm_without_the_shown_text_is_refused_in_hebrew_too(home):
    from jarves import locale_parts
    ctx, p, pid = _setup(home)
    for bad in ({}, {"text": ""}, {"text": "   "}, {"text": 7}, {"text": None}, {"text": ["x"]}):
        st, out = _op(ctx, id=pid, **bad)
        assert st == 400 and out["code"] == "PREF_TEXT_REQUIRED"
    assert p.get(pid)["state"] == "proposed"
    errors = [_op(ctx, id=pid)[1]["error"]]
    p.edit(pid, B, by="phone", repropose=True)
    errors.append(_op(ctx, id=pid, text=A)[1]["error"])
    he = locale_parts.catalog("he")["dict"]                                     # pillars.js shows JL.t(error)
    assert all(he.get(e) and he[e] != e for e in errors), errors


def test_dashboard_confirm_of_an_item_removed_meanwhile_is_404(home):
    ctx, p, pid = _setup(home)
    real = p.confirm

    def removed_first(i, **kw):                      # removed after the route's existence check
        p.remove(i, by="test")
        return real(i, **kw)
    p.confirm = removed_first
    assert prefs_mod._ctx(ctx) is p                  # review R4: the route uses this same cached instance
    assert _op(ctx, id=pid, text=A)[0] == 404 and p.get(pid) is None


def test_dashboard_confirm_matches_normalised_and_max_length_wording(home):
    """Review R3: `expect` is compared normalised (case, spacing, punctuation) and clamped like stored text."""
    ctx, p, pid = _setup(home)
    assert _op(ctx, id=pid, text="  SHORT answers,  in the morning! ")[0] == 200
    long_a = ("word " * prefs_mod.MAX_TEXT)[:prefs_mod.MAX_TEXT + 50]
    pid2 = p.suggest(long_a, "I said so", "voice")["item"]["id"]
    assert _op(ctx, id=pid2, text=long_a)[0] == 200                             # longer than stored: clamped first


# ---------------------------------------------------------------------- voice
def _registry(p, on_ask, accepts_meta=True):
    reg = SkillRegistry()

    async def confirm(prompt, **meta):
        return on_ask(prompt, meta)

    async def plain(prompt):
        return on_ask(prompt, {})
    confirm.accepts_meta = True
    reg.confirm = confirm if accepts_meta else plain
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg


async def test_voice_confirm_for_old_wording_after_an_edit_leaves_the_new_wording_proposed(home):
    for accepts_meta in (True, False):                # a plain confirm(prompt) callback must be SHOWN the wording too
        p = prefs_mod.Preferences(open_db(home))
        pid = p.suggest(A + str(accepts_meta), "I said so", "voice")["item"]["id"]
        asks: list = []

        def edit_while_asking(prompt, meta):          # the owner hears A; the edit to B commits before the yes lands
            shown = p.get(pid)["text"]
            assert shown in prompt, prompt            # gpt blocker (#1740): the question itself carries the words
            if accepts_meta:
                assert meta["details"]["preference"] == shown
            asks.append(shown)
            if len(asks) == 1:
                p.edit(pid, B + str(accepts_meta), by="phone", repropose=True)
            return True
        reg = _registry(p, edit_while_asking, accepts_meta)
        r = await reg.call("pref_confirm", {"id": pid}, origin="model")
        assert not r.ok and r.code == "PREF_CONFLICT" and p.get(pid)["state"] == "proposed"
        r = await reg.call("pref_confirm", {"id": pid}, origin="user")         # asked again, now with B
        assert r.ok and p.get(pid)["state"] == "confirmed" and len(asks) == 2
        assert asks == [A + str(accepts_meta), B + str(accepts_meta)]


async def test_voice_confirm_handler_that_was_never_shown_the_wording_does_not_confirm(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I said so", "voice")["item"]["id"]
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    r = await tool.handler(tool.args(id=pid))                            # no approval step ran (nothing was shown)
    assert not r.ok and r.code == "PREF_NOT_SHOWN" and p.get(pid)["state"] == "proposed"
    assert "_shown" not in tool.args.model_fields and tool.args(id=pid, _shown=A)._shown is None   # model cannot set it


async def test_registry_hands_approval_meta_and_handler_the_same_args_instance():
    """Review R2: the voice guard depends on this; a copy or re-validation between the two steps must fail here."""
    from pydantic import BaseModel
    from jarves.skills import Skill, Tool, ToolResult
    seen: list = []

    class Args(BaseModel):
        n: int = 1

    async def handler(a):
        seen.append(a)
        return ToolResult(True, "ok")

    class S(Skill):
        name, description = "s1514", "test"

        def tools(self):
            return [Tool("t1514", "t", handler, Args, permission="action", confirm="ok?",
                         approval_meta=lambda a: seen.append(a) or {})]
    reg = SkillRegistry()

    async def yes(prompt):
        return True
    reg.confirm = yes
    reg.register(S())
    reg.allowed_actions |= {"t1514"}
    assert (await reg.call("t1514", {"n": 2}, origin="model")).ok
    assert len(seen) == 2 and seen[0] is seen[1]


NODE = pytest.mark.skipif(not shutil.which("node") and not os.environ.get("CI"), reason="node is not installed (CI must have it)")
STATIC = Path(prefs_mod.__file__).parent / "dashboard_static"
PAGE_PROBE = """
const words = JSON.parse(process.argv[3]); let gets = 0, out = [];
global.window = {}; global.PAGES = {}; global.JL = {t: (s) => s}; global.tx = (he, en) => en;
global.el = (tag, attrs, ...kids) => ({tag, attrs: attrs || {}, kids: kids.flat(), textContent: "", addEventListener() {}});
global.pill = global.header = (...a) => a;
const text = (n) => n.textContent || (n.kids || []).filter((k) => typeof k === "string").join("");
const kids = (n) => n.kids || (Array.isArray(n) ? n : []);
const find = (n, f) => !n || typeof n !== "object" ? null : f(n) ? n : kids(n).map((k) => find(k, f)).find(Boolean) || null;
const walk = (n, f, row) => { if (!n || typeof n !== "object") return;
  if (n.attrs && n.attrs.class === "card") { const b = find(n, (x) => x.tag === "b"); row = b ? b.kids[0] : "page"; }
  f(n, row); kids(n).forEach((k) => walk(k, f, row)); };
let confirm; const renders = [];
global.render = (...nodes) => { renders.push(nodes);
  walk(nodes, (n) => { if (n.tag === "button" && n.kids[0] === "Confirm, remember it" && !confirm) confirm = n; }); };
global.api = async (path, opts) => {
  if (!opts) return {proposed: words[gets++].map(([id, t]) => ({id, text: t, topic: "other"})), items: [], declined: [], topics: ["other"]};
  const e = new Error("changed"); e.code = process.argv[2]; throw e; };
global.route = () => PAGES.preferences();
require(process.argv[1]);
(async () => { await PAGES.preferences(); await confirm.attrs.onclick(); await new Promise((r) => setTimeout(r, 10));
  renders.forEach((nodes) => walk(nodes, (n, row) => { if (n.tag === "b") out.push(n.kids[0]);
    if (n.attrs && n.attrs.role === "status" && text(n)) out.push("note:" + text(n) + "@" + row); }));
  console.log(JSON.stringify(out)); })();"""


def _page(code, words):
    out = subprocess.run(["node", "-e", PAGE_PROBE, str(STATIC / "pillars.js"), code, json.dumps(words)],
                         capture_output=True, text=True, timeout=30, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@NODE
@pytest.mark.parametrize("code, reloads", [("PREF_CONFLICT", True), ("PREF_TEXT_REQUIRED", True), ("OTHER", False)])
def test_dashboard_page_re_renders_with_the_new_wording_after_a_stale_confirm(code, reloads):
    """Review R1 (#1740): after 409/400 the page re-fetches /api/prefs at once; other errors stay on the page message."""
    got = _page(code, [[[1, "A"], [2, "C"]], [[1, "B"], [2, "C"]]])
    assert got == (["A", "C", "B", "note:Failed: changed@B", "C"] if reloads else ["note:Failed: changed@page", "A", "C"])


@NODE
def test_dashboard_stale_confirm_note_goes_to_the_confirmed_row_or_the_page_if_it_is_gone():
    """Review R2 (13e8a27): the note is keyed by preference id, not given to the first status span of the next render."""
    assert _page("PREF_CONFLICT", [[[1, "A"], [2, "C"]], [[2, "C"]]]) == ["A", "C", "note:Failed: changed@page", "C"]


@NODE
def test_dashboard_api_errors_carry_the_server_code():
    """Review R1 (13e8a27): the real api() of app.js, with a stubbed fetch, puts the server's code on the error."""
    probe = """
    const src = require("fs").readFileSync(process.argv[1], "utf8");
    const api = new Function("fetch", "window", src.slice(src.indexOf("async function api("), src.indexOf("\\nconst usd")) + "; return api;")(
      async () => ({ok: false, statusText: "Conflict", json: async () => ({error: "changed", code: "PREF_CONFLICT"})}), {});
    api("/api/prefs/op", {method: "POST"}).then(() => console.log("no error"), (e) => console.log(e.message + "|" + e.code));"""
    out = subprocess.run(["node", "-e", probe, str(STATIC / "app.js")], capture_output=True, text=True, timeout=30,
                         check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "changed|PREF_CONFLICT"


@pytest.mark.parametrize("given, asked", [("Send «x»?", "Send «x»?"), ("  ", "ok?"), (7, "ok?"), (None, "ok?")])
async def test_approval_meta_prompt_replaces_the_question_only_when_it_is_real_text(given, asked):
    """gpt blocker (#1740): approval_meta's "prompt" reaches a plain confirm(prompt); junk keeps the tool's prompt."""
    from pydantic import BaseModel
    from jarves.skills import Skill, Tool, ToolResult
    prompts: list = []

    class Args(BaseModel):
        n: int = 1

    async def handler(_a):
        return ToolResult(True, "ok")

    class S(Skill):
        name, description = "s1514p", "test"

        def tools(self):
            return [Tool("t1514p", "t", handler, Args, permission="action", confirm="ok?",
                         approval_meta=lambda a: {"prompt": given})]
    reg = SkillRegistry()

    async def plain(prompt):
        prompts.append(prompt)
        return True
    reg.confirm = plain
    reg.register(S())
    reg.allowed_actions |= {"t1514p"}
    assert (await reg.call("t1514p", {}, origin="model")).ok and prompts == [asked]
