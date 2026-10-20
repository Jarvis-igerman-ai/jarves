"""Core integration of the camera, kept out of app.py: `attach(core)` builds the CameraService, registers the
switches and the Camera Skill, installs the untrusted-image guard and starts the supervisor. app.py needs three
one-line hooks (start, stop, inbound presence.changed). Nothing opens the sensor until the `camera` switch is on.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from .. import logs
from ..skills import ToolResult
from .detect import make_detector, make_embedder
from .service import CameraService
from .switches import Switches

TAINT_WINDOW_S = 60.0
GREET_COOLDOWN_S = 1800.0


def install_taint_guard(registry, window_s: float = TAINT_WINDOW_S, clock=time.monotonic) -> Callable[[], None]:
    """After image-derived text entered the conversation, any action/destructive tool the MODEL calls in the next
    `window_s` must be approved by the user first (text in a photo is untrusted, like e-mail). Returns the
    function that marks the conversation as tainted. Wraps registry.call on this registry instance only."""
    state = {"until": -1e9}
    inner = registry.call

    async def call(name, raw_args=None, *, origin="model"):
        tool = registry.get(name)
        if (origin == "model" and tool is not None and tool.permission in ("action", "destructive")
                and not name.startswith("camera_") and clock() < state["until"]):
            logs.info("camera", "UNTRUSTED_GUARD", f"{name} after image content: asking the user first", tool=name)
            if not await registry.confirm(f"לבצע {name}? (אחרי תוכן מתמונה) / Run {name}? (after image content)"):
                return ToolResult(False, "cancelled: not confirmed after image content", code="TOOL_DECLINED")
        return await inner(name, raw_args, origin=origin)

    registry.call = call

    def taint() -> None:
        state["until"] = clock() + window_s
    return taint


def greeting(name: str | None, hour: int | None = None) -> str:
    h = datetime.now().hour if hour is None else hour
    word = "בוקר טוב" if 5 <= h < 12 else "צהריים טובים" if h < 17 else "ערב טוב" if h < 22 else "לילה טוב"
    return f"{word}, {name}!" if name else f"{word}!"


FACE_PREVIEW_S = 20.0           # the face asks for this long; the service caps any opening at PREVIEW_MAX_S
ENABLE_PROMPT = "להדליק את המצלמה? / Turn the camera on?"


async def on_link(core, t: str, p: dict, client) -> None:
    """Privacy strip messages (link.py allow-lists the roles):
    camera.preview {open: bool, seconds?}  -> camera.preview_state {open, reason?, seconds_left?, last_seen, frame?}
        face: frames follow as camera.preview_frame; cli (dashboard): {open, start: true} opens it, each later
        {open: true} poll keeps it open PREVIEW_LEASE_S and the reply carries the latest frame.
    camera.set_enabled {on: bool} (face)   -> camera.enabled {on, active, reason}
        off at once; on only after the person confirms on the screen (same approval as the voice feature_set)."""
    from .service import PREVIEW_LEASE_S, PREVIEW_MAX_S
    svc: CameraService | None = getattr(core, "camera", None)
    if svc is None:
        await core.link.reply(client, "error", {"code": "CAMERA_UNAVAILABLE", "message": "no camera service"})
        return
    if t == "camera.preview":
        if p.get("open") is True and client.role == "cli" and p.get("start") is not True and not svc.preview_active():
            st = {"open": False, "reason": "timeout"}       # a poll never re-opens a preview that ended (the cap)
        elif p.get("open") is True:
            secs = p.get("seconds")
            secs = (float(secs) if isinstance(secs, (int, float)) and not isinstance(secs, bool) and secs == secs
                    else FACE_PREVIEW_S)
            st = svc.preview_open(min(max(secs, 1.0), PREVIEW_MAX_S) if client.role == "face" else PREVIEW_LEASE_S,
                                  by=client.role)
        else:
            await svc.preview_close("closed")
            st = {"open": False, "reason": "closed"}
        st["last_seen"] = svc.last_seen
        if client.role == "cli" and st["open"] and svc.preview_frame is not None:
            st["frame"] = svc.preview_frame
        await core.link.reply(client, "camera.preview_state", st)
    elif t == "camera.set_enabled":
        on = p.get("on")
        if not isinstance(on, bool):
            await core.link.reply(client, "error", {"code": "LINK_BAD_MESSAGE", "message": "on must be true or false"})
            return
        if not on:
            await svc.preview_close("camera_off")
            core.features.set("camera", False, by="face")
            await _reply_enabled(core, client)
        elif core.features.enabled("camera"):
            await _reply_enabled(core, client)
        else:
            # Not awaited here: the confirmation answer arrives on this same connection (the handler must return).
            core._spawn(_enable_after_confirm(core, client))
    privacy = getattr(core, "privacy_state", None)
    if privacy is not None:
        await privacy.push(force=True)


async def _enable_after_confirm(core, client) -> None:
    if await core.ask_confirm(ENABLE_PROMPT, title="Camera on / הדלקת המצלמה", risk="high", kind="camera",
                              source="user", why="the camera button on the face was pressed"):
        core.features.set("camera", True, by="face")
    else:
        core.features.request("camera", by="face")       # the dashboard shows it for approval
        logs.info("camera", "CAMERA_ENABLE_DECLINED", "camera switch not turned on: not confirmed on the screen")
    await _reply_enabled(core, client)
    privacy = getattr(core, "privacy_state", None)
    if privacy is not None:
        await privacy.push(force=True)


async def _reply_enabled(core, client) -> None:
    st = core.features.status("camera")
    await core.link.reply(client, "camera.enabled", {"on": st["enabled"], "active": st["active"],
                                                      "reason": st["reason"]})


def attach(core) -> CameraService:
    from ..config import api_billing_allowed
    from ..skills.camera import CameraSkill
    from .vision import Vision
    cfg, home = core.cfg, core.home
    models_dir = home / "models"
    vision = None
    if api_billing_allowed(cfg.ai):
        vision = Vision(cfg.camera.vision_model or cfg.ai.text_model, cfg.assistant_name, core.budget,
                        reasoning_effort=cfg.ai.voice_reasoning_effort, service_tier=cfg.ai.voice_service_tier)

    async def send(t: str, p: dict) -> None:
        await core.link.send_face(t, p)

    def activity() -> bool:
        busy = getattr(core, "_busy", None)
        return core.live is not None or core.listening or (busy is not None and not busy.done())

    last_greet = {"t": -1e9}

    def on_arrive(person: str | None) -> None:
        busy = getattr(core, "_busy", None)
        if (not cfg.camera.greet or core.live is not None or (busy is not None and not busy.done())
                or time.monotonic() - last_greet["t"] < GREET_COOLDOWN_S):
            return
        last_greet["t"] = time.monotonic()
        core._run_busy(core.speak(greeting(person), cache=person is None))

    svc = CameraService(cfg.camera, home, switches=Switches(core.memory), memory=core.memory, health=core.health,
                        send=send, activity=activity, vision=vision, on_arrive=on_arrive,
                        models=lambda: (make_detector(models_dir), make_embedder(models_dir)))
    taint = install_taint_guard(core.registry)
    core.registry.allowed_actions |= {"camera_enroll"}      # still asks the person for consent on screen
    core.registry.register(CameraSkill(svc, core.registry, on_untrusted=taint))
    try:                                                    # TOOLS.md was generated before this Skill existed
        core._write_tools_md()                              # skills + the Features section
    except Exception:
        logs.warn("camera", "TOOLS_MD_FAILED", "could not refresh TOOLS.md with the camera tools")
    svc.start()
    return svc
