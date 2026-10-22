"""V5 pillars, software slice: Goals and Adaptive Memory (learned preferences). Real stores (SQLite in tmp), the real
Skill registry with a scripted owner, the real dashboard route handlers and the CLI through jarves.cli.main. No
network, no browser, no sleeping: clocks are injected and the owner's answers are event-driven."""
from __future__ import annotations

import json

import pytest

from jarves import cli, dashboard, goals as goals_mod, preferences as prefs_mod
from jarves.config import Config
from jarves.features import CATALOG
from jarves.skills import SkillRegistry
from jarves.userdata import open_db
from owner_harness import OwnerAnswers

DAY = 86400.0
T0 = 1_800_000_000.0                     # a fixed "now"


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def registry(goals=None, prefs=None, answer=None, asked=None):
    reg = SkillRegistry()

    async def confirm(prompt, **meta):
        if asked is not None:
            asked.append((prompt, meta))
        return bool(answer)
    confirm.accepts_meta = True
    reg.confirm = confirm
    if goals is not None:
        reg.register(goals_mod.make_skill(goals, reg))
        reg.allowed_actions |= goals_mod.ALLOWED_ACTIONS
    if prefs is not None:
        reg.register(prefs_mod.make_skill(prefs))
        reg.allowed_actions |= prefs_mod.ALLOWED_ACTIONS
    return reg


# ---------------------------------------------------------------------- goals
async def test_goal_set_by_voice_tracked_by_steps_and_seen_on_dashboard_and_cli(home, capsys):
    db = open_db(home)
    g = goals_mod.Goals(db)
    reg = registry(g)
    r = await reg.call("goal_add", {"title": "לרוץ עשרה קילומטר", "target_date": "2026-12-31",
                                    "steps": ["לקנות נעליים", "לרוץ שלושה קילומטר"]}, origin="model")
    assert r.ok, r.message
    gid = r.data["goal"]["id"]
    steps = r.data["goal"]["steps"]
    assert r.data["goal"]["progress"] == 0 and len(steps) == 2
    r = await reg.call("goal_step_done", {"step_id": steps[0]["id"]}, origin="model")
    assert r.ok and r.data["goal"]["progress"] == 50
    db.close()

    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = goals_mod.api_goals(ctx, None, None)
        assert st == 200 and body["goals"][0]["title"] == "לרוץ עשרה קילומטר" and body["goals"][0]["progress"] == 50
        st, body = goals_mod.api_goal_op(ctx, None, {"op": "step_set", "step_id": steps[1]["id"], "done": True})
        assert st == 200 and body["goal"]["progress"] == 100
        st, _ = goals_mod.api_goal_add(ctx, None, {"title": "ללמוד גיטרה", "steps": "תרגול יומי, שיעור ראשון"})
        assert st == 200
        assert goals_mod.api_goal_op(ctx, None, {"op": "status", "id": gid, "status": "bogus"})[0] == 400
        assert goals_mod.api_goal_op(ctx, None, {"op": "update", "id": gid, "title": "10K"})[1]["goal"]["title"] == "10K"
        assert goals_mod.api_goal_op(ctx, None, {"op": "update", "id": 999, "title": "x"})[0] == 404
    finally:
        ctx.close()

    assert cli.main(["goals", "list"]) == 0
    out = capsys.readouterr().out
    assert "10K" in out and "100%" in out and "ללמוד גיטרה" in out
    assert cli.main(["goals", "add", "read a book", "--step", "pick one"]) == 0
    assert cli.main(["goals", "status", str(gid), "paused"]) == 0
    assert cli.main(["goals", "status", str(gid), "nonsense"]) == 2
    capsys.readouterr()


def test_goal_without_steps_uses_the_percent_the_owner_sets(home):
    g = goals_mod.Goals(open_db(home))
    x = g.add("save up")
    assert x["progress"] == 0
    assert g.update(x["id"], percent=40)["progress"] == 40
    assert g.update(x["id"], percent=500)["progress"] == 100           # clamped
    assert g.set_status(x["id"], "done")["progress"] == 100


def test_sensitive_or_bad_goal_input_is_refused_and_nothing_is_stored(home):
    db = open_db(home)
    g = goals_mod.Goals(db)
    for bad in ("הסיסמה שלי היא 1234", "save for my credit card 4580 1234 5678 9012", "pay the debt, salary 12000"):
        with pytest.raises(ValueError):
            g.add(bad)
    with pytest.raises(ValueError):
        g.add("fine goal", steps=["my password is hunter2"])
    with pytest.raises(ValueError):
        g.add("   ")
    with pytest.raises(ValueError):
        g.add("dated", target_date="31/12/2026")
    ok = g.add("learn Hebrew poems")
    with pytest.raises(ValueError):
        g.add("Learn Hebrew Poems")                                       # same open title
    with pytest.raises(ValueError):
        g.update(ok["id"], details="passcode 99")
    assert [r["title"] for r in db.rows("SELECT title FROM goals")] == ["learn Hebrew poems"]
    assert db.rows("SELECT * FROM goal_steps") == []                       # the failed add left no half-stored steps


async def test_checkin_only_on_request_and_never_schedules_anything(home):
    db = open_db(home)
    clock = Clock()
    g = goals_mod.Goals(db, clock=clock)
    a = g.add("old goal", target_date="2026-01-01", steps=["one", "two"])
    g.add("fresh goal")
    clock.t += 10 * DAY
    g.update(g.list()[1]["id"], percent=10)                                # touched today
    assert all(x["last_checkin"] is None for x in db.rows("SELECT last_checkin FROM goals"))   # nothing ran by itself
    assert db.rows("SELECT id FROM jobs") == [] and db.rows("SELECT id FROM alerts") == []

    reg = registry(g)
    r = await reg.call("goal_checkin", {}, origin="model")
    assert r.ok
    by_title = {x["title"]: x for x in r.data["goals"]}
    assert by_title["old goal"]["stalled"] and by_title["old goal"]["overdue"] and by_title["old goal"]["next_steps"] == ["one", "two"]
    assert not by_title["fresh goal"]["stalled"]
    assert "past its date" in r.message and "no movement" in r.message
    assert all(x["last_checkin"] == clock.t for x in db.rows("SELECT last_checkin FROM goals"))
    assert db.rows("SELECT id FROM jobs") == [] and db.rows("SELECT id FROM alerts") == []   # still nothing scheduled
    assert a["id"]


async def test_outward_step_needs_the_owners_approval_before_it_counts_as_done(home):
    db = open_db(home)
    g = goals_mod.Goals(db)
    goal = g.add("move flat", steps=["tell the landlord"])
    g.add_step(goal["id"], "email the landlord", outward=True)
    sid = [s["id"] for s in g.get(goal["id"])["steps"] if s["outward"]][0]
    asked: list = []
    deny = registry(g, answer=False, asked=asked)
    r = await deny.call("goal_step_done", {"step_id": sid}, origin="model")
    assert not r.ok and r.code == "NOT_APPROVED" and len(asked) == 1
    assert g.get(goal["id"])["steps_done"] == 0                           # nothing recorded without the owner
    allow = registry(g, answer=True, asked=asked)
    assert (await allow.call("goal_step_done", {"step_id": sid}, origin="model")).ok
    assert g.get(goal["id"])["steps_done"] == 1 and len(asked) == 2
    plain = [s["id"] for s in g.get(goal["id"])["steps"] if not s["outward"]][0]
    assert (await deny.call("goal_step_done", {"step_id": plain}, origin="model")).ok and len(asked) == 2   # inward: no question


async def test_goals_never_call_tools_or_send_anything(home):
    """A goal is a record: setting, stepping and checking in touch no other skill (no tool call, no send)."""
    db = open_db(home)
    g = goals_mod.Goals(db)
    reg = registry(g)
    called = []

    from jarves.skills import Skill, Tool, ToolResult

    class Spy(Skill):
        name = "spy"
        description = "records calls"

        def tools(self):
            async def h(_a):
                called.append(1)
                return ToolResult(True, "x")
            return [Tool("send_message", "send", h, permission="action")]
    reg.register(Spy())
    reg.allowed_actions |= {"send_message"}
    await reg.call("goal_add", {"title": "tell mum", "steps": ["send_message to mum"]}, origin="model")
    await reg.call("goal_checkin", {}, origin="model")
    assert called == []


async def test_goal_delete_asks_everywhere_and_edit_is_free(home):
    db = open_db(home)
    g = goals_mod.Goals(db)
    goal = g.add("temp goal", steps=["a"])
    asked: list = []
    reg = registry(g, answer=False, asked=asked)
    r = await reg.call("goal_delete", {"id": goal["id"]}, origin="model")
    assert not r.ok and g.get(goal["id"]) and asked                       # model origin: owner asked, said no
    reg_yes = registry(g, answer=True, asked=asked)
    assert (await reg_yes.call("goal_delete", {"id": goal["id"]}, origin="model")).ok
    assert g.get(goal["id"]) is None and db.rows("SELECT * FROM goal_steps") == []

    keep = g.add("dashboard goal")
    db.close()
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = goals_mod.api_goal_op(ctx, None, {"op": "delete", "id": keep["id"], "approved": True})
        assert st == 403 and body["needs_approval"]                       # nobody to ask / a flag is not an approval
        with OwnerAnswers(home, False):
            assert goals_mod.api_goal_op(ctx, None, {"op": "delete", "id": keep["id"]})[0] == 403
        assert len(goals_mod.api_goals(ctx, None, None)[1]["goals"]) == 1
        with OwnerAnswers(home, True) as owner:
            st, body = goals_mod.api_goal_op(ctx, None, {"op": "delete", "id": keep["id"]})
        assert st == 200 and body["deleted"] and owner.asked[0]["source"] == "dashboard"
        assert goals_mod.api_goals(ctx, None, None)[1]["goals"] == []
    finally:
        ctx.close()


def test_cli_goal_delete_needs_approval_and_checkin_is_a_command(home, capsys):
    assert cli.main(["goals", "add", "tidy garage", "--step", "buy boxes"]) == 0
    assert cli.main(["goals", "delete", "1"]) == 1                        # no terminal, no --yes
    assert "approval needed" in capsys.readouterr().err
    assert cli.main(["goals", "checkin"]) == 0
    assert "tidy garage" in capsys.readouterr().out
    assert cli.main(["goals", "delete", "1", "--yes"]) == 0
    assert cli.main(["goals", "delete", "1", "--yes"]) == 1
    capsys.readouterr()


# ---------------------------------------------------------------------- adaptive memory
async def test_preference_is_only_a_proposal_until_the_owner_confirms(home):
    db = open_db(home)
    p = prefs_mod.Preferences(db)
    asked: list = []
    reg = registry(prefs=p, answer=False, asked=asked)
    r = await reg.call("pref_suggest", {"text": "short answers in the morning", "evidence": "אני מעדיף תשובות קצרות בבוקר",
                                        "topic": "voice"}, origin="model")
    assert r.ok and r.data["status"] == "proposed" and not asked
    pid = r.data["item"]["id"]
    assert p.for_prompt() == []                                           # a proposal influences nothing
    r = await reg.call("pref_confirm", {"id": pid}, origin="model")       # the owner is asked and says no
    assert not r.ok and asked and p.get(pid)["state"] == "proposed" and p.for_prompt() == []
    yes = registry(prefs=p, answer=True, asked=asked)
    r = await yes.call("pref_confirm", {"id": pid}, origin="model")
    assert r.ok and p.get(pid)["state"] == "confirmed" and p.for_prompt() == ["short answers in the morning"]
    shown = await yes.call("pref_show", {}, origin="model")
    assert shown.ok and "short answers" in shown.message


async def test_sensitive_or_unsupported_suggestions_are_refused_and_never_stored(home):
    db = open_db(home)
    p = prefs_mod.Preferences(db)
    reg = registry(prefs=p)
    cases = [("my bank password is swordfish", "I said my password is swordfish"),
             ("remember my card 4580123456789012", "my card is 4580123456789012"),
             ("I take medication for blood pressure", "I take medication every day"),
             ("אני מרוויח משכורת גבוהה", "המשכורת שלי גבוהה")]
    for text, ev in cases:
        r = await reg.call("pref_suggest", {"text": text, "evidence": ev}, origin="model")
        assert not r.ok and r.code == "PREF_REFUSED", text
    with pytest.raises(ValueError):
        p.suggest("likes tea", "")                                        # no evidence = a guess = refused
    with pytest.raises(ValueError):
        p.add_confirmed("the pin is 4321")
    assert db.rows("SELECT * FROM learned_prefs") == []


def test_declined_known_duplicate_and_pending_limit(home):
    db = open_db(home)
    clock = Clock()
    p = prefs_mod.Preferences(db, clock=clock)
    a = p.suggest("Call me by my first name", "call me by my first name")
    assert p.suggest("call me by my FIRST name!", "again")["status"] == "known_proposed"      # same preference, one row
    p.decline(a["item"]["id"])
    assert p.suggest("call me by my first name", "again")["status"] == "known_declined"        # not asked again
    assert [r["state"] for r in p.items()] == ["declined"] and p.for_prompt() == []
    for i in range(prefs_mod.MAX_PROPOSED):
        p.suggest(f"preference number {i}", f"I said {i}")
    with pytest.raises(ValueError):
        p.suggest("one too many", "I said so")
    clock.t += prefs_mod.PROPOSAL_TTL_S + 1                                # unanswered suggestions expire
    assert [r for r in p.items() if r["state"] == "proposed"] == []
    assert p.suggest("one too many", "I said so")["status"] == "proposed"


def test_stale_conflict_edit_and_remove(home):
    db = open_db(home)
    clock = Clock()
    p = prefs_mod.Preferences(db, clock=clock)
    a = p.add_confirmed("answer in short sentences", "voice")["item"]
    b = p.suggest("answer in long sentences", "I like long sentences", "voice")["item"]
    r = p.confirm(b["id"])
    assert [c["id"] for c in r["conflicts"]] == [a["id"]]                  # shown, never auto-merged
    assert len(p.for_prompt()) == 2
    clock.t += prefs_mod.STALE_S + DAY
    assert p.for_prompt() == [] and all(i["stale"] for i in p.items("confirmed"))
    assert p.reaffirm(a["id"]) and p.for_prompt() == ["answer in short sentences"]
    assert p.edit(a["id"], "answer in very short sentences")["text"] == "answer in very short sentences"
    with pytest.raises(ValueError):
        p.edit(a["id"], "answer in long sentences")                        # collides with the other item
    with pytest.raises(ValueError):
        p.edit(a["id"], "my password is x")
    assert p.remove(a["id"]) and p.get(a["id"]) is None and not p.remove(a["id"])
    assert p.clear() == 1 and p.items() == []


async def test_forgetting_by_voice_asks_the_owner(home):
    db = open_db(home)
    p = prefs_mod.Preferences(db)
    pid = p.add_confirmed("likes jazz", "music")["item"]["id"]
    asked: list = []
    assert not (await registry(prefs=p, answer=False, asked=asked).call("pref_forget", {"id": pid}, origin="model")).ok
    assert p.get(pid) and asked
    assert (await registry(prefs=p, answer=True).call("pref_forget", {"id": pid}, origin="model")).ok
    assert p.get(pid) is None


def test_dashboard_and_cli_show_edit_and_remove_every_learned_item(home, capsys):
    db = open_db(home)
    p = prefs_mod.Preferences(db)
    pid = p.suggest("prefers tea", "I prefer tea to coffee", "food")["item"]["id"]
    db.close()
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = prefs_mod.api_prefs(ctx, None, None)
        assert st == 200 and body["proposed"][0]["evidence"] == "I prefer tea to coffee" and body["items"] == []
        assert prefs_mod.api_pref_op(ctx, None, {"op": "confirm", "id": pid})[0] == 200
        assert prefs_mod.api_prefs(ctx, None, None)[1]["items"][0]["text"] == "prefers tea"
        assert prefs_mod.api_pref_op(ctx, None, {"op": "edit", "id": pid, "text": "prefers green tea"})[0] == 200
        assert prefs_mod.api_pref_op(ctx, None, {"op": "edit", "id": pid, "text": "password 12345678"})[0] == 400
        assert prefs_mod.api_pref_add(ctx, None, {"text": "no calls after nine", "topic": "schedule"})[0] == 200
        assert prefs_mod.api_pref_add(ctx, None, {"text": "iban GB00 1234"})[0] == 400
        assert prefs_mod.api_pref_op(ctx, None, {"op": "nope", "id": pid})[0] == 400
        assert prefs_mod.api_pref_op(ctx, None, {"op": "remove", "id": 999})[0] == 404
    finally:
        ctx.close()
    assert cli.main(["prefs", "list"]) == 0
    out = capsys.readouterr().out
    assert "prefers green tea" in out and "no calls after nine" in out
    assert cli.main(["prefs", "remove", str(pid)]) == 0
    assert cli.main(["prefs", "remove", str(pid)]) == 1
    assert cli.main(["prefs", "add", "the code is 99887766"]) == 2
    capsys.readouterr()


# ---------------------------------------------------------------------- surfaces
def test_logs_carry_no_goal_or_preference_text(home):
    """Every logs.* call in the two modules uses a fixed literal message and classified fields (lint) ..."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("log_lint", Path(__file__).parents[1] / "tools" / "log_lint.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    src = Path(goals_mod.__file__).parent
    bad = [v for name in ("goals.py", "preferences.py") for v in lint.scan_file(src / name)[1]]
    assert bad == [], bad


def test_logged_lines_do_not_contain_the_text(home, monkeypatch):
    seen: list = []
    from jarves import logs
    real = logs.info
    monkeypatch.setattr(logs, "info", lambda *a, **k: (seen.append((a, k)), real(*a, **k))[1])
    db = open_db(home)
    goals_mod.Goals(db).add("secret-ish title quokka", steps=["step quokka"])
    prefs_mod.Preferences(db).suggest("likes quokka", "I like quokka", by="voice")
    assert seen and "quokka" not in json.dumps(seen, ensure_ascii=False)


def test_both_features_are_registered_with_guides_and_switches(home):
    import importlib
    importlib.import_module("jarves.operations")
    for fid in ("goals", "adaptive_memory"):
        assert fid in CATALOG
    from jarves import guides
    owners = {fid for g in guides.load().values() for fid in g.features}
    assert {"goals", "adaptive_memory"} <= owners
    assert "jarves.goals" in dashboard.PLUGIN_MODULES and "jarves.preferences" in dashboard.PLUGIN_MODULES


def test_routes_pages_and_nav_on_the_real_dashboard(home):
    from pathlib import Path
    d = dashboard.Dashboard(home, Config()).start()
    try:
        from test_dashboard import get, req
        assert get(d, "/api/goals")["goals"] == []
        assert get(d, "/api/prefs")["items"] == []
        r, data = req(d, "POST", "/api/goals", headers={"X-Jarves": "1"}, body={"title": "g1"})
        assert r.status == 200 and json.loads(data)["goal"]["title"] == "g1"
        r, data = req(d, "POST", "/api/prefs", headers={"X-Jarves": "1"}, body={"text": "likes quiet"})
        assert r.status == 200
        r, data = req(d, "POST", "/api/goals/checkin", headers={"X-Jarves": "1"}, body={})
        assert r.status == 200 and "g1" in json.loads(data)["text"]
    finally:
        d.stop()
    static = Path(dashboard.__file__).parent / "dashboard_static"
    html = (static / "index.html").read_text(encoding="utf-8")
    for page in ("goals", "preferences"):
        assert f'data-page="{page}"' in html
    assert "/static/pillars.js" in html
    assert "goals: pageGoals, preferences: pagePreferences" in (static / "pillars.js").read_text(encoding="utf-8")
