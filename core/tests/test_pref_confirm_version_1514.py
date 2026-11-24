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
        return on_ask(meta)

    async def plain(prompt):
        return on_ask({})
    confirm.accepts_meta = True
    reg.confirm = confirm if accepts_meta else plain
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg


async def test_voice_confirm_for_old_wording_after_an_edit_leaves_the_new_wording_proposed(home):
    for accepts_meta in (True, False):                # a plain confirm(prompt) callback binds the wording too
        p = prefs_mod.Preferences(open_db(home))
        pid = p.suggest(A + str(accepts_meta), "I said so", "voice")["item"]["id"]
        asks: list = []

        def edit_while_asking(meta):                  # the owner hears A; the edit to B commits before the yes lands
            asks.append(meta.get("details", {}).get("preference"))
            if len(asks) == 1:
                p.edit(pid, B + str(accepts_meta), by="phone", repropose=True)
            return True
        reg = _registry(p, edit_while_asking, accepts_meta)
        r = await reg.call("pref_confirm", {"id": pid}, origin="model")
        assert not r.ok and r.code == "PREF_CONFLICT" and p.get(pid)["state"] == "proposed"
        r = await reg.call("pref_confirm", {"id": pid}, origin="user")         # asked again, now with B
        assert r.ok and p.get(pid)["state"] == "confirmed" and len(asks) == 2
        if accepts_meta:
            assert asks == [A + "True", B + "True"]


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


@pytest.mark.skipif(not shutil.which("node") and not os.environ.get("CI"), reason="node is not installed (CI must have it)")
@pytest.mark.parametrize("code, reloads", [("PREF_CONFLICT", True), ("PREF_TEXT_REQUIRED", True), ("OTHER", False)])
def test_dashboard_page_re_renders_with_the_new_wording_after_a_stale_confirm(code, reloads):
    """Review R1 (#1740): after 409/400 the page re-fetches /api/prefs at once and keeps the note; other errors stay put."""
    static = Path(prefs_mod.__file__).parent / "dashboard_static"
    probe = """
    const words = ["A", "B"]; let gets = 0, shown = [];
    global.window = {}; global.PAGES = {}; global.JL = {t: (s) => s}; global.tx = (he, en) => en;
    global.el = (tag, attrs, ...kids) => ({tag, attrs: attrs || {}, kids: kids.flat(), textContent: "", addEventListener() {}});
    global.pill = global.header = (...a) => a;
    const walk = (n, f) => { if (n && typeof n === "object") { f(n); (n.kids || (Array.isArray(n) ? n : [])).forEach((k) => walk(k, f)); } };
    global.render = (...nodes) => walk(nodes, (n) => { if (n.tag === "b") shown.push(n.kids[0]);
      if (n.attrs && n.attrs.role === "status" && n.textContent) shown.push("note:" + n.textContent); });
    global.api = async (path, opts) => {
      if (!opts) return {proposed: [{id: 1, text: words[gets++], topic: "other"}], items: [], declined: [], topics: ["other"]};
      const e = new Error("changed"); e.code = process.argv[2]; throw e; };
    global.route = () => PAGES.preferences();
    require(process.argv[1]);
    (async () => {
      let confirm; global.render = ((r) => (...n) => { walk(n, (x) => { if (x.tag === "button" && x.kids[0] === "Confirm, remember it") confirm = x; }); r(...n); })(global.render);
      await PAGES.preferences(); await confirm.attrs.onclick(); await new Promise((r) => setTimeout(r, 10));
      console.log(JSON.stringify(shown));
    })();"""
    out = subprocess.run(["node", "-e", probe, str(static / "pillars.js"), code], capture_output=True, text=True,
                         timeout=30, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == (["A", "note:Failed: changed", "B"] if reloads else ["A"])     # 2nd render: note, then B


def test_dashboard_api_errors_carry_the_server_code():
    app = (Path(prefs_mod.__file__).parent / "dashboard_static" / "app.js").read_text(encoding="utf-8")
    assert "err.code = data.code" in app                  # pillars.js re-renders on PREF_CONFLICT / PREF_TEXT_REQUIRED
