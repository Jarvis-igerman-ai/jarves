"""SW-V2 M1 always-on runtime (spec 4.1/4.2/6.1): background Core, one instance per user, tray, autostart at login,
display window that opens/closes without stopping the Core, sleep/resume and audio recovery.

Interface for other packages (stable):
    from jarves import runtime
    runtime.alerts_paused(home)          # WP-M3 alerts engine: hold proactive alerts while True
    runtime.current()                    # the Core's Runtime (None outside a Core): .view(), .show(), .hide()
    runtime.PLATFORM_STATUS              # Windows / Pi status of every M1 capability (dashboard, capability matrix)
Link (role cli): {"type": "runtime.set", "payload": {"display": "show"|"hide", "mic_muted": bool,
"alerts_paused": bool}} -> "runtime.state" {applied, runtime}. The full view is also in every `status` snapshot
under "runtime". The face gets {"type": "display.close"} when the Core hides it.
"""
from __future__ import annotations

import asyncio

from .. import features as feat, logs
from .service import Runtime, alerts_paused, current, read_state

QUIT_DELAY_S = 4.0      # voice quit / end: first wait QUIT_DELAY_S / 4, then for the spoken goodbye (bounded)

ISSUE_PI = "https://github.com/igalgerman/JARVIS/issues/20"
EVIDENCE = "docs/reviews/SWV2_WP-M1.md"

# Truthful status per target (spec 5). "verified_on_device" only with a real run on that device.
PLATFORM_STATUS: dict[str, dict] = {
    "background_core": {
        "windows": {"status": "verified_on_device", "evidence": EVIDENCE + " (real hidden-mode run, pythonw, no window)"},
        "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "no Pi device yet; same code path, "
                           "systemd user service untested", "roadmap_issue": ISSUE_PI}},
    "single_instance": {
        "windows": {"status": "verified_on_device", "evidence": EVIDENCE + " (second start refused, exit 3)"},
        "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "fcntl lock tested on CI-style unit tests "
                           "only, not on a Pi", "roadmap_issue": ISSUE_PI}},
    "display_control": {
        "windows": {"status": "verified_on_device",
                    "evidence": "owner hands-on show/hide/close check on the Windows PC, 19 Oct 2026; "
                    "core/tests/test_runtime.py, core/tests/test_face_protocol_godot.py"},
        "linux_arm64_pi": {"status": "planned", "blocker": "Godot performance on the Pi is unmeasured "
                           "(renderer portability, spec 4.2)", "roadmap_issue": ISSUE_PI}},
    "tray_icon": {
        "windows": {"status": "verified_on_device", "evidence": "owner hands-on tray icon and menu check on the "
                    "Windows PC, 19 Oct 2026; core/tests/test_runtime.py"},
        "linux_arm64_pi": {"status": "not_applicable", "equivalent": "dashboard Home page (show/hide, mute, "
                           "pause alerts, status, quit)", "roadmap_issue": ISSUE_PI}},
    "autostart": {
        "windows": {"status": "implemented_unverified", "blocker": "HKCU Run key written through a fake registry in "
                    "tests; a real Windows login was not exercised", "evidence": "core/tests/test_runtime.py"},
        "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "systemd user unit generated and "
                           "tested as a file only", "roadmap_issue": ISSUE_PI}},
    "resume_recovery": {
        "windows": {"status": "implemented_unverified", "blocker": "sleep and headset unplug simulated (clock jump, "
                    "stalled mic); a real sleep/unplug cycle is pending", "evidence": "core/tests/test_runtime.py"},
        "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "Pi USB mic re-plug untested",
                           "roadmap_issue": ISSUE_PI}},
    "idle_no_cloud": {
        "windows": {"status": "implemented_unverified", "blocker": "contract test: no voice session or speech while "
                    "idle in hidden mode; the real wake mic is the user's QA", "evidence": "core/tests/test_runtime.py"},
        "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "Pi mic/acoustics untested",
                           "roadmap_issue": ISSUE_PI}},
}


def _probe_tray(c: feat.ProbeCtx) -> tuple[bool, str]:
    from .tray import desktop_session, library_available
    ok, why = desktop_session()
    return library_available() if ok else (ok, why)


def _probe_autostart(c: feat.ProbeCtx) -> tuple[bool, str]:
    import sys
    if sys.platform == "win32" or sys.platform.startswith("linux"):
        return True, ""
    return False, f"autostart is not supported on {sys.platform}"


def _probe_display(c: feat.ProbeCtx) -> tuple[bool, str]:
    from .viewer import find_godot
    rt = current()
    if rt is not None and rt._custom_launcher:
        return True, ""
    exe = getattr(getattr(c.cfg, "runtime", None), "viewer_exe", "")
    return (True, "") if find_godot(exe) is not None else (False, "Godot viewer not found (set GODOT_EXE)")


def _register(fid: str, *args, **kw) -> None:
    if fid in feat.CATALOG and getattr(feat.CATALOG[fid], "targets", None):
        return                              # already registered (re-import in tests)
    f = feat.register(fid, *args, **kw)
    f.targets = PLATFORM_STATUS.get(fid, {})    # read by the capability matrix / dashboard (see PLATFORM_STATUS)


_register("background_core", "ממשיך לפעול ברקע", "Keep running in the background", True,
          config="runtime.keep_running",
          about="closing the window only hides JARVES; \"Hey Jarvis\" keeps working (Quit in the tray stops him)")
_register("display_control", "להופיע / להיעלם בפקודה", "Show / hide on command", True, probe=_probe_display,
          tools=["show_yourself", "hide_yourself"], about="\"show yourself\" / \"hide yourself\" open or close the window")
_register("tray_icon", "סמל במגש המערכת", "Tray icon", True, probe=_probe_tray, config="runtime.tray",
          applies="restart", about="notification-area icon: dashboard, show/hide, mute, pause alerts, status, quit")
_register("autostart", "הפעלה עם הכניסה למחשב", "Start at login", False, probe=_probe_autostart,
          about="starts JARVES invisibly when you log in (Windows: Startup apps; Pi: systemd user service)")


def make_skill(rt: Runtime):
    from ..skills import Skill, Tool, ToolResult

    class DisplaySkill(Skill):
        name = "display"
        description = "Show or hide my window (face/room); I keep listening either way"

        def tools(self) -> list[Tool]:
            async def show(_):
                ok, detail = await rt.show("voice")
                if not ok:
                    return ToolResult(False, f"לא הצלחתי לפתוח את החלון: {detail}", {"detail": detail},
                                      "DISPLAY_UNAVAILABLE")
                return ToolResult(True, "אני כאן." if detail != "already visible" else "אני כבר על המסך.",
                                  {"display": rt.display_state()})

            async def hide(_):
                await rt.hide("voice")
                return ToolResult(True, "נעלמתי מהמסך, אבל אני עדיין מקשיב. תגיד \"היי ג'ארוויס\" כשתצטרך אותי.",
                                  {"display": "hidden"})

            return [
                Tool("show_yourself", "Open my window (face/room) on the screen when the user asks to see me "
                     "(\"show yourself\", \"תופיע\", \"תראה את עצמך\").", show, permission="action", timeout_s=20),
                Tool("hide_yourself", "Close my window when the user asks me to hide (\"hide yourself\", "
                     "\"תיעלם\", \"תסתתר\"). I keep running and listening. NOT for quitting / shutting down / "
                     "closing the app completely: that is quit_jarves.", hide, permission="action",
                     timeout_s=10),
            ]

    class AppSkill(Skill):
        # separate from "display" (its feature gate): quitting must work without a display / Godot
        name = "app"
        description = "Quit the JARVES app completely when the user asks"

        def tools(self) -> list[Tool]:
            async def quit_(_):
                # SW-V3 P0.1 (real session 19 Oct): "שט דאון. תסגור" opened the dashboard and "לסגור את האפליקציה
                # לגמרי" could only hide the window: there was no tool for the user's real request. The same
                # orderly stop as Quit in the tray, a moment later so the goodbye sentence is still spoken.
                logs.info("runtime", "QUIT_REQUESTED", "quit requested by voice", by="voice")
                asyncio.get_running_loop().create_task(_quit_after_goodbye(rt.core))
                return ToolResult(True, "אני נסגר עכשיו לגמרי ומפסיק להקשיב. כדי להפעיל אותי שוב, פתח את "
                                  "ג'ארוויס במחשב.", {"stopping": True})

            async def end_(_):
                # SW-V3 P0.1 (real session 19 Oct 23:21): "תסגור את הסשן" got "I can't close the conversation from
                # here, close my window" (false: closing the window only hides JARVES). Ends only this voice
                # conversation, after the goodbye; the Core, wake word and window stay.
                core = rt.core
                live = getattr(core, "live", None)
                if live is None:
                    return ToolResult(False, "אין כרגע שיחה קולית פתוחה.", code="NO_CONVERSATION")
                logs.info("runtime", "CONVERSATION_END_REQUESTED", "end of the voice conversation requested by voice")
                asyncio.get_running_loop().create_task(_end_after_goodbye(core, live))
                return ToolResult(True, "סיימנו לעכשיו. כדי לדבר שוב, תגיד \"היי ג'ארוויס\" או לחץ על כפתור "
                                  "הדיבור.", {"ending": True})

            return [
                Tool("end_conversation", "End only this voice conversation (stop listening until the user calls me "
                     "again) when the user asks to end / close the conversation or session (\"תסגור את הסשן\", "
                     "\"סיימנו\", \"end the conversation\", and a bare \"תסגור\" / \"close\" with nothing else). "
                     "JARVES keeps running; the smaller, safe choice when closing is unclear. Not for quitting the app.",
                     end_, permission="action", timeout_s=5),
                Tool("quit_jarves", "Quit the JARVES app completely (the Core stops, the microphone closes, no more "
                     "listening) ONLY when the user explicitly asks to quit, exit or shut down JARVES / the whole app "
                     "entirely (\"תסגור את עצמך לגמרי\", \"לסגור את האפליקציה\", \"שט דאון\", \"צא\", \"quit\"). "
                     "A bare \"close\" or \"close the session/conversation\" is end_conversation, not this. Never "
                     "the computer itself. If unclear, ask once first. Never open the dashboard instead.", quit_,
                     permission="action", timeout_s=5),
            ]

    return DisplaySkill(), AppSkill()


async def _quit_after_goodbye(core) -> None:
    live = getattr(core, "live", None)
    if live is not None:
        await _after_goodbye(core, live)
    else:
        await asyncio.sleep(QUIT_DELAY_S / 4)
    core.stop_event.set()


async def _end_after_goodbye(core, live) -> None:
    await _after_goodbye(core, live)
    if core.live is not live:
        return
    core.live = None
    await live.stop("user")


async def _after_goodbye(core, live, max_wait_s: float = 15.0) -> None:
    """Let the goodbye sentence (and any tool still running) finish (bounded)."""
    import time
    t_mark = time.monotonic()
    await asyncio.sleep(QUIT_DELAY_S / 4)
    deadline = time.monotonic() + max_wait_s
    # silent live run 20 Oct: ended 1 s after the tool, before the goodbye was even generated. Wait for the
    # backend answer after this tool AND its speech (bounded).
    while time.monotonic() < deadline and core.live is live and (
            getattr(live, "_tools_running", 0) or getattr(live, "_backend_busy", False)
            or getattr(live, "_last_speech_t", t_mark + 1) <= t_mark
            or time.monotonic() < getattr(live, "_speaking_until", 0.0) + 1.0):
        await asyncio.sleep(0.25)


def attach(core, *, mode: str = "foreground", launcher=None, tray_factory=None, autostart=None) -> Runtime:
    """Called from Core.start once DevicePort exists (before the wake word is set up)."""
    rt = Runtime(core, mode=mode, launcher=launcher, tray_factory=tray_factory, autostart=autostart)
    core.registry.allowed_actions |= {"show_yourself", "hide_yourself", "quit_jarves", "end_conversation"}
    for skill in make_skill(rt):
        core.registry.register(skill)
    core.features.listeners.append(lambda fid, on: _feature_changed(rt, fid, on))
    from .privacy_state import PrivacyPublisher         # privacy.state for the face / room / dashboard strip
    core.privacy_state = PrivacyPublisher(core)
    core.privacy_state.start()
    rt.start()
    core.features.invalidate("display_control")        # probed earlier without a Runtime: the cached "no Godot" is stale
    sync_autostart(rt)
    return rt


def sync_autostart(rt: Runtime) -> None:
    """Apply the user's autostart switch to the OS entry, but only when the user ever set it (never on defaults)."""
    st = rt.core.features.status("autostart")
    if st["source"] != "user":
        return
    try:
        if st["enabled"] and not rt.autostart.enabled():
            rt.autostart.enable()
        elif not st["enabled"] and rt.autostart.enabled():
            rt.autostart.disable()
    except Exception as e:
        from .. import logs
        logs.warn("runtime", "AUTOSTART_SYNC_FAILED", f"could not apply autostart: {type(e).__name__}")


def _feature_changed(rt: Runtime, fid: str, on: bool) -> None:
    if fid == "autostart":
        sync_autostart(rt)


# ---------------------------------------------------------------------- dashboard (Home page)
def _ask_core(ctx, msg: dict, expect: str) -> dict | None:
    from ..cli import _ws_call
    return _ws_call(ctx.home, ctx.cfg.link.port, msg, expect, timeout=4)


def api_runtime(ctx, m, body):
    """The four states + voice/wake + platform status. Live from the Core, else the last saved view, marked."""
    from . import autostart as au
    snap = _ask_core(ctx, {"type": "status.request", "payload": {}}, "status")
    if snap is not None and snap.get("runtime"):
        view, live = snap["runtime"], True
    else:
        saved = read_state(ctx.home)
        view, live = dict(saved.get("live") or {}), False
        view["core"] = {**(view.get("core") or {}), "state": "stopped"}
        view["prefs"] = saved.get("prefs") or {}
    try:
        auto = au.adapter().describe()
    except Exception as e:
        auto = {"enabled": False, "error": type(e).__name__}
    import platform as _pf
    host = {"os": _pf.system(), "machine": _pf.machine()}          # the dashboard header's device chip (P0.2)
    return 200, {"runtime": view, "live": live, "autostart": auto, "platform_status": PLATFORM_STATUS, "host": host,
                 # privacy strip on Home (privacy_state.py); None while the Core is down = shown as "unknown"
                 "privacy": snap.get("privacy") if snap else None,
                 "snapshot": {k: snap.get(k) for k in ("core", "voice_mode", "wake_listening", "live_session",
                                                       "mic_muted", "version")} if snap else None}


def api_camera_preview_poll(ctx, m, body):
    """Home page "What does he see?": a poll keeps an open preview alive a few seconds and returns the latest frame
    (never opens one). Frames come from the Core's RAM over the local link; the dashboard keeps nothing."""
    return _preview_call(ctx, {"open": True})


def api_camera_preview_set(ctx, m, body):
    """POST {"start": true} opens the preview, {"open": false} closes it."""
    body = body or {}
    if body.get("open") is False:
        return _preview_call(ctx, {"open": False})
    if body.get("start") is True:
        return _preview_call(ctx, {"open": True, "start": True})
    return 400, {"error": "body needs {\"start\": true} or {\"open\": false}"}


def _preview_call(ctx, msg: dict):
    who = getattr(ctx, "principal", None)
    if who is not None and who.get("kind") != "local":      # paired phone over plain-HTTP Wi-Fi: no camera images
        return 403, {"error": "the camera preview is shown only on this computer", "open": False}
    r = _ask_core(ctx, {"type": "camera.preview", "payload": msg}, "camera.preview_state")
    if r is None:
        return 409, {"error": "JARVES is not running; start it first (jarvis start)", "open": False}
    return 200, r


def api_runtime_set(ctx, m, body):
    body = body or {}
    change = {k: body[k] for k in ("display", "mic_muted", "alerts_paused") if k in body}
    out: dict = {}
    if "autostart" in body:
        if not isinstance(body["autostart"], bool):
            return 400, {"error": "autostart must be true or false"}
        f = feat.Features(ctx.cfg, ctx.home)
        f.set("autostart", body["autostart"], by="dashboard")
        from . import autostart as au
        a = au.adapter()
        try:
            a.enable() if body["autostart"] else a.disable()
        except Exception as e:
            return 500, {"error": f"autostart: {type(e).__name__}: {str(e)[:120]}"}
        out["autostart"] = a.describe()
    if change:
        if change.get("display") not in (None, "show", "hide"):
            return 400, {"error": "display must be show or hide"}
        r = _ask_core(ctx, {"type": "runtime.set", "payload": change}, "runtime.state")
        if r is None:
            return 409, {"error": "JARVES is not running; start it first (jarvis start)", **out}
        out.update(r)
    if not out:
        return 400, {"error": "nothing to change (display, mic_muted, alerts_paused, autostart)"}
    return 200, out


def api_core_stop(ctx, m, body):
    """Quit from the dashboard (the same orderly shutdown as `jarvis stop`); the Core sends no reply to it."""
    from ..cli import _ws_call
    _ws_call(ctx.home, ctx.cfg.link.port, {"type": "core.shutdown", "payload": {}}, "never", timeout=1.5)
    return 200, {"ok": True, "note": "shutdown requested"}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/runtime", api_runtime)
    register_route("POST", r"/api/runtime", api_runtime_set)
    register_route("POST", r"/api/runtime/stop", api_core_stop)
    register_route("GET", r"/api/camera/preview", api_camera_preview_poll)
    register_route("POST", r"/api/camera/preview", api_camera_preview_set)


__all__ = ["Runtime", "attach", "alerts_paused", "current", "read_state", "PLATFORM_STATUS", "dashboard_routes"]
