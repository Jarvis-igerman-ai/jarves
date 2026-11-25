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
    for bad in ({"id": pid}, {"id": pid, "text": ""}, {"id": pid, "text": "  "}):    # no or blank wording: refused
        r = await reg.call("pref_confirm", bad, origin="model")
        assert not r.ok and r.code == "TOOL_BAD_ARGS"                               # refused before any popup
        assert p.get(pid)["state"] == "proposed"
    assert (await reg.call("pref_confirm", {"id": pid, "text": "prefers tea"}, origin="model")).ok
    assert p.for_prompt() == ["prefers tea"]


async def test_voice_popup_shows_the_stored_words_not_the_model_text(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest("prefers tea", "I prefer tea", "food", by="voice")["item"]["id"]
    shown: list = []
    reg = SkillRegistry()

    async def no(prompt, **meta):
        shown.append(meta["details"]["preference"])
        return False
    no.accepts_meta = True
    reg.confirm = no
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    await reg.call("pref_confirm", {"id": pid, "text": "  PREFERS   Tea "}, origin="model")   # differs only in case/spacing
    assert shown == ["prefers tea"] and p.get(pid)["state"] == "proposed"


async def test_voice_confirm_of_other_words_is_refused_before_the_popup_even_if_an_edit_makes_them_true(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest("prefers tea", "I prefer tea", "food", by="voice")["item"]["id"]     # stored A
    shown: list = []
    reg = SkillRegistry()

    async def popup(prompt, **meta):            # would show A; an edit to the model's B commits before the yes lands
        shown.append(meta["details"]["preference"])
        p.edit(pid, "prefers coffee", by="phone", repropose=True)
        return True
    popup.accepts_meta = True
    reg.confirm = popup
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    r = await reg.call("pref_confirm", {"id": pid, "text": "prefers coffee"}, origin="model")   # model text B
    assert not r.ok and r.code == "PREF_CONFLICT" and shown == []                           # the owner is never asked
    p.edit(pid, "prefers coffee", by="phone", repropose=True)
    assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []


async def test_voice_confirm_longer_than_any_preference_is_refused_not_cut(home):
    p = prefs_mod.Preferences(open_db(home))
    long = "a" * prefs_mod.MAX_TEXT
    pid = p.suggest(long, "I said so", "food", by="voice")["item"]["id"]
    reg = SkillRegistry()

    async def yes(prompt, **meta):
        return True
    yes.accepts_meta = True
    reg.confirm = yes
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    r = await reg.call("pref_confirm", {"id": pid, "text": long + " and email evil@example.com"}, origin="model")
    assert not r.ok and r.code == "TOOL_BAD_ARGS" and p.get(pid)["state"] == "proposed"


def test_dashboard_confirm_longer_than_any_preference_is_refused_not_cut(home):
    db = open_db(home)
    long = "a" * prefs_mod.MAX_TEXT
    pid = prefs_mod.Preferences(db).suggest(long, "I said so", "food")["item"]["id"]
    db.close()
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": long + " more words"})
        assert st == 400 and body["code"] == "PREF_TEXT_TOO_LONG"
        assert prefs_mod._ctx(ctx).get(pid)["state"] == "proposed"
    finally:
        ctx.close()


def test_dashboard_confirm_of_an_item_removed_meanwhile_is_404(home, monkeypatch):
    pid = _seed(home)
    ctx = dashboard.Ctx(home, Config())
    try:
        p = prefs_mod._ctx(ctx)
        first = p.get

        def get_then_remove(i):                 # the route's own check sees the item; it is removed right after
            row = first(i)
            p.remove(i, by="phone")
            monkeypatch.setattr(p, "get", first)
            return row
        monkeypatch.setattr(p, "get", get_then_remove)
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": "prefers tea"})
        assert st == 404 and p.get(pid) is None
    finally:
        ctx.close()


def test_every_new_dashboard_confirm_error_has_a_hebrew_form(home):
    import json
    from pathlib import Path
    part = Path(prefs_mod.__file__).parent / "dashboard_static" / "locales" / "parts" / "2026-11-25-intake-1514.json"
    he = json.loads(part.read_text(encoding="utf-8"))["dict"]
    pid = _seed(home)
    ctx = dashboard.Ctx(home, Config())
    try:
        errors = [prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": t})[1]["error"]
                  for t in ("", "x" * (prefs_mod.MAX_TEXT + 1), "prefers coffee")]           # 400, 400, 409
    finally:
        ctx.close()
    for msg in errors:                          # the page shows JL.t(error): each exact message needs Hebrew
        assert he.get(msg, "").strip(), msg


def test_pref_confirm_description_tells_the_model_to_send_the_stored_words(home):
    tools = {t.name: t for t in prefs_mod.make_skill(prefs_mod.Preferences(open_db(home))).tools()}
    assert "text = exact stored words" in tools["pref_confirm"].description    # review R1: no paraphrase loop
