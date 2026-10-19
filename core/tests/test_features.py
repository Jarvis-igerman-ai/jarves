"""Feature switches (SW-V2 WP-I, features.py): enabled vs available, persistence, legacy config, registry gate,
voice tool policy, Settings delegation, CLI, dashboard routes and the running Core. Offline, silent, no hardware."""
import asyncio
import json
import re

import pytest

from jarves import features as feat
from jarves.config import Config
from jarves.features import CATALOG, Features
from jarves.memory import MemoryStore
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult


def make(home, cfg=None, **kw):
    return Features(cfg or Config(), home, **kw)


def test_defaults_privacy_features_off(home):
    f = make(home)
    for fid in ("camera", "face_recognition", "camera_when_away", "gmail_send"):
        assert not f.enabled(fid), fid
        assert CATALOG[fid].risk == "high"
    for fid in ("music", "web_search", "background_tasks", "memory_curator", "wake_word", "greeting", "chimes",
                "dashboard", "workspace_agent_edit", "voice_change", "google_calendar", "gmail_read"):
        assert f.enabled(fid), fid
    assert not f.enabled("memory_llm_pass")
    assert f.enabled("diagnosis_llm_pass")         # WP-H brief: AI review ON by default (budgeted, max/day)


def test_enabled_and_available_are_separate(home):
    f = make(home)
    f.set("camera", True)
    assert f.enabled("camera")
    ok, why = f.available("camera")            # no camera on the test machine (or none reported by a Skill)
    if not ok:
        assert not f.active("camera") and "camera" in why
        assert f.reason("camera") == why
    f.set("camera", False)
    assert f.reason("camera") == "turned off"
    # dependency: face recognition needs the camera feature active
    f.set("face_recognition", True)
    assert not f.active("face_recognition")


def test_camera_reported_by_a_skill_makes_it_available(home):
    comps = {"camera": {"status": "ok"}}
    f = make(home, components=lambda: comps, skills=lambda: {"camera"})
    f.set("camera", True)
    assert f.active("camera")
    comps["camera"] = {"status": "unavailable", "detail": "camera unplugged"}
    f.invalidate()
    assert f.available("camera") == (False, "camera unplugged")


def test_google_needs_token_and_connector(home):
    f = make(home, skills=lambda: set())
    ok, why = f.available("gmail_read")
    assert not ok and why.startswith("Google account not connected (gmail)") and "jarvis google start" in why
    (home / "google").mkdir()                       # WP-K: the real token file of google_auth (was google_token.json)
    (home / "google" / "token.json").write_text('{"refresh_token": "1//x", "scopes": []}', encoding="utf-8")
    f.invalidate()
    ok, why = f.available("gmail_read")
    assert not ok and "not installed" in why
    f2 = make(home, skills=lambda: {"gmail"})
    assert f2.available("gmail_read") == (True, "")


def test_no_api_key_reasons(home):
    f = make(home)
    assert f.available("background_tasks") == (False, "no OpenAI API key (run `jarvis key`)")
    cfg = Config()
    cfg.ai.provider = "offline"
    assert make(home, cfg).available("web_search")[1].startswith("AI is offline")


def test_persisted_atomically_and_reloaded(home):
    f = make(home)
    f.set("music", False, by="cli")
    raw = json.loads((home / "features.json").read_text(encoding="utf-8"))
    assert raw["enabled"]["music"] is False and raw["changed"]["music"]["by"] == "cli"
    assert not (home / "features.tmp").exists()
    assert not make(home).enabled("music")
    assert make(home).status("music")["source"] == "user"
    make(home).reset("music")
    assert make(home).enabled("music")


def test_corrupt_file_falls_back_to_defaults(home):
    (home / "features.json").write_text("{not json", encoding="utf-8")
    f = make(home)
    assert f.enabled("music") and not f.enabled("camera")
    assert (home / "features.bad.json").exists()


def test_legacy_config_is_the_default_and_switch_wins(home):
    cfg = Config.model_validate({"voice": {"greeting": False, "chime": False, "wake_enabled": False},
                                 "ai": {"web_search": False, "tasks_enabled": False}, "music": {"provider": "none"}})
    f = make(home, cfg)
    for fid in ("greeting", "chimes", "wake_word", "web_search", "background_tasks", "music"):
        assert not f.enabled(fid), fid
        assert f.status(fid)["source"] == "config"
    assert make(home).status("greeting")["source"] == "default"
    f.set("greeting", True)
    f.apply_to_config()
    assert cfg.voice.greeting is True              # the loaded Config mirrors the one source of truth
    f.reset("greeting")                            # back to config.toml, not to the mirrored value
    assert not f.enabled("greeting")
    f.apply_to_config()
    assert cfg.voice.greeting is False


def test_other_process_change_reaches_listeners(home):
    core_side = make(home)
    seen = []
    core_side.listeners.append(lambda fid, on: seen.append((fid, on)))
    assert core_side.enabled("chimes")
    make(home).set("chimes", False, by="dashboard")      # e.g. the dashboard process
    import os, time
    p = home / "features.json"
    os.utime(p, (time.time() + 5, time.time() + 5))       # coarse file clocks: make the change visible
    core_side.poll()
    assert seen == [("chimes", False)] and not core_side.enabled("chimes")


def test_import_legacy_curator_overrides(home):
    mem = MemoryStore(home / "memory.db")
    mem.set_setting("override:curator.auto", "off")
    mem.set_setting("override:curator.llm", "on")
    f = make(home)
    assert sorted(f.import_legacy(feat.legacy_settings(mem))) == ["memory_curator", "memory_llm_pass"]
    assert not f.enabled("memory_curator") and f.enabled("memory_llm_pass")
    f.set("memory_curator", True)
    assert f.import_legacy(feat.legacy_settings(mem)) == []      # once only; the switch is the truth now
    assert f.enabled("memory_curator")
    mem.close()


def test_settings_curator_keys_delegate(home):
    from jarves.skills.settings import Settings
    from jarves.workspace import WorkspaceError
    mem = MemoryStore(home / "memory.db")
    st = Settings(mem)
    st.features = f = make(home)
    assert st.get("curator.auto") == "on" and st.get("curator.llm") == "off"
    st.set("curator.auto", "off")
    assert not f.enabled("memory_curator") and st.get("curator.auto") == "off"
    with pytest.raises(WorkspaceError, match="approval"):
        st.set("curator.llm", "on")                # medium risk: not from a conversation without approval
    assert not f.enabled("memory_llm_pass")
    mem.close()


class _Music(Skill):
    name = "music"

    def tools(self):
        async def play(_):
            return ToolResult(True, "playing")
        return [Tool("music_play", "play", play, permission="action")]


class _Ws(Skill):
    name = "settings"

    def tools(self):
        async def edit(_):
            return ToolResult(True, "edited")
        return [Tool("workspace_edit", "edit", edit, permission="read"),
                Tool("list_settings", "list", edit, permission="read")]


async def test_registry_hides_and_refuses_inactive_features(home):
    f = make(home, components=lambda: {"music": {"status": "ok"}})
    reg = SkillRegistry()
    reg.gate = f.gate
    reg.register(_Music())
    reg.register(_Ws())
    names = lambda: {t.name for t in reg.tools()}
    assert {"music_play", "workspace_edit", "list_settings"} <= names()
    f.set("music", False)
    f.set("workspace_agent_edit", False)                # tool-level ownership inside a Skill
    assert names() == {"list_settings"}                 # live: the next tool list
    assert reg.get("music_play") is None
    r = await reg.call("music_play", {})
    assert not r.ok and r.code == "FEATURE_OFF" and "turned off" in r.message
    f.set("music", True)
    assert (await reg.call("music_play", {})).ok


async def test_broken_gate_does_not_hide_tools(home):
    reg = SkillRegistry()
    reg.register(_Music())
    reg.gate = lambda s, t: 1 / 0
    assert [t.name for t in reg.tools()] == ["music_play"]


async def test_voice_tool_policy(home):
    f = make(home)
    reg = SkillRegistry()
    reg.register(feat.make_skill(f, reg))
    reg.allowed_actions |= {"feature_set"}
    asked = []

    async def no(prompt):
        asked.append(prompt)
        return False
    reg.confirm = no
    r = await reg.call("feature_set", {"id": "chimes", "on": False})       # off: always allowed
    assert r.ok and not f.enabled("chimes") and asked == []
    r = await reg.call("feature_set", {"id": "chimes", "on": True})        # low risk on: allowed
    assert r.ok and f.enabled("chimes") and asked == []
    r = await reg.call("feature_set", {"id": "camera", "on": True})        # high risk: on-screen approval
    assert not r.ok and r.code == "FEATURE_NEEDS_APPROVAL" and not f.enabled("camera")
    assert asked and "Camera" in asked[0]
    assert f.status("camera")["requested"]["by"] == "voice"               # shown on the dashboard

    async def yes(prompt):
        return True
    reg.confirm = yes
    r = await reg.call("feature_set", {"id": "camera", "on": True})
    assert r.ok and f.enabled("camera") and f.status("camera")["requested"] is None
    assert "לא זמינה" in r.message or f.available("camera")[0]          # truthful when no camera
    r = await reg.call("feature_set", {"id": "nope", "on": True})
    assert r.code == "FEATURE_UNKNOWN"
    r = await reg.call("features_list", {})
    assert r.ok and "camera:" in r.message and len(r.data["features"]) == len(CATALOG)


def test_capabilities_text_is_truthful(home):
    f = make(home)
    md = f.capabilities_markdown()
    assert "## Features" in md and "מצלמה / Camera (camera): turned off" in md
    f.set("camera", True)
    md = f.capabilities_markdown()
    ok, why = f.available("camera")
    assert ok or f"(camera): {why}" in md
    note = f.front_note()
    assert "מצלמה" in note and len(note) < 800


def test_register_api_for_other_packages(home):
    try:
        feat.register("test_widget", "וידג'ט", "Widget", False, probe=lambda c: (False, "no widget"),
                      needs=["music"], risk="medium", tools=["widget_go"])
        f = make(home)
        assert not f.enabled("test_widget")
        f.set("test_widget", True)
        assert f.available("test_widget") == (False, "no widget")
        assert f.gate("x", "widget_go") is not None
        with pytest.raises(ValueError):
            feat.register("bad", "x", "x", True, risk="extreme")
    finally:
        CATALOG.pop("test_widget", None)


def test_cli_features(home, capsys):
    from jarves.cli import main
    assert main(["features"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"camera\s+off\s+high", out)
    assert main(["features", "off", "music"]) == 0
    assert "music: off" in capsys.readouterr().out
    assert not make(home).enabled("music")
    assert main(["features", "on", "nonsense"]) == 2
    assert main(["features", "reset", "music"]) == 0
    assert make(home).enabled("music")
    assert main(["features", "off", "dashboard"]) == 0
    assert main(["dashboard", "--no-open"]) == 1          # switched off: refuses to start


def test_dashboard_routes(home):
    from jarves import dashboard
    cfg = Config()
    ctx = dashboard.Ctx(home, cfg)
    try:
        assert "jarves.features" in dashboard.load_plugins()
        st, body = feat.api_features(ctx, None, None)
        assert st == 200 and {x["id"] for x in body["features"]} == set(CATALOG)
        m = re.match(r"^/api/features/([a-z0-9_]+)$", "/api/features/camera")
        st, body = feat.api_feature_set(ctx, m, {"enabled": True})      # the dashboard is the user's approval
        assert st == 200 and body["enabled"] and body["changed_by"] == "dashboard"
        assert feat.api_feature_set(ctx, m, {"enabled": "yes"})[0] == 400
        assert feat.api_feature_set(ctx, re.match(r"(.*)", "zzz"), {"enabled": True})[0] == 404
        st, body = feat.api_feature_set(ctx, m, {"reset": True})
        assert not body["enabled"]
    finally:
        ctx.close()


async def test_core_follows_switches(home, cfg):
    from conftest import FakeSTT, FakeTTS
    from jarves.app import Core
    from jarves.skills.music import FakePlayer
    from jarves.voice.audio import SimAudio
    mem = MemoryStore(home / "memory.db")
    mem.set_setting("override:curator.auto", "off")          # pre-WP-I switch: adopted
    mem.close()
    make(home).set("chimes", False)
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    try:
        assert cfg.voice.chime is False                      # mirrored onto the loaded config
        assert not c.features.enabled("memory_curator") and not c.curator.enabled("curator.auto")
        assert "features_list" in {t.name for t in c.registry.tools()}
        tools_md = (home / "workspace" / "TOOLS.md").read_text(encoding="utf-8")
        assert "## Features" in tools_md and "camera" in tools_md
        assert "music_play" in {t.name for t in c.registry.tools()}
        r = await c.registry.call("feature_set", {"id": "music", "on": False})
        assert r.ok and "music_play" not in {t.name for t in c.registry.tools()}
        assert "Music (music): turned off" in (home / "workspace" / "TOOLS.md").read_text(encoding="utf-8")
        assert (await c.registry.call("music_play", {"query": "x"})).code == "FEATURE_OFF"
        await c.registry.call("feature_set", {"id": "chimes", "on": True})
        assert cfg.voice.chime is True                       # live: the next chime plays
        await c.registry.call("feature_set", {"id": "wake_word", "on": False})
        assert cfg.voice.wake_enabled is False and not c._wake_should_listen()
    finally:
        await c.stop()
