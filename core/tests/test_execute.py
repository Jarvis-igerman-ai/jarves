import asyncio

import httpx
import pytest

from jarves.config import Config
from jarves.features import Features
from jarves.google_auth import ALL_SCOPES, CALENDAR_WRITE
from jarves.memory import MemoryStore
from jarves.text_session import execute
from test_calendar_write import FakeCalendar, seed


def test_execute_offline_memory_without_core_or_audio(tmp_path, monkeypatch):
    from jarves.app import Core
    from jarves.deviceport import DevicePort
    def forbidden(*a, **kw):
        raise AssertionError("Text request must not start Core or devices")
    monkeypatch.setattr(Core, "start", forbidden)
    monkeypatch.setattr(DevicePort, "__init__", forbidden)
    cfg = Config()
    cfg.ai.provider = "offline"
    output = []
    assert asyncio.run(execute(cfg, tmp_path, "remember my wife's email is spouse@example.com", write=output.append)) == 0
    mem = MemoryStore(tmp_path / "memory.db")
    assert "spouse@example.com" in mem.by_kind("contact")[0]["text"]
    mem.close()
    assert not (tmp_path / "core.lock").exists()
    assert not (tmp_path / "health.json").exists()


@pytest.mark.parametrize("enabled,answer,writes", [(True, "yes", 1), (True, "no", 0), (False, "yes", 0)])
def test_execute_calendar_uses_real_registry_gates_and_terminal_confirmation(tmp_path, enabled, answer, writes):
    seed(tmp_path, (*ALL_SCOPES, CALENDAR_WRITE))
    cfg = Config()
    Features(cfg, tmp_path).set("calendar_write", enabled)
    fake = FakeCalendar()
    prompts, output, results = [], [], []
    class Agent:
        def __init__(self, reg):
            self.reg = reg
        def instructions(self):
            return "base"
        async def reply(self, text):
            assert "add" in text
            result = await self.reg.call("calendar_create_event", {"title": "Dentist", "day": "tomorrow", "start": "10:00"})
            results.append(result)
            return result.message
    def factory(cfg, reg, memory, home, budget):
        assert budget is not None
        return Agent(reg)
    assert asyncio.run(execute(cfg, tmp_path, "add a calendar event tomorrow at 10", agent_factory=factory,
                              transport=httpx.MockTransport(fake.handler),
                              read=lambda prompt: prompts.append(prompt) or answer, write=output.append)) == 0
    assert len(fake.writes) == writes
    assert bool(prompts) is enabled
    if enabled:
        assert "Dentist" in prompts[0] and "10:00" in prompts[0]
    else:
        assert results[0].code == "FEATURE_OFF"


def test_interactive_followup_same_agent_and_raw_text_links(tmp_path):
    output, seen = [], []
    answers = iter(["Dentist at 10", ""])
    class Agent:
        def instructions(self):
            return "base"
        async def reply(self, text):
            seen.append(text)
            return "What title/time?" if len(seen) == 1 else "Event: https://www.google.com/calendar/event?eid=fixture"
    agent = Agent()
    def factory(*a, **kw):
        return agent
    assert asyncio.run(execute(Config(), tmp_path, "add an event tomorrow", interactive=True,
                              agent_factory=factory, read=lambda _: next(answers), write=output.append)) == 0
    assert seen == ["add an event tomorrow", "Dentist at 10"]
    assert "https://www.google.com/calendar/event?eid=fixture" in output[-1]
    assert "standalone terminal text session" in agent.instructions()


def test_execute_keeps_core_generated_references_and_excludes_runtime_tools(tmp_path):
    from jarves.workspace import Workspace
    workspace = Workspace(tmp_path / "workspace")
    workspace.write_generated("TOOLS.md", "Core camera tools marker")
    class Agent:
        def __init__(self, reg):
            self.reg = reg
        def instructions(self):
            return "base"
        async def reply(self, text):
            names = {t.name for t in self.reg.tools()}
            assert "contact_lookup" in names and "workspace_read" in names and "features_list" in names
            assert not names & {"camera_describe", "camera_enroll", "task_start", "feature_set"}
            result = await self.reg.call("workspace_read", {"file": "TOOLS"})
            assert "contact_lookup" in result.message and "Core camera tools marker" not in result.message
            return "ok"
    asyncio.run(execute(Config(), tmp_path, "tools", agent_factory=lambda cfg, reg, *a, **kw: Agent(reg), write=lambda _: None))
    assert workspace.read("TOOLS.md") == "Core camera tools marker"


def test_execute_cli_help_and_standalone_dispatch(tmp_path, monkeypatch, capsys):
    from jarves import cli
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as result:
        cli.main(["execute", "--help"])
    assert result.value.code == 0 and "--interactive" in capsys.readouterr().out
    assert cli.main(["execute", "remember my wife's email is spouse@example.com"]) == 0
    assert not (tmp_path / "core.lock").exists()
