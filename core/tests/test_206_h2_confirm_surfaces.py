"""#206 H2 follow-up (#1580): the voice tool, desktop dashboard and CLI confirm only the words the owner saw.

Each test injects a reword (the model's edit, which re-proposes the item) between what the surface showed and the
confirm. Before the fix every surface ran the unguarded confirm and the NEW words ended up confirmed unseen."""
import pytest

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


def test_cli_confirm_with_the_listed_words_refuses_a_reword_made_after_list(home, capsys):
    p, pid = _proposed(home)
    assert cli.main(["prefs", "list"]) == 0 and OLD in capsys.readouterr().out       # what the owner saw
    p.edit(pid, NEW, by="voice", repropose=True)                      # the model rewords it before the confirm
    assert cli.main(["prefs", "confirm", str(pid), "--text", OLD]) == 1
    assert "changed meanwhile" in capsys.readouterr().err
    assert p.get(pid)["state"] == "proposed" and p.for_prompt() == []
    assert cli.main(["prefs", "confirm", str(pid), "--text", NEW]) == 0
    assert NEW in capsys.readouterr().out and p.for_prompt() == [NEW]


def test_cli_confirm_without_text_needs_a_terminal_and_a_yes(home, capsys, monkeypatch):
    p, pid = _proposed(home)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["prefs", "confirm", str(pid)]) == 2               # nothing to ask in: refused, nothing saved
    assert "--text" in capsys.readouterr().err and p.for_prompt() == []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _q: "n")
    assert cli.main(["prefs", "confirm", str(pid)]) == 1 and p.for_prompt() == []

    def reword_while_asking(_q):                                      # the words change while the question is open
        p.edit(pid, NEW, by="voice", repropose=True)
        return "y"
    monkeypatch.setattr("builtins.input", reword_while_asking)
    assert cli.main(["prefs", "confirm", str(pid)]) == 1
    assert OLD in capsys.readouterr().out and p.for_prompt() == []    # it printed OLD and did not save NEW
    monkeypatch.setattr("builtins.input", lambda _q: "y")
    assert cli.main(["prefs", "confirm", str(pid)]) == 0 and p.for_prompt() == [NEW]


async def test_voice_confirm_by_the_user_origin_also_shows_the_popup_and_works(home):
    p, pid = _proposed(home)
    asked: list = []
    assert (await registry(prefs=p, answer=True, asked=asked).call("pref_confirm", {"id": pid}, origin="user")).ok
    assert asked and p.for_prompt() == [OLD]                          # no origin skips the popup that binds the words


async def test_a_denied_popup_leaves_nothing_for_a_later_confirm(home):
    p, pid = _proposed(home)
    reg = registry(prefs=p, answer=False)
    assert not (await reg.call("pref_confirm", {"id": pid}, origin="model")).ok
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    assert (await tool.handler(prefs_mod.ConfirmArgs(id=pid))).code == "PREF_NOT_SHOWN"
    assert prefs_mod.ConfirmArgs.model_validate({"id": pid, "_shown": OLD})._shown is None   # the model cannot set it
    assert p.for_prompt() == []


def test_dashboard_confirm_errors_have_hebrew_text():
    from jarves import locale_parts
    he = locale_parts.catalog("he")["dict"]
    for en in ("confirm needs the text you saw: reload the page", "the preference changed meanwhile: reload the page"):
        assert en in he and he[en] != en                              # the page shows JL.t(error): Hebrew exists


async def test_voice_confirm_without_a_shown_popup_fails_closed(home):
    p, pid = _proposed(home)
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    r = await tool.handler(prefs_mod.ConfirmArgs(id=pid))                  # the handler alone: no popup computed the words
    assert not r.ok and r.code == "PREF_NOT_SHOWN" and "Do not retry" in r.message and p.get(pid)["state"] == "proposed"


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


async def test_an_over_long_legacy_row_is_refused_with_a_way_out_not_a_dead_conflict(home):
    p, pid = _proposed(home)
    long = "x " * prefs_mod.MAX_TEXT                                   # longer than any write allows today
    p.db.x("UPDATE learned_prefs SET text=? WHERE id=?", (long, pid))
    asked: list = []
    r = await registry(prefs=p, answer=True, asked=asked).call("pref_confirm", {"id": pid}, origin="model")
    assert len(asked[0][1]["details"]["preference"]) == prefs_mod.MAX_TEXT     # the popup shows only part of it
    assert not r.ok and r.code == "PREF_TOO_LONG" and "shorten" in r.message and p.for_prompt() == []


def test_dashboard_only_maps_a_confirm_keyerror_to_404(home, monkeypatch):
    p, pid = _proposed(home)

    def bug(self, i, **kw):
        raise KeyError("unrelated")
    monkeypatch.setattr(prefs_mod.Preferences, "decline", bug)
    ctx = dashboard.Ctx(home, Config())
    try:
        with pytest.raises(KeyError):                                 # a real bug is not hidden as "no such preference"
            prefs_mod.api_pref_op(ctx, None, {"op": "decline", "id": pid})
    finally:
        ctx.close()


@pytest.mark.parametrize("origin", ["model", "user"])
async def test_every_approval_path_for_pref_confirm_runs_approval_meta(home, origin):
    # review R2: the words are bound only by approval_meta; pin that no allowed path (any origin, an approval that
    # answers yes without a person, e.g. a standing allow) reaches the handler without it, so no PREF_NOT_SHOWN loop.
    p, pid = _proposed(home)
    tool = next(t for t in prefs_mod.make_skill(p).tools() if t.name == "pref_confirm")
    assert tool.confirm and tool.approval_meta is not None and tool.permission == "action"
    calls: list = []
    reg = registry(prefs=p, answer=True)
    real = reg._tools["pref_confirm"][1].approval_meta
    reg._tools["pref_confirm"][1].approval_meta = lambda a: calls.append(a) or real(a)
    r = await reg.call("pref_confirm", {"id": pid}, origin=origin)
    assert r.ok and len(calls) == 1 and p.for_prompt() == [OLD]


@pytest.mark.parametrize("permission", ["action", "destructive"])
@pytest.mark.parametrize("origin", ["model", "user"])
async def test_registry_hands_approval_meta_and_the_handler_the_same_args_object(origin, permission):
    # review R2 of #1680: pref_confirm binds the shown words on the args object approval_meta sees, so the registry
    # must pass that very object to the handler for every tool with approval_meta (a copy would fail every confirm).
    from pydantic import BaseModel
    from jarves.skills import Skill, SkillRegistry, Tool, ToolResult

    class A(BaseModel):
        x: int = 0
    seen: dict = {}

    async def handler(a):
        seen["handler"] = a
        return ToolResult(True, "done")

    class S(Skill):
        name = "probe"

        def tools(self):
            return [Tool("probe_act", "probe", handler, A, permission=permission, confirm="Do it?",
                         approval_meta=lambda a: seen.setdefault("meta", a) and {})]
    reg = SkillRegistry()

    async def yes(prompt, **meta):
        return True
    yes.accepts_meta = True
    reg.confirm = yes
    reg.register(S())
    reg.allowed_actions |= {"probe_act"}
    r = await reg.call("probe_act", {"x": 1}, origin=origin)
    assert r.ok, r.message
    assert seen["meta"] is seen["handler"]


async def test_confirm_after_a_conflict_asks_again_and_is_not_replayed(home):
    # review R3 of #1680: a PREF_CONFLICT must not be cached or replayed; re-asking the owner with the same id shows
    # the NEW words and confirms exactly those.
    p, pid = _proposed(home)
    asked: list = []
    reg = registry(prefs=p, answer=True, asked=asked)
    real = reg.confirm

    async def edit_once(prompt, **meta):
        if len(asked) == 0:
            p.edit(pid, NEW, by="voice", repropose=True)
        return await real(prompt, **meta)
    edit_once.accepts_meta = True
    reg.confirm = edit_once
    first = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert not first.ok and first.code == "PREF_CONFLICT"
    again = await reg.call("pref_confirm", {"id": pid}, origin="model")
    assert again.ok and "already_done" not in again.data and "cached" not in again.data
    assert [a[1]["details"]["preference"] for a in asked] == [OLD, NEW] and p.for_prompt() == [NEW]
