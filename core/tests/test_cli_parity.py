"""V4 P0.3 (#17): CLI / dashboard parity. Real Features, Timers, Lists, ApprovalService and dashboard route handlers in
tmp dirs; the CLI is run through jarves.cli.main. No sensors, no network, no sleeping (the clock is injected and
waits are event-driven)."""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

from jarves import approvals as appr
from jarves import cli, cli_parity, dashboard, features as feat, lists as lists_mod, timers as timers_mod
from jarves.config import Config
from jarves.features import CATALOG, Features
from jarves.skills import SkillRegistry
from jarves.userdata import open_db
from owner_harness import OwnerAnswers

REASON = "Pear is not running"


@pytest.fixture
def unavailable_music(monkeypatch):
    """One real feature whose prerequisite is missing: enabled-but-unavailable on every surface."""
    monkeypatch.setattr(CATALOG["music"], "probe", lambda c: (False, REASON))


def fresh(home) -> Features:
    return Features(Config(), home)


# ====================================================================== O-02: one rule for CLI, dashboard, tool
async def test_turning_on_an_unavailable_feature_is_the_same_everywhere(home, capsys, unavailable_music):
    shown = {}

    # 1. CLI
    fresh(home).set("music", False)
    assert cli.main(["features", "on", "music"]) == 0
    shown["cli"] = capsys.readouterr().out
    assert f"music: unavailable ({REASON})" in shown["cli"]
    f = fresh(home)
    assert f.enabled("music") and not f.active("music")                   # SAVED as ON, not refused

    # 2. dashboard route
    fresh(home).set("music", False)
    ctx = dashboard.Ctx(home, Config())
    try:
        m = re.match(r"^/api/features/([a-z0-9_]+)$", "/api/features/music")
        st, body = feat.api_feature_set(ctx, m, {"enabled": True})
    finally:
        ctx.close()
    assert st == 200 and body["enabled"] and not body["active"]
    shown["dashboard"] = body["state_text"]
    assert fresh(home).enabled("music")

    # 3. the model tool
    fresh(home).set("music", False)
    f = fresh(home)
    reg = SkillRegistry()
    reg.register(feat.make_skill(f, reg))
    reg.allowed_actions |= {"feature_set"}
    r = await reg.call("feature_set", {"id": "music", "on": True})
    assert r.ok and f.enabled("music") and not f.active("music")
    shown["tool"] = r.data["state_text"]
    assert f"לא זמינה ({REASON})" in r.message                              # Hebrew wording of the same state

    assert shown["dashboard"] == shown["tool"] == f"unavailable ({REASON})"
    assert shown["cli"].startswith(f"music: {shown['dashboard']}")


def test_state_text_words(home, unavailable_music):
    f = fresh(home)
    f.set("music", False)
    assert feat.state_text(f.status("music")) == "off" and feat.state_text(f.status("music"), "he") == "כבויה"
    st = feat.switch(f, "music", True, by="cli")
    assert st["state"] == "unavailable" and st["state_text_he"] == f"לא זמינה ({REASON})"
    f.set("chimes", True)
    assert feat.state_text(f.status("chimes")) == "on"


# ====================================================================== spoken approvals: kind of action only
SECRET = "העברה של 4,000 שקל לחשבון 12-345-6789, הסיסמה היא hunter2"


@pytest.mark.parametrize("title,kind,details,expect_en", [
    ("whatsapp_send", "message", {"recipient": "Dana"}, "Send a WhatsApp message to Dana?"),
    ("telegram_send", "message", {}, "Send a Telegram message?"),
    ("gmail_send_draft", "message", {}, "Send an e-mail?"),
    ("shell_propose", "shell", {"command": "rm -rf /secret"}, "Run a command on this computer?"),
    ("Turn on Send e-mail / להפעיל", "feature", {"feature": "gmail_send"}, "Change a setting?"),
    ("list_clear", "tool", {}, "Delete something?"),
    ("something_new", "tool", {}, "Do something that needs your approval?"),
])
def test_spoken_question_names_only_the_kind(title, kind, details, expect_en):
    en, he = appr.spoken_kind(title, kind, details)
    assert en == expect_en
    assert re.search(r"[֐-׿]", he)
    for leak in ("rm -rf", "secret", "hunter2", "12-345"):
        assert leak not in en and leak not in he


async def test_voice_channel_hears_the_kind_while_popup_keeps_the_full_text(home):
    from jarves.app import _VoiceApproval
    heard, ev = [], asyncio.Event()

    class Live:
        async def confirm_pending(self, prompt, voice=False):
            heard.append(prompt)
            ev.set()

        def confirm_done(self):
            pass

    svc = appr.ApprovalService(home, timeout_s=30)
    svc.channels.append(_VoiceApproval(SimpleNamespace(live=Live())))
    shown = []
    svc.on_ask.append(lambda a: shown.append(a.public()))
    full = f"לשלוח ל-Dana את ההודעה: {SECRET}?"
    task = asyncio.create_task(svc.ask(full, title="whatsapp_send", kind="message", risk="medium",
                                       details={"recipient": "Dana"}))
    await asyncio.wait_for(ev.wait(), 30)
    assert heard == ["Send a WhatsApp message to Dana?"]                 # ONE language (English default)
    for leak in ("4,000", "hunter2", "12-345", "הסיסמה"):
        assert leak not in heard[0]
    assert shown and shown[0]["prompt"] == full                           # popup / dashboard text is unchanged
    ok, _ = svc.answer(shown[0]["id"], True, channel="dashboard")
    assert ok and await asyncio.wait_for(task, 30) is True


async def test_live_note_forbids_reading_details_aloud():
    from jarves.voice import live as live_mod
    src = open(live_mod.__file__, encoding="utf-8").read()
    assert "never read out message text, commands, addresses" in src


# ====================================================================== timers: CLI -> store -> dashboard -> ring
async def test_timer_added_by_cli_is_on_the_dashboard_and_rings(home, capsys):
    assert cli.main(["timers", "add", "10m", "pasta"]) == 0
    assert "set for" in capsys.readouterr().out
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = timers_mod.api_timers(ctx, None, None)
        assert st == 200 and [t["label"] for t in body["timers"]] == ["pasta"]
        tid = body["timers"][0]["id"]
        assert 590 <= body["timers"][0]["seconds_left"] <= 600
        st, added = timers_mod.api_timer_add(ctx, None, {"kind": "timer", "minutes": 1, "label": "tea"})
        assert st == 200 and added["label"] == "tea"
        assert timers_mod.api_timer_add(ctx, None, {"kind": "alarm", "at": "99:99"})[0] == 400
    finally:
        ctx.close()
    assert cli.main(["timers", "list"]) == 0
    out = capsys.readouterr().out
    assert "pasta" in out and "tea" in out and "(2 running)" in out

    db = open_db(home)                                    # the Core's own ring loop, with the clock moved on
    rang, ev = [], asyncio.Event()

    async def deliver(t, text):
        rang.append((t["label"], text))
        ev.set()
        return True
    tm = timers_mod.Timers(db, clock=lambda: db.row("SELECT due FROM timers WHERE id=?", (tid,))["due"] + 1)
    try:
        assert await tm.poll(deliver) >= 1
        await asyncio.wait_for(ev.wait(), 30)
        assert any(label == "pasta" for label, _ in rang)
    finally:
        db.close()


def test_timer_cancel_by_cli_and_dashboard(home, capsys):
    assert cli.main(["timers", "add", "5m", "a"]) == 0
    assert cli.main(["timers", "add", "--at", "07:30", "wake up"]) == 0
    capsys.readouterr()
    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = timers_mod.api_timers(ctx, None, None)
        ids = {t["label"]: t["id"] for t in body["timers"]}
        assert set(ids) == {"a", "wake up"}
        assert timers_mod.api_timer_op(ctx, re.match(r"(\d+)", str(ids["a"])), {"op": "cancel"})[0] == 200
        assert timers_mod.api_timer_op(ctx, re.match(r"(\d+)", str(ids["a"])), {"op": "cancel"})[0] == 404
    finally:
        ctx.close()
    assert cli.main(["timers", "cancel", "--label", "wake"]) == 0
    assert cli.main(["timers", "cancel", "--label", "wake"]) == 1
    assert cli.main(["timers", "cancel"]) == 2
    assert cli.main(["timers", "add", "banana"]) == 2


@pytest.mark.parametrize("text,seconds", [("10", 600), ("90s", 90), ("1h30m", 5400), ("0.5m", 30)])
def test_duration_parser(text, seconds):
    assert cli_parity.parse_duration(text) == seconds


# ====================================================================== lists: voice tool, dashboard, CLI
async def test_list_added_by_voice_ticked_on_dashboard_read_by_cli(home, capsys):
    db = open_db(home)
    reg = SkillRegistry()
    reg.register(lists_mod.make_skill(lists_mod.Lists(db)))
    reg.allowed_actions |= lists_mod.ALLOWED_ACTIONS
    r = await reg.call("list_add", {"list_name": "shopping", "items": ["חלב", "לחם", "ביצים"]})
    assert r.ok
    db.close()

    ctx = dashboard.Ctx(home, Config())
    try:
        st, body = lists_mod.api_lists(ctx, None, None, {"name": ["shopping"]})
        assert st == 200 and [i["text"] for i in body["items"]] == ["חלב", "לחם", "ביצים"]
        assert lists_mod.api_list_op(ctx, None, {"op": "check", "list_name": "shopping", "item": "חלב"})[0] == 200
        assert lists_mod.api_list_add(ctx, None, {"list_name": "shopping", "items": "גבינה, שמן"})[0] == 200
        assert lists_mod.api_list_op(ctx, None, {"op": "check", "list_name": "shopping", "item": "xyz"})[0] == 404
        # clearing asks for approval: the dashboard refuses without it
        st, body = lists_mod.api_list_op(ctx, None, {"op": "clear", "list_name": "shopping"})
        assert st == 403 and body["needs_approval"]               # nobody could be asked: refused, nothing cleared
        with OwnerAnswers(home, False):                           # the owner presses Deny on the popup
            assert lists_mod.api_list_op(ctx, None, {"op": "clear", "list_name": "shopping"})[0] == 403
        assert len(lists_mod.api_lists(ctx, None, None, {"name": ["shopping"]})[1]["items"]) == 5
    finally:
        ctx.close()

    assert cli.main(["lists", "show", "shopping", "--all"]) == 0
    out = capsys.readouterr().out
    assert "[x] חלב" in out and "[ ] לחם" in out and "[ ] גבינה" in out
    assert cli.main(["lists", "show"]) == 0
    assert "shopping" in capsys.readouterr().out


def test_cli_list_edit_and_clear_needs_approval(home, capsys):
    assert cli.main(["lists", "add", "todo", "call mom", "pay rent"]) == 0
    assert cli.main(["lists", "check", "todo", "call mom"]) == 0
    assert cli.main(["lists", "remove", "todo", "nothing here"]) == 1
    capsys.readouterr()
    assert cli.main(["lists", "clear", "todo"]) == 1                     # no terminal, no --yes: refused
    assert "approval needed" in capsys.readouterr().err
    assert cli.main(["lists", "clear", "todo", "--yes"]) == 0             # only the ticked-off item
    assert cli.main(["lists", "show", "todo", "--all"]) == 0
    out = capsys.readouterr().out
    assert "call mom" not in out and "pay rent" in out
    assert cli.main(["lists", "clear", "todo", "--everything", "--yes"]) == 0
    ctx = dashboard.Ctx(home, Config())
    try:
        assert lists_mod.api_lists(ctx, None, None, {"name": ["todo"]})[1]["items"] == []
        lists_mod.api_list_add(ctx, None, {"list_name": "todo", "items": ["x"]})
        with OwnerAnswers(home, True) as owner:
            st, body = lists_mod.api_list_op(ctx, None, {"op": "clear", "list_name": "todo", "only_done": False,
                                                           "approved": True})     # the flag is ignored: the popup decides
        assert st == 200 and body["cleared"] == 1 and len(owner.asked) == 1
        assert owner.asked[0]["details"] == {"list": "todo"} and owner.asked[0]["source"] == "dashboard"
    finally:
        ctx.close()


def test_notes_jobs_reports_cli_are_read_only_lists(home, capsys):
    from jarves.notes import Notes
    from jarves.scheduler import Scheduler
    db = open_db(home)
    n = Notes(db).add("Trip plan", "book the hotel", ["travel"])
    Scheduler(db).preview("every day at 08:00", "say good morning", name="morning", action="reminder")
    db.close()
    assert cli.main(["notes", "list"]) == 0
    assert "Trip plan" in capsys.readouterr().out
    assert cli.main(["notes", "list", "--tag", "travel"]) == 0
    assert "Trip plan" in capsys.readouterr().out
    assert cli.main(["notes", "show", str(n["id"])]) == 0
    assert "book the hotel" in capsys.readouterr().out
    assert cli.main(["notes", "show", "999"]) == 1
    assert cli.main(["jobs", "list"]) == 0
    assert "morning" in capsys.readouterr().out
    assert cli.main(["reports", "list", "--period", "this_week"]) == 0
    assert cli.main(["reports", "show", "999"]) == 1


# ====================================================================== the parity table
def _command_exists(name: str) -> bool:
    try:
        cli.main([name, "--help"])
    except SystemExit as e:
        return e.code == 0
    return False


def _entries(table: dict) -> list:
    return [(k, v) for k, v in table.items()]


def test_every_feature_switch_works_from_the_cli(home, monkeypatch, capsys):
    """`jarvis features on|off|reset ID` accepts every dashboard-visible switch (installs are never started)."""
    from jarves import feature_setup
    monkeypatch.setattr(feature_setup, "prepare", lambda fid, f, h: (True, ""))
    for fid in CATALOG:
        for action in ("on", "off", "reset"):
            assert cli.main(["features", action, fid]) == 0, (fid, action)
    capsys.readouterr()


def test_every_feature_has_a_cli_surface_or_a_dashboard_only_marker():
    import importlib
    for name in dashboard.PLUGIN_MODULES:
        importlib.import_module(name)
    from jarves.setup_wizard import load_addons
    load_addons()
    missing = sorted(set(CATALOG) - set(cli_parity.FEATURES))
    stale = sorted(set(cli_parity.FEATURES) - set(CATALOG))
    assert not missing, f"new feature(s) with neither a CLI command nor a 'dashboard only' marker: {missing}"
    assert not stale, f"cli_parity.FEATURES names features that no longer exist: {stale}"
    for fid, ref in cli_parity.FEATURES.items():
        _check_marker("feature " + fid, ref)


def _check_marker(what: str, ref) -> None:
    if isinstance(ref, tuple):
        kind, reason = ref
        assert kind in ("dashboard", "voice") and len(reason) >= 12, (what, ref)
    else:
        assert _command_exists(ref), f"{what}: `jarvis {ref}` is not a CLI command"


def test_every_tool_group_has_a_cli_surface_or_a_marker():
    import ast
    from pathlib import Path
    root = Path(cli_parity.__file__).parent
    groups: set[str] = set()
    for p in root.rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ClassDef) and any(getattr(b, "id", getattr(b, "attr", "")) == "Skill" for b in n.bases):
                for s in n.body:
                    if (isinstance(s, ast.Assign) and getattr(s.targets[0], "id", "") == "name"
                            and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str)):
                        groups.add(s.value.value)
    assert len(groups) >= 35, "the scan must find the real Skills"
    missing = sorted(groups - set(cli_parity.TOOL_GROUPS))
    stale = sorted(set(cli_parity.TOOL_GROUPS) - groups)
    assert not missing, f"tool group(s) with neither a CLI command nor a marker: {missing}"
    assert not stale, f"cli_parity.TOOL_GROUPS names Skills that no longer exist: {stale}"
    for g, ref in cli_parity.TOOL_GROUPS.items():
        _check_marker("tool group " + g, ref)


def test_new_pages_are_in_the_navigation_and_registered():
    from pathlib import Path
    static = Path(dashboard.__file__).parent / "dashboard_static"
    html = (static / "index.html").read_text(encoding="utf-8")
    for page in ("timers", "lists"):
        m = re.search(rf'<div class="nav-group" data-group="(\w+)">(?:(?!</div>\s*<div class="nav-group").)*?data-page="{page}"',
                      html, re.S)
        assert m and m.group(1) == "everyday", page                    # under Everyday, next to jobs / reports
    js = (static / "m3.js").read_text(encoding="utf-8")
    assert "timers: pageTimers, lists: pageLists" in js


def test_routes_are_registered_on_the_real_dashboard(home):
    d = dashboard.Dashboard(home, Config()).start()
    try:
        import json
        from test_dashboard import get, req
        assert get(d, "/api/timers")["timers"] == []
        assert get(d, "/api/lists")["lists"] == []
        r, data = req(d, "POST", "/api/timers", headers={"X-Jarves": "1"}, body={"kind": "timer", "minutes": 2, "label": "x"})
        assert r.status == 200 and json.loads(data)["label"] == "x"
        assert [t["label"] for t in get(d, "/api/timers")["timers"]] == ["x"]
    finally:
        d.stop()
