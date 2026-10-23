"""V5 follow-ups (owner decisions of 21 Oct 2026): confirmed preferences in the instructions, the goal-done line,
proactive offers inside a conversation, and local development drafts. Real stores (SQLite in tmp), the real Skill
registry, the real dashboard handlers and CLI; no network, no browser, no sleeping: clocks are injected."""
from __future__ import annotations

import json

import pytest

from jarves import calm, cli, dashboard, devmanager as dev_mod, goals as goals_mod, preferences as prefs_mod
from jarves import proactive as proactive_mod
from jarves.config import Config
from jarves.context_trace import BACKEND_MARKERS, FRONT_MARKERS, estimate_tokens
from jarves.diag import db as diag_db, tickets
from jarves.features import CATALOG
from jarves.skills import SkillRegistry
from jarves.store import Store
from jarves.userdata import open_db

DAY = 86400.0
T0 = 1_800_000_000.0                       # 10:00 in Asia/Jerusalem (winter)
NIGHT = T0 + 13 * 3600                     # 23:00 local: quiet hours


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Feats:
    def __init__(self, on=True):
        self.on = on

    def active(self, fid):
        return self.on


class FakeLive:
    def __init__(self):
        self.calls = 0

    def session_config(self):
        self.calls += 1
        return {"instructions": "BASE"}


class FakeAgent:
    def instructions(self):
        return "BASE"


def confirmed(prefs, *texts):
    for t in texts:
        prefs.add_confirmed(t)


# ---------------------------------------------------------------------- 1. confirmed preferences in the instructions
def test_only_confirmed_preferences_reach_the_instructions_and_proposals_never(home):
    p = prefs_mod.Preferences(open_db(home))
    p.add_confirmed("short answers in the morning")
    p.suggest("loud music", "I like loud music")                       # proposed: never used
    d = p.suggest("no jazz", "no jazz please")["item"]
    p.decline(d["id"])
    block = prefs_mod.prompt_block(p)
    assert "short answers in the morning" in block and "loud music" not in block and "jazz" not in block
    assert block.startswith(prefs_mod.PROMPT_HEAD)


def test_block_is_empty_without_confirmed_items_and_respects_the_token_budget(home):
    p = prefs_mod.Preferences(open_db(home), clock=Clock())
    assert prefs_mod.prompt_block(p) == ""
    for i in range(60):
        p.add_confirmed(f"I like the colour number {i} very much indeed")
    block = prefs_mod.prompt_block(p)
    assert 0 < estimate_tokens(block) <= prefs_mod.PROMPT_TOKENS + 8
    assert block.count("\n- ") < 60                                    # cut by the budget, never unbounded


def test_stale_and_sensitive_items_are_not_injected_and_new_sensitive_text_is_refused(home):
    clock = Clock()
    db = open_db(home)
    p = prefs_mod.Preferences(db, clock=clock)
    p.add_confirmed("tea without sugar")
    clock.t += 200 * DAY
    assert prefs_mod.prompt_block(p) == ""                             # not reaffirmed for 180 days
    p.reaffirm(1)
    assert "tea without sugar" in prefs_mod.prompt_block(p)
    with pytest.raises(ValueError):
        p.add_confirmed("my password is hunter2")                      # still refused
    db.x("INSERT INTO learned_prefs (text, norm, topic, state, evidence, source, created, updated, confirmed_at) "
         "VALUES ('card 1234567890123456','x','other','confirmed','e','voice',?,?,?)", (clock.t, clock.t, clock.t))
    assert "1234567890123456" not in prefs_mod.prompt_block(p)         # defence in depth at injection time


def test_voice_and_backend_instructions_follow_the_switch_and_edits_at_once(home):
    p = prefs_mod.Preferences(open_db(home))
    p.add_confirmed("answers in English only")
    feats = Feats(True)
    live, agent = FakeLive(), FakeAgent()
    prefs_mod.attach_live(live, p, feats)
    prefs_mod.augment_instructions(agent, p, feats)
    assert "answers in English only" in live.session_config()["instructions"]
    assert "answers in English only" in agent.instructions()
    p.edit(1, "answers in Hebrew only")                                # editable: the next request sees the edit
    assert "answers in Hebrew only" in agent.instructions() and "English only" not in agent.instructions()
    p.remove(1)                                                        # removable
    assert agent.instructions() == "BASE" and live.session_config()["instructions"] == "BASE"
    p.add_confirmed("calm voice")
    feats.on = False                                                   # the switch
    assert agent.instructions() == "BASE" and live.session_config()["instructions"] == "BASE"


def test_preference_block_is_a_named_block_on_the_context_page():
    import re
    for markers in (FRONT_MARKERS, BACKEND_MARKERS):
        assert any(re.search(rx, prefs_mod.PROMPT_HEAD) and bid == "prefs" for rx, bid, *_ in markers)


def test_dashboard_shows_what_is_sent_and_the_switch_state(home):
    ctx = dashboard.Ctx(home, Config())
    try:
        prefs_mod.api_pref_add(ctx, None, {"text": "no spoilers"})
        st, body = prefs_mod.api_prefs(ctx, None, None)
        assert st == 200 and body["in_prompt"] is True and "no spoilers" in body["prompt_text"]
        from jarves.features import api_feature_set
        import types
        st, _ = api_feature_set(ctx, types.SimpleNamespace(group=lambda i: prefs_mod.FEATURE), {"enabled": False})
        assert st == 200
        body = prefs_mod.api_prefs(ctx, None, None)[1]
        assert body["in_prompt"] is False and body["prompt_text"] == ""
    finally:
        ctx.close()


def test_cli_prefs_prompt_shows_and_switches(home, capsys):
    assert cli.main(["prefs", "add", "decaf after noon"]) == 0
    capsys.readouterr()
    assert cli.main(["prefs", "prompt", "show"]) == 0
    out = capsys.readouterr().out
    assert "decaf after noon" in out
    assert cli.main(["prefs", "prompt", "off"]) == 0
    assert "off" in capsys.readouterr().out.splitlines()[0]
    assert cli.main(["prefs", "prompt", "on"]) == 0
    capsys.readouterr()


# ---------------------------------------------------------------------- 2. the goal-done line
def make_cheer(home, *, at_pc=True, quiet=None, clock=None, ok=True):
    clock = clock or Clock()
    db = open_db(home)
    goals = goals_mod.Goals(db, clock=clock)
    said = []

    async def say(text):
        said.append(text)
        return ok
    cheer = goals_mod.GoalCheer(goals, quiet=quiet or (lambda: calm.quiet_hours_now(db, clock())),
                                at_pc=lambda: at_pc, say=say, clock=clock)
    return clock, goals, cheer, said


async def test_finished_goal_gets_one_short_english_line_once(home):
    clock, goals, cheer, said = make_cheer(home)
    g = goals.add("learn guitar")
    assert await cheer.check() == "" and said == []                    # not finished
    clock.t += 60
    goals.set_status(g["id"], "done")
    assert await cheer.check() == goals_mod.CHEER["en"]
    assert said == ["Done, nice work."]
    assert await cheer.check() == "" and len(said) == 1                # never twice


async def test_hebrew_goal_gets_the_hebrew_line(home):
    clock, goals, cheer, said = make_cheer(home)
    g = goals.add("לרוץ עשרה קילומטר")
    clock.t += 5
    goals.set_status(g["id"], "done")
    await cheer.check()
    assert said == [goals_mod.CHEER["he"]]


async def test_no_line_when_the_owner_is_away_and_it_expires_instead_of_surprising_later(home):
    clock, goals, cheer, said = make_cheer(home, at_pc=False)
    g = goals.add("tidy garage")
    clock.t += 5
    goals.set_status(g["id"], "done")
    assert await cheer.check() == "" and said == []                    # away: silent
    clock.t += goals_mod.CHEER_MAX_AGE_S + 1
    cheer.at_pc = lambda: True
    assert await cheer.check() == "" and said == []                    # back too late: still silent
    assert await cheer.check() == ""


async def test_no_line_in_quiet_hours_even_at_the_pc(home):
    clock, goals, cheer, said = make_cheer(home, clock=Clock(NIGHT))
    g = goals.add("sleep earlier")
    clock.t += 5
    goals.set_status(g["id"], "done")
    assert await cheer.check() == "" and said == []
    db = goals.db
    db.set_setting("alerts.quiet_enabled", False)                      # the alerts' own switch does not unlock it
    assert calm.quiet_hours_now(db, clock()) is True


async def test_goals_finished_before_start_are_not_cheered_and_a_failed_say_retries(home):
    clock = Clock()
    db = open_db(home)
    goals = goals_mod.Goals(db, clock=clock)
    old = goals.add("old goal")
    goals.set_status(old["id"], "done")
    clock.t += 10
    _c, _g, cheer, said = make_cheer(home, clock=clock, ok=False)      # same db file: first start sets the cursor
    assert await cheer.check() == "" and said == []
    new = goals.add("new goal")
    clock.t += 5
    goals.set_status(new["id"], "done")
    assert await cheer.check() == "" and len(said) == 1                # speech failed: nothing recorded as spoken
    cheer.say = _ok(said)
    assert await cheer.check() == goals_mod.CHEER["en"]                # retried while still fresh


def _ok(said):
    async def say(text):
        said.append(text)
        return True
    return say


def test_quiet_hours_and_presence_gates():
    db_clock = Clock(T0)

    class Core:
        live = None
        camera = None
    assert calm.owner_at_pc(Core()) is False                           # unknown = not at the PC
    Core.live = object()
    assert calm.owner_at_pc(Core()) is True

    class Cam:
        class tracker:
            present = True
    c2 = Core()
    c2.live, c2.camera = None, Cam()
    assert calm.owner_at_pc(c2) is True
    assert calm.uses_hebrew("שלום") and not calm.uses_hebrew("hello")
    assert db_clock() == T0


async def test_operations_say_goal_line_uses_announce_in_a_conversation_and_respects_the_switch(home):
    from types import SimpleNamespace
    from jarves.operations import Operations

    announced = []

    class Live:
        async def announce(self, note, **kw):
            announced.append(note)
            return True
    core = SimpleNamespace(stopping=False, live=Live(), features=Feats(True))
    ops = SimpleNamespace(core=core)
    assert await Operations._say_goal_line(ops, "Done, nice work.") is True
    assert "Done, nice work." in announced[0] and "call tools" in announced[0]
    core.features = Feats(False)
    assert await Operations._say_goal_line(ops, "Done, nice work.") is False and len(announced) == 1


# ---------------------------------------------------------------------- 3. proactive offers
def make_proactive(home, clock=None):
    clock = clock or Clock()
    db = open_db(home)
    goals = goals_mod.Goals(db, clock=clock)
    return clock, goals, proactive_mod.Proactive(db, goals, clock=clock)


def test_default_is_on_request_nothing_is_offered_but_the_list_works(home):
    clock, goals, p = make_proactive(home)
    goals.add("stalled goal")
    clock.t += 8 * DAY
    assert p.mode() == "on_request"
    assert p.offer() is None                                           # never offered by default
    items = p.refresh()
    assert len(items) == 1 and "no movement for 8 days" in items[0]["text"] and items[0]["state"] == "new"


def test_one_offer_per_conversation_inside_a_conversation_only_and_never_twice(home):
    clock, goals, p = make_proactive(home)
    goals.add("goal a")
    goals.add("goal b")
    clock.t += 9 * DAY
    p.set_mode("in_conversation")
    live, feats = FakeLive(), Feats(True)
    proactive_mod.attach_live(live, p, feats)
    text = live.session_config()["instructions"]
    assert text.startswith("BASE") and proactive_mod.OFFER_NOTE in text and "goal a" in text and "goal b" not in text
    assert live.session_config()["instructions"] == text               # a reconnect reuses the same single offer
    live2 = FakeLive()
    proactive_mod.attach_live(live2, p, feats)
    assert "goal b" in live2.session_config()["instructions"]          # the next conversation: the next item
    live3 = FakeLive()
    proactive_mod.attach_live(live3, p, feats)
    assert live3.session_config()["instructions"] == "BASE"            # nothing new: nothing said


def test_no_offer_in_quiet_hours_when_the_switch_is_off_or_after_dismissal(home):
    clock, goals, p = make_proactive(home, Clock(NIGHT))
    goals.add("late goal")
    clock.t += 9 * DAY - 13 * 3600 + 13 * 3600                         # still 23:00 local, 9 days on
    p.set_mode("in_conversation")
    assert calm.quiet_hours_now(p.db, clock()) and p.offer() is None
    clock.t += 11 * 3600                                               # 10:00: allowed
    live = FakeLive()
    proactive_mod.attach_live(live, p, Feats(False))                   # feature switched off
    assert live.session_config()["instructions"] == "BASE"
    item = p.refresh()[0]
    p.dismiss(item["id"])
    assert p.refresh() == [] and p.offer() is None                     # dismissed for the same situation: gone
    goals.add("another")                                               # a new situation gives a new item later
    clock.t += 8 * DAY
    assert len(p.refresh()) == 1


def test_overdue_goal_item_and_the_item_goes_away_when_the_goal_moves(home):
    clock, goals, p = make_proactive(home)
    g = goals.add("report", target_date="2027-01-10")                  # before T0 (2027-01-15)
    kinds = {i["kind"] for i in p.refresh()}
    assert kinds == {"goal_overdue"}
    goals.set_status(g["id"], "done")
    assert p.refresh() == []


async def test_proactive_tools_are_tiny_and_work_on_request(home):
    clock, goals, p = make_proactive(home)
    reg = SkillRegistry()
    reg.register(proactive_mod.make_skill(p))
    reg.allowed_actions |= proactive_mod.ALLOWED_ACTIONS
    goals.add("walk daily")
    clock.t += 8 * DAY
    r = await reg.call("proactive_show", {}, origin="model")
    assert r.ok and "walk daily" in r.message
    iid = r.data["items"][0]["id"]
    assert (await reg.call("proactive_dismiss", {"id": iid}, origin="model")).ok
    assert (await reg.call("proactive_show", {}, origin="model")).message == "nothing to mention"
    assert not (await reg.call("proactive_dismiss", {"id": 99}, origin="model")).ok
    for t in proactive_mod.make_skill(p).tools():
        assert len(t.description) <= 70


def test_proactive_dashboard_and_cli(home, capsys):
    db = open_db(home)
    goals_mod.Goals(db).add("dash goal", target_date="2020-01-01")
    db.close()
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = proactive_mod.api_proactive(ctx, None, None)
        assert st == 200 and body["mode"] == "on_request" and body["items"][0]["kind"] == "goal_overdue"
        assert proactive_mod.api_proactive_op(ctx, None, {"op": "mode", "mode": "in_conversation"})[0] == 200
        assert proactive_mod.api_proactive_op(ctx, None, {"op": "mode", "mode": "always"})[0] == 400
        assert proactive_mod.api_proactive(ctx, None, None)[1]["mode"] == "in_conversation"
        iid = body["items"][0]["id"]
        assert proactive_mod.api_proactive_op(ctx, None, {"op": "dismiss", "id": iid})[1]["ok"] is True
    finally:
        ctx.close()
    assert cli.main(["proactive", "mode"]) == 0
    assert "in_conversation" in capsys.readouterr().out
    assert cli.main(["proactive", "mode", "on_request"]) == 0
    assert cli.main(["proactive", "list"]) == 0
    capsys.readouterr()


# ---------------------------------------------------------------------- 4. development drafts: local only
def make_store(home):
    st = Store(home / "store.db")
    diag_db.ensure(st)
    return st


def test_suggestion_is_a_local_draft_hidden_from_health_and_never_posted(home):
    st = make_store(home)
    d = dev_mod.suggest(st, "Add a dark mode to the face", "The face is too bright at night.", "ticket", by="voice")
    assert d["status"] == "draft" and d["title"].startswith("[dev] ") and "Development Manager" in d["body"]
    assert [x["id"] for x in dev_mod.listing(st)] == [d["id"]]
    assert tickets.listing(st) == []                                   # not mixed into the Health page tickets
    assert dev_mod.discard(st, d["id"]) is True and dev_mod.listing(st) == []
    assert dev_mod.discard(st, 999) is False
    st.close()


def test_sensitive_or_empty_drafts_are_refused_and_secrets_redacted(home):
    st = make_store(home)
    for title, body in (("", ""), ("fix my password hunter2", ""), ("idea", "call me 0541234567 or card 4111111111111111")):
        with pytest.raises(ValueError):
            dev_mod.suggest(st, title, body)
    # Issue #17: an unknown kind (the model says "idea", "bug", "post") is mapped, never a refusal
    assert dev_mod.suggest(st, "idea one", "x", kind="post")["severity"] == "suggestion"
    assert dev_mod.suggest(st, "idea two", "x", kind="bug")["severity"] == "ticket"
    d = dev_mod.suggest(st, "contact owner", "mail me at someone@example.com about it")
    assert "someone@example.com" not in d["body"]
    assert len(dev_mod.listing(st)) == 1
    st.close()


async def test_posting_needs_a_private_repo_and_the_owners_approval_and_default_stays_local(home):
    st = make_store(home)
    d = dev_mod.suggest(st, "Improve the greeting", "Shorter please")
    calls = []

    def run(argv, stdin=None):
        calls.append(argv)
        if argv[:2] == ["repo", "view"]:
            return 0, json.dumps({"visibility": "PRIVATE"})
        if argv[:2] == ["issue", "list"]:
            return 0, "[]"
        return 0, "https://example.invalid/issue/1"

    async def no(prompt, **kw):
        return False

    async def yes(prompt, **kw):
        assert "Improve the greeting" in prompt                        # the full text is what the owner approves
        return True
    r = await tickets.file(st, d["id"], ask=yes, repo="", by="test", run=run)     # default: no repo set
    assert r["status"] == "draft" and "stays local" in r["reason"] and calls == []
    r = await tickets.file(st, d["id"], ask=no, repo="me/priv", by="test", run=run)
    assert r["status"] == "draft" and not any(c[:2] == ["issue", "create"] for c in calls)
    r = await tickets.file(st, d["id"], ask=yes, repo="me/priv", by="test", run=run)
    assert r["status"] == "filed" and any(c[:2] == ["issue", "create"] for c in calls)
    st.close()


async def test_dev_tools_save_list_discard_and_never_post(home):
    reg = SkillRegistry()
    reg.register(dev_mod.make_skill(lambda: make_store(home)))
    reg.allowed_actions |= dev_mod.ALLOWED_ACTIONS
    r = await reg.call("dev_suggest", {"title": "Voice tuning idea", "body": "calmer pace"}, origin="model")
    assert r.ok and "nothing was posted" in r.message
    assert "Voice tuning idea" in (await reg.call("dev_drafts", {}, origin="model")).message
    bad = await reg.call("dev_suggest", {"title": "my password is x"}, origin="model")
    assert not bad.ok and bad.code == "DEV_REFUSED"
    assert (await reg.call("dev_discard", {"id": r.data["draft"]["id"]}, origin="model")).ok
    assert (await reg.call("dev_drafts", {}, origin="model")).message == "no drafts"
    assert not any(t.name.startswith("dev_") and "post" in t.name for t in dev_mod.make_skill(None).tools())
    for t in dev_mod.make_skill(None).tools():
        assert len(t.description) <= 80


def test_dev_dashboard_and_cli(home, capsys):
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = dev_mod.api_draft_add(ctx, None, {"title": "Dashboard idea", "body": "b", "kind": "ticket"})
        assert st == 200 and body["draft"]["status"] == "draft"
        assert dev_mod.api_draft_add(ctx, None, {"title": ""})[0] == 400
        st, body = dev_mod.api_drafts(ctx, None, None)
        assert st == 200 and body["repo_set"] is False and [d["title"] for d in body["drafts"]] == ["[dev] Dashboard idea"]
        assert dev_mod.api_draft_op(ctx, None, {"op": "discard", "id": 999})[0] == 404
        assert dev_mod.api_draft_op(ctx, None, {"op": "file", "id": 1})[0] == 400      # posting is not an op here
    finally:
        ctx.close()
    assert cli.main(["dev", "suggest", "CLI idea", "--body", "from the shell"]) == 0
    assert "saved locally, nothing was posted" in capsys.readouterr().out
    assert cli.main(["dev", "list"]) == 0
    assert "CLI idea" in capsys.readouterr().out
    assert cli.main(["dev", "show", "2"]) == 0
    assert "from the shell" in capsys.readouterr().out
    assert cli.main(["dev", "file", "2"]) in (0, 1)                    # no repo set: stays local, nothing posted
    assert "stays local" in capsys.readouterr().out
    assert cli.main(["dev", "discard", "2"]) == 0
    assert cli.main(["dev", "show", "2"]) == 1
    capsys.readouterr()


# ---------------------------------------------------------------------- features, guides, parity, privacy
FOLLOWUP_FEATURES = ("prefs_in_prompt", "goal_done_line", "proactive_intel", "dev_manager")


def test_each_followup_has_a_feature_row_a_guide_a_cli_entry_and_the_dashboard_pages(home):
    import importlib
    importlib.import_module("jarves.operations")
    from jarves import cli_parity, guides, hebrew
    owners = {fid for g in guides.load().values() for fid in g.features}
    for fid in FOLLOWUP_FEATURES:
        assert fid in CATALOG and fid in owners and fid in cli_parity.FEATURES
        assert hebrew.he(CATALOG[fid].about) != CATALOG[fid].about      # a Hebrew description exists
    for mod in ("jarves.proactive", "jarves.devmanager"):
        assert mod in dashboard.PLUGIN_MODULES
    from pathlib import Path
    static = Path(dashboard.__file__).parent / "dashboard_static"
    html = (static / "index.html").read_text(encoding="utf-8")
    assert 'data-page="proactive"' in html and 'data-page="devdrafts"' in html
    js = (static / "pillars.js").read_text(encoding="utf-8")
    assert "proactive: pageProactive" in js and "devdrafts: pageDevDrafts" in js and "prefs_in_prompt" in js
    for group in ("proactive", "dev"):
        assert group in cli_parity.TOOL_GROUPS


def test_logs_carry_no_text_of_goals_preferences_or_drafts(home, monkeypatch):
    from jarves import logs
    seen = []
    for lvl in ("info", "warn", "error", "debug"):
        monkeypatch.setattr(logs, lvl, lambda *a, _s=seen, **k: _s.append((a, k)))
    db = open_db(home)
    st = make_store(home)
    p = prefs_mod.Preferences(db)
    p.add_confirmed("likes quokka")
    dev_mod.suggest(st, "quokka idea", "quokka body")
    with pytest.raises(ValueError):
        dev_mod.suggest(st, "quokka password hunter2")
    clock = Clock()
    g = goals_mod.Goals(db, clock=clock)
    g.add("quokka goal", target_date="2020-01-01")
    pr = proactive_mod.Proactive(db, g, clock=clock)
    pr.set_mode("in_conversation")
    pr.offer()
    prefs_mod.prompt_block(p)
    assert seen and "quokka" not in json.dumps(seen, ensure_ascii=False)
    st.close()
