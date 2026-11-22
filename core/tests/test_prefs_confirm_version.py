"""Issue #1514: a preference confirm carries the version (`updated`) of the wording it was shown. An edit that lands
between showing wording A and the confirm must not let that confirm approve wording B, on any path."""
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarves import mobile_prefs, preferences as prefs_mod
from jarves.preferences import PrefChanged, Preferences
from jarves.userdata import open_db


@pytest.fixture
def prefs(home):
    t = [1000.0]                                          # a frozen clock: the edit happens in the same tick as the read
    p = Preferences(open_db(home), clock=lambda: t[0])
    item = p.suggest("Play jazz while cooking", "I love jazz when I cook", "music")["item"]
    return p, item["id"], item["updated"]


A, B = "Play jazz while cooking", "Play loud metal while cooking"


def _edit_to_b(p, pid):
    p.edit(pid, B, by="voice", repropose=True)


def test_store_confirm_with_the_old_version_after_an_edit_stays_proposed(prefs):
    p, pid, shown = prefs
    _edit_to_b(p, pid)
    with pytest.raises(PrefChanged):
        p.confirm(pid, by="phone", shown=shown)
    assert p.get(pid)["state"] == "proposed" and not p.for_prompt()
    r = p.confirm(pid, by="phone", shown=p.get(pid)["updated"])         # the current version: confirmed
    assert r["item"]["state"] == "confirmed" and p.for_prompt() == ["Play loud metal while cooking"]


def test_phone_confirm_needs_the_shown_version(prefs, home):
    p, pid, shown = prefs
    mob = SimpleNamespace(bridge=None, _prefs=p, home=home)
    assert mobile_prefs.action(mob, {"op": "confirm", "pref": pid, "text": A, "updated": "x"}, "dev")[0] == 400
    p.edit(pid, A, by="voice")                            # an A -> A rewrite: same words, newer version (#206 H2 can't see it)
    st, body = mobile_prefs.action(mob, {"op": "confirm", "pref": pid, "text": A, "updated": shown}, "dev")
    assert st == 409 and body["code"] == "PREF_CHANGED" and p.get(pid)["state"] == "proposed"
    _edit_to_b(p, pid)
    st, body = mobile_prefs.action(mob, {"op": "confirm", "pref": pid, "text": B, "updated": p.get(pid)["updated"]}, "dev")
    assert st == 200 and body["state"] == "confirmed"


def test_dashboard_confirm_needs_the_shown_version(prefs):
    p, pid, shown = prefs
    ctx = SimpleNamespace(_prefs=p)
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "updated": "x"})[0] == 400
    _edit_to_b(p, pid)
    st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "updated": shown})
    assert st == 409 and body["code"] == "PREF_CHANGED" and body["error_he"] and p.get(pid)["state"] == "proposed"
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "updated": p.get(pid)["updated"]})[0] == 200
    assert p.get(pid)["state"] == "confirmed"


def _voice(p, during_popup):
    from jarves.skills import SkillRegistry
    reg = SkillRegistry()

    async def popup(prompt, **meta):                       # the owner sees the wording, then taps yes
        during_popup()
        return True
    popup.accepts_meta = True
    reg.confirm = popup
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg


def test_voice_confirm_after_an_edit_during_the_popup_is_refused(prefs):
    p, pid, _ = prefs
    reg = _voice(p, lambda: _edit_to_b(p, pid))             # wording A in the popup, B committed before the yes
    r = asyncio.run(reg.call("pref_confirm", {"id": pid}, origin="model"))
    assert not r.ok and r.code == "PREF_CHANGED" and p.get(pid)["state"] == "proposed" and not p.for_prompt()
    r = asyncio.run(_voice(p, lambda: None).call("pref_confirm", {"id": pid}, origin="model"))
    assert r.ok and p.get(pid)["state"] == "confirmed"      # a fresh popup shows B: that yes confirms B


def test_every_write_moves_the_version_forward_within_one_clock_tick(prefs):
    p, pid, v0 = prefs                                    # review R1: frozen clock, edit -> confirm -> decline
    _edit_to_b(p, pid)
    v1 = p.get(pid)["updated"]
    p.confirm(pid, by="phone", shown=v1)
    p.decline(pid, by="phone")
    for stale in (v0, v1):                                # no write may put an older version back
        with pytest.raises(PrefChanged):
            p.confirm(pid, by="phone", shown=stale)
    assert p.get(pid)["state"] == "declined"


def test_voice_confirm_without_an_approval_popup_fails_closed(prefs):
    p, pid, _ = prefs                                     # review R3: no approval_meta ran, so no wording was shown
    confirm = {t.name: t for t in prefs_mod.make_skill(p).tools()}["pref_confirm"]
    r = asyncio.run(confirm.handler(confirm.args(id=pid)))
    assert not r.ok and r.code == "PREF_NOT_SHOWN" and p.get(pid)["state"] == "proposed"


def test_phone_errors_tell_an_old_page_to_reload_in_both_languages(prefs, home):
    p, pid, shown = prefs                                 # review R4/R5; a page without the words: #206 H2's reload answer
    mob = SimpleNamespace(bridge=None, _prefs=p, home=home)
    st, body = mobile_prefs.action(mob, {"op": "confirm", "pref": pid}, "dev")
    assert st == 400 and body["code"] == "PREF_TEXT_REQUIRED" and "reload" in body["error"]
    _edit_to_b(p, pid)
    st, body = mobile_prefs.action(mob, {"op": "confirm", "pref": pid, "text": B, "updated": shown}, "dev")
    assert st == 409 and body["error_he"]


DASH_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const calls = [], [mode] = process.argv.slice(2);
let routed = 0, nodes = [];
const el = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(9), textContent: "", addEventListener() {} });
const ctx = { console, Promise, JSON, Object, el, PAGES: {}, tx: (he, en) => en, JL: { t: (s) => "T:" + s },
  header: () => el("h"), pill: () => el("pill"), render: (...n) => { nodes = n; }, route: () => { routed++; },
  api: async (path, opts) => {
    if (!opts) return { proposed: [{ id: 7, text: "A", topic: "music", updated: 1000.5 }], items: [], declined: [],
                        topics: ["music"], in_prompt: true, prompt_text: "" };
    calls.push(JSON.parse(opts.body));
    if (mode === "changed") { const e = new Error("changed"); e.code = "PREF_CHANGED"; throw e; }
    return {};
  } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), ctx);
const find = (n, label) => !n || typeof n !== "object" ? null : (n.tag === "button" && (n.kids || []).includes(label)) ? n
  : (Array.isArray(n) ? n : n.kids || []).map((k) => find(k, label)).find(Boolean) || null;
(async () => {
  await ctx.PAGES.preferences();
  await find({ kids: nodes }, "Confirm, remember it").attrs.onclick();
  await new Promise((r) => setTimeout(r, 10));
  const notes = []; if (mode === "changed") { await ctx.PAGES.preferences(); JSON.stringify(nodes, (k, v) => {
    if (v && v.attrs && v.attrs.role === "status" && v.textContent) notes.push(v.textContent); return v; }); }
  console.log(JSON.stringify({ calls, routed, notes }));
})();
"""


STATIC = Path(prefs_mod.__file__).parent / "dashboard_static"


def _node(tmp_path, harness: str, mode: str, js: Path) -> dict:
    node = shutil.which("node")
    if not node:
        if os.environ.get("CI"):                          # CI must run the page tests: a missing node is a failure there
            pytest.fail("node is not installed on this CI runner")
        pytest.skip("node is not installed")
    (tmp_path / "h.js").write_text(harness, encoding="utf-8")
    return json.loads(subprocess.run([node, str(tmp_path / "h.js"), mode, str(js)], capture_output=True, encoding="utf-8",
                                     check=True, timeout=30).stdout)


@pytest.mark.parametrize("mode", ["ok", "changed"])
def test_dashboard_page_sends_the_shown_version_and_reloads_when_it_changed(mode, tmp_path):
    out = _node(tmp_path, DASH_HARNESS, mode, STATIC / "pillars.js")   # review R2/R4/R5: the real pillars.js
    assert out["calls"] == [{"op": "confirm", "id": 7, "updated": 1000.5}] and out["routed"] == 1
    if mode == "changed":
        assert "T:This preference changed since it was shown. Here is the new wording: confirm again if it is right." in out["notes"]


PHONE_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const [mode, file] = process.argv.slice(2), posts = [];
let gets = 0;
const mk = (tag) => ({ tag, className: "", textContent: "", kids: [], attrs: {}, on: {},
  appendChild(c) { this.kids.push(c); return c; }, append(...c) { this.kids.push(...c); },
  replaceChildren(...c) { this.kids = c; }, setAttribute(k, v) { this.attrs[k] = v; },
  addEventListener(k, f) { this.on[k] = f; } });
const ctx = { console, Promise, JSON, Date, Math, setTimeout, document: { createElement: mk }, window: {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(file, "utf8"), ctx);
const all = (n) => [n, ...n.kids.flatMap(all)];
const cx = { lang: () => "he",
  api: async () => { gets++; return { in_prompt: true, items: [], declined: [],
                                      proposed: [{ id: 7, text: "A", topic: "music", updated: 1000.5 }] }; },
  post: async (path, body) => { posts.push([path, body]);
    if (mode === "changed") { const e = new Error("changed"); e.code = "PREF_CHANGED"; e.status = 409; throw e; }
    return { ok: true }; } };
const root = mk("div");
(async () => {
  ctx.window.JarvesPrefs.mount(root, cx);
  await new Promise((r) => setTimeout(r, 10));
  const he = ctx.window.JarvesPrefs.catalogs.he;
  all(root).find((n) => n.tag === "button" && n.textContent === he.confirm).on.click();
  await new Promise((r) => setTimeout(r, 10));
  const notes = all(root).filter((n) => n.attrs.role === "status").map((n) => n.textContent);
  console.log(JSON.stringify({ posts, gets, notes, changed: he.changed }));
})();
"""


@pytest.mark.parametrize("mode", ["ok", "changed"])
def test_phone_page_sends_the_shown_version_and_reloads_when_it_changed(mode, tmp_path):
    out = _node(tmp_path, PHONE_HARNESS, mode, STATIC / "mobile" / "prefs.js")   # review R1: the real phone page
    assert len(out["posts"]) == 1 and out["posts"][0][0] == "/api/m/prefs/op"
    body = out["posts"][0][1]
    assert {k: body[k] for k in ("op", "pref", "updated")} == {"op": "confirm", "pref": 7, "updated": 1000.5}
    assert out["gets"] == 2                               # load() ran again: the page shows the current wording
    if mode == "changed":
        assert out["notes"] == [out["changed"]] and "השתנתה" in out["changed"]   # the localized note, not "failed"


@pytest.mark.parametrize("origin", ["model", "user"])
@pytest.mark.parametrize("with_meta", [True, False])
def test_voice_confirm_works_for_every_origin_and_popup_type(prefs, origin, with_meta):
    p, pid, _ = prefs                                     # review R3: approval_meta runs on every path pref_confirm takes
    from jarves.skills import SkillRegistry
    reg = SkillRegistry()

    async def popup(prompt, **meta):
        return True
    if with_meta:
        popup.accepts_meta = True
    reg.confirm = popup
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    r = asyncio.run(reg.call("pref_confirm", {"id": pid}, origin=origin))
    if with_meta:
        assert r.ok and p.get(pid)["state"] == "confirmed"
    else:                                                 # a plain yes/no prompt never showed the wording: fail closed
        assert not r.ok and r.code == "PREF_NOT_SHOWN" and p.get(pid)["state"] == "proposed"


def test_the_model_cannot_supply_the_shown_version(prefs):
    p, pid, v = prefs                                     # review R4: `_shown` is not an argument
    confirm = {t.name: t for t in prefs_mod.make_skill(p).tools()}["pref_confirm"]
    args = confirm.args.model_validate({"id": pid, "_shown": v})
    assert args._shown is None
    r = asyncio.run(confirm.handler(args))
    assert not r.ok and r.code == "PREF_NOT_SHOWN" and p.get(pid)["state"] == "proposed"
    reg = _voice(p, lambda: None)
    reg.confirm = lambda prompt, **meta: asyncio.sleep(0, result=False)          # the owner says no
    reg.confirm.accepts_meta = True
    assert not asyncio.run(reg.call("pref_confirm", {"id": pid, "_shown": v}, origin="model")).ok
    assert p.get(pid)["state"] == "proposed"


def test_dashboard_confirm_from_an_old_page_is_told_to_reload(prefs):
    p, pid, _ = prefs
    st, body = prefs_mod.api_pref_op(SimpleNamespace(_prefs=p), None, {"op": "confirm", "id": pid})
    assert st == 400 and body["code"] == "PREF_PAGE_OLD" and body["error_he"] and p.get(pid)["state"] == "proposed"


@pytest.mark.parametrize("shown", [None, "current"])
def test_confirm_of_an_item_removed_after_the_read_is_not_found_not_changed(prefs, shown):
    p, pid, ver = prefs
    row, real_get = p.get(pid), p.get
    p.remove(pid, by="dashboard")
    p.get = lambda i: row if i == pid else real_get(i)    # the removal lands between get() and the UPDATE
    with pytest.raises(KeyError):
        p.confirm(pid, by="cli", shown=ver if shown else None)
    assert prefs_mod.api_pref_op(SimpleNamespace(_prefs=p), None, {"op": "confirm", "id": pid, "updated": ver})[0] == 404


def test_phone_confirm_without_the_version_cannot_confirm_after_an_a_b_a_rewrite(prefs, home):
    p, pid, _ = prefs                                     # review (gpt blocker): the old words match again, the version not
    mob = SimpleNamespace(bridge=None, _prefs=p, home=home)
    _edit_to_b(p, pid)
    p.edit(pid, A, by="voice")                            # back to A: a cached page's words match once more
    st, body = mobile_prefs.action(mob, {"op": "confirm", "pref": pid, "text": A}, "dev")
    assert st == 400 and body["code"] == "PREF_PAGE_OLD" and body["error_he"] and p.get(pid)["state"] == "proposed"


PHONE_API_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const body = src.slice(src.indexOf("  async function api("), src.indexOf("  const post = "));
const ctx = { JSON, Error, Promise, console, token: "t", lang: "he", t: (k) => k, setConn() {}, forget() {},
  fetch: async () => ({ ok: false, status: 409, json: async () => ({ error: "changed", error_he: "השתנתה", code: "PREF_CHANGED" }) }) };
vm.createContext(ctx);
vm.runInContext(body + "\nthis.api = api;", ctx);
ctx.api("/api/m/prefs/op", { method: "POST" }).catch((e) => console.log(JSON.stringify({ code: e.code, status: e.status, msg: e.message })));
"""


def test_the_real_phone_api_copies_the_error_code_for_the_changed_note(tmp_path):
    out = _node(tmp_path, PHONE_API_HARNESS, str(STATIC / "mobile" / "app.js"), STATIC / "mobile" / "app.js")
    assert out == {"code": "PREF_CHANGED", "status": 409, "msg": "השתנתה"}   # review (sonnet): prefs.js reads e.code


def test_only_confirms_that_show_the_details_count_as_showing_the_wording(home):
    from jarves.app import Core                           # review: approval_meta runs only when the owner sees its details
    from jarves.approvals import audited
    from jarves.skills import shows_details

    async def plain(prompt):
        return False

    async def meta_aware(prompt, **meta):
        return False
    meta_aware.accepts_meta = True
    assert shows_details(Core.ask_confirm)                # production: the approvals popup shows the details
    assert not shows_details(plain) and shows_details(meta_aware)
    # audited() still takes meta for its audit record, but its terminal confirm gets the prompt only
    assert audited(home, plain).accepts_meta and not shows_details(audited(home, plain))
    assert not shows_details(audited(home, meta_aware))


def test_voice_confirm_behind_the_audited_terminal_confirm_is_not_shown(prefs, home):
    p, pid, _ = prefs                                     # review R1: the #1542 blocker one wrapper deeper
    from jarves.approvals import audited
    from jarves.skills import SkillRegistry

    async def plain_yes(prompt):                          # a terminal yes/no: the wording is not on screen
        return True
    reg = SkillRegistry()
    reg.confirm = audited(home, plain_yes)
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    r = asyncio.run(reg.call("pref_confirm", {"id": pid}, origin="user"))
    assert not r.ok and r.code == "PREF_NOT_SHOWN" and p.get(pid)["state"] == "proposed"


def test_every_learned_prefs_update_moves_the_version_forward():
    import re                                             # review R3: no UPDATE may set `updated` from the clock alone
    src = Path(prefs_mod.__file__).read_text(encoding="utf-8")
    updates = re.findall(r'"UPDATE learned_prefs SET[^"]*"(?:\s*\+\s*_BUMP)?', src)
    assert updates and all("_BUMP" in u for u in updates) and "updated=?" not in src.replace("AND updated=?", "")
