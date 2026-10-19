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
        if hasattr(core, "_write_tools_md"):               # keeps the Features section (WP-M4 fix)
            core._write_tools_md()
        else:
            from .. import workspace as wsp
            core.workspace.write_generated("TOOLS.md", wsp.tools_markdown(core.registry, core.settings.view()))
    except Exception:
        logs.warn("camera", "TOOLS_MD_FAILED", "could not refresh TOOLS.md with the camera tools")
    svc.start()
    return svc
