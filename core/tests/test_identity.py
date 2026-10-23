"""Identity & voice (SW-V2 6.3, D12, D18; WP-M4): display name, spoken name and wake phrase stay separate; the voice
catalog is honest (no fake preview); changing the voice MODEL needs cost approval; bad stored voices recover."""
import asyncio
import json

import numpy as np
import pytest

from jarves import capabilities
from jarves.config import Config
from jarves.identity import REALTIME_VOICES, Identity, IdentityError, dashboard_routes, make_skill, voice_catalog
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.settings import LIVE_VOICES
from jarves.voice import wake_train as wt


@pytest.fixture
def mem(home):
    m = MemoryStore(home / "memory.db")
    yield m
    m.close()


def tiny_head(path, phrase="hey max"):
    rng = np.random.default_rng(0)
    x = rng.standard_normal((40, wt.WINDOWS, wt.EMB)).astype(np.float32)
    y = np.array([0, 1] * 20)
    x[y == 1] += 1.0
    wt.save_head(path, wt.train_head(x, y, epochs=3), {"phrase": phrase, "threshold": 0.6})


def test_names_are_separate_validated_and_applied(home, mem):
    cfg = Config()
    ident = Identity(home, cfg, mem)
    assert ident.names() == {"display_name": "JARVES", "spoken_name": "ג'ארוויס"}
    ident.set_names("Max", "מקס", by="dashboard")
    assert (cfg.assistant_name, cfg.assistant_name_spoken) == ("Max", "מקס")
    c2 = Config()
    Identity(home, c2).apply_to_config()                       # next Core start
    assert c2.assistant_name == "Max" and c2.voice.wake_word == "hey_jarvis" and c2.voice.wake_phrase == "Hey Jarvis"
    for bad in ("", "x" * 41, "<script>", "sk-abc;rm", "a\x00b"):
        with pytest.raises(IdentityError):
            ident.set_names(display_name=bad)
    d = json.loads((home / "identity.json").read_text(encoding="utf-8"))
    assert d["changed"]["display_name"]["by"] == "dashboard"


async def test_rename_by_voice_reads_back_and_says_the_wake_phrase_stays(home, mem):
    ident = Identity(home, Config(), mem)
    reg = SkillRegistry()
    reg.register(make_skill(ident))
    prompts = []

    async def no(p):
        prompts.append(p)
        return False
    reg.confirm = no
    r = await reg.call("assistant_rename", {"display_name": "Max", "spoken_name": "מקס"})
    assert r.code == "TOOL_DECLINED" and ident.names()["display_name"] == "JARVES"
    assert "מקס" in prompts[0] and "Hey Jarvis" in prompts[0]

    async def yes(p):
        return True
    reg.confirm = yes
    r = await reg.call("assistant_rename", {"display_name": "Max", "spoken_name": "מקס"})
    assert r.ok and "מקס" in r.message and r.data["wake_phrase"] == "Hey Jarvis"


def test_voice_catalog_is_complete_and_honest():
    cat = voice_catalog(Config())
    live = [v for v in cat if v["mode"] == "live"]
    rt = [v for v in cat if v["mode"] == "realtime"]
    assert len(live) == 22 == len(LIVE_VOICES) and len(rt) == len(REALTIME_VOICES) == 10
    assert all(v["preview"] is None and "No preview" in v["preview_note"] for v in cat)
    assert live[0]["usd_per_minute"] == 0.05 and rt[0]["cost_kind"] == "estimate"
    assert {"marin", "cedar"} <= {v["voice"] for v in rt}


def test_voice_choice_cost_rules_and_recovery(home, mem):
    cfg = Config()
    ident = Identity(home, cfg, mem)
    r = ident.choose_voice("live:meridian")                    # same model: no approval
    assert r["ok"] and mem.get_setting("override:voice.voice") == "meridian"
    r = ident.choose_voice("realtime:marin")                    # another model: price changes
    assert not r["ok"] and r["needs_approval"] and "$" in r["reason"] and ident.voice()["mode"] == "live"
    r = ident.choose_voice("realtime:marin", approve_cost=True)
    assert r["ok"] and ident.voice() ["mode"] == "realtime" and ident.voice()["realtime_voice"] == "marin"
    c2 = Config()
    Identity(home, c2).apply_to_config()
    assert c2.voice.mode == "realtime" and c2.voice.realtime_voice == "marin"
    with pytest.raises(IdentityError):
        ident.choose_voice("live:not_a_voice")
    mem.set_setting("override:voice.voice", "retired_voice")    # e.g. a voice OpenAI removed
    v = ident.voice()
    assert v["live_voice"] == "vesper" and v["notes"]


def test_wake_phrase_choice(home, mem, tmp_path):
    ident = Identity(home, Config(), mem)
    model = home / "wake" / "hey_max.npz"
    tiny_head(model)
    w = ident.set_wake(str(model))
    assert w["phrase"] == "hey max" and w["threshold"] == 0.6
    c = Config()
    Identity(home, c).apply_to_config()
    assert c.voice.wake_word.endswith("hey_max.npz") and c.voice.wake_threshold == 0.6 and c.voice.wake_phrase == "hey max"
    outside = tmp_path / "elsewhere.npz"
    tiny_head(outside)
    with pytest.raises(IdentityError):
        ident.set_wake(str(outside), only_in=home / "wake")    # the dashboard cannot point anywhere
    (home / "x.tflite").write_text("x")
    with pytest.raises(IdentityError):
        ident.set_wake(str(home / "missing.npz"))
    model.unlink()                                             # a deleted model falls back to Hey Jarvis
    assert ident.wake()["model"] == "hey_jarvis" and ident.wake()["missing"] == "hey_max.npz"


def test_dashboard_routes(home, mem):
    routes = {}
    dashboard_routes(lambda m, p, h: routes.__setitem__((m, p), h))

    class Ctx:
        pass
    ctx = Ctx()
    ctx.home, ctx.cfg, ctx.memory = home, Config(), mem
    st, v = routes[("GET", r"/api/identity")](ctx, None, None)
    assert st == 200 and v["wake"]["builtin"] and v["wake"]["capability"]["spoken"] == capabilities.WAKE_PHRASE["spoken"]
    assert v["wake"]["capability"]["targets"]["windows"]["status"] == capabilities.WAKE_PHRASE["targets"]["windows"]["status"]
    assert v["wake"]["capability"]["custom"]["linux_arm64_pi"]["roadmap_issue"] == capabilities.PI_ROADMAP
    assert routes[("POST", r"/api/identity/names")](ctx, None, {"display_name": ""})[0] == 400
    st, r = routes[("POST", r"/api/identity/voice")](ctx, None, {"voice": "realtime:cedar"})
    assert st == 409 and r["needs_approval"] and r["error"]
    assert routes[("POST", r"/api/identity/voice")](ctx, None, {"voice": "realtime:cedar", "approve_cost": True})[0] == 200
    assert routes[("POST", r"/api/identity/wake")](ctx, None, {"model": "C:/Windows/win.ini"})[0] == 400
    assert not any(m == "DELETE" for m, _ in routes)


def test_config_wake_word_replaced_by_identity_is_warned_not_silent(home, mem, caplog):
    """V4-VC-8: a custom voice.wake_word in config.toml is overridden by identity.json; that is now said once."""
    import logging
    cfg = Config()
    cfg.voice.wake_word = str(home / "my_head.npz")
    with caplog.at_level(logging.WARNING):
        Identity(home, cfg, mem).apply_to_config()
    assert cfg.voice.wake_word == "hey_jarvis"
    assert any("wake model chosen" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        Identity(home, Config(), mem).apply_to_config()          # the default config: nothing to say
    assert not any("wake model chosen" in r.getMessage() for r in caplog.records)


def test_wake_test_apply_writes_where_the_threshold_is_read(home, mem):
    from jarves.cli import apply_wake_threshold
    model = home / "wake" / "hey_max.npz"
    tiny_head(model)
    Identity(home, Config(), mem).set_wake(str(model))
    cfgf = home / "config.toml"
    cfgf.write_text("[voice]\nwake_threshold = 0.95\n", encoding="utf-8")
    msg = apply_wake_threshold(home, Config(), cfgf, 0.5)
    assert "identity.json" in msg
    c = Config()
    Identity(home, c).apply_to_config()
    assert c.voice.wake_threshold == 0.5                       # the applied value really is the live one
    home2 = home / "plain"
    (home2 / "wake").mkdir(parents=True)
    cf2 = home2 / "config.toml"
    cf2.write_text("[voice]\n", encoding="utf-8")
    assert "config.toml" in apply_wake_threshold(home2, Config(), cf2, 0.4) and "0.4" in cf2.read_text()
