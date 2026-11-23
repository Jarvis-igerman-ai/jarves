"""#206 H2 follow-up (#1580): the voice tool, desktop dashboard and CLI confirm only the words the owner saw.

Each test injects a reword (the model's edit, which re-proposes the item) between what the surface showed and the
confirm. Before the fix every surface ran the unguarded confirm and the NEW words ended up confirmed unseen."""
from jarves import cli, dashboard, preferences as prefs_mod
from jarves.config import Config
from jarves.userdata import open_db
from test_v5_pillars import registry

OLD, NEW = "short answers in the morning", "always cc reports to someone else"


def _proposed(home):
    p = prefs_mod.Preferences(open_db(home))
    return p, p.suggest(OLD, "I like short answers in the morning", by="voice")["item"]["id"]


async def test_voice_confirm_applies_only_the_words_the_popup_showed(home):
    p, pid = _proposed(home)
    asked: list = []
    reg = registry(prefs=p, answer=True, asked=asked)
    real = reg.confirm

    async def edit_while_the_popup_is_open(prompt, **meta):
        p.edit(pid, NEW, by="voice", repropose=True)
        return await real(prompt, **meta)
    edit_while_the_popup_is_open.accepts_meta = True
    reg.confirm = edit_while_the_popup_is_open
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert asked[0][1]["details"]["preference"] == OLD
    assert not r.ok and r.code == "PREF_CONFLICT"
    assert p.get(pid)["state"] == "proposed" and p.get(pid)["text"] == NEW and p.for_prompt() == []


async def test_voice_confirm_without_a_race_still_remembers(home):
    p, pid = _proposed(home)
    assert (await registry(prefs=p, answer=True).call("pref_confirm", {"id": pid}, origin="model")).ok
    assert p.for_prompt() == [OLD]


def test_dashboard_confirm_needs_and_checks_the_shown_text(home):
    p, pid = _proposed(home)
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid})
        assert st == 400 and body["code"] == "PREF_TEXT_REQUIRED"
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": 5})
        assert st == 400 and body["code"] == "PREF_TEXT_REQUIRED"
        p.edit(pid, NEW, by="voice", repropose=True)                  # the page still shows OLD
        st, body = prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": OLD})
        assert st == 409 and body["code"] == "PREF_CONFLICT"
        assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []
        assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": NEW})[0] == 200
        assert p.for_prompt() == [NEW]
    finally:
        ctx.close()


def test_cli_confirm_applies_only_the_words_it_prints(home, capsys, monkeypatch):
    p, pid = _proposed(home)
    real = prefs_mod.Preferences.confirm

    def edit_first(self, i, **kw):                                    # a reword lands after the CLI read the item
        self.edit(i, NEW, by="voice", repropose=True)
        return real(self, i, **kw)
    monkeypatch.setattr(prefs_mod.Preferences, "confirm", edit_first)
    assert cli.main(["prefs", "confirm", str(pid)]) == 1
    assert "changed meanwhile" in capsys.readouterr().err
    assert p.get(pid)["state"] == "proposed" and p.get(pid)["text"] == NEW
    monkeypatch.setattr(prefs_mod.Preferences, "confirm", real)
    assert cli.main(["prefs", "confirm", str(pid)]) == 0
    assert NEW in capsys.readouterr().out and p.for_prompt() == [NEW]


async def test_voice_confirm_without_a_shown_popup_fails_closed(home):
    p, pid = _proposed(home)
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    r = await tool.handler(prefs_mod.IdArgs(id=pid))                  # the handler alone: no popup computed the words
    assert not r.ok and r.code == "PREF_CONFLICT" and p.get(pid)["state"] == "proposed"


async def test_a_preference_removed_between_read_and_confirm_answers_not_found(home, monkeypatch):
    p, pid = _proposed(home)

    def gone(self, i, **kw):
        raise KeyError(i)
    monkeypatch.setattr(prefs_mod.Preferences, "confirm", gone)
    r = await registry(prefs=p, answer=True).call("pref_confirm", {"id": pid}, origin="model")
    assert not r.ok and r.code == "PREF_NOT_FOUND"
    ctx = dashboard.Ctx(home, Config())
    try:
        assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid, "text": OLD})[0] == 404
    finally:
        ctx.close()
