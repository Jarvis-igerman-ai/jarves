"""Self-settings Skill: the assistant can read and edit its workspace files, list its skills and settings, and
change its voice ("תזכור ש..." -> remember / USER.md, "תשנה את האופי שלך" -> SOUL.md, "תחליף קול" -> set_voice).

Safety (enforced here and by the registry, never by prompt text):
- only the workspace files whose spec allows it (workspace.SPECS); AGENTS.md, TOOLS.md and HEARTBEAT.md are
  user-only; secrets are rejected by Workspace.validate; every write keeps a backup (workspace_undo).
- editing SOUL.md / IDENTITY.md, replacing a whole file and undo are "destructive": the user approves first.
- settings: ONLY the keys in ALLOWED (stored as overrides in memory.db `settings`); config.toml, secrets.env and
  API keys are never reachable from here.
"""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field

from ..memory import MemoryStore
from ..workspace import SPECS, Workspace, WorkspaceError
from . import Skill, Tool, ToolResult

# GPT-Live voices, official list checked 2026-10-19 (developers.openai.com/api/docs/guides/live-conversations).
# The voice is fixed per session: "Start a new session to change it". Hebrew is not a listed voice language.
LIVE_VOICES = {
    "vesper": "British English, masculine (current default)", "marin": "API default voice",
    "meridian": "North American, masculine", "stone": "Irish, masculine", "ripple": "Australian, masculine",
    "cinder": "Southern US, masculine", "beacon": "Filipino English, masculine", "gleam": "North American, feminine",
    "willow": "Irish, feminine", "quartz": "Australian, feminine", "delta": "Southern US, feminine",
    "tempo": "Brazilian Portuguese, masculine", "bossa": "Brazilian Portuguese, feminine",
    "flitz": "German, masculine", "nuri": "German, feminine", "juni": "German, feminine",
    "sillage": "French, masculine", "brise": "French, feminine", "nira": "Hindi, feminine",
    "shida": "Japanese, masculine", "harema": "Japanese, feminine", "noeul": "Korean, feminine",
}
ON_OFF = ("on", "off")
# key -> (description, allowed values or None, default when unset; None = use config.toml)
ALLOWED: dict[str, tuple[str, tuple[str, ...] | None, str | None]] = {
    "voice.voice": ("GPT-Live voice", tuple(LIVE_VOICES), None),
    "curator.auto": ("learn durable facts from conversations (local rules, no network)", ON_OFF, "on"),
    "ui.cli_language": ("language of what the command-line wizards print: en | he | both (env JARVES_CLI_LANG wins)",
                        ("en", "he", "both"), "en"),
    "curator.llm": ("also ask the backend model to extract facts after a conversation (costs one request)",
                    ON_OFF, "off"),
}
EDITABLE = tuple(s.name.removesuffix(".md") for s in SPECS.values() if s.agent_edit)   # SOUL, IDENTITY, USER, MEMORY
FileName = Literal["AGENTS", "SOUL", "IDENTITY", "USER", "MEMORY", "TOOLS", "SETUP", "FEATURES", "HEARTBEAT"]
EditName = Literal["SOUL", "IDENTITY", "USER", "MEMORY"]
NoteName = Literal["USER", "MEMORY"]


class Settings:
    """Allowlisted runtime overrides on top of config.toml (persisted in memory.db)."""

    def __init__(self, memory: MemoryStore, config_defaults: dict[str, str] | None = None) -> None:
        self.memory = memory
        self.config_defaults = config_defaults or {}
        self.features = None          # features.Features (set by the Core): curator.* are feature switches

    def _feature(self, key: str) -> str | None:
        from ..features import SETTING_FEATURES
        return SETTING_FEATURES.get(key) if self.features is not None else None

    def get(self, key: str) -> str:
        if key not in ALLOWED:
            raise KeyError(key)
        if fid := self._feature(key):
            return "on" if self.features.enabled(fid) else "off"
        _, _, default = ALLOWED[key]
        return self.memory.get_setting(f"override:{key}") or default or self.config_defaults.get(key, "")

    def set(self, key: str, value: str) -> str:
        if key not in ALLOWED:
            raise WorkspaceError(f"{key} cannot be changed from a conversation; allowed: {', '.join(ALLOWED)}")
        _, choices, _ = ALLOWED[key]
        value = str(value).strip().lower()
        if choices is not None and value not in choices:
            raise WorkspaceError(f"{key} must be one of: {', '.join(choices)}")
        if fid := self._feature(key):
            if value == "on" and self.features.feature(fid).risk != "low":
                raise WorkspaceError(f"{key} is the feature switch {fid}: turning it on needs the user's approval "
                                     f"(use feature_set, or the dashboard Features page)")
            try:
                self.features.set(fid, value == "on", by="voice")
            except PermissionError as e:             # BetaLocked: the one beta-gate message
                raise WorkspaceError(str(e)) from e
            return value
        self.memory.set_setting(f"override:{key}", value)
        return value

    def reset(self, key: str) -> bool:
        if fid := self._feature(key):
            self.features.reset(fid)
        return self.memory.delete_setting(f"override:{key}")

    def view(self) -> dict:
        return {"settings": {k: {"value": self.get(k), "about": d, "choices": list(c) if c else None}
                             for k, (d, c, _) in ALLOWED.items()},
                "voices": [f"{n} ({d})" for n, d in LIVE_VOICES.items()]}


class FileArgs(BaseModel):
    file: FileName = Field(...)


class ReadFileArgs(FileArgs):
    offset: int = Field(0, ge=0)


class NoteArgs(BaseModel):
    file: NoteName = Field("USER", description='USER = about the user, MEMORY = other notes')
    text: str = Field(..., min_length=1, max_length=400)
    section: str | None = Field(None, max_length=60)


class EditArgs(BaseModel):
    file: EditName = Field(...)
    text: str = Field(..., min_length=1, max_length=3000)
    mode: Literal["append", "replace"] = Field("append")


class VoiceArgs(BaseModel):
    voice: str = Field(..., description='voice name from list_settings')
    restart_now: bool = Field(True, description='restart the conversation so the new voice is heard')


class SettingArgs(BaseModel):
    key: str = Field(..., description='key from list_settings')
    value: str = Field(..., max_length=40)


class SettingsSkill(Skill):
    name = "settings"
    description = "My own settings, voice, personality and workspace files"

    def __init__(self, ws: Workspace, settings: Settings, registry=None,
                 on_voice_change: Callable[[str, bool], None] | None = None) -> None:
        self.ws, self.settings, self.registry = ws, settings, registry
        self.on_voice_change = on_voice_change      # set by the Core: (voice, restart_now)

    def tools(self) -> list[Tool]:
        ws, st = self.ws, self.settings

        async def read_file(a: ReadFileArgs):
            name = ws.path(a.file).name
            text = ws.read(name)
            end = min(len(text), a.offset + 4000)
            return ToolResult(True, text[a.offset:end] or "(empty)",
                              {"file": name, "chars": len(text), "offset": a.offset,
                               "next_offset": end if end < len(text) else None,
                               "agent_can_edit": SPECS[name].agent_edit})

        async def note(a: NoteArgs):
            try:
                r = ws.append(a.file, a.text, by="agent", section=a.section)
            except WorkspaceError as e:
                return ToolResult(False, str(e), code="WORKSPACE_REFUSED")
            return ToolResult(True, "רשמתי." if r["changed"] else "זה כבר רשום.", {"file": r["name"]})

        def proposed(a: EditArgs) -> str:
            name = ws.path(a.file).name
            old = ws.read(name)
            return a.text if a.mode == "replace" else f"{old.rstrip()}\n{a.text.strip()}\n"

        async def edit(a: EditArgs):
            try:
                r = ws.write(a.file, proposed(a), by="agent", note=a.mode)
            except WorkspaceError as e:
                return ToolResult(False, str(e), code="WORKSPACE_REFUSED")
            msg = "עדכנתי." if r["changed"] else "לא היה מה לשנות."
            if r["changed"] and "front" in SPECS[r["name"]].inject:
                msg += " זה נכנס לתוקף מעכשיו."
            return ToolResult(True, msg, {"file": r["name"], "version": r["version"]})

        async def edit_impact(a: EditArgs) -> tuple[int | None, str]:
            name = ws.path(a.file).name
            try:
                ws.validate(name, proposed(a), "agent")
            except WorkspaceError:
                return 1, f"לשנות את {name}? / Change {name}?"       # the handler then refuses with the reason
            added = [l[1:] for l in ws.preview(name, proposed(a)).splitlines()
                     if l.startswith("+") and not l.startswith("+++")]
            gist = " ".join(" ".join(added).split())[:120]
            verb = "להחליף את כל" if a.mode == "replace" else "להוסיף ל"
            return 1, f"{verb} {name}: \"{gist}\"? / Update {name}?"

        async def undo(a: FileArgs):
            try:
                r = ws.undo(a.file, by="agent")
            except WorkspaceError as e:
                return ToolResult(False, str(e), code="WORKSPACE_REFUSED")
            return ToolResult(True, "החזרתי את הגרסה הקודמת.", {"file": r["name"]})

        async def undo_impact(a: FileArgs) -> tuple[int | None, str]:
            name = ws.path(a.file).name
            return 1, f"להחזיר את {name} לגרסה הקודמת? / Undo the last change to {name}?"

        async def list_skills(_):
            if self.registry is None:
                return ToolResult(False, "skills list unavailable", code="NOT_READY")
            items = {}
            for t in self.registry.tools():
                items.setdefault(self.registry._tools[t.name][0].name, []).append(t.name)
            text = "; ".join(f"{s} ({self.registry.skills[s].description}): {', '.join(ts)}" for s, ts in items.items())
            return ToolResult(True, text, {"skills": items})

        async def list_settings(_):
            v = st.view()
            text = "; ".join(f"{k} = {x['value']}" for k, x in v["settings"].items())
            return ToolResult(True, text, v)

        async def set_voice(a: VoiceArgs):
            voice = a.voice.strip().lower()
            if voice not in LIVE_VOICES:
                return ToolResult(False, f"אין קול בשם {a.voice}. אפשר: {', '.join(list(LIVE_VOICES)[:8])}...",
                                  {"voices": list(LIVE_VOICES)}, "BAD_VOICE")
            if st.get("voice.voice") == voice:
                return ToolResult(True, f"זה כבר הקול שלי ({voice}).", {"voice": voice})
            st.set("voice.voice", voice)
            if self.on_voice_change is not None:
                self.on_voice_change(voice, a.restart_now)
            when = "אני מתחיל את השיחה מחדש עם הקול החדש." if a.restart_now else "הקול החדש יתחיל בשיחה הבאה."
            return ToolResult(True, f"החלפתי לקול {voice}. {when}", {"voice": voice, "restart": a.restart_now})

        async def set_setting(a: SettingArgs):
            if a.key == "voice.voice":
                return await set_voice(VoiceArgs(voice=a.value))
            try:
                value = st.set(a.key, a.value)
            except WorkspaceError as e:
                return ToolResult(False, str(e), code="SETTING_REFUSED")
            return ToolResult(True, f"עדכנתי: {a.key} = {value}.", {"key": a.key, "value": value})

        return [
            Tool("workspace_read", "Read a workspace file (SOUL, IDENTITY, USER, MEMORY, AGENTS, TOOLS, SETUP, FEATURES): 'read SETUP', 'תקרא את מדריך ההתקנה'; long ones: follow next_offset until null.",
                 read_file, ReadFileArgs, permission="read", cache_s=30.0),
            Tool("workspace_note", "Add ONE short line to USER.md or MEMORY.md: 'note that I like tea', 'תרשום שאני אוהב תה'. Never passwords or codes.", note, NoteArgs,
                 permission="action"),
            Tool("workspace_edit", "Change SOUL/IDENTITY/USER/MEMORY on request ('be funnier', 'תשנה את האופי שלך'). Asks the user to approve first.",
                 edit, EditArgs, permission="destructive", impact=edit_impact),
            Tool("workspace_undo", "Undo the last workspace file change (asks first): 'undo that', 'תבטל את השינוי'.", undo, FileArgs,
                 permission="destructive", impact=undo_impact),
            Tool("list_skills", "My skills and their tools: 'what skills do you have', 'אילו יכולות יש לך'.", list_skills,
                 permission="read"),
            Tool("list_settings", "Settings I may change, values and voices: 'what can I change', 'אילו הגדרות יש'.",
                 list_settings, permission="read"),
            Tool("set_voice", "Change my voice ('change voice', 'תחליף קול'). No voice named: offer two or three from list_settings first.", set_voice, VoiceArgs, permission="action"),
            Tool("set_setting", "Change one allowlisted setting from list_settings: 'change a setting', 'תשנה הגדרה'.", set_setting, SettingArgs,
                 permission="action"),
        ]
