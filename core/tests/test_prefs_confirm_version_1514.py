"""#1514: a preference confirm carries the wording the owner was shown. A confirm for old words that lands after an
edit leaves the new words proposed (dashboard API and the voice confirm path; the phone op is covered in
test_mobile_prefs.py)."""
from jarves import dashboard
from jarves import preferences as prefs_mod
from jarves.config import Config
from jarves.userdata import open_db
from test_v5_pillars import registry


def _proposed(home, text="short answers in the morning"):
    db = open_db(home)
    pid = prefs_mod.Preferences(db).suggest(text, "I like short answers", "voice")["item"]["id"]
    db.close()
    return pid


def test_1514_dashboard_confirm_of_old_words_after_an_edit_leaves_the_new_words_proposed(home):
    pid = _proposed(home)
    ctx = dashboard.Ctx(home, Config())
    try:
        p = prefs_mod._ctx(ctx)
        p.edit(pid, "long answers all day", by="phone", repropose=True)        # lands after the page showed the old words
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": "short answers in the morning"})
        assert st == 409 and body["code"] == "PREF_CONFLICT" and "reload the page" in body["error"]
        assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid})        # no shown words: refused
        assert st == 400 and body["code"] == "PREF_TEXT_REQUIRED" and p.get(pid)["state"] == "proposed"
        st, _ = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": "long answers all day"})
        assert st == 200 and p.for_prompt() == ["long answers all day"]
    finally:
        ctx.close()


async def test_1514_voice_confirm_applies_only_to_the_words_the_approval_showed(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest("short answers in the morning", "I like short answers", "voice")["item"]["id"]
    asked: list = []

    async def answer(prompt, **meta):                 # the owner sees wording A; wording B is saved before the tap
        asked.append(meta["details"]["preference"])
        p.edit(pid, "long answers all day", by="phone", repropose=True)
        return True
    answer.accepts_meta = True
    reg = registry(prefs=p)
    reg.confirm = answer
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert asked == ["short answers in the morning"]
    assert not r.ok and r.code == "PREF_CONFLICT"
    assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []
    ok = registry(prefs=p, answer=True)
    assert (await ok.call("pref_confirm", {"id": pid}, origin="model")).ok      # shown the current words: confirmed
    assert p.for_prompt() == ["long answers all day"]


async def test_1514_voice_confirm_without_shown_words_fails_closed(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest("short answers in the morning", "I like short answers", "voice")["item"]["id"]
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    r = await tool.handler(tool.args(id=pid))           # the approval popup never ran for these args
    assert not r.ok and r.code == "PREF_NOT_SHOWN"
    assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []


async def test_1514_user_origin_confirm_also_runs_the_popup_and_binds_its_words(home):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.suggest("short answers in the morning", "I like short answers", "voice")["item"]["id"]
    asked: list = []
    r = await registry(prefs=p, answer=True, asked=asked).call("pref_confirm", {"id": pid}, origin="user")
    assert r.ok and asked and asked[0][1]["details"]["preference"] == "short answers in the morning"
    assert p.for_prompt() == ["short answers in the morning"]


def test_1514_dashboard_conflict_stays_409_even_if_it_were_a_value_error(home, monkeypatch):
    pid = _proposed(home)

    class Both(prefs_mod.PrefConflict, ValueError):
        pass

    def boom(self, *a, **k):
        raise Both(pid)
    monkeypatch.setattr(prefs_mod.Preferences, "confirm", boom)
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": "short answers in the morning"})
        assert st == 409 and body["code"] == "PREF_CONFLICT"
    finally:
        ctx.close()
