import os
import asyncio
import json
import logging

import httpx
import pytest

from jarves import logs
from jarves.brain import AgentBrain, Brain, match_intent
from jarves.config import AIConfig, ConfigError, load_config
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry, Skill, Tool, ToolResult
from jarves.skills.builtin import ClockSkill, MemorySkill
from jarves.skills.music import FakePlayer, MusicSkill, PearDesktopPlayer, Track, parse_search


# ---------------------------------------------------------------- config
def test_config_defaults_and_errors(tmp_path):
    cfg, _ = load_config(tmp_path / "missing.toml")
    assert cfg.voice.mode == "live" and cfg.ai.text_model.startswith("gpt-6")
    p = tmp_path / "c.toml"
    p.write_text('[ai]\ntext_modle = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="text_modle"):
        load_config(p)
    p.write_text('[ai]\napi_key = "sk-abc"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="secrets are not allowed"):
        load_config(p)
    p.write_text('[link]\nhost = "0.0.0.0"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="loopback"):
        load_config(p)
    p.write_text('[ai\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML"):
        load_config(p)


# ---------------------------------------------------------------- logs
def test_redaction_and_private_fields(tmp_path):
    path = logs.setup(tmp_path, console=False)
    logs.info("ai", "T", "key sk-abcdefghijklmnop and Bearer abc.def and token=secret123", text="private words")
    line = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "sk-abcdef" not in line["msg"] and "abc.def" not in line["msg"] and "secret123" not in line["msg"]
    assert line["text"] == "<13 chars>" and line["component"] == "ai" and line["ts"].endswith("+00:00")


def test_log_rotation_is_bounded(tmp_path):
    logs.setup(tmp_path, max_bytes=10_000, backups=2, console=False)
    for i in range(2000):
        logs.info("core", "SPAM", "x" * 50)
    files = list(tmp_path.glob("jarves.log*"))
    assert len(files) <= 3 and sum(f.stat().st_size for f in files) < 40_000


def test_debug_mode_expires(tmp_path):
    logs.setup(tmp_path, console=False)
    logs.enable_debug(5)
    assert logging.getLogger("jarves").level == logging.DEBUG
    logs._debug_until = 1.0                    # pretend the window has passed
    logs.info("core", "X", "after expiry")
    assert logging.getLogger("jarves").level == logging.INFO


# ---------------------------------------------------------------- memory
def test_memory_persists_and_clears(tmp_path):
    m = MemoryStore(tmp_path / "m.db")
    a = m.add("אני אוהב קפה שחור")
    m.add("my dog is Rex")
    m.close()
    m = MemoryStore(tmp_path / "m.db")
    assert m.count() == 2 and m.search("קפה")[0]["id"] == a
    assert m.delete(a) and not m.delete(a)
    assert m.clear() == 1 and m.count() == 0
    with pytest.raises(ValueError):
        m.add("x" * 501)
    assert m.bump_usage("ai_requests", 2) == 2 and m.usage_today("ai_requests") == 2


# ---------------------------------------------------------------- skills
class EchoSkill(Skill):
    """A second, independent Skill: proves registration needs no Core change."""
    name = "echo"

    def tools(self):
        from pydantic import BaseModel

        class A(BaseModel):
            word: str

        async def echo(a):
            return ToolResult(True, a.word)

        async def boom(_):
            raise RuntimeError("bug")

        async def slow(_):
            await asyncio.sleep(5)
            return ToolResult(True, "late")
        return [Tool("echo", "echo a word", echo, A, permission="read"), Tool("boom", "crashes", boom, permission="read"),
                Tool("slow", "too slow", slow, timeout_s=0.05, permission="read"),
                Tool("danger", "needs ok", echo, A, permission="destructive", confirm="sure?")]


async def test_registry_validation_errors_and_isolation():
    r = SkillRegistry()
    r.register(EchoSkill())
    with pytest.raises(ValueError):
        r.register(EchoSkill())
    assert (await r.call("echo", {"word": "hi"})).message == "hi"
    assert (await r.call("echo", {})).code == "TOOL_BAD_ARGS"
    assert (await r.call("echo", "not json")).code == "TOOL_BAD_ARGS"
    assert (await r.call("nope")).code == "TOOL_UNKNOWN"
    assert (await r.call("boom")).code == "TOOL_CRASH"
    assert (await r.call("slow")).code == "TOOL_TIMEOUT"
    assert (await r.call("danger", {"word": "x"})).code == "TOOL_DECLINED"     # default: deny

    async def yes(_):
        return True
    r.confirm = yes
    assert (await r.call("danger", {"word": "x"})).ok
    r.disabled.add("echo")
    assert (await r.call("echo", {"word": "hi"})).code == "SKILL_DISABLED"
    tools = SkillRegistry()
    tools.register(EchoSkill())
    ft = tools.agent_tools()
    assert {t.name for t in ft} == {"echo", "boom", "slow", "danger"}
    out = json.loads(await ft[0].on_invoke_tool(None, '{"word": "yo"}'))
    assert out["ok"] and out["message"] == "yo"


# ---------------------------------------------------------------- intents
@pytest.mark.parametrize("text,tool,args", [
    ("נגן את שלמה ארצי", "music_play", {"query": "שלמה ארצי"}),
    ("תנגן לי שירים של עומר אדם", "music_play", {"query": "עומר אדם"}),
    ("ג'ארביס, נגן Bohemian Rhapsody", "music_play", {"query": "Bohemian Rhapsody"}),
    ("play songs by Queen", "music_play", {"query": "Queen"}),
    ("עצור", "music_pause", {}), ("תמשיך", "music_resume", {}), ("השיר הבא", "music_next", {}),
    ("תעלה את הווליום ל 40", "music_volume", {"percent": 40}), ("מה מתנגן עכשיו?", "music_status", {}),
    ("מה השעה?", "get_time", {}), ("מה שעה?", "get_time", {}), ("תזכור שאני אוהב קפה", "remember", {"text": "אני אוהב קפה"}),
    ("מה אתה זוכר עליי", "recall", {}), ("תשכח את הכל", "forget_all", {}), ("תשכח שאני אוהב קפה", "forget", {"match": "אני אוהב קפה"}),
    ("סטטוס", "get_status", {}),
])
def test_intents(text, tool, args):
    i = match_intent(text)
    assert i is not None and i.tool == tool and i.args == args


@pytest.mark.parametrize("text", ["שים לב שהדלת פתוחה", "מה מזג האוויר בחיפה", "ספר לי בדיחה"])
def test_non_commands_go_to_ai(text):
    assert match_intent(text) is None


async def test_brain_offline_and_budget(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mem = MemoryStore(tmp_path / "m.db")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    reg.register(MemorySkill(mem))
    agent = AgentBrain(AIConfig(), reg, mem)
    b = Brain(reg, agent)
    r = await b.respond("ספר לי בדיחה")
    assert r.source == "offline" and r.code == "AI_NO_KEY"
    assert (await b.respond("מה השעה")).source == "command"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mem.bump_usage("ai_requests", 200)
    assert agent.available()[1] == "AI_BUDGET"
    mem.add("אני אוהב קפה")
    assert "אני אוהב קפה" in agent.instructions()


# ---------------------------------------------------------------- music
async def test_music_skill_with_fake_player():
    p = FakePlayer([Track("a1", "Ahava", "Shlomo Artzi"), Track("b2", "Other", "Band")])
    s = MusicSkill(p, max_volume=60)
    tools = {t.name: t for t in s.tools()}
    from jarves.skills.music import PlayArgs, VolumeArgs
    r = await tools["music_play"].handler(PlayArgs(query="artzi"))
    assert r.ok and r.data["now_playing"]["video_id"] == "a1"
    assert (await tools["music_play"].handler(PlayArgs(query="zzz"))).code == "MUSIC_NOT_FOUND"
    r = await tools["music_volume"].handler(VolumeArgs(percent=90))
    assert p.volume == 60 and "60" in r.message
    await tools["music_pause"].handler(None)
    assert "מושהה" in (await tools["music_status"].handler(None)).message
    p.offline = True
    r = await tools["music_next"].handler(None)
    assert not r.ok and r.code == "MUSIC_PLAYER_OFFLINE"
    assert (await s.health())[0] == "unavailable"


async def test_music_play_not_confirmed_is_reported_truthfully():
    class Stuck(FakePlayer):
        async def play_track(self, track):
            self.np = None                      # player accepted the command but never started
    s = MusicSkill(Stuck(), verify_tries=2)
    from jarves.skills.music import PlayArgs
    r = await {t.name: t for t in s.tools()}["music_play"].handler(PlayArgs(query="song"))
    assert not r.ok and r.code == "MUSIC_NOT_CONFIRMED"


SEARCH = {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {
    "contents": [
        {"musicCardShelfRenderer": {"title": {"runs": [{"text": "Yoshvim Al Hagag",
                                                      "navigationEndpoint": {"watchEndpoint": {"videoId": "TOP1"}}}]},
                                    "subtitle": {"runs": [{"text": "Song • Shlomo Artzi"}]}}},
        {"musicShelfRenderer": {"contents": [{"musicResponsiveListItemRenderer": {
            "playlistItemData": {"videoId": "S2"},
            "flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "Ahava Zara"}]}}},
                            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "Shlomo Artzi"}]}}}]}},
            {"musicResponsiveListItemRenderer": {"flexColumns": []}}]}}]}}}}]}}}


def test_parse_search():
    t = parse_search(SEARCH)
    assert [x.video_id for x in t] == ["TOP1", "S2"] and t[1].subtitle == "Shlomo Artzi"


async def test_pear_adapter_protocol(tmp_path):
    seen = []
    state = {"secret": "1", "song": None}

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path))
        if req.url.path.startswith("/auth/"):
            return httpx.Response(200, json={"accessToken": "tok" + state["secret"]})
        if req.headers.get("authorization") != "Bearer tok" + state["secret"]:
            return httpx.Response(401)
        if req.url.path == "/api/v1/search":
            return httpx.Response(200, json=SEARCH)
        if req.url.path == "/api/v1/queue" and req.method == "POST":
            state["song"] = json.loads(req.content)["videoId"]
            return httpx.Response(204)
        if req.url.path == "/api/v1/song":
            if state["song"] is None:
                return httpx.Response(204)
            return httpx.Response(200, json={"title": "T", "artist": "A", "videoId": state["song"], "isPaused": False,
                                             "songDuration": 200, "elapsedSeconds": 1})
        return httpx.Response(204)
    p = PearDesktopPlayer("http://127.0.0.1:26538", tmp_path / "id", transport=httpx.MockTransport(handler))
    from jarves.skills.music import MusicError
    with pytest.raises(MusicError) as e:            # unpaired: no automatic pairing dialog
        await p.search("artzi")
    assert e.value.code == "MUSIC_NOT_PAIRED" and not any(x[1].startswith("/auth/") for x in seen)
    await p.pair()                                  # explicit user-initiated pairing
    tracks = await p.search("artzi")
    await p.play_track(tracks[0])
    assert ("POST", "/api/v1/queue") in seen and ("POST", "/api/v1/play") in seen
    state["secret"] = "2"                       # Pear restarted: old token rejected -> transparent re-pair
    np = await p.now_playing()
    assert np.video_id == "TOP1" and not np.paused
    assert (tmp_path / "id").read_text().startswith("jarves-")
    await p.close()


async def test_pear_offline(tmp_path):
    def down(req):
        raise httpx.ConnectError("refused")
    p = PearDesktopPlayer("http://127.0.0.1:1", tmp_path / "id", transport=httpx.MockTransport(down))
    from jarves.skills.music import MusicError
    with pytest.raises(MusicError) as e:
        await p.now_playing()
    assert e.value.code == "MUSIC_PLAYER_OFFLINE"


def test_secrets_file_loads_key_env_wins(tmp_path, monkeypatch):
    from jarves.config import load_secrets, save_secret, secrets_file
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert load_secrets(tmp_path) == []
    secrets_file(tmp_path).write_text('# c\nOPENAI_API_KEY="sk-file"\nOTHER=x\n', encoding="utf-8")
    assert load_secrets(tmp_path) == ["OPENAI_API_KEY"] and os.environ["OPENAI_API_KEY"] == "sk-file"
    assert "OTHER" not in os.environ
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert load_secrets(tmp_path) == [] and os.environ["OPENAI_API_KEY"] == "sk-env"   # real env wins
    save_secret("OPENAI_API_KEY", "sk-new", tmp_path)
    assert secrets_file(tmp_path).read_text(encoding="utf-8").count("OPENAI_API_KEY=") == 1


def _cli(home, monkeypatch, *argv, stdin=""):
    import io
    from jarves import cli
    monkeypatch.setenv("JARVES_HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
    return cli.main(list(argv))


def test_cli_setup_paths(tmp_path, monkeypatch, capsys):
    (tmp_path / "config.toml").write_text("[ai]\nconnection = 'nope'\n", encoding="utf-8")
    assert _cli(tmp_path, monkeypatch, "setup") == 2                       # invalid config -> clear error
    assert "CONFIG ERROR" in capsys.readouterr().err
    (tmp_path / "config.toml").write_text("api_key = 'sk-x'\n", encoding="utf-8")
    assert _cli(tmp_path, monkeypatch, "setup") == 2                       # secrets in config rejected
    (tmp_path / "config.toml").unlink()
    assert _cli(tmp_path, monkeypatch, "setup", stdin="9\n") == 1          # bad choice
    assert not (tmp_path / "setup_done").exists()
    assert _cli(tmp_path, monkeypatch, "setup", stdin="3\n") == 0          # api key mode, key skipped
    assert "jarvis key" in capsys.readouterr().out and (tmp_path / "setup_done").exists()
    (tmp_path / "config.toml").write_text("[ai]\nconnection = \"chatgpt\"\n", encoding="utf-8")
    assert _cli(tmp_path, monkeypatch, "setup", "--if-needed") == 0        # signed out later: hint, no forced login
    assert "jarvis chatgpt login" in capsys.readouterr().out
