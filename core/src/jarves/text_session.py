"""Short-lived text requests using existing Skills, without starting the Core or any device service."""
from __future__ import annotations

import asyncio
from pathlib import Path

from . import features as feat, workspace as wsp
from .brain import Brain
from .budget import Budget
from .memory import MemoryStore
from .skills import Skill, SkillRegistry
from .skills.builtin import ClockSkill, MemorySkill, StatusSkill
from .ui_blocks import UiBlocksSkill
from .skills.local_contacts import LocalContactsSkill
from .skills.settings import Settings, SettingsSkill


CONFIRM_WORDS = ("yes", "y", "כן")       # an explicit yes only; Enter / anything else cancels


class _FeatureRead(Skill):
    name = "features"
    description = "Read configured feature availability; activation uses jarvis features"

    def __init__(self, features):
        self.features = features

    def tools(self):
        return [t for t in feat.make_skill(self.features).tools() if t.name == "features_list"]


class _TextWorkspace(wsp.Workspace):
    """Text-mode generated references never replace a running Core's capability files."""
    def read(self, name):
        key = self.path(name).name
        if key in getattr(self, "references", {}):
            return self.references[key]
        return super().read(name)


NOTE_TERMINAL = ("\nThis is a standalone terminal text session. No Core, microphone, camera capture, face, scheduler "
                 "or background task service was started. Only the supplied tools are executable. Reply in text "
                 "with useful detail and real returned links if requested. Terminal confirmation handles actions. "
                 "If details are missing, ask; never invent a title, time, recipient or permission. Feature activation "
                 "uses jarvis features on ID; runtime-only operations require starting JARVES explicitly.")


def build(cfg, home: Path, confirm, *, agent_factory=None, transport=None, note: str = NOTE_TERMINAL):
    """The text-only pieces (memory, budget, Skills, Brain) without any runtime start. `confirm` is the registry's
    approval callable. Returns a namespace; the caller closes it with `await close_session(s)`."""
    from types import SimpleNamespace
    from .app import make_agent
    from .identity import Identity
    from .skills.google import register_google
    from .skills.music import MusicSkill, PearDesktopPlayer

    memory = MemoryStore(home / "memory.db")
    budget = Budget(memory, cfg.ai)
    registry = SkillRegistry()
    features = feat.Features(cfg, home, skills=lambda: set(registry.skills))
    features.import_legacy(feat.legacy_settings(memory))
    features.apply_to_config(cfg)
    Identity(home, cfg, memory).apply_to_config(cfg)
    registry.gate = features.gate
    registry.confirm = confirm
    from . import drive_mode                     # V6 #216: off by default; never approves, only defers
    drive_mode.attach(registry, features, home, memory)
    registry.register(ClockSkill())
    registry.register(UiBlocksSkill())
    registry.register(MemorySkill(memory))
    registry.register(LocalContactsSkill(memory))
    registry.allowed_actions.add("contact_remember")
    registry.register(_FeatureRead(features))
    registry.register(StatusSkill(lambda: {"core": "not started by this standalone text session", "components": {
        "runtime": {"status": "disabled", "detail": "Text session only: no voice, camera capture, face or background services"}}}))
    settings = Settings(memory, {"voice.voice": cfg.voice.voice})
    settings.features = features
    workspace = _TextWorkspace(home / "workspace")
    registry.register(SettingsSkill(workspace, settings, registry))
    if cfg.music.provider != "none" or features.enabled("music"):
        registry.register(MusicSkill(PearDesktopPlayer(cfg.music.pear_url, home / "pear_client_id", cfg.music.timeout_s),
                                     cfg.music.max_volume))
    register_google(registry, home, transport=transport)
    # Generate the actual text-mode tool list. Personal workspace files are preserved.
    workspace.references = {"TOOLS.md": wsp.tools_markdown(registry, settings.view()),
                            "FEATURES.md": "# Features\n" + features.capabilities_markdown() + "\n" +
                            "\n".join(f"- {f.id}: {f.about or f.title_en}" for f in list(feat.CATALOG.values()))}
    agent = (agent_factory or make_agent)(cfg, registry, memory, home, budget=budget)
    if agent is not None:
        wsp.augment_instructions(agent, workspace)
        previous = agent.instructions
        agent.instructions = lambda: previous() + note
    return SimpleNamespace(brain=Brain(registry, agent), agent=agent, registry=registry, memory=memory, budget=budget,
                           features=features, workspace=workspace, settings=settings)


async def close_session(s) -> None:
    await s.registry.drain()
    s.budget.close()
    s.memory.close()


async def execute(cfg, home: Path, text: str, *, interactive=False, read=input, write=print,
                  agent_factory=None, transport=None) -> int:
    """No runtime launch, device capture, scheduler or implicit feature activation. Input is always user text."""
    async def confirm(prompt):
        try:
            # Terminal-only session: synchronous input leaves no abandoned stdin thread on cancellation.
            answer = read(prompt + "\nType yes (כן) to confirm, anything else cancels: ")
            ok = answer.strip().lower() in CONFIRM_WORDS
            if not ok:
                write("Not confirmed: nothing was done.")
            return ok
        except EOFError:
            return False
    from .approvals import audited
    sess = build(cfg, home, audited(home, confirm), agent_factory=agent_factory, transport=transport)
    brain = sess.brain
    try:
        while True:
            reply = await brain.respond(text, spoken=False)
            write(reply.text)
            if not reply.ok:
                write(f"[{reply.code}]")
            if not interactive:
                return 0 if reply.ok else 1
            try:
                text = read("You (Enter to finish): ").strip()
            except EOFError:
                return 0 if reply.ok else 1
            if not text:
                return 0 if reply.ok else 1
    finally:
        await close_session(sess)
