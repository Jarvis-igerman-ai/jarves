"""#1514: a preference confirm applies only to the wording it was shown. An edit that commits after the owner saw
wording A, but before their confirm lands, leaves wording B `proposed` (dashboard route and voice tool; the phone op
was already guarded and is covered in test_mobile_prefs.py)."""
from __future__ import annotations

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

A, B = "short answers in the morning", "long answers in the morning"


@pytest.fixture
def dash(home):
    db = open_db(home)
    pid = prefs_mod.Preferences(db).suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    db.close()
    ctx = dashboard.Ctx(home, Config())
    yield ctx, prefs_mod._ctx(ctx), pid
    ctx.close()


def test_dashboard_confirm_of_old_wording_after_an_edit_is_refused(dash):
    ctx, p, pid = dash
    p.edit(pid, B, by="phone", repropose=True)                 # the edit commits while the page still shows A
    st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": A})
    assert (st, body["code"]) == (409, "PREF_CONFLICT")
    assert (p.get(pid)["state"], p.get(pid)["text"]) == ("proposed", B)
    st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": B})
    assert st == 200 and p.get(pid)["state"] == "confirmed"


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "  "}, {"text": 7}, {"text": None}])
def test_dashboard_confirm_without_the_shown_text_confirms_nothing(dash, body):
    ctx, p, pid = dash
    st, res = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, **body})
    assert (st, res["code"]) == (400, "PREF_TEXT_REQUIRED")
    assert p.get(pid)["state"] == "proposed"


def test_dashboard_confirm_of_an_item_removed_mid_request_is_not_found(dash, monkeypatch):
    ctx, p, pid = dash
    real = p.confirm
    def removed_then_confirm(*a, **k):
        p.remove(pid)
        return real(*a, **k)
    monkeypatch.setattr(p, "confirm", removed_then_confirm)
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": A})[0] == 404


def _voice(p, during_ask=None):
    reg, asked = SkillRegistry(), []
    async def confirm(prompt, **meta):
        asked.append(meta)
        if during_ask:
            during_ask()                                       # the edit lands while the owner reads the popup
        return True
    confirm.accepts_meta = True
    reg.confirm = confirm
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg, asked


async def test_voice_confirm_applies_only_to_the_wording_the_approval_showed(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    reg, asked = _voice(p, lambda: p.edit(pid, B, by="phone", repropose=True))
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert asked[0]["details"]["preference"] == A
    assert (r.ok, r.code) == (False, "PREF_CONFLICT")
    assert (p.get(pid)["state"], p.get(pid)["text"]) == ("proposed", B)
    reg, asked = _voice(p)
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert r.ok and asked[0]["details"]["preference"] == B and p.get(pid)["state"] == "confirmed"


async def test_voice_confirm_that_was_never_shown_the_wording_confirms_nothing(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    r = await tool.handler(tool.args(id=pid))                  # no approval ran, so no wording was shown
    assert (r.ok, r.code) == (False, "PREF_NOT_SHOWN") and p.get(pid)["state"] == "proposed"


async def test_voice_confirm_of_an_item_removed_after_the_approval_is_not_found(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    reg, _ = _voice(p, lambda: p.remove(pid))
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert (r.ok, r.code) == (False, "PREF_NOT_FOUND")


@pytest.mark.parametrize("approved", [True, False])
async def test_voice_shown_wording_comes_only_from_the_approval_the_handler_runs_after(home, approved):
    """Review R2: the registry calls approval_meta exactly once per call, only to build the approval it then asks,
    on the same args object the handler gets; a declined approval confirms nothing."""
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I prefer short answers in the morning", "voice")["item"]["id"]
    reg, asked = _voice(p)
    if not approved:
        async def no(prompt, **meta):
            asked.append(meta)
            return False
        no.accepts_meta = True
        reg.confirm = no
    tool = reg.get("pref_confirm")
    seen, real_meta, real_handler = [], tool.approval_meta, tool.handler
    def meta_spy(a):
        seen.append(("meta", a, len(asked)))
        return real_meta(a)
    async def handler_spy(a):
        seen.append(("run", a, len(asked)))
        return await real_handler(a)
    tool.approval_meta, tool.handler = meta_spy, handler_spy
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert seen[0][0] == "meta" and seen[0][2] == 0 and len(asked) == 1          # built right before the one ask
    assert asked[0]["details"]["preference"] == seen[0][1]._shown == A        # the words shown = the words bound
    if approved:
        assert [s[0] for s in seen] == ["meta", "run"] and seen[1][1] is seen[0][1] and r.ok
        assert p.get(pid)["state"] == "confirmed"
    else:
        assert [s[0] for s in seen] == ["meta"] and not r.ok and p.get(pid)["state"] == "proposed"


PILLARS_HARNESS = r"""
const calls = [], renders = [];
const flat = (xs) => xs.flat(9).filter((x) => x !== null && x !== undefined && x !== false);
global.el = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: flat(kids), textContent: "", addEventListener() {} });
global.tx = (he, en) => en; global.header = (...a) => ({ tag: "header", attrs: {}, kids: [] });
global.pill = () => null; global.JL = { t: (s) => "T(" + s + ")" }; global.PAGES = {}; global.window = {};
global.render = (...kids) => renders.push(flat(kids));
global.route = () => { calls.push(["route"]); return PAGES.preferences(); };
global.api = async (path, opts) => {
  if (!opts) return { proposed: [{ id: 5, text: "wording A", topic: "voice" }], items: [], declined: [], topics: [],
                      in_prompt: false, prompt_text: "" };
  calls.push(["post", JSON.parse(opts.body)]);
  const e = new Error(process.argv[2]); e.code = process.argv[3] || undefined; throw e;
};
require(process.argv[1]);
const walk = (n, f) => { if (!n || typeof n !== "object") return; f(n); (n.kids || []).forEach((k) => walk(k, f)); };
const find = (pred) => { let hit = null; renders[renders.length - 1].forEach((r) => walk(r, (n) => { if (!hit && pred(n)) hit = n; })); return hit; };
(async () => {
  await PAGES.preferences();
  await find((n) => n.tag === "button" && n.kids.includes("Confirm, remember it")).attrs.onclick();
  await new Promise((r) => setTimeout(r, 0));
  const msg = find((n) => n.attrs.role === "status");
  console.log(JSON.stringify({ calls, renders: renders.length, msg: msg.textContent }));
})();
"""


@pytest.mark.skipif(not shutil.which("node") and not os.environ.get("CI"), reason="node is not installed (CI must have it)")
@pytest.mark.parametrize("code,reloads", [("PREF_CONFLICT", True), ("PREF_TEXT_REQUIRED", True), ("", False)])
def test_dashboard_page_reloads_and_explains_a_refused_confirm(code, reloads):
    """Review R3/R4: the Confirm button sends the shown words; a refused confirm reloads the list (new wording) and
    shows the server's message through the catalog (JL.t, Hebrew in locales/parts); other errors stay as before."""
    js = Path(prefs_mod.__file__).parent / "dashboard_static/pillars.js"
    err = "the preference changed meanwhile: reload the page"
    out = subprocess.run(["node", "-e", PILLARS_HARNESS, str(js), err, code], capture_output=True, text=True,
                         timeout=30, check=False)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert res["calls"][0] == ["post", {"op": "confirm", "id": 5, "text": "wording A"}]
    assert (["route"] in res["calls"], res["renders"]) == (reloads, 2 if reloads else 1)
    assert res["msg"] == "Failed: T(" + err + ")"


def test_dashboard_refusal_texts_have_hebrew():
    from jarves import locale_parts
    he = locale_parts.catalog("he")["dict"]
    for en in ("confirm needs the text you saw: reload the page", "the preference changed meanwhile: reload the page"):
        assert he.get(en) and "מחדש" in he[en]
