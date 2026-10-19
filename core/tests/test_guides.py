"""Feature guides (SW-V2 WP-L, guides/): every feature has a guide, links valid, Hebrew present, live state, the
dashboard API, JARVES's help Skill walking a setup step by step, setup hints and the proactive suggestions (once,
snooze, stop, quiet hours). Offline, silent, no hardware, no network."""
import importlib
import re
from pathlib import Path

import pytest

# every module that registers features (the Core imports all of them)
for _m in ("jarves.app", "jarves.operations", "jarves.runtime", "jarves.usage", "jarves.backup", "jarves.appearance",
           "jarves.skills.generated", "jarves.guides"):
    importlib.import_module(_m)

from jarves import features as feat  # noqa: E402
from jarves import guides  # noqa: E402
from jarves.config import Config  # noqa: E402
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult  # noqa: E402

HE = re.compile(r"[֐-׿]")
STATIC = Path(guides.__file__).parents[1] / "dashboard_static"
OTHER_PAGES = {"connections"}          # WP-K's Connections page (linked, not duplicated)


def make(home, **kw):
    return feat.Features(Config(), home, **kw)


def _connect_google(home):
    """What `jarvis google start` leaves behind (WP-K google_auth token file)."""
    (home / "google").mkdir(exist_ok=True)
    (home / "google" / "token.json").write_text('{"refresh_token": "1//x", "scopes": []}', encoding="utf-8")


# ---------------------------------------------------------------------- the data
def test_every_registered_feature_has_exactly_one_guide():
    owners: dict[str, list[str]] = {}
    for g in guides.load().values():
        for fid in g.features:
            owners.setdefault(fid, []).append(g.id)
    missing = [fid for fid in feat.CATALOG if fid not in owners]
    assert not missing, f"features without a guide (add core/src/jarves/guides/data/<id>.toml): {missing}"
    assert not {k: v for k, v in owners.items() if len(v) > 1}


def test_required_topics_have_guides():
    for gid in ("voice_ai", "music", "web_search", "background_tasks", "memory", "diagnosis", "wake_word", "greeting",
                "chimes", "persona", "voice_change", "camera", "face_recognition", "camera_when_away",
                "google_calendar", "gmail", "appearance", "skill_creation", "scheduler", "notes_files", "alerts",
                "autonomy", "usage", "backup", "runtime", "updates", "lan_dashboard", "custom_wake_phrase", "privacy",
                "help", "api_bill", "api_budget"):
        assert gid in guides.load(), gid


def _pages() -> set[str]:
    js = "\n".join(p.read_text(encoding="utf-8") for p in STATIC.glob("*.js"))
    pages = set(re.findall(r"PAGES\.(\w+)\s*=", js)) | set(re.findall(r"PAGES\[\"([\w-]+)\"\]\s*=", js))
    pages |= set(re.findall(r'page === "(\w+)"', js)) | {"home"}
    for block in re.findall(r"Object\.assign\(PAGES,\s*\{(.*?)\}\)", js, re.S):
        pages |= set(re.findall(r"(\w+)\s*:", block))
    return pages | OTHER_PAGES


def test_guides_text_hebrew_and_links_valid():
    gs = guides.load()
    pages = _pages()
    for g in gs.values():
        for k in ("title_he", "what_he"):
            assert HE.search(getattr(g, k)), (g.id, k)
        assert g.title_en and g.what_en and g.cost_he and g.privacy_he, g.id
        assert g.steps, g.id
        for s in g.steps:
            assert HE.search(s.he) and s.en, g.id
            kind, _, arg = s.action.partition(":")
            assert kind in ("", "feature_on", "open", "command", "test", "set_budget"), (g.id, s.action)
            if kind == "feature_on":
                assert arg in g.features, (g.id, arg)
            if kind == "open":
                m = re.fullmatch(r"#/([\w-]+)(?:/(\w+))?", arg)
                assert m and m.group(1) in pages, (g.id, arg)
                if m.group(1) == "guide":
                    assert m.group(2) in gs, (g.id, arg)
            if kind == "test":
                assert g.test or g.features, g.id
            if s.check:
                ck, _, carg = s.check.partition(":")
                assert ck in ("enabled", "available", "active", "probe"), (g.id, s.check)
                if ck == "probe":
                    assert carg in guides.EXTRA_PROBES
                else:
                    assert carg in g.features or carg in feat.CATALOG, (g.id, s.check)
        for he, en in g.say:
            assert HE.search(he) and en, g.id
        for p in g.problems:
            assert HE.search(p["he"]) and HE.search(p["fix_he"]) and p["en"] and p["fix_en"], g.id
        for link in g.links:
            assert link in gs, (g.id, link)
        for p in g.pages:
            assert p in pages, (g.id, p)
        assert g.test in ("",) + tuple(guides.TESTS), g.id
        assert g.probe in ("",) + tuple(guides.EXTRA_PROBES), g.id
        assert g.category in ("start", "voice", "everyday", "google", "memory", "camera", "costs", "system",
                              "privacy"), g.id
    assert [c["id"] for c in guides.checklist(make(None))][:2] == ["voice_ai", "wake_word"]


def test_find_by_hebrew_english_and_alias():
    for name, gid in (("גוגל", "google_calendar"), ("Google", "google_calendar"), ("מוזיקה", "music"),
                      ("wake word", "wake_word"), ("מצלמה", "camera"), ("gmail_send", "gmail"),
                      ("admin key", "api_bill"), ("תקציב", "api_budget"), ("מה אתה יכול לעשות", "help")):
        assert guides.find(name).id == gid, name
    assert guides.find("") is None and guides.find("zzqqx") is None


# ---------------------------------------------------------------------- live state
def test_state_follows_live_switches_and_probes(home):
    f = make(home, skills=lambda: {"calendar", "gmail"})
    g = guides.load()["google_calendar"]
    s = guides.state(g, f)
    assert s["state"] == "setup" and "Google" in s["reason"]
    _connect_google(home)
    f.invalidate()
    assert guides.state(g, f)["state"] == "on"
    f.set("google_calendar", False)
    assert guides.state(g, f)["state"] == "off"
    cam = guides.state(guides.load()["camera"], f)
    assert cam["state"] == "off"                          # default OFF: a choice, not "setup needed"
    assert guides.state(guides.load()["custom_wake_phrase"], f)["state"] == "planned"
    assert guides.state(guides.load()["api_bill"], f)["state"] == "off"     # optional admin key
    assert guides.state(guides.load()["api_budget"], f)["state"] == "setup"
    guides.set_budget(home, 20)
    assert guides.state(guides.load()["api_budget"], f)["state"] == "on"


def test_platform_status_comes_from_feature_targets(home):
    f = make(home)
    d = guides.full(guides.load()["scheduler"], f)
    assert d["platforms"]["source"] == "scheduler" and d["platforms"]["pi"]["status"] == "implemented_unverified"
    d = guides.full(guides.load()["custom_wake_phrase"], f)
    assert d["platforms"]["windows"]["status"] == "planned"


def test_briefing_capped_hot_and_index(home):
    f = make(home)
    b = guides.briefing(f)
    assert len(b) <= guides.BRIEFING_BACKEND_CHARS + 30
    assert "Needs setup" in b and "google_calendar" in b and "explain_feature" in b
    assert "Music" in b or "music" in b
    f.set("music", False)
    assert "Music (turned off" in guides.briefing(f)      # recomputed: follows the switch at once
    assert len(guides.briefing(f, limit=400)) <= 430
    assert len(guides.front_briefing(f)) <= guides.BRIEFING_FRONT_CHARS + 1
    idx = guides.index_markdown()
    assert idx.startswith("## Guides") and "music (מוזיקה)" in idx


# ---------------------------------------------------------------------- safe tests
def test_test_now_checks_are_local_and_truthful(home, monkeypatch):
    f = make(home)
    r = guides.run_test(guides.load()["backup"], f)
    assert not r["ok"] and HE.search(r["he"])
    from jarves import backup
    backup.create(home, "manual")
    assert guides.run_test(guides.load()["backup"], f)["ok"]
    monkeypatch.setattr(feat, "_tcp_open", lambda url, timeout=0.3: False)
    r = guides.run_test(guides.load()["music"], f)
    assert not r["ok"] and "Pear" in r["en"]
    assert not guides.run_test(guides.load()["voice_ai"], f)["ok"]       # no key in the test env
    r = guides.run_test(guides.load()["camera"], f)
    assert isinstance(r["ok"], bool)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
    assert not guides.run_test(guides.load()["api_bill"], f)["ok"]       # no network without a key


def test_admin_key_test_uses_costs_api(home, monkeypatch):
    from jarves import usage
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-test")
    calls = []

    def fake_get(url, headers, params):
        calls.append(url)
        return 200, {"data": [{"results": [{"amount": {"value": 3.5, "currency": "usd"}}]}], "has_more": False}
    monkeypatch.setattr(usage, "_http_get", fake_get)
    r = guides.run_test(guides.load()["api_bill"], make(home))
    assert r["ok"] and "$3.50" in r["en"] and calls == [usage.COSTS_URL]


# ---------------------------------------------------------------------- dashboard
def test_dashboard_routes(home):
    from jarves import dashboard
    ctx = dashboard.Ctx(home, Config())
    try:
        assert "jarves.guides" in dashboard.load_plugins()
        st, d = guides.api_guides(ctx, None, None)
        assert st == 200 and {g["id"] for g in d["guides"]} == set(guides.load())
        assert d["checklist"] and d["setup_needed"] >= 1 and d["help"]["proactive_help"] is True
        st, g = guides.api_guide(ctx, re.match(r"(\w+)", "music"), None)
        assert st == 200 and g["steps"] and g["say"] and g["feature_rows"][0]["id"] == "music"
        assert guides.api_guide(ctx, re.match(r"(\w+)", "nope"), None)[0] == 404
        st, r = guides.api_guide_test(ctx, re.match(r"(\w+)", "backup"), {})
        assert st == 200 and r["ok"] is False
        m = re.match(r"(\w+)/(\d+)", "greeting/1")
        guides._ctx(ctx)[0].set("greeting", False)
        st, r = guides.api_guide_step(ctx, m, {})
        assert st == 200 and r["feature"]["enabled"] is True
        st, r = guides.api_guide_step(ctx, re.match(r"(\w+)/(\d+)", "api_budget/1"), {})
        assert st == 200 and r["api_monthly_budget_usd"] == 20
        assert guides.api_guide_step(ctx, re.match(r"(\w+)/(\d+)", "voice_ai/1"), {})[0] == 400   # user does it
        assert guides.api_help(ctx, None, {"op": "snooze", "guide": "music", "days": 3})[0] == 200
        assert "music" in guides.api_guides(ctx, None, None)[1]["help"]["snoozed"]
        assert guides.api_help(ctx, None, {"op": "reset"})[0] == 200
        assert guides.api_help(ctx, None, {"op": "bogus"})[0] == 400
    finally:
        ctx.close()


def test_static_files_wired():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/static/guides.js" in html and "/static/guides.css" in html
    js = (STATIC / "guides.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "PAGES.guides" in js and "PAGES.guide " in js.replace("=", " ")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "#/guide-for/" in app
    assert "guidesSetupCard" in (STATIC / "home.js").read_text(encoding="utf-8")       # WP-K Home


# ---------------------------------------------------------------------- JARVES: help Skill, step by step
async def _call(reg, name, args=None):
    return await reg.call(name, args or {}, origin="model")


async def test_help_walks_google_setup_one_step_at_a_time(home):
    f = make(home, skills=lambda: {"calendar", "gmail"})
    st = guides.GuideState(home)
    reg = SkillRegistry()
    opened = []

    async def opener(page):
        opened.append(page)
        return "http://127.0.0.1:1/?t=secret"
    reg.register(guides.make_skill(f, st, reg, opener=opener))
    reg.allowed_actions |= guides.HELP_ACTIONS
    # "איך אני מחבר את גוגל?"
    r = await _call(reg, "explain_feature", {"name": "גוגל"})
    assert r.ok and r.data["guide"] == "google_calendar" and r.data["state"] == "setup"
    assert "jarvis google start" in r.message
    r = await _call(reg, "setup_next_step", {"name": "google"})
    assert r.data["step"] == 1 and r.data["total"] == 3 and "jarvis google start" in r.message
    # user: "done" -- but Google is still not connected: the check says so, the step stays
    r = await _call(reg, "setup_next_step", {"name": "google", "done": True})
    assert r.code == "GUIDE_STEP_NOT_DONE" and r.data["step"] == 1 and "Google" in r.data["why"]
    r = await _call(reg, "run_guide_step", {"name": "google"})          # a terminal step: he can only explain
    assert r.code == "GUIDE_STEP_MANUAL" and r.data["command"] == "jarvis google start"
    _connect_google(home)      # the user ran the wizard
    f.invalidate()
    r = await _call(reg, "setup_next_step", {"name": "google", "done": True})
    assert r.data["step"] == 2 and r.data["action"] == "open:#/connections"
    r = await _call(reg, "run_guide_step", {"name": "google"})
    assert r.ok and opened == ["#/connections"] and "secret" not in r.to_model()
    r = await _call(reg, "setup_next_step", {"name": "google", "done": True})
    assert r.data.get("finished") and r.data["test_ok"]                  # step 3's check (active) already passes
    assert "סיימנו" in r.message
    r = await _call(reg, "open_guide", {"name": "מוזיקה"})
    assert r.ok and opened[-1] == "#/guide/music"


async def test_risky_step_needs_on_screen_approval(home):
    f = make(home)
    st = guides.GuideState(home)
    reg = SkillRegistry()
    asked = []

    async def deny(prompt):
        asked.append(prompt)
        return False
    reg.confirm = deny
    reg.register(guides.make_skill(f, st, reg))
    reg.allowed_actions |= guides.HELP_ACTIONS
    r = await _call(reg, "run_guide_step", {"name": "camera", "step": 2})
    assert r.code == "FEATURE_NEEDS_APPROVAL" and asked and not f.enabled("camera")
    assert f.status("camera")["requested"]                              # shows on the dashboard for approval
    r = await _call(reg, "run_guide_step", {"name": "greeting", "step": 1})   # low risk: done at once
    assert r.ok and f.enabled("greeting")


async def test_whats_not_set_up_and_unknown(home):
    f = make(home)
    reg = SkillRegistry()
    reg.register(guides.make_skill(f, guides.GuideState(home), reg))
    r = await _call(reg, "whats_not_set_up")
    ids = {x["id"] for x in r.data["needs_setup"]}
    assert {"voice_ai", "google_calendar"} <= ids and "camera" in r.data["off_by_choice"]
    assert (await _call(reg, "explain_feature", {"name": "zzqqx"})).code == "GUIDE_UNKNOWN"


# ---------------------------------------------------------------------- setup hints on tool errors
async def test_feature_off_error_carries_setup_hint(home):
    f = make(home)

    class Cal(Skill):
        name = "calendar"

        def tools(self):
            async def h(_):
                return ToolResult(True, "x")
            return [Tool("calendar_list", "x", h, permission="read")]

    class Music(Skill):
        name = "music"

        def tools(self):
            async def h(_):
                return ToolResult(False, "Pear is not running", code="MUSIC_PLAYER_OFFLINE")
            return [Tool("music_play", "x", h, permission="read")]
    reg = SkillRegistry()
    reg.gate = f.gate
    reg.register(Cal())
    reg.register(Music())

    class Core:
        features = f
    helper = guides.Helper.__new__(guides.Helper)
    helper.core = Core()
    reg.setup_hint = helper.hint
    r = await _call(reg, "calendar_list")
    assert r.code == "FEATURE_OFF"
    h = r.data["setup_hint"]
    assert h["guide"] == "google_calendar" and h["dashboard"] == "#/guide/google_calendar" and h["next_step_en"]
    assert "setup_hint" in r.to_model()
    f.set("music", True)
    monkey = feat.CATALOG["music"].probe
    feat.CATALOG["music"].probe = lambda c: (True, "")
    try:
        f.invalidate()
        r = await _call(reg, "music_play")
        assert r.code == "MUSIC_PLAYER_OFFLINE" and r.data["setup_hint"]["guide"] == "music"
    finally:
        feat.CATALOG["music"].probe = monkey


# ---------------------------------------------------------------------- proactive suggestions
class FakeLive:
    def session_config(self):
        return {"instructions": "BASE"}


def _session(f, sug):
    live = FakeLive()
    guides.attach_live(live, f, sug)
    first = live.session_config()["instructions"]
    again = live.session_config()["instructions"]          # a reconnect in the same conversation: same text
    assert first == again
    return first


def test_suggests_once_then_waits_days(home):
    f = make(home)
    now = [1_000_000.0]
    st = guides.GuideState(home)
    sug = guides.Suggester(f, st, clock=lambda: now[0])
    s1 = _session(f, sug)
    assert "BASE" in s1 and "Setup suggestion" in s1 and "חיבור לבינה המלאכותית" in s1   # first run: checklist #1
    s2 = _session(f, sug)
    assert "חיבור לבינה המלאכותית" not in s2.split("Setup suggestion")[-1]     # never the same one twice in N days
    picked = [st.read()["suggested"]]
    now[0] += 8 * 86400
    s3 = _session(f, sug)
    assert "Setup suggestion" in s3 and picked
    assert len(st.read()["suggested"]) >= 2


def test_snooze_dismiss_stop_and_quiet(home):
    f = make(home)
    st = guides.GuideState(home)
    quiet = [True]
    sug = guides.Suggester(f, st, quiet=lambda: quiet[0])
    assert sug.pick() is None                                # quiet hours: nothing
    quiet[0] = False
    first = sug.pick()
    st.snooze(first.guide.id, 3)
    assert sug.pick().guide.id != first.guide.id
    for g in guides.load().values():
        st.dismiss(g.id)
    assert sug.pick() is None
    st.reset()
    f.set("proactive_help", False)                           # "תפסיק להציע"
    assert sug.pick() is None and "Setup suggestion" not in _session(f, sug)
    assert "delegate it" in _session(f, sug)                 # the help routing stays


async def test_voice_snooze_and_stop_suggestions(home):
    f = make(home)
    st = guides.GuideState(home)
    reg = SkillRegistry()
    reg.register(guides.make_skill(f, st, reg))
    reg.allowed_actions |= guides.HELP_ACTIONS
    r = await _call(reg, "setup_snooze", {"name": "google", "days": 5})
    assert r.ok and "google_calendar" in st.read()["snoozed"]
    r = await _call(reg, "setup_snooze", {"name": "music", "days": 0})
    assert r.data["dismissed"] and "music" in st.read()["dismissed"]
    r = await _call(reg, "stop_suggestions", {"on": False})
    assert r.ok and not f.enabled("proactive_help")
    sug = guides.Suggester(f, st)
    assert sug.pick() is None


def test_failing_feature_is_suggested_first(home):
    f = make(home)
    f.set("music", True)
    st = guides.GuideState(home)
    sug = guides.Suggester(f, st, failing=lambda: {"music"})
    assert sug.pick().guide.id == "music" and sug.pick().why == "failing"


# ---------------------------------------------------------------------- the running Core
async def test_core_has_help_skill_and_guide_index(home, cfg):
    from conftest import FakeSTT, FakeTTS
    from jarves.app import Core
    from jarves.skills.music import FakePlayer
    from jarves.voice.audio import SimAudio
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    try:
        names = {t.name for t in c.registry.tools()}
        assert {"explain_feature", "whats_not_set_up", "setup_next_step", "run_guide_step", "open_guide"} <= names
        tools_md = (home / "workspace" / "TOOLS.md").read_text(encoding="utf-8")
        assert "## Guides" in tools_md and "google_calendar" in tools_md
        assert c.registry.setup_hint is not None
        r = await c.registry.call("explain_feature", {"name": "מוזיקה"})
        assert r.ok and r.data["guide"] == "music"
        if c.agent is not None:
            assert "What I can do" in c.agent.instructions()
    finally:
        await c.stop()


def test_workweek_setting(home):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from jarves import scheduler as S
    now = datetime(2026, 10, 19, 9, 0, tzinfo=ZoneInfo("Asia/Jerusalem"))
    assert S.parse_when("כל ימי חול בשבע", now).spec.endswith("0-4")
    assert S.parse_when("כל ימי חול בשבע", now, "1-5").spec.endswith("1-5")
    assert S.parse_when("כל ימי חול בשבע", now, "bogus").spec.endswith("0-4")


@pytest.mark.parametrize("gid", ["music", "google_calendar", "camera"])
def test_full_view_shape(home, gid):
    d = guides.full(guides.load()[gid], make(home))
    for k in ("steps", "say", "expect_he", "cost_he", "privacy_he", "problems", "platforms", "feature_rows", "links"):
        assert k in d, k
