"""R-027 / JAR-196: renaming him through the identity layer (the dashboard route) changes the next chat, and renaming
back undoes it. Hermetic: fake audio/STT/TTS, no network, no key."""
from conftest import FakeSTT, FakeTTS, free_port

from jarves.app import Core
from jarves.config import Config
from jarves.identity import dashboard_routes
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio

OLD_SPOKEN = Config().assistant_name_spoken


class _Ctx:                                   # what the in-process dashboard hands its route handlers
    def __init__(self, core):
        self.home, self.cfg, self.memory = core.home, core.cfg, core.memory


def _post_names(core, display, spoken):
    routes = {}
    dashboard_routes(lambda method, path, fn: routes.__setitem__((method, path), fn))
    return routes[("POST", r"/api/identity/names")](_Ctx(core), "POST",
                                                   {"display_name": display, "spoken_name": spoken})


async def _started(home):
    cfg = Config()                            # fresh config.toml values: only identity.json carries the name
    cfg.link.port, cfg.voice.greeting = free_port(), False
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    return c


def _assert_named(c, written, spoken, old_written, old_spoken):
    prompt = c.agent.instructions()           # the system prompt the next chat turn is built from
    assert f"You are {written}," in prompt and f"You are {old_written}," not in prompt
    assert spoken in c.hello_text() and old_spoken not in c.hello_text()


async def test_rename_reaches_the_next_chat_after_restart_and_undo_restores_it(home):
    c = await _started(home)
    try:
        _assert_named(c, "JARVES", OLD_SPOKEN, "Max", "מקס")
        assert _post_names(c, "Max", "מקס") == (200, {"display_name": "Max", "spoken_name": "מקס"})
        assert "מקס" in c.hello_text() and OLD_SPOKEN not in c.hello_text()   # greeting changes at once
    finally:
        await c.stop()
    c = await _started(home)
    try:
        _assert_named(c, "Max", "מקס", "JARVES", OLD_SPOKEN)
        assert _post_names(c, "JARVES", OLD_SPOKEN)[0] == 200                  # undo
    finally:
        await c.stop()
    c = await _started(home)
    try:
        _assert_named(c, "JARVES", OLD_SPOKEN, "Max", "מקס")
    finally:
        await c.stop()

