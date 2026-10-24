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
    assert t["status"] == "done" and t["verified_in"] == wizard.__version__
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


def test_diff_mode_shows_changed_unverified_and_smoke(home):
    st = {"baseline": {"version": "4.0.1", "date": "2026-10-22"}, "topics": {}, "features": {}}
    log = "## 4.0.2\n- Fixed: WhatsApp bridge reconnect.\n\n## 4.0.1\n- old\n"
    for t in wizard.TOPICS:
        for f in wizard._features_of(t):
            st["features"][f] = {"version": "4.0.1"}
    ids = wizard.topic_list(st, "4.0.2", log)
    assert "whatsapp" in ids and set(wizard.SMOKE) <= set(ids) and "telegram" not in ids
    st["features"].pop("telegram")
    assert "telegram" in wizard.topic_list(st, "4.0.2", log)
    assert wizard.mode({}) == "baseline" and wizard.mode(st) == "diff"


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


def test_wizard_api_is_fast_and_probes_connections_once(ctx, monkeypatch):
    """#148: the tour must not re-probe connections on every call (the Pear TCP connect made each call ~0.4 s+)."""
    import time
    from jarves import connect
    wizard._CONN_CACHE.clear()
    calls = []
    real = connect.items_json
    monkeypatch.setattr(connect, "items_json", lambda *a, **k: (calls.append(1), real(*a, **k, pear_probe=lambda u: False))[1])
    call(ctx, "GET", "/api/wizard")                          # warm (guides load once)
    t0 = time.perf_counter()
    for _ in range(5):
        call(ctx, "GET", "/api/wizard")
        call(ctx, "GET", "/api/wizard/topic/voice")
    assert time.perf_counter() - t0 < 1.5
    assert len(calls) == 1
    call(ctx, "POST", "/api/wizard/topic/voice/choice", {"choice": "done"})     # a change drops the cache
    call(ctx, "GET", "/api/wizard")
    assert len(calls) == 2
