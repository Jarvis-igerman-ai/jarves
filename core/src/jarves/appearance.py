"""Appearance (SW-V2 M2, spec 6.4 / D08): which presentation the Godot face app shows - the original face, a simple
2D avatar, a low-poly 3D avatar, or hidden (no renderer). One persisted choice, switched at runtime by:
- voice: the `set_appearance` tool ("תחליף לאווטאר תלת מימד"), or the offline command rule (INTENT_RULES),
- the dashboard (GET/POST /api/appearance; the dashboard is the user),
- a file edit of <state_dir>/appearance.json (atomic; the Core notices within ~1 s).

The Core only SENDS `appearance.set {mode}` to the face (at connect and on every change). A switch never touches the
Core, the voice session or the link; all renderers consume the same face events (face.set_state, speaker.level,
face.emotion, face.set_gaze, assistant.activity), see godot/face/appearance.gd and docs/APPEARANCE.md.

Interface:
    a = Appearance(home, send=link.send_face)      # send: async (type, payload) -> bool
    a.mode / a.set("avatar3d", by="voice") / a.status() / await a.push() / await a.watch()
    normalize("תלת מימד") -> "avatar3d"
    attach(core)                                   # app.py: one line after the link exists
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from . import features, logs

MODES = ("face", "avatar2d", "avatar3d", "hidden")
DEFAULT = "face"
STATE_FILE = "appearance.json"
PI_ISSUE = "https://github.com/igalgerman/jarves/issues/3"     # real roadmap issue: Pi gaps of this package
TITLES = {"face": ("פנים", "Face"), "avatar2d": ("אווטאר דו-מימדי", "2D avatar"),
          "avatar3d": ("אווטאר תלת-מימדי", "3D avatar"), "hidden": ("מוסתר", "Hidden")}
_PI_BLOCKER = "not run on a Raspberry Pi 5 yet: Godot ARM64 fps/RAM on the round display unmeasured"
# Truthful per-mode platform status (spec section 5). Windows "verified_on_device" only where the owner used it.
TARGETS = {
    "face": {"windows": {"status": "verified_on_device", "evidence": "used live by the owner (SW-V1/SW-V2 sessions)"},
             "linux_arm64_pi": {"status": "planned", "blocker": _PI_BLOCKER, "roadmap_issue": PI_ISSUE}},
    "avatar2d": {"windows": {"status": "implemented_unverified",
                             "evidence": "headless Godot --check + offscreen previews (godot/previews/appearance); "
                                         "no owner visual review yet"},
                 "linux_arm64_pi": {"status": "planned", "blocker": _PI_BLOCKER, "roadmap_issue": PI_ISSUE}},
    "avatar3d": {"windows": {"status": "implemented_unverified",
                             "evidence": "headless Godot --check + offscreen previews (godot/previews/appearance); "
                                         "no owner visual review yet"},
                 "linux_arm64_pi": {"status": "planned", "roadmap_issue": PI_ISSUE,
                                    "blocker": "3D on the Pi 5 GPU unmeasured; may need the low-quality path or the "
                                               "2D avatar as fallback"}},
    "hidden": {"windows": {"status": "implemented_unverified", "evidence": "headless Godot --check (no renderer drawn)"},
               "linux_arm64_pi": {"status": "planned", "roadmap_issue": PI_ISSUE,
                                  "blocker": "backlight/display off on the Pi not wired (display adapter)"}},
}

_AVATAR = r"אווטאר|אוואטר|אווטר|אבטאר|אוטאר"            # how speech-to-text spells "avatar"
# spoken / typed words -> mode (Hebrew first; checked in this order)
_WORDS = (
    ("hidden", r"מוסתר|נסתר|בלי\s+פנים|ללא\s+פנים|hidden|no\s+face"),
    ("avatar3d", r"תלת|3\s*d|3-?d|three"),
    ("avatar2d", r"דו|2\s*d|2-?d|two|פשוט|simple"),
    ("avatar2d", _AVATAR + r"|avatar"),
    ("face", r"פנים|פרצוף|face|original|מקורי|רגיל"),
)


def normalize(text) -> str | None:
    """A mode id from a mode id or free words ("אווטאר תלת מימד", "3D avatar", "הפנים הרגילות"); None if unclear."""
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if t in MODES:
        return t
    t = t[:80]
    for mode, rx in _WORDS:
        if re.search(rx, t):
            return mode
    return None


class Appearance:
    """The persisted choice for one data dir. Safe to use from the Core and the dashboard (separate processes)."""

    def __init__(self, home: Path | None, send: Callable[[str, dict], Awaitable[bool]] | None = None) -> None:
        self.path = home / STATE_FILE if home is not None else None
        self.send = send
        self._state = {"mode": DEFAULT, "changed": {}}
        self._mtime: float | None = None
        self._pushed: str | None = None          # last mode sent to the connected face
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            st = self.path.stat()
        except OSError:
            return
        if st.st_mtime == self._mtime:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            mode = raw.get("mode") if isinstance(raw, dict) else None
            if mode not in MODES:
                raise ValueError("unknown mode")
            self._state = {"mode": mode, "changed": dict(raw.get("changed") or {})}
        except (OSError, ValueError, AttributeError) as e:
            logs.warn("appearance", "APPEARANCE_FILE_BAD", "appearance file unreadable; keeping the current mode",
                      file=self.path.name, mode=self._state["mode"], error_type=type(e).__name__)
        self._mtime = st.st_mtime

    @property
    def mode(self) -> str:
        self._load()
        return self._state["mode"]

    def set(self, mode: str, *, by: str = "user") -> dict:
        m = normalize(mode)
        if m is None:
            raise ValueError(f"unknown appearance {mode!r}; one of {', '.join(MODES)}")
        self._state = {"mode": m, "changed": {"by": by, "ts": time.time()}}
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self._mtime = self.path.stat().st_mtime
        logs.info("appearance", "APPEARANCE_SET", "appearance changed", mode=m, by=by)
        return self.status()

    def status(self) -> dict:
        mode = self.mode
        ch = self._state.get("changed") or {}
        return {"mode": mode, "title_he": TITLES[mode][0], "title_en": TITLES[mode][1],
                "changed_by": ch.get("by"), "changed_at": ch.get("ts"),
                "modes": [{"id": m, "title_he": TITLES[m][0], "title_en": TITLES[m][1], "targets": TARGETS[m]}
                          for m in MODES],
                "note": "Switching changes only the picture; the Core and the voice keep running."}

    async def push(self, force: bool = False) -> bool:
        """Send the current mode to the face (if one is connected). True = sent."""
        mode = self.mode
        if self.send is None or (mode == self._pushed and not force):
            return False
        ok = bool(await self.send("appearance.set", {"mode": mode}))
        self._pushed = mode if ok else None
        return ok

    async def watch(self, interval_s: float = 1.0) -> None:
        """Push a change written by another process (dashboard, file edit)."""
        while True:
            await asyncio.sleep(interval_s)
            try:
                if self._pushed is not None and self.mode != self._pushed:
                    await self.push()
            except Exception:
                logs.error("appearance", "APPEARANCE_WATCH_FAILED", "appearance watch failed", exc=True)


# ---------------------------------------------------------------------- voice Skill + offline command
def make_skill(app: Appearance):
    from .skills import Skill, Tool, ToolResult

    class SetArgs(BaseModel):
        mode: str = Field(..., description="face | avatar2d | avatar3d | hidden (Hebrew words are accepted too, "
                                           "e.g. 'אווטאר תלת מימד')")

    class AppearanceSkill(Skill):
        name = "appearance"
        description = "How I look on the screen: face, 2D avatar, 3D avatar or hidden"

        def tools(self) -> list[Tool]:
            async def get(_):
                st = app.status()
                return ToolResult(True, f"המראה הנוכחי: {st['title_he']}.", {"mode": st["mode"], "modes": list(MODES)})

            async def set_(a: SetArgs):
                m = normalize(a.mode)
                if m is None:
                    return ToolResult(False, "לא הבנתי איזה מראה. אפשר: פנים, אווטאר דו-מימדי, אווטאר תלת-מימדי או "
                                             "מוסתר.", {"modes": list(MODES)}, "APPEARANCE_UNKNOWN")
                if m == app.mode:
                    return ToolResult(True, f"אני כבר במראה {TITLES[m][0]}.", {"mode": m})
                app.set(m, by="voice")
                shown = await app.push()
                msg = f"עברתי ל{TITLES[m][0]}." if shown else f"שמרתי: {TITLES[m][0]}. יופיע כשהמסך יתחבר."
                return ToolResult(True, msg, {"mode": m, "shown": shown})

            return [
                Tool("appearance_get", "Which presentation I use on the screen now.", get, permission="read"),
                Tool("set_appearance", "Switch how I look on the screen when the user asks (\"תחליף לאווטאר תלת "
                     "מימד\", \"תחזור לפנים\"): face, avatar2d, avatar3d or hidden. Only the picture changes.",
                     set_, SetArgs, permission="action"),
            ]

    return AppearanceSkill()


_VERB = r"(?:תחליף|החלף|תעבור|עבור|תשנה|שנה|תחזור|חזור|switch|change)"
_TARGET = (r"(?:" + _AVATAR + r"|avatar|פנים|פרצוף|face|(?:דו|תלת)[\s-]?מימד|מצב\s+(?:מוסתר|נסתר)|hidden|"
           r"[23]\s*-?d)")
INTENT_RULES = [
    ("set_appearance",
     re.compile(rf"^{_VERB}(?:\s+(?:את\s+)?(?:ה)?מראה(?:\s+שלך)?)?\s+(?:ל|אל\s*|to\s+(?:the\s+)?)?(?:ה)?({_TARGET}.{{0,30}})$",
                re.IGNORECASE),
     lambda m: {"mode": m.group(1)}),
]

features.register("appearance", "החלפת מראה", "Appearance switching", True, tools=["set_appearance"],
                  about="face / 2D avatar / 3D avatar / hidden, by voice or here; only the picture changes",
                  targets={"windows": {"status": "implemented_unverified",
                                       "evidence": "pytest test_appearance + headless Godot --check"},
                           "linux_arm64_pi": {"status": "planned", "blocker": _PI_BLOCKER, "roadmap_issue": PI_ISSUE}})


def attach(core) -> Appearance:
    """Core integration (app.py: one line after the link exists): the Skill, the push at face connect, the watcher."""
    app = Appearance(core.home, send=core.link.send_face)
    core.registry.allowed_actions |= {"set_appearance"}      # reversible, user-visible, no data: low-risk action
    core.registry.register(make_skill(app))
    inner = core.link.on_face_connected

    async def connected() -> None:
        if inner is not None:
            await inner()
        await app.push(force=True)
    core.link.on_face_connected = connected
    core._bg.append(asyncio.create_task(app.watch()))
    return app


# ---------------------------------------------------------------------- dashboard
def api_get(ctx, m, body):
    return 200, Appearance(ctx.home).status()


def api_set(ctx, m, body):
    mode = (body or {}).get("mode")
    if normalize(mode) is None or not isinstance(mode, str):
        return 400, {"error": f"body needs {{\"mode\": one of {', '.join(MODES)}}}"}
    return 200, Appearance(ctx.home).set(mode, by="dashboard")


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/appearance", api_get)
    register_route("POST", r"/api/appearance", api_set)
