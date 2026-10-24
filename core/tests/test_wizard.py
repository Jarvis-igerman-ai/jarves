"""Setup and tour wizard (#58/#60/#61): topics vs guides/features, state, diff mode, secrets, bug report, sign-off sheet,
dashboard routes and the CLI. No network, no sensors, no browser."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from jarves import cli, dashboard, guides, release_signoff as so, wizard
from jarves.config import Config
from jarves.features import CATALOG, Features


def ctx_of(home):
    return dashboard.Ctx(home, Config())


def call(ctx, method, path, body=None):
    for m, rx, h in dashboard._ROUTES:
        mm = rx.match(path)
        if m == method and mm:
            return h(ctx, mm, body)
    raise AssertionError(f"no route {method} {path}")


@pytest.fixture
def ctx(home, monkeypatch):
    monkeypatch.setenv("JARVES_REPO_DIR", str(home))
    (home / "docs" / "reviews").mkdir(parents=True)
    (home / "core").mkdir()
    dashboard.load_plugins()
    c = ctx_of(home)
    yield c
    c.close()


def test_topics_point_at_real_guides_and_features():
    dashboard.PLUGIN_MODULES  # noqa: B018  (modules registering features are imported by the Ctx)
    g = guides.load()
    assert len(wizard.TOPICS) == 16
    for t in wizard.TOPICS:
        assert t.guides and all(x in g for x in t.guides), t.id
        assert t.page.startswith("#/") and t.cli.startswith("jarvis"), t.id
    assert "setup_tour" in CATALOG and "setup_tour" in g["setup_tour"].features


def test_overview_baseline_and_progress(ctx):
    d = call(ctx, "GET", "/api/wizard")[1]
    assert d["mode"] == "baseline" and d["total"] == 16 and d["percent"] == 0
    assert call(ctx, "POST", "/api/wizard/topic/music/choice", {"choice": "skipped"})[0] == 200
    d = call(ctx, "GET", "/api/wizard")[1]
    assert d["finished"] == 1 and [t["status"] for t in d["topics"] if t["id"] == "music"] == ["skipped"]
    assert call(ctx, "POST", "/api/wizard/topic/music/choice", {"choice": "bogus"})[0] == 400
    assert call(ctx, "GET", "/api/wizard/topic/nope")[0] == 404


def test_works_writes_verified_record_and_learn_card(ctx):
    call(ctx, "POST", "/api/wizard/topic/productivity/choice", {"choice": "done"})
    r = call(ctx, "POST", "/api/wizard/topic/productivity/result", {"result": "works"})[1]
    assert r["result"] == "works"
    st = wizard.read_state(ctx.home)
    assert st["features"]["timers"]["version"] == wizard.__version__ and st["features"]["timers"]["by"] == "owner"
    t = call(ctx, "GET", "/api/wizard/topic/productivity")[1]
    assert t["status"] == "works" and t["verified_in"] == wizard.__version__
    c = t["card"]
    assert 1 <= len(c["say"]) <= 3 and c["page"] == "#/timers" and c["cli"] and c["guide"] == "timers"


def test_fails_creates_one_content_free_bug_report(ctx):
    call(ctx, "POST", "/api/wizard/topic/music/result", {"result": "fails", "note": "no sound"})
    call(ctx, "POST", "/api/wizard/topic/music/result", {"result": "fails", "note": "still no sound"})
    issues = ctx.store.issues("open")
    mine = [i for i in issues if i["kind"] == "setup_tour"]
    assert len(mine) == 1 and mine[0]["count"] == 2 and "music" in mine[0]["title"].lower()
    ev = mine[0]["evidence"]
    assert "no sound" not in ev and "topic" in ev and "features" in ev


def test_failed_recheck_clears_previous_owner_verification(ctx):
    call(ctx, "POST", "/api/wizard/topic/telegram/result", {"result": "works"})
    assert wizard.read_state(ctx.home)["features"]["telegram"]["by"] == "owner"
    call(ctx, "POST", "/api/wizard/topic/telegram/result", {"result": "fails"})
    st = wizard.read_state(ctx.home)
    assert "telegram" not in st["features"]
    assert "verified_in" not in st["topics"]["telegram"]


def test_secret_goes_to_secret_store_not_state(ctx, home):
    tok = "123456789:" + "A" * 35
    assert call(ctx, "POST", "/api/wizard/secret/telegram", {"value": "bad"})[0] == 400
    assert call(ctx, "POST", "/api/wizard/secret/telegram", {"value": tok})[0] == 200
    assert tok in (home / "secrets.env").read_text(encoding="utf-8")
    call(ctx, "POST", "/api/wizard/topic/telegram/choice", {"choice": "done"})
    assert tok not in (home / wizard.STATE_FILE).read_text(encoding="utf-8")
    assert call(ctx, "POST", "/api/wizard/secret/other", {"value": "x"})[0] == 404


def test_high_risk_switch_needs_confirmation(ctx):
    assert call(ctx, "POST", "/api/wizard/topic/phone/switch", {})[0] == 403


def test_test_is_local_and_reports_parts(ctx):
    r = call(ctx, "POST", "/api/wizard/topic/cli/test")[1]
    assert r["parts"] and all({"ok", "he", "en"} <= set(p) for p in r["parts"])
    a = call(ctx, "POST", "/api/wizard/automated")[1]
    assert a["passed"] + len(a["failed"]) + a["off_by_choice"] == 16


def test_diff_since_version_asks_only_changed_and_failed(home):
    """A new release re-asks a finished topic only when its CHANGELOG names it or it failed (#152)."""
    log = "## 4.0.2\n- Fixed: WhatsApp bridge reconnect.\n\n## 4.0.1\n- Music: new player.\n"
    st = {"schema": wizard.SCHEMA, "features": {}, "topics": {
        t.id: {"done": {"how": "works", "version": "4.0.1", "rev": 1, "ts": 1}} for t in wizard.TOPICS}}
    st["topics"]["voice"]["done"]["how"] = "fails"
    st["topics"]["telegram"]["done"]["how"] = "skipped"
    assert wizard.queue(st, "4.0.1.360", log) == []                    # another build of the same release: nothing
    assert wizard.queue(st, "4.0.2", log) == ["whatsapp"]               # WhatsApp changed; voice's failure waits
    log2 = log.replace("WhatsApp bridge", "WhatsApp bridge and wake word")
    assert wizard.queue(st, "4.0.2", log2) == ["voice", "whatsapp"]     # a release that names the failed topic
    assert wizard.again_reason(wizard.BY_ID["voice"], st["topics"]["voice"]["done"], "4.0.2", log2) == "retest"
    assert "music" not in wizard.changed_since("4.0.1", "4.0.2", log)  # 4.0.1's own section is not "since"
    assert wizard.changed_since("4.0.0", "4.0.2", log) >= {"music", "whatsapp"}
    assert "telegram" not in wizard.queue(st, "4.0.3", log + "## 4.0.3\n- Telegram fixes\n")   # skipped stays skipped
    assert wizard.mode({}) == "baseline"


def test_rev_bump_reasks_a_topic(home, monkeypatch):
    st = {"schema": wizard.SCHEMA, "features": {}, "topics": {"music": {"done": {"how": "works", "version": "4.0.1", "rev": 1}}}}
    assert "music" not in wizard.queue(st, "4.0.1", "")
    monkeypatch.setattr(wizard.BY_ID["music"], "rev", 2)
    assert "music" in wizard.queue(st, "4.0.1", "")


def test_every_answer_but_later_finishes_and_persists(ctx):
    for tid, how in (("cli", "seen"), ("productivity", "works"), ("telegram", "skipped")):
        assert call(ctx, "POST", f"/api/wizard/topic/{tid}/done", {"how": how})[0] == 200
    call(ctx, "POST", "/api/wizard/topic/dashboard/learned")              # old "Got it, next" finishes too (#147)
    d = call(ctx, "GET", "/api/wizard")[1]                                 # a fresh read = a page reload
    assert {"cli", "productivity", "telegram", "dashboard"} <= set(d["done"])
    assert not {"cli", "productivity", "telegram", "dashboard"} & set(d["left"])
    assert d["finished"] == 4 and d["total"] == 16 and d["percent"] == 25
    by = {t["id"]: t for t in d["topics"]}
    assert by["cli"]["status"] == "seen" and by["productivity"]["status"] == "works"
    assert by["cli"]["done"]["version"] == wizard.stable_version()
    assert call(ctx, "POST", "/api/wizard/topic/cli/done", {"how": "bogus"})[0] == 400
    assert call(ctx, "POST", "/api/wizard/topic/nope/done", {"how": "seen"})[0] == 404


def test_did_not_work_goes_to_failed_and_returns_when_its_bug_is_resolved(ctx):
    """Owner decision 24 Oct: Did not work leaves the to-do list for Failed (n), is NOT done, and comes back by
    itself when its linked bug entry is closed."""
    call(ctx, "POST", "/api/wizard/topic/music/done", {"how": "fails", "note": "no sound"})
    d = call(ctx, "GET", "/api/wizard")[1]
    assert d["failed"] == ["music"] and "music" not in d["left"] and "music" not in d["done"]
    assert d["finished"] == 0 and d["total"] == 16
    bug = next(i for i in ctx.store.issues("open") if i["kind"] == "setup_tour")
    ctx.store.set_issue_status(bug["id"], "resolved")
    d = call(ctx, "GET", "/api/wizard")[1]
    assert "music" in d["left"] and not d["failed"]
    assert next(t for t in d["topics"] if t["id"] == "music")["again"] == "retest"


def test_quick_check_is_opt_in_and_keeps_skipped(ctx):
    for tid in wizard.SMOKE:
        call(ctx, "POST", f"/api/wizard/topic/{tid}/done", {"how": "seen"})
    call(ctx, "POST", "/api/wizard/topic/music/done", {"how": "skipped"})
    assert not set(wizard.SMOKE) & set(call(ctx, "GET", "/api/wizard")[1]["left"])     # never re-asked by itself
    r = call(ctx, "POST", "/api/wizard/control", {"op": "quick"})[1]
    assert "music" not in r["back"] and set(r["back"]) == set(wizard.SMOKE) - {"music"}
    assert set(r["back"]) <= set(call(ctx, "GET", "/api/wizard")[1]["left"])


def test_later_moves_to_the_end_and_again_reopens(ctx):
    call(ctx, "POST", "/api/wizard/topic/voice/choice", {"choice": "later"})
    d = call(ctx, "GET", "/api/wizard")[1]
    assert d["left"][-1] == "voice" and d["next"] == "conversation" and "voice" not in d["done"]
    call(ctx, "POST", "/api/wizard/topic/voice/done", {"how": "works"})
    assert "voice" not in call(ctx, "GET", "/api/wizard")[1]["left"]
    assert call(ctx, "POST", "/api/wizard/topic/voice/again")[0] == 200
    d = call(ctx, "GET", "/api/wizard")[1]
    assert d["left"][0] == "voice" and "voice" not in d["done"]
    assert wizard.read_state(ctx.home)["topics"]["voice"]["history"][-1]["how"] == "works"


def test_old_progress_is_remembered_after_the_update(home):
    """4.0.1 wizard.json (setup/test/learned fields, no schema) -> those topics stay finished (#152)."""
    from jarves.channels_common import write_private_json
    write_private_json(home / wizard.STATE_FILE, {"features": {}, "topics": {
        "music": {"setup": "skipped"}, "voice": {"setup": "done", "test": "works", "verified_in": "4.0.1.349"},
        "cli": {"learned": True}, "google": {"setup": "done"}, "home": {"setup": "later", "ts": 5}}})
    o = wizard.overview(home)
    assert set(o["done"]) == {"music", "voice", "cli", "google"} and o["left"][-1] == "home"
    assert wizard.read_state(home)["schema"] == wizard.SCHEMA
    assert wizard.read_state(home)["topics"]["voice"]["done"]["version"] == "4.0.1"


def test_overview_is_local_and_fast(home, monkeypatch):
    """No connection probe, no switch probe, no network for the page's first paint (#148 speed budget)."""
    import time as _t

    def boom(*a, **k):
        raise AssertionError("overview must not probe connections")
    monkeypatch.setattr(wizard, "_connect_states", boom)
    monkeypatch.setattr(wizard.feat, "Features", boom)
    wizard.overview(home)                                                   # warm the guide/CHANGELOG caches
    t0 = _t.perf_counter()
    for _ in range(5):
        o = wizard.overview(home)
    assert (_t.perf_counter() - t0) / 5 < 0.15
    c = o["topics"][0]
    for k in ("do_en", "say_en", "expect_en", "do_he", "say_he", "expect_he"):
        assert c[k] and len(c[k]) <= 120, (k, c[k])


def test_every_topic_has_three_short_card_lines():
    for t in wizard.TOPICS:
        lines = wizard.card_lines(t)
        assert all(lines[k] for k in lines), t.id
        assert all(len(v) <= 120 for v in lines.values()), t.id


def test_cli_left_done_again_reset(home, monkeypatch, capsys):
    monkeypatch.setenv("JARVES_REPO_DIR", str(home))
    assert cli.main(["tour", "--done", "music", "--how", "skip"]) == 0
    assert cli.main(["tour", "--done", "cli"]) == 0
    capsys.readouterr()
    assert cli.main(["tour", "--left"]) == 0
    out = capsys.readouterr().out
    assert "14 left" in out and " music " not in out and " voice " in out
    assert cli.main(["tour", "--done", "nope"]) == 2
    assert cli.main(["tour", "--again", "music"]) == 0
    assert "music" in wizard.overview(home)["left"]
    assert cli.main(["tour", "--reset"]) == 2                               # needs --yes
    assert cli.main(["tour", "--reset", "--yes"]) == 0
    assert len(wizard.overview(home)["left"]) == 16


def test_signoff_sheet_roundtrip(ctx, home):
    call(ctx, "POST", "/api/wizard/topic/music/choice", {"choice": "skipped"})
    call(ctx, "POST", "/api/wizard/topic/productivity/result", {"result": "works"})
    call(ctx, "POST", "/api/wizard/topic/voice/result", {"result": "fails", "note": "x"})
    r = call(ctx, "POST", "/api/wizard/signoff", {})[1]
    sheet = so.load(home, wizard.stable_version())
    by = {x.id: x for x in sheet.rows}
    assert by["W-music"].result == "accepted-blocked" and by["W-music"].note
    assert by["W-productivity"].result == "pass" and by["W-voice"].result == "fail"
    assert r["file"].endswith(f"MANUAL_TEST_{wizard.stable_version()}.md") and r["open"]
    # standing rows of an existing sheet survive a regeneration
    sheet.rows.append(so.Row("S-1", "Standing", "do", "ok", "pass"))
    so.save(home, sheet)
    call(ctx, "POST", "/api/wizard/signoff", {})
    assert "S-1" in {x.id for x in so.load(home, wizard.stable_version()).rows}


def test_cli_tour_status_and_signoff(home, monkeypatch, capsys):
    monkeypatch.setenv("JARVES_REPO_DIR", str(home))
    (home / "docs" / "reviews").mkdir(parents=True)
    (home / "core").mkdir()
    assert cli.main(["tour", "--status"]) == 0
    assert "Setup and tour" in capsys.readouterr().out
    assert cli.main(["tour", "--status", "--json"]) == 0
    assert '"mode": "baseline"' in capsys.readouterr().out
    assert cli.main(["tour", "--signoff"]) == 1                   # rows are still pending, so the exit code says so
    assert (home / "docs" / "reviews" / f"MANUAL_TEST_{wizard.stable_version()}.md").is_file()


def test_voice_tool_opens_the_tour_page(home):
    import asyncio
    from jarves.skills import SkillRegistry
    f = Features(Config(), home)
    opened = []

    async def opener(page):
        opened.append(page)
        return "http://127.0.0.1:1/?t=x"
    reg = SkillRegistry()
    skill = guides.make_skill(f, guides.GuideState(home), reg, opener=opener)
    tool = next(t for t in skill.tools() if t.name == "show_tour")
    res = asyncio.run(tool.handler(tool.args(topic="music")))
    assert res.ok and opened == ["#/setup/music"]
    res = asyncio.run(tool.handler(tool.args(topic="")))
    assert opened[-1] == "#/setup"


def test_setup_page_is_the_wizard_and_addons_moved():
    root = __import__("pathlib").Path(wizard.__file__).parent / "dashboard_static"
    assert "PAGES.setup = pageTour" in (root / "wizard.js").read_text(encoding="utf-8")
    assert "PAGES.addons" in (root / "setup.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    assert 'href="#/setup" data-page="setup"' in html and 'href="#/addons"' in html
    assert re.search(r'wizard\.js', html)


def test_next_marks_the_topic_done_and_progress_advances(ctx):
    """#147 (semantics of #152 + owner decision 24 Oct): 'Got it, next' (learned) or a test result finishes the topic;
    Later is not done; Did not work goes to Failed (out of the to-do list, not done)."""
    assert call(ctx, "GET", "/api/wizard")[1]["finished"] == 0
    call(ctx, "POST", "/api/wizard/topic/productivity/learned")
    t = call(ctx, "GET", "/api/wizard/topic/productivity")[1]
    assert t["status"] == "seen"
    d = call(ctx, "GET", "/api/wizard")[1]
    assert d["finished"] == 1 and d["percent"] > 0
    call(ctx, "POST", "/api/wizard/topic/voice/result", {"result": "works"})
    assert call(ctx, "GET", "/api/wizard/topic/voice")[1]["status"] == "works"
    call(ctx, "POST", "/api/wizard/topic/music/result", {"result": "skip"})
    assert call(ctx, "GET", "/api/wizard/topic/music")[1]["status"] == "skipped"
    call(ctx, "POST", "/api/wizard/topic/phone/choice", {"choice": "later"})
    call(ctx, "POST", "/api/wizard/topic/phone/learned")
    assert call(ctx, "GET", "/api/wizard/topic/phone")[1]["status"] == "later"      # Next never overrides Later / Skip
    call(ctx, "POST", "/api/wizard/topic/cli/result", {"result": "fails"})
    assert call(ctx, "GET", "/api/wizard/topic/cli")[1]["status"] == "fails"
    d = call(ctx, "GET", "/api/wizard")[1]
    assert "cli" in d["failed"] and "cli" not in d["done"] and "cli" not in d["left"]   # a failed test is not done
