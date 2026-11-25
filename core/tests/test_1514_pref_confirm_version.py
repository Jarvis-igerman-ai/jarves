"""#1514: a preference confirm carries the words the owner was shown; a confirm for old words after an edit leaves the
new words proposed (voice approval and dashboard; the phone path is covered in test_mobile_prefs)."""
from jarves import dashboard
from jarves import preferences as prefs_mod
from jarves.config import Config
from jarves.skills import SkillRegistry
from jarves.userdata import open_db

OLD, NEW = "short answers in the morning", "long answers in the morning"


def _registry(p, during_approval=None):
    reg, asked = SkillRegistry(), []

    async def confirm(prompt, **meta):
        asked.append(meta)
        if during_approval:
            during_approval()             # the edit commits while the owner looks at the old words
        return True
    confirm.accepts_meta = True
    reg.confirm = confirm
    reg.register(prefs_mod.make_skill(p))
    reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg, asked


async def test_voice_confirm_of_old_words_after_an_edit_leaves_the_new_words_proposed(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(OLD, "I prefer short answers", "voice")["item"]["id"]
    reg, asked = _registry(p, during_approval=lambda: p.edit(pid, NEW, by="voice", repropose=True))
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert asked[0]["details"]["preference"] == OLD
    assert not r.ok and r.code == "PREF_CONFLICT"
    assert p.get(pid)["state"] == "proposed" and p.get(pid)["text"] == NEW and p.for_prompt() == []
    reg2, asked2 = _registry(p)                                   # asked again with the new words: confirmed
    r = await reg2.call("pref_confirm", {"id": pid}, origin="model")
    assert r.ok and asked2[0]["details"]["preference"] == NEW and p.for_prompt() == [NEW]


async def test_voice_confirm_without_a_shown_approval_is_not_applied(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(OLD, "I prefer short answers", "voice")["item"]["id"]
    reg, _ = _registry(p)
    tool = next(t for s in reg.skills.values() for t in s.tools() if t.name == "pref_confirm")
    r = await tool.handler(prefs_mod.ConfirmArgs(id=pid))             # the handler run without the approval step
    assert not r.ok and r.code == "PREF_NOT_SHOWN" and p.get(pid)["state"] == "proposed"


def test_dashboard_confirm_carries_the_shown_words(home):
    db = open_db(home)
    p = prefs_mod.Preferences(db)
    pid = p.suggest(OLD, "I prefer short answers", "voice")["item"]["id"]
    p.edit(pid, NEW, by="voice", repropose=True)
    db.close()
    ctx = dashboard.Ctx(home, Config())
    try:
        st, out = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": OLD})
        assert st == 409 and out["code"] == "PREF_CONFLICT"
        assert prefs_mod.api_prefs(ctx, None, None)[1]["proposed"][0]["text"] == NEW
        st, out = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid})
        assert st == 400 and out["code"] == "PREF_TEXT_REQUIRED"
        st, out = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": NEW})
        assert st == 200 and out["item"]["state"] == "confirmed"
    finally:
        ctx.close()


async def test_user_origin_confirm_also_runs_the_approval_and_binds_its_words(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest(OLD, "I prefer short answers", "voice")["item"]["id"]
    reg, asked = _registry(p, during_approval=lambda: p.edit(pid, NEW, by="voice", repropose=True))
    r = await reg.call("pref_confirm", {"id": pid}, origin="user")
    assert asked[0]["details"]["preference"] == OLD and not r.ok and r.code == "PREF_CONFLICT"
    assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []


async def test_a_suggestion_longer_than_the_cap_is_stored_capped_and_both_confirms_succeed(home):
    p = prefs_mod.Preferences(open_db(home))
    long_text = "short answers " * 30                                  # 420 chars, over MAX_TEXT
    pid = p.suggest(long_text, "I prefer short answers", "voice")["item"]["id"]
    stored = p.get(pid)["text"]
    assert len(stored) <= prefs_mod.MAX_TEXT
    reg, asked = _registry(p)
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert r.ok and asked[0]["details"]["preference"] == stored and p.get(pid)["state"] == "confirmed"
    pid2 = p.suggest("long " + long_text, "I prefer", "voice")["item"]["id"]
    assert p.confirm(pid2, by="dashboard", expect=p.get(pid2)["text"])["item"]["state"] == "confirmed"


def test_the_text_required_message_has_hebrew():
    from jarves import locale_parts
    assert "confirm needs the text you saw: reload the page" in locale_parts.catalog("he")["dict"]
