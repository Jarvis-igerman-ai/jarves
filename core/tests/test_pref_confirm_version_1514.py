"""#1514: a preference confirm carries the wording it was shown (dashboard and voice), like the phone op does.

The order the issue names: wording A is shown, the owner says yes, an edit to B commits, then the confirm lands.
That late confirm must leave B proposed; a confirm for the current wording still confirms it.
"""
from jarves import dashboard
from jarves import preferences as prefs_mod
from jarves.config import Config
from jarves.skills import SkillRegistry
from jarves.userdata import open_db

A, B = "short answers in the morning", "long answers in the evening"


def _proposed(home, text=A):
    db = open_db(home)
    pid = prefs_mod.Preferences(db).suggest(text, "I said so", "voice")["item"]["id"]
    db.close()
    return pid


# ---------------------------------------------------------------------- dashboard
def test_dashboard_confirm_for_old_wording_after_an_edit_leaves_the_new_wording_proposed(home):
    pid = _proposed(home)
    ctx = dashboard.Ctx(home, Config())
    p = prefs_mod._ctx(ctx)
    shown = prefs_mod.api_prefs(ctx, None, None)[1]["proposed"][0]["text"]     # what pillars.js renders and sends
    p.edit(pid, B, by="phone", repropose=True)                                  # the edit commits first
    st, out = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": shown})
    assert st == 409 and out["code"] == "PREF_CONFLICT"
    assert p.get(pid)["state"] == "proposed" and p.get(pid)["text"] == B and p.for_prompt() == []
    shown = prefs_mod.api_prefs(ctx, None, None)[1]["proposed"][0]["text"]     # the page reloads: B is shown
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": shown})[0] == 200
    assert p.get(pid)["state"] == "confirmed" and p.for_prompt() == [B]


def test_dashboard_confirm_without_the_shown_text_is_refused(home):
    pid = _proposed(home)
    ctx = dashboard.Ctx(home, Config())
    for bad in ({}, {"text": ""}, {"text": "   "}, {"text": 7}, {"text": None}):
        st, out = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, **bad})
        assert st == 400 and out["code"] == "PREF_TEXT_REQUIRED"
    assert prefs_mod._ctx(ctx).get(pid)["state"] == "proposed"


def test_dashboard_confirm_of_an_item_removed_meanwhile_is_404(home):
    pid = _proposed(home)
    ctx = dashboard.Ctx(home, Config())
    p = prefs_mod._ctx(ctx)
    real = p.confirm

    def removed_first(i, **kw):                      # removed after the route's existence check
        p.remove(i, by="test")
        return real(i, **kw)
    p.confirm = removed_first
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": A})[0] == 404


# ---------------------------------------------------------------------- voice
def _registry(p, on_ask):
    reg = SkillRegistry()

    async def confirm(prompt, **meta):
        return on_ask(meta)
    confirm.accepts_meta = True
    reg.confirm = confirm
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg


async def test_voice_confirm_for_old_wording_after_an_edit_leaves_the_new_wording_proposed(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I said so", "voice")["item"]["id"]
    seen: list = []

    def edit_while_asking(meta):                     # the owner hears A; the edit to B commits before the yes lands
        seen.append(meta["details"]["preference"])
        if len(seen) == 1:
            p.edit(pid, B, by="phone", repropose=True)
        return True
    reg = _registry(p, edit_while_asking)
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert not r.ok and r.code == "PREF_CONFLICT" and seen == [A]
    assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")      # asked again, now with B
    assert r.ok and seen == [A, B] and p.for_prompt() == [B]


async def test_voice_confirm_handler_that_was_never_shown_the_wording_does_not_confirm(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I said so", "voice")["item"]["id"]
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    r = await tool.handler(tool.args(id=pid))                            # no approval step ran (nothing was shown)
    assert not r.ok and r.code == "PREF_NOT_SHOWN" and p.get(pid)["state"] == "proposed"


async def test_voice_confirm_through_a_plain_confirm_callback_binds_and_confirms_the_current_wording(home):
    """Review R1: a confirm(prompt) callback without `accepts_meta` still binds the wording before the ask."""
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(A, "I said so", "voice")["item"]["id"]
    reg, prompts = SkillRegistry(), []

    async def plain(prompt):                         # no **meta, no accepts_meta
        prompts.append(prompt)
        if len(prompts) == 1:
            p.edit(pid, B, by="phone", repropose=True)
        return True
    reg.confirm = plain
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert not r.ok and r.code == "PREF_CONFLICT" and p.get(pid)["state"] == "proposed"
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert r.ok and len(prompts) == 2 and p.for_prompt() == [B]


async def test_a_max_length_preference_confirms_on_dashboard_and_voice(home):
    """Review R2: every writer clamps to MAX_TEXT, so the [:MAX_TEXT] copy both paths send matches the stored words."""
    long_a = ("word " * prefs_mod.MAX_TEXT)[:prefs_mod.MAX_TEXT + 50]
    pid = _proposed(home, long_a)
    ctx = dashboard.Ctx(home, Config())
    p = prefs_mod._ctx(ctx)
    shown = prefs_mod.api_prefs(ctx, None, None)[1]["proposed"][0]["text"]
    assert len(shown) <= prefs_mod.MAX_TEXT
    assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": shown})[0] == 200
    p.edit(pid, "x" + long_a, by="phone", repropose=True)                 # stored clamped again, now proposed
    assert p.get(pid)["state"] == "proposed"
    r = await _registry(p, lambda meta: True).call("pref_confirm", {"id": pid}, origin="model")
    assert r.ok and p.get(pid)["state"] == "confirmed"


def test_dashboard_confirm_errors_reach_the_owner_in_hebrew(home):
    """Review R2: pillars.js shows JL.t(error); both new confirm errors have a Hebrew catalog text."""
    from jarves import locale_parts
    pid = _proposed(home)
    ctx = dashboard.Ctx(home, Config())
    errors = [prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid})[1]["error"]]
    prefs_mod._ctx(ctx).edit(pid, B, by="phone", repropose=True)
    errors.append(prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": A})[1]["error"])
    he = locale_parts.catalog("he")["dict"]
    assert all(he.get(e) and he[e] != e for e in errors), errors
