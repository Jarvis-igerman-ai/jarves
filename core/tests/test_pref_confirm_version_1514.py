"""#1514: a preference confirm carries the wording it was shown, on the dashboard API and the voice tool.

The order the issue names: the owner is shown wording A and taps confirm, an edit to wording B commits, then the
in-flight confirm lands. It must leave B proposed (a defined conflict code), while a confirm of the current words works.
"""
from jarves import dashboard, preferences as prefs_mod
from jarves.config import Config
from jarves.skills import SkillRegistry
from jarves.userdata import open_db


def _seed(home, text="prefers tea"):
    db = open_db(home)
    pid = prefs_mod.Preferences(db).suggest(text, "I prefer tea", "food")["item"]["id"]
    db.close()
    return pid


def test_dashboard_confirm_of_old_wording_after_an_edit_leaves_the_new_wording_proposed(home):
    pid = _seed(home)
    ctx = dashboard.Ctx(home, Config())
    try:
        p = prefs_mod._ctx(ctx)
        p.edit(pid, "prefers green tea", by="phone", repropose=True)            # the edit commits first
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": "prefers tea"})
        assert st == 409 and body["code"] == "PREF_CONFLICT"
        assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": "prefers green tea"})
        assert st == 200 and p.get(pid)["state"] == "confirmed"
    finally:
        ctx.close()


def test_dashboard_confirm_without_the_shown_wording_is_refused(home):
    pid = _seed(home)
    ctx = dashboard.Ctx(home, Config())
    try:
        for extra in ({}, {"text": "  "}, {"text": 5}):
            st, body = prefs_mod.api_pref_op(ctx, None, dict({"op": "confirm", "id": pid}, **extra))
            assert st == 400 and body["code"] == "PREF_TEXT_REQUIRED"
        assert prefs_mod._ctx(ctx).get(pid)["state"] == "proposed"
    finally:
        ctx.close()


async def test_voice_confirm_that_lands_after_an_edit_during_the_popup_is_not_applied(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest("prefers tea", "I prefer tea", "food", by="voice")["item"]["id"]
    shown: list = []
    reg = SkillRegistry()

    async def popup(prompt, **meta):            # the owner sees wording A; the edit to B commits before the yes lands
        shown.append(meta["details"]["preference"])
        p.edit(pid, "prefers green tea", by="phone", repropose=True)
        return True
    popup.accepts_meta = True
    reg.confirm = popup
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    r = await reg.call("pref_confirm", {"id": pid, "text": "prefers tea"}, origin="model")
    assert shown == ["prefers tea"]
    assert not r.ok and r.code == "PREF_CONFLICT" and p.get(pid)["state"] == "proposed" and p.for_prompt() == []


async def test_voice_confirm_of_the_current_wording_confirms_and_a_missing_wording_is_rejected(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest("prefers tea", "I prefer tea", "food", by="voice")["item"]["id"]
    reg = SkillRegistry()

    async def yes(prompt, **meta):
        return True
    yes.accepts_meta = True
    reg.confirm = yes
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    assert not (await reg.call("pref_confirm", {"id": pid}, origin="model")).ok          # no wording: invalid args
    assert p.get(pid)["state"] == "proposed"
    assert (await reg.call("pref_confirm", {"id": pid, "text": "prefers tea"}, origin="model")).ok
    assert p.for_prompt() == ["prefers tea"]
