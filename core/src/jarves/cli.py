"""jarves command line: run the Core and offline troubleshooting tools (work even when the Core is down)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, clilang, logs
from .config import (ConfigError, load_config, load_secrets, openai_key_present, save_secret, secrets_file,
                     state_dir)
from .health import read_snapshot


def _ws_call(home: Path, port: int, msg: dict, expect: str, timeout: float = 5) -> dict | None:
    import websockets
    from websockets.asyncio.client import connect

    async def go():
        token = (home / "link_token").read_text(encoding="utf-8").strip()
        async with connect(f"ws://127.0.0.1:{port}", open_timeout=timeout) as ws:
            await ws.send(json.dumps({"type": "hello", "payload": {"token": token, "client": "cli"}}))
            await ws.send(json.dumps(msg))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if m["type"] == expect:
                    return m["payload"]
    try:
        return asyncio.run(go())
    except (OSError, asyncio.TimeoutError, FileNotFoundError, websockets.WebSocketException):
        return None


def apply_wake_threshold(home: Path, cfg, config_path: Path, rec: float) -> str:
    """`jarvis wake test --apply`: write the threshold where it is really read. A threshold saved with a custom
    wake model (identity.json) beats config.toml at every start (Identity.apply_to_config), so it goes there."""
    from .identity import Identity
    ident = Identity(home, cfg)
    w = ident.wake()
    if w.get("threshold") is not None:
        ident.set_wake(w["model"], w["phrase"], rec, by="cli")
        return (f"Saved: wake_threshold {rec} for {Path(str(w['model'])).name} in identity.json (it overrides "
                f"config.toml, so this is the value used). Restart JARVES to use it.")
    set_toml_value(config_path, "voice", "wake_threshold", str(rec))
    return f"Saved: wake_threshold {rec} in {config_path}, which is the value used. Restart JARVES to use it."


def _apply_log_args(a, cfg) -> None:
    if getattr(a, "log_level", None):
        cfg.log.console_level = a.log_level.upper()
    if getattr(a, "module", None):
        cfg.log.modules = [m for m in a.module.split(",") if m]
    if getattr(a, "hide", None):
        cfg.log.hide = [m for m in a.hide.split(",") if m]


EXIT_ALREADY_RUNNING = 3      # systemd: RestartPreventExitStatus=3 (runtime/autostart.py)


def _quiet_streams() -> None:
    """pythonw.exe (background Core) has no console: sys.stdout/stderr are None and any print would crash."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _lean_numeric_threads() -> None:
    """SW-V3 P0.3: numpy's OpenBLAS starts one worker per CPU at import and commits ~32 MB for each (measured on
    the owner's 8-thread laptop: +231 MB private memory, 7 idle threads). The always-on Core only does tiny
    vector maths (audio frames, face embeddings); STT / wake / camera use their own runtimes (CTranslate2,
    ONNX Runtime, OpenCV). Must run before numpy is first imported; an explicit user setting wins."""
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


def cmd_run(a, cfg, home) -> int:
    _lean_numeric_threads()
    from .app import Core
    from .runtime.lock import AlreadyRunning, InstanceLock
    background = getattr(a, "background", False)
    if background:
        _quiet_streams()
    _apply_log_args(a, cfg)
    mode = "background" if background else ("desktop" if getattr(a, "desktop", False) else "foreground")
    from . import profiles
    prof = profiles.info(home)["name"]
    clash = profiles.port_conflicts(prof, cfg.link.port)
    if clash:                           # two profiles' Cores would fight over one link port
        print(f"Profile {prof} uses link port {cfg.link.port}, which profile {', '.join(clash)} also uses. Set another "
              f"[link] port in {home / 'config.toml'} (jarvis profile create picks free ones).", file=sys.stderr)
        return 2
    lock = InstanceLock(home)
    try:
        lock.acquire(mode)              # one Core per user: autostart, `jarvis start` and `jarvis` never double up
    except AlreadyRunning as e:
        where = "" if prof == profiles.DEFAULT else f"--profile {prof} "
        print(f"{e} (profile {prof}). Use `jarvis {where}status`, `jarvis {where}show` or `jarvis {where}stop`.",
              file=sys.stderr)
        if getattr(a, "show", False):
            _ws_call(home, cfg.link.port, {"type": "runtime.set", "payload": {"display": "show"}}, "runtime.state")
        return EXIT_ALREADY_RUNNING
    if not background:
        print(f"JARVES Core starting{'' if prof == profiles.DEFAULT else f' (profile {prof})'}. "
              f"Terminal log: {cfg.log.console_level}"
              + (f", modules {','.join(cfg.log.modules)}" if cfg.log.modules else "")
              + (f", hiding {','.join(cfg.log.hide)}" if cfg.log.hide else "")
              + ". Stop: Ctrl+C or `jarvis stop`.", file=sys.stderr)
    core = Core(cfg, home, console_logs=not background, runtime_mode=mode)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    _fault = logs.enable_faulthandler(home / "logs")    # SW-V4 B: native crashes -> logs/crash.txt (frames only)
    try:
        if mode == "desktop":
            asyncio.run(_run_desktop(core))
        elif mode == "background":
            asyncio.run(_run_background(core, show=getattr(a, "show", False)))
        else:
            asyncio.run(core.run())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"Core failed to start: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"JARVES could not become ready: {e}", file=sys.stderr)
        return 2
    finally:
        lock.release()
    return 0


async def _run_background(core, show: bool = False) -> None:
    """No console, no window: the tray (when available), the local wake word and the link stay up until Quit /
    `jarvis stop`. Nothing billable opens while idle: a voice session only starts after "Hey Jarvis"."""
    await core.start()
    try:
        core._startup_stage("ready", "Ready (running in the background)")   # a window opened later shows at once
        if show:
            await core.runtime.show("start")
        await core.stop_event.wait()
    finally:
        await core.stop()


async def _launch_viewer(port: int):
    """The loading window + room (runtime/viewer.py finds Godot: GODOT_EXE, WinGet, PATH)."""
    from .runtime.viewer import launch_godot
    return await launch_godot(port)


def _keeps_running(core) -> bool:
    """M1: with the background_core switch on (default) closing the window only hides JARVES."""
    feats = getattr(core, "features", None)
    if feats is not None:
        return feats.enabled("background_core")
    return bool(getattr(getattr(core.cfg, "runtime", None), "keep_running", False))


async def _run_desktop(core, launcher=None) -> None:
    print("Loading JARVES... the room opens after voice is ready.", flush=True)
    viewer = await (launcher or _launch_viewer)(core.cfg.link.port)
    viewer_done = asyncio.create_task(viewer.wait())
    core.preopened_viewer = viewer             # the Runtime owns this window from the start: no second one on `show`
    stop = asyncio.create_task(core.stop_event.wait())

    async def boot():
        await core.start()
        await core.prepare_desktop()

    startup = asyncio.create_task(boot())
    try:
        done, _ = await asyncio.wait({startup, viewer_done, stop}, return_when=asyncio.FIRST_COMPLETED)
        if viewer_done in done and startup not in done and stop not in done and _keeps_running(core):
            # closed during loading: JARVES keeps starting in the background (tray / "Hey Jarvis" / jarvis show)
            done, _ = await asyncio.wait({startup, stop}, return_when=asyncio.FIRST_COMPLETED)
        if startup in done:
            await startup
            if _keeps_running(core):
                rt = getattr(core, "runtime", None)
                if rt is not None and viewer.returncode is None:
                    rt.viewer.adopt(viewer)          # Show/Hide/tray manage the window this session opened
                await stop
            else:
                await asyncio.wait({viewer_done, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not startup.done():
            startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        if hasattr(core, "dev"):
            await core.stop()
        if viewer.returncode is None:
            viewer.terminate()
        await asyncio.gather(viewer_done, return_exceptions=True)
        stop.cancel()
        await asyncio.gather(stop, return_exceptions=True)


def cmd_logs(a, cfg, home) -> int:
    """Read the JSON log file with the terminal formatter; filter by level/module; -f follows a running Core."""
    import logging as _logging
    import time as _time
    path = home / "logs" / "jarves.log"
    lvl = logs.level_no((a.log_level or "INFO").upper())
    flt = logs.ModuleFilter(lvl, (a.module or "").split(","), (a.hide or "").split(","))
    fmt = logs.ConsoleFormatter(color=sys.stdout.isatty())

    def show(line: str) -> None:
        try:
            d = json.loads(line)
        except ValueError:
            return
        lv = d.get("level", "INFO")
        r = _logging.LogRecord("jarves", logs.level_no(lv) if lv in logs.LEVELS else 20, "", 0, d.get("msg", ""), None, None)
        r.created = datetime.fromisoformat(d["ts"]).timestamp() if d.get("ts") else _time.time()
        r.component, r.code = d.get("component", "-"), d.get("code", "-")
        r.template = True       # the file's own line (masked when written); the formatter applies today's rules too
        r.fields = {k: v for k, v in d.items()
                    if k not in ("ts", "level", "component", "code", "msg", "session", "cid", "v")}
        if flt.filter(r):
            print(fmt.format(r), flush=True)
    if not path.exists():
        print(f"no log yet at {path}")
        return 1
    with path.open(encoding="utf-8", errors="replace") as f:
        history = f.readlines()[-a.lines:]
        for line in history:
            show(line)
        until_stopped = getattr(a, "until_stopped", False)
        def stopped(lines):
            state = None
            for line in lines:
                try:
                    code = json.loads(line).get("code")
                except ValueError:
                    continue
                if code in ("CORE_STARTING", "CORE_READY", "CORE_STOPPED"):
                    state = code
            return state == "CORE_STOPPED"
        if until_stopped and stopped(history):
            return 0
        while a.follow:
            line = f.readline()
            if line:
                show(line)
                if until_stopped and stopped([line]):
                    return 0
            else:
                try:
                    _time.sleep(0.3)
                except KeyboardInterrupt:
                    return 0
    return 0


def runtime_summary(rt: dict | None) -> str:
    """One line for the four separate states (spec 4.1): Core, display, mic, alerts."""
    if not rt:
        return "runtime: unknown"
    c, d, m, al, vo = (rt.get(k) or {} for k in ("core", "display", "mic", "alerts", "voice"))
    voice = ("in conversation" if vo.get("live_session") else "listening for Hey Jarvis (local, no cloud)"
             if vo.get("wake_listening") else f"voice {vo.get('mode', '?')}")
    return (f"Core {c.get('state', '?')} ({c.get('mode', '?')}, pid {c.get('pid', '?')}) | window {d.get('state', '?')}"
            f" | mic {'MUTED' if m.get('muted') else 'on'} | alerts {'PAUSED' if al.get('paused') else 'on'}"
            f" | {voice} | tray {(rt.get('tray') or {}).get('state', '?')}")


def _privacy_note(cfg, home) -> str:
    """"Privacy: off — conversation text is kept in logs" when log.privacy is off (config, or the runtime switch)."""
    on, until = bool(cfg.log.privacy), 0.0
    try:
        d = json.loads((home / "logs" / logs.PRIVACY_FILE).read_text(encoding="utf-8"))
        flag = d.get("privacy")
        on = flag if isinstance(flag, bool) else on
        u = d.get("until")
        until = float(u) if flag is False and isinstance(u, (int, float)) and not isinstance(u, bool) else 0.0
        if until and until <= time.time():           # the 24 hour full-text window is over: privacy is on again
            on, until = True, 0.0
    except (OSError, ValueError, AttributeError):
        pass
    if on:
        return ""
    from .clilang import fmt
    if until:
        end = time.strftime("%H:%M %d/%m", time.localtime(until))
        return fmt(f"טקסט מלא לפי בקשה עד {end}, ואז הפרטיות חוזרת לבד",
                   f"Full text on request until {end}, then privacy turns on again by itself")
    return fmt(logs.PRIVACY_OFF_NOTE_HE, logs.PRIVACY_OFF_NOTE)


def cmd_status(a, cfg, home) -> int:
    live = _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status")
    if live is None:
        from .runtime import read_state
        snap = read_snapshot(home / "health.json")
        try:
            from . import limits as _limits
            for line in _limits.status_lines(_limits.views_from_home(home, cfg.ai)):
                print(line)
        except Exception:
            pass
        print("Core is NOT running (no answer on the local link).")
        from .updates import beta_label
        if beta_label(home):
            print(f"This is a BETA build ({beta_label(home)}); `jarvis update --channel stable` goes back to stable updates.")
        prefs = read_state(home).get("prefs") or {}
        if prefs:
            print(f"Saved choices: window {'hidden' if prefs.get('display_hidden') else 'shown'}, mic "
                  f"{'muted' if prefs.get('mic_muted') else 'on'}, alerts {'paused' if prefs.get('alerts_paused') else 'on'}")
        if snap:
            print(f"Last snapshot is {snap['age_s']} s old (stale, not current truth):")
            print(json.dumps(snap, indent=1, ensure_ascii=False))
        return 1
    prof = live.get("profile") or {}
    from .updates import beta_label
    from . import limits as _limits
    for line in _limits.status_lines(live.get("limits") or []):      # no silent limits: the FIRST lines of the status
        print(line)
    beta = beta_label(home)
    print(runtime_summary(live.get("runtime"))
          + (f" | profile {prof.get('name')}{' (TEST)' if prof.get('test') else ''}" if prof else "")
          + (f" | BETA {beta}" if beta else ""))
    note = _privacy_note(cfg, home)
    if note:                                 # SW-V4 B: development mode is said plainly
        print(note)
    if not getattr(a, "brief", False):
        print(json.dumps(live, indent=1, ensure_ascii=False))
    return status_exit_code(live)


# `jarvis status` exit codes (O-05, documented in `jarvis status --help`): 0 ready, 1 Core not running,
# 3 degraded (a real component problem), 4 ready except that the Godot face is not connected (a headless run).
EXIT_READY, EXIT_NOT_RUNNING, EXIT_DEGRADED, EXIT_HEADLESS = 0, 1, 3, 4


def status_exit_code(live: dict) -> int:
    if live.get("core") == "ready":
        return EXIT_READY
    bad = {k for k, c in (live.get("components") or {}).items()
           if isinstance(c, dict) and c.get("status") in ("unavailable", "degraded")}
    return EXIT_HEADLESS if live.get("core") == "degraded" and bad == {"face_link"} else EXIT_DEGRADED


def _background_spawn(home: Path, show: bool) -> None:
    """Start a window-less Core as its own process (pythonw on Windows: no console), detached from this terminal."""
    from .runtime.autostart import core_command
    flags = 0
    if sys.platform == "win32":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    cmd = core_command(["--show"] if show else [])
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
              start_new_session=sys.platform != "win32", cwd=str(home))
    try:        # leave `uv run`'s job object, or the Core would be killed when this short command exits
        subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kw)
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, **kw)


def ensure_core(home: Path, cfg, wait: float = 60.0) -> bool:
    """The Core answers on the local link, starting it in the background (like `jarvis start`) when it does not."""
    import time as _time
    if _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status", timeout=2) is not None:
        return True
    print("Starting JARVES in the background...", file=sys.stderr)
    _background_spawn(home, False)
    deadline = _time.monotonic() + wait
    while _time.monotonic() < deadline:
        _time.sleep(0.5)
        if _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status", timeout=2) is not None:
            return True
    return False


def cmd_chat(a, cfg, home) -> int:
    """jarvis chat / jarvis text: silent terminal text chat with the running Core (no microphone, no speech)."""
    from . import chat_cli
    if not ensure_core(home, cfg, wait=a.wait):
        print(f"JARVES did not answer within {a.wait} s. See: jarvis logs -n 80", file=sys.stderr)
        return 1
    try:
        return asyncio.run(chat_cli.run_chat(home, cfg.link.port, silent=a.silent, verbose=a.verbose))
    except KeyboardInterrupt:
        return 0
    except OSError:
        print("Could not connect to the Core on the local link. See: jarvis status", file=sys.stderr)
        return 1


def cmd_start(a, cfg, home) -> int:
    """jarvis start [--show]: JARVES in the background (tray icon, "Hey Jarvis"), returning the terminal at once."""
    import time as _time
    st = _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status", timeout=2)
    if st is not None:
        print("JARVES is already running.  " + runtime_summary(st.get("runtime")))
        if a.show:
            _ws_call(home, cfg.link.port, {"type": "runtime.set", "payload": {"display": "show"}}, "runtime.state")
        return 0
    _background_spawn(home, a.show)
    deadline = _time.monotonic() + a.wait
    while _time.monotonic() < deadline:
        _time.sleep(0.5)
        st = _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status", timeout=2)
        if st is not None:
            print("JARVES started in the background.  " + runtime_summary(st.get("runtime")))
            print("Tray icon: Dashboard, Show/Hide, Mute, Pause alerts, Status, Diagnostics, Quit.  Stop: jarvis stop")
            return 0
    print(f"JARVES did not answer within {a.wait} s. See: jarvis logs -n 80", file=sys.stderr)
    return 1


def cmd_display(a, cfg, home) -> int:
    """jarvis show | hide: open or close the window; the Core keeps running either way."""
    what = "show" if a.cmd == "show" else "hide"
    r = _ws_call(home, cfg.link.port, {"type": "runtime.set", "payload": {"display": what}}, "runtime.state", timeout=8)
    if r is None:
        print("JARVES is not running. Start it with: jarvis start" + (" --show" if what == "show" else ""))
        return 1
    res = (r.get("applied") or {}).get("display") or {}
    print(f"{what}: {res.get('detail', '?')}" + ("" if res.get("ok", True) else "  (failed)"))
    return 0 if res.get("ok", True) else 1


def cmd_talk(a, cfg, home) -> int:
    """jarvis talk [--stop]: start (or end) a live voice conversation without the wake word - tests the whole voice path."""
    r = _ws_call(home, cfg.link.port, {"type": "talk.stop" if a.stop else "talk.start", "payload": {}}, "talk.state",
                 timeout=20)
    if r is None:
        print("JARVES is not running. Start it with: jarvis start")
        return 1
    print(r.get("message", "?"))
    if r.get("ok") and r.get("live"):
        print("Speak now. End it with: jarvis talk --stop")
    return 0 if r.get("ok") else 1


def cmd_autostart(a, cfg, home) -> int:
    """jarvis autostart on | off | status: start JARVES invisibly at login (the user's choice; default off)."""
    from .features import Features
    from .runtime import autostart as au
    import jarves.runtime  # noqa: F401  (registers the autostart feature)
    ad = au.adapter()
    if a.action == "status":
        d = ad.describe()
        print(f"autostart: {'ON' if d.get('enabled') else 'off'} ({d.get('mechanism') or 'not supported'})"
              + (f"\n  {d['entry']}" if d.get("entry") else "") + (f"\n  note: {d['note']}" if d.get("note") else ""))
        return 0
    try:
        if a.action == "on":
            where = ad.enable()
            print(f"JARVES will start in the background when you log in.\n  {where}")
        else:
            print("autostart removed." if ad.disable() else "autostart was not on.")
    except NotImplementedError as e:
        print(str(e), file=sys.stderr)
        return 2
    feats, want = Features(cfg, home), a.action == "on"
    if feats.enabled("autostart") != want:       # an unchanged switch is not rewritten (the uninstaller runs `off`: user data stays byte-for-byte)
        feats.set("autostart", want, by="cli")   # the dashboard shows the same choice
    return 0


def cmd_stop(a, cfg, home) -> int:
    """Orderly Core shutdown through the authenticated local link."""
    import websockets
    from websockets.asyncio.client import connect

    async def go():
        token = (home / "link_token").read_text(encoding="utf-8").strip()
        async with connect(f"ws://127.0.0.1:{cfg.link.port}", open_timeout=3) as ws:
            await ws.send(json.dumps({"type": "hello", "payload": {"token": token, "client": "cli"}}))
            await ws.send(json.dumps({"type": "core.shutdown", "payload": {}}))
            await asyncio.sleep(0.5)
    try:
        asyncio.run(go())
    except (OSError, FileNotFoundError, websockets.WebSocketException):
        print("Core is not running")
        return 1
    print("shutdown requested")
    return 0


def _git_rev() -> str:
    from .updates import build_info
    if build_info().get("commit"):                 # installed build: no git, the build records its commit
        return build_info()["commit"][:12]
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _private_line(line: str) -> str:
    """Support bundles are for sharing (old name; the rule lives in diagbundle.private_line, issue #15)."""
    from .diagbundle import private_line
    return private_line(line) or ""         # a malformed line is dropped, never copied


def cmd_diag(a, cfg, home) -> int:
    """User-initiated, redacted support bundle (no secrets, no transcripts, no audio)."""
    from . import diagbundle                    # SW-V2 M5: sanitized, size-bounded bundle
    if getattr(a, "ci", False):                 # SW-V4 O: the CI-uploaded bundle (redacted + scanned)
        from .diag.service import ci_bundle
        return ci_bundle(home, cfg, Path(a.out or "diag"))
    name = f"jarves-diag-{datetime.now():%Y%m%d-%H%M%S}.zip"
    out = Path(a.out or home / name)
    if out.is_dir():                            # `--out <folder>`: the bundle goes inside (was a PermissionError traceback)
        out = out / name
    out.parent.mkdir(parents=True, exist_ok=True)
    snap = _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status") or read_snapshot(home / "health.json")
    man = diagbundle.build(home, cfg, out, lines=a.lines, max_bytes=int(a.max_mb * 1024 * 1024), status=snap,
                           commit=_git_rev())
    print(f"wrote {out} ({man['bytes'] / 1024:.0f} KB, {man['log_lines']} log lines"
          + (f", {man['log_lines_cut_for_size']} older lines cut for size" if man["log_lines_cut_for_size"] else "")
          + "; personal content and credentials masked). Review it before sharing.")
    return 0


def cmd_memory(a, cfg, home) -> int:
    from .memory import MemoryStore
    m = MemoryStore(home / "memory.db")
    if a.action == "list":
        for r in m.list(500):
            print(f"{r['id']:4d}  {r['created'][:10]}  {r['text']}")
        print(f"({m.count()} memories)")
    elif a.action == "delete":
        try:
            mid = int(a.id)
        except (TypeError, ValueError):
            print("memory delete needs the number from `jarvis memory list`, e.g. jarvis memory delete 12", file=sys.stderr)
            return 2
        print("deleted" if m.delete(mid) else "not found")
    elif a.action == "clear":
        if a.yes or input("Delete ALL memories? type yes: ").strip() == "yes":
            print(f"deleted {m.clear()} memories")
    return 0


def cmd_personal(a, cfg, home) -> int:
    """Contact, event and email tools with the same validation, switches and read-back policy as voice."""
    from .memory import MemoryStore
    from .skills import SkillRegistry
    from .skills.builtin import MemorySkill
    from .skills.local_contacts import LocalContactsSkill
    from .skills.google import register_google

    memory = MemoryStore(home / "memory.db")
    reg = SkillRegistry()
    reg.gate = _cli_features(cfg, home).gate
    reg.register(MemorySkill(memory))
    reg.register(LocalContactsSkill(memory))
    reg.allowed_actions.add("contact_remember")

    async def confirm(prompt):
        return input(prompt + "\nType yes to confirm: ").strip().lower() == "yes"
    from .approvals import audited
    reg.confirm = audited(home, confirm)          # same approvals audit log as the Core

    async def run():
        if a.cmd == "contacts" and a.action != "search":
            if a.action == "remember":
                return await reg.call("contact_remember", {"label": a.label, "email": a.email, "name": a.name}, origin="user")
            if a.action == "forget":
                row = memory.get(a.id)
                if not row or row.get("kind") != "contact":
                    from .skills import ToolResult
                    return ToolResult(False, "No contact with that memory id.", code="NOT_FOUND")
                # Use model origin here to require confirmation even for deleting a single record.
                return await reg.call("forget", {"id": a.id}, origin="model")
            return await reg.call("contact_lookup", {"query": a.query}, origin="user")
        auth = register_google(reg, home)
        if auth is None:
            return await reg.call("google_connect_help", {}, origin="user")
        if a.cmd == "contacts":
            from .skills.contacts import register_contacts
            register_contacts(reg, auth)
            if reg.get("google_contacts_search") is None:
                from .skills import ToolResult
                return ToolResult(False, "Google Contacts unavailable: " + _cli_features(cfg, home).reason("google_contacts"),
                                  code="FEATURE_OFF")
            return await reg.call("google_contacts_search", {"query": a.query, "limit": a.limit}, origin="user")
        if a.cmd == "mail":
            drafted = await reg.call("gmail_create_draft", {"to": a.to, "subject": a.subject, "body": a.body}, origin="user")
            if not drafted.ok or not a.send:
                return drafted
            print(drafted.to_model())
            return await reg.call("gmail_send_draft", {"draft_id": drafted.data["draft_id"]}, origin="user")
        if a.action == "create":
            return await reg.call("calendar_create_event", {"title": a.title, "day": a.day, "start": a.start,
                                  "duration_min": a.duration, "calendar": a.calendar, "guests": a.guest}, origin="user")
        found = await reg.call("calendar_find_events", {"day": a.day, "query": a.query}, origin="user")
        if not found.ok or a.action == "find":
            return found
        events = found.data.get("untrusted_events", [])
        if not events:
            return found
        print(json.dumps(events, ensure_ascii=False, indent=2))
        handle = input("Enter the exact event handle to share (blank cancels): ").strip()
        if not any(e.get("handle") == handle for e in events):
            from .skills import ToolResult
            return ToolResult(False, "No event selected; nothing shared.", code="TOOL_CANCELLED")
        return await reg.call("calendar_share_event", {"handle": handle, "guests": a.guest, "scope": a.scope}, origin="user")

    try:
        result = asyncio.run(run())
        print(result.to_model())
        # google_connect_help answers ok=True for the model (it is guidance, not a fault), but the command the user
        # typed did NOT happen: exit 1 so scripts and the shell see it (V4-CLI-1).
        return 0 if result.ok and result.code != "GOOGLE_NOT_CONNECTED" else 1
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.")
        return 1
    finally:
        memory.close()


PEAR_DOWNLOAD = "https://github.com/pear-devs/pear-desktop/releases"


def find_pear() -> Path | None:
    """Pear Desktop executable: the portable copy in %LOCALAPPDATA%\\Programs, or a normal install."""
    import os
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    cands = sorted((local / "Programs").glob("PearDesktop-*/app/YouTube Music.exe"), reverse=True)
    cands += [local / "Programs" / "youtube-music" / "YouTube Music.exe",
              local / "Programs" / "pear-desktop" / "Pear Desktop.exe"]
    return next((c for c in cands if c.exists()), None)


def cmd_music(a, cfg, home) -> int:
    from .skills.music import MusicError, PearDesktopPlayer
    if a.action == "open":
        exe = find_pear()
        if exe is None:
            print(f"Pear Desktop not found. Download it from {PEAR_DOWNLOAD}")
            return 1
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        print(f"Opening Pear Desktop ({exe}).\n"
              "First time: sign in to YouTube inside Pear, then run:  jarvis music pair   and click Allow.")
        return 0
    p = PearDesktopPlayer(cfg.music.pear_url, home / "pear_client_id", cfg.music.timeout_s)

    async def go():
        try:
            if a.action == "pair":
                print("Requesting access. If Pear Desktop shows a dialog, click 'Allow'.")
                await p.pair(wait_s=90)
                print("paired OK")
            elif a.action == "status":
                np = await p.now_playing()
                print(np if np else "nothing playing")
            elif a.action == "play":
                tracks = await p.search(a.query)
                if not tracks:
                    print("no results")
                    return 1
                await p.play_track(tracks[0])
                await asyncio.sleep(3)
                print(await p.now_playing())
            elif a.action in ("pause", "resume", "next"):
                await getattr(p, a.action)()
            return 0
        except MusicError as e:
            print(f"{e.code}: {e.message}. {e.action}")
            return 1
        finally:
            await p.close()
    return asyncio.run(go())


def cmd_ask(a, cfg, home) -> int:
    """One-shot question through the full brain + Skills, without Godot."""
    from .app import make_agent
    from .brain import Brain
    from .memory import MemoryStore
    from .skills import SkillRegistry
    from .skills.builtin import ClockSkill, MemorySkill
    from .skills.music import MusicSkill, PearDesktopPlayer
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    mem = MemoryStore(home / "memory.db")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    reg.register(MemorySkill(mem))
    from .skills.local_contacts import LocalContactsSkill
    reg.register(LocalContactsSkill(mem))
    reg.allowed_actions.add("contact_remember")
    reg.register(MusicSkill(PearDesktopPlayer(cfg.music.pear_url, home / "pear_client_id"), cfg.music.max_volume))
    agent = make_agent(cfg, reg, mem, home)
    r = asyncio.run(Brain(reg, agent).respond(" ".join(a.text)))
    print(f"[{r.source}{'/' + r.tool if r.tool else ''} {r.code}] {r.text}")
    return 0 if r.ok else 1


def cmd_execute(a, cfg, home) -> int:
    from .text_session import execute
    text = " ".join(a.text).strip()
    if not text:
        print('Provide a request: jarvis execute "add a calendar event tomorrow at 10"', file=sys.stderr)
        return 2
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    logs.new_cid()
    try:
        return asyncio.run(execute(cfg, home, text, interactive=a.interactive, read=input))
    except KeyboardInterrupt:
        print("Cancelled. Completed actions are not undone; check their status before retrying.")
        return 1


def cmd_chatgpt(a, cfg, home) -> int:
    """Sign in with ChatGPT: the user's own browser consent; tokens stay in <state_dir>/chatgpt (never logged)."""
    from .brain import BrainUnavailable
    from .chatgpt_auth import ChatGPTAuth, ChatGPTAuthError
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    auth = ChatGPTAuth(home)

    async def go() -> int:
        try:
            if a.action == "login":
                print("Opening your browser to connect JARVES to your ChatGPT plan (official OpenAI sign-in).")
                st = await auth.sign_in()
                print(f"connected: {st['email'] or 'ChatGPT account'}")
                if not st["plan_usage"]:
                    print("Plan usage was NOT granted, so JARVES cannot use your plan. Run login again and allow it,"
                          " or use an API key.")
                    return 1
                await _models(auth)
                if cfg.ai.connection == "api_key":
                    print('To use it, set ai.connection = "chatgpt" (or "combined") in config.toml and restart the Core.')
            elif a.action == "logout":
                ok = await auth.sign_out(forget=a.forget)
                print("disconnected" + ("" if ok else " locally (remote revocation not confirmed: you can also remove"
                                        " JARVES in ChatGPT Settings > Connected apps)"))
            elif a.action == "status":
                st = auth.status()
                print(json.dumps({**st, "ai.connection": cfg.ai.connection,
                                  "models": [m["slug"] for m in auth.models()]}, indent=1))
                return 0 if st["signed_in"] else 1
            elif a.action == "models":
                await _models(auth)
            return 0
        except (ChatGPTAuthError, BrainUnavailable) as e:
            print(f"{e.code}: {e.message}. {e.action}")
            return 1

    async def _models(auth) -> None:
        from .chatgpt_brain import ChatGPTBrain
        from .memory import MemoryStore
        from .skills import SkillRegistry
        b = ChatGPTBrain(cfg.ai, SkillRegistry(), MemoryStore(home / "memory.db"), auth)
        models = await b.list_models()
        print("models on your plan: " + (", ".join(m["slug"] for m in models) or "(none)"))
    return asyncio.run(go())


# Issue #13: GPT-Live starts delegated backend requests server-side; JARVES counts them but cannot strictly cap
# them, so a provider-side limit is the real hard ceiling.
SPEND_LIMIT_ADVICE = ("IMPORTANT: set a monthly spend limit for your OpenAI project (platform.openai.com > "
                      "Settings > Limits). JARVES's daily limits are a local guard; live voice can exceed them slightly.")


def set_connection(path: Path, connection: str) -> None:
    """Write `[ai] connection = ...` into config.toml, keeping everything else in the file."""
    import re
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f'connection = "{connection}"'
    if re.search(r"(?m)^connection\s*=.*$", text):
        text = re.sub(r"(?m)^connection\s*=.*$", line, text, count=1)
    elif re.search(r"(?m)^\[ai\]\s*$", text):
        text = re.sub(r"(?m)^\[ai\]\s*$", "[ai]\n" + line, text, count=1)
    else:
        text = text.rstrip() + ("\n\n" if text.strip() else "") + f"[ai]\n{line}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def cmd_setup(a, cfg, home) -> int:
    """First-run wizard (setup_wizard.py): language, connection + key/sign-in, name, wake phrase, voice, every
    add-on ON/OFF, privacy, budget, Google; also --answers (silent installs), --defaults and --check-addons."""
    from . import setup_wizard as sw
    from .chatgpt_auth import ChatGPTAuth
    path = a.config or home / "config.toml"
    if getattr(a, "check_addons", False):
        result = sw.check_addons()
        print_check = sw.print_check
        if getattr(a, "json", False):
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print_check(result)
        return 0 if result["ok"] else 1
    answers = None
    if getattr(a, "answers", None) is not None:
        if not a.answers.strip():              # the installer's silent run line without /SETUPANSWERS=
            return 0
        try:
            answers = json.loads(Path(a.answers).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            print(f"cannot read answers file {a.answers}: {e}", file=sys.stderr)
            return 2
        if not isinstance(answers, dict):
            print("the answers file must be a JSON object", file=sys.stderr)
            return 2
    if a.if_needed and not sw.first_run(home) and answers is None:
        conn = cfg.ai.connection
        if conn in ("chatgpt", "combined") and not ChatGPTAuth(home).status()["plan_usage"]:
            print("ChatGPT is not connected, so JARVES starts with offline commands only.\n"
                  "Reconnect any time:  jarvis chatgpt login   (or change mode: jarvis setup)")
        if conn in ("combined", "api_key") and not openai_key_present():
            print("No OpenAI API key saved, so live voice is off.  Add it:  jarvis key")
        return 0

    def chatgpt_login() -> int:
        print("Sign in to ChatGPT in the browser window and click Allow.")
        a.action, a.forget = "login", False
        return cmd_chatgpt(a, cfg, home)

    def google_start() -> int:
        from argparse import Namespace
        from .google_auth import cmd_google
        return cmd_google(Namespace(action="start", client=None, calendar_only=False, send=False, downloads=None,
                                    restart=False, calendar_write=False, contacts=False, config=a.config), cfg, home)
    def connect_start() -> int:
        from .connect import Connector
        return Connector(home, cfg).run()

    def tour_start() -> int:
        """Open the setup tour in its own process so terminal setup can finish."""
        kw = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if sys.platform == "win32" else {}
        subprocess.Popen([sys.executable, "-m", "jarves", "tour"], **kw)
        return 0

    def browser_setup() -> int:
        """Hebrew: continue in the browser (the Setup page renders Hebrew correctly in any terminal)."""
        import webbrowser

        from .dashboard import Dashboard
        try:
            interactive = bool(sys.stdin and sys.stdin.isatty())
        except (AttributeError, ValueError):
            interactive = False
        if not interactive:        # piped input: the dashboard would run until Ctrl+C that nobody can press (hung CI shards)
            return sw.CONTINUE_IN_TERMINAL
        try:
            dash = Dashboard(home, cfg, 0)
        except OSError as e:
            print(f"cannot start the dashboard: {e}  (continuing in the terminal)", file=sys.stderr)
            return sw.CONTINUE_IN_TERMINAL
        print("Setup is open in your browser (page Setup & add-ons). Press Ctrl+C here when you are done.", flush=True)
        webbrowser.open(dash.url + "#/setup")
        try:
            dash.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            dash.stop()
        return 0
    try:
        interactive_setup = bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, ValueError):
        interactive_setup = False
    try:
        wiz = sw.Wizard(cfg, home, config_path=path, answers=answers, defaults=getattr(a, "defaults", False),
                        lang=getattr(a, "lang", None), restart=getattr(a, "restart", False), yes=getattr(a, "yes", False),
                        actions={"chatgpt_login": chatgpt_login, "google_start": google_start, "connect_start": connect_start,
                                 **({"tour_start": tour_start} if interactive_setup and _cli_features(cfg, home).enabled("dashboard") else {}),
                                 **({"browser_setup": browser_setup} if _cli_features(cfg, home).enabled("dashboard") else {})})
        return wiz.run()
    except sw.AnswerError as e:
        print(f"answers: {e}", file=sys.stderr)
        return 2


def _ask_key(home) -> int:
    import getpass
    print("Paste your OpenAI API key (from platform.openai.com/api-keys). It is hidden while you paste.")
    key = getpass.getpass("API key: ").strip()
    if not key:
        return 1
    if not key.startswith("sk-"):
        print("That does not look like an OpenAI key (it should start with sk-). Nothing saved.")
        return 1
    print(f"saved to {save_secret('OPENAI_API_KEY', key, home)} (private to your Windows user, not in the project)")
    return 0


def _ask_admin_key(home) -> int:
    """WP-L guide "Real API bill": the optional read-only OpenAI Admin key for the Costs API (usage.py)."""
    import getpass
    print("Paste your OpenAI ADMIN key (platform.openai.com/settings/organization/admin-keys, Read only).\n"
          "It is only used to read this month's real cost. It is hidden while you paste.")
    key = getpass.getpass("Admin key: ").strip()
    if not key:
        return 1
    from .usage import save_admin_key
    try:
        save_admin_key(home, key)
    except ValueError as e:
        print(e)
        return 1
    print(f"saved to {secrets_file(home)} (private to your Windows user, not in the project)")
    return 0


def cmd_key(a, cfg, home) -> int:
    """Store / remove the OpenAI API key (or with --admin the read-only Admin key) in <state_dir>/secrets.env."""
    path = secrets_file(home)
    name = "OPENAI_ADMIN_KEY" if getattr(a, "admin", False) else "OPENAI_API_KEY"
    if a.remove:
        if name == "OPENAI_ADMIN_KEY":
            from .usage import remove_admin_key
            remove_admin_key(home)
        elif path.exists():
            lines = [l for l in path.read_text(encoding="utf-8-sig").splitlines()
                     if l.partition("=")[0].strip() != name]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{'admin ' if name != 'OPENAI_API_KEY' else ''}key removed (restart JARVES)")
        return 0
    if a.show_path:
        print(path)
        return 0
    rc = _ask_admin_key(home) if name == "OPENAI_ADMIN_KEY" else _ask_key(home)
    if rc == 0:
        print("Restart JARVES to use it:  jarvis stop  then  jarvis")
    return rc


def set_toml_value(path: Path, section: str, key: str, literal: str) -> None:
    """Set `key = literal` inside `[section]` of a TOML file, keeping everything else (comments included)."""
    import re
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"{key} = {literal}"
    m = re.search(rf"(?ms)^\[{re.escape(section)}\]\s*$(.*?)(?=^\[|\Z)", text)
    if m:
        body = m.group(1)
        if re.search(rf"(?m)^{re.escape(key)}\s*=.*$", body):
            body = re.sub(rf"(?m)^{re.escape(key)}\s*=.*$", line, body, count=1)
        else:
            body = "\n" + line + body
        text = text[:m.start(1)] + body + text[m.end(1):]
    else:
        text = text.rstrip() + ("\n\n" if text.strip() else "") + f"[{section}]\n{line}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _confirm_or_none(v):
    """The same local speech check the Core uses (voice.wake_confirm), or None when off / not loadable."""
    if not v.wake_confirm:
        return None
    from .voice.wake_confirm import WhisperConfirm
    c = WhisperConfirm(v.wake_confirm_model)
    ok, msg = c.check()
    if not ok:
        print(f"(two-stage check off for this test: {msg})")
    return c if ok else None


async def _wake_listen_test(det, *, mic_factory, seconds: float, phrase: str, threshold: float, v,
                            confirm=None, out=print) -> dict:
    """`jarvis wake test`: listen `seconds`, report per attempt the level, wake score and (two-stage) what the local
    speech check heard, then ONE verdict with the exact fix (voice/wake_calib.diagnose). Audio stays in RAM."""
    import time as _t
    import numpy as np
    from .voice.audio import AudioUnavailable
    from .voice.wake_calib import diagnose
    from .voice.wake_confirm import AutoGain, SpeechMonitor, name_match
    from .voice.wakeword import level_meter
    agc = AutoGain(initial_gain_db=v.wake_gain_db) if v.wake_agc else None
    mon = SpeechMonitor(min_score=0.0)
    ring = np.zeros(int(16000 * 2.5), np.int16)
    gate = v.wake_gate if confirm is not None else threshold
    attempts: list[dict] = []
    cur = {"score": 0.0}
    best, n, nonzero = 0.0, 0, False
    out(f'Say "{phrase}" 3-4 times with a pause between, normally, from where you will sit.  ({seconds} s)\n')
    mic = mic_factory()
    t_end = None
    try:
        async for chunk in mic.mic_chunks(16000, 2560):
            t_end = t_end or _t.monotonic() + seconds
            n += 1
            raw = np.frombuffer(chunk, "<i2")
            nonzero = nonzero or bool(np.abs(raw).max() > 2) if raw.size else nonzero
            fed = agc.process(chunk) if agc is not None else chunk
            a = np.frombuffer(fed, "<i2")
            ring = np.roll(ring, -len(a))
            ring[-len(a):] = a
            det.feed(fed)
            sc = float(det.last_score)
            best = max(best, sc)
            before = mon.bursts
            mon.feed(chunk, sc)
            if mon._speech_ms:
                cur["score"] = max(cur["score"], sc)
            if mon.bursts > before:                         # one utterance ended: report it
                at = {"level_db": mon.last_burst_db, "score": round(cur["score"], 3), "heard": None, "match": None}
                if confirm is not None and cur["score"] >= gate:
                    text = confirm.transcribe(ring.astype(np.float32) / 32768.0)
                    at["heard"], at["match"] = text, name_match(text)
                attempts.append(at)
                cur["score"] = 0.0
                ok = at["score"] >= threshold or at["match"]
                out(f"\r attempt {len(attempts)}: level {at['level_db']:.0f} dBFS, wake score {at['score']:.2f}"
                    + (f", heard \"{at['heard']}\"" if at["heard"] is not None else "")
                    + ("  -> would WAKE" if ok else "  -> no wake") + " " * 20)
            lv = level_meter(chunk)
            bar = "#" * int(sc * 30)
            out(f"\r mic {'|' * int(lv * 12):<12} wake {sc:4.2f} {bar:<30} best {best:4.2f}   ", end="", flush=True)
            if _t.monotonic() >= t_end:
                break
    except AudioUnavailable as e:
        return {"error": f"{e.code}: {e.message}. {e.action}"}
    finally:
        mic.close()
    out("\n")
    speech = [x["level_db"] for x in attempts if x["level_db"] is not None]
    speech_db = max(speech) if speech else None
    peaks = [x["score"] for x in attempts if x["score"] >= 0.1 or x["match"]]
    near = sum(1 for x in attempts if gate <= x["score"] < threshold and not x["match"])
    verdict, advice = diagnose(floor_db=mon.floor_db, speech_db=speech_db, best_score=best, attempts=len(attempts),
                               gate=gate, threshold=threshold, confirmed=sum(bool(x["match"]) for x in attempts),
                               digital_silence=n > 5 and not nonzero)
    out(f"Room noise {mon.floor_db:.0f} dBFS, loudest speech "
        + (f"{speech_db:.0f} dBFS" if speech_db is not None else "none")
        + f", {len(attempts)} attempt(s), best wake score {best:.2f} (instant wake at {threshold}"
        + (f", local speech check from {gate}" if confirm is not None else "") + f"), near misses {near}.")
    out(f"{verdict}: {advice}")
    return {"verdict": verdict, "advice": advice, "floor_db": mon.floor_db, "speech_db": speech_db, "best": best,
            "attempts": attempts, "peaks": peaks, "near_misses": near}


def cmd_wake(a, cfg, home) -> int:
    """Calibrate "Hey Jarvis" to YOUR voice and mic: live score bar, then a recommended threshold."""
    import time as _time
    from .voice.audio import AudioUnavailable, StreamingAudio
    from .voice.wakeword import WakeWordUnavailable, make_detector
    from .identity import Identity
    Identity(home, cfg).apply_to_config(cfg)            # the chosen wake model / phrase (dashboard, wake train)
    v = cfg.voice
    if a.action == "train":
        return _wake_train(a, cfg, home)
    if a.action == "eval":
        return _wake_eval(a, cfg, home)
    if getattr(a, "list_mics", False):
        import sounddevice as sd
        from .app import _mic_name
        print(f"In use now: {_mic_name(v.input_device)}  (voice.input_device = {v.input_device!r})")
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                print(f"  {i:3d}  {d['name']}")
        print("Pick one with:  voice.input_device = <number or name>  in config.toml, then jarvis wake test again.")
        return 0
    model = a.model or v.wake_word
    phrase = v.wake_phrase if not a.model else Path(a.model).stem.replace("_", " ")

    async def go() -> int:
        try:
            det = make_detector(model, 1.01)                # never "triggers": we only read the raw score
            await det.load()
        except WakeWordUnavailable as e:
            print(f"{e.code}: {e.message}. {e.action}")
            return 1
        threshold = v.wake_threshold
        if model.endswith(".npz") and a.model:
            threshold = det.head["meta"].get("threshold") or threshold
        from .app import _mic_name
        print(f"Microphone: {_mic_name(v.input_device)}   wake model: {Path(model).name}   threshold: {threshold}")
        if a.false_wakes:
            return await false_wakes(det, threshold)
        confirm = None if a.model else _confirm_or_none(v)
        res = await _wake_listen_test(det, mic_factory=lambda: StreamingAudio(v.input_device, v.output_device,
                                                                               rate=16000),
                                      seconds=a.seconds, phrase=phrase, v=v, confirm=confirm,
                                      threshold=max(threshold, v.wake_accept) if confirm is not None else threshold)
        if res.get("error"):
            print(res["error"])
            return 1
        from .voice.wake_calib import save_calibration
        mic_name = _mic_name(v.input_device)
        cal = save_calibration(home, mic_name, floor_db=res["floor_db"], speech_db=res["speech_db"],
                               verdict=res["verdict"], by="wake test")
        print(f"Saved the level calibration for {mic_name}: starting gain +{cal['gain_db']} dB "
              f"(applies at the next start).")
        peaks = res["peaks"]
        if res["verdict"] not in ("OK", "NEAR_MISS") or not peaks:
            return 1
        rec = round(min(0.5, max(0.15, 0.6 * min(peaks))), 2)
        print(f"Heard {len(peaks)} attempt(s), scores {', '.join(f'{x:.2f}' for x in peaks)}.")
        print(f"Recommended wake_threshold = {rec}   (now {v.wake_threshold}).  Lower = easier to trigger, but more false wakes.")
        if a.apply and a.model:
            Identity(home, cfg).set_wake(a.model, threshold=rec, by="cli")
            print(f"Saved: {Path(a.model).name} is now the wake phrase (threshold {rec}). Restart JARVES to use it.")
        elif a.apply:
            print(apply_wake_threshold(home, cfg, Path(a.config) if a.config else home / "config.toml", rec))
        else:
            print("Apply it with:  jarvis wake test --apply" + (f' --model "{a.model}"' if a.model else ""))
        return 0

    async def false_wakes(det, threshold: float) -> int:
        """Room audio for N minutes (TV, talking, quiet): how often would it wake by mistake?"""
        print(f"Measuring false wakes for {a.false_wakes} min: do NOT say the wake phrase; keep the room as usual "
              "(talking, TV, music). Nothing is recorded or sent; only the score is read.\n")
        mic = StreamingAudio(v.input_device, v.output_device, rate=16000)
        hits, best, cool = 0, 0.0, 0.0
        t0 = _time.monotonic()
        t_end = t0 + a.false_wakes * 60
        try:
            async for chunk in mic.mic_chunks(16000, 2560):
                det.feed(chunk)
                sc, now = det.last_score, _time.monotonic()
                best = max(best, sc)
                if sc >= threshold and now >= cool:
                    hits, cool = hits + 1, now + 2.0
                print(f"\r {int(now - t0) // 60:3d} min  false wakes {hits}  highest score {best:4.2f}   ", end="",
                      flush=True)
                if now >= t_end:
                    break
        except AudioUnavailable as e:
            print(f"\n{e.code}: {e.message}. {e.action}")
            return 1
        finally:
            mic.close()
        hours = max(1e-6, (_time.monotonic() - t0) / 3600)
        print(f"\n\n{hits} false wake(s) in {hours * 60:.0f} min = {hits / hours:.1f} per hour at threshold {threshold}. "
              f"Highest score {best:.2f}. Aim for under 0.5 per hour; raise the threshold if it is higher.")
        return 0 if hits / hours <= 0.5 else 1
    return asyncio.run(go())


def _wake_eval(a, cfg, home) -> int:
    """`jarvis wake eval`: synthetic positives/negatives through the real WakeListener (voice/wake_eval.py):
    the plain wake model at voice.wake_threshold vs the configured pipeline (AGC / two-stage). Free and local
    unless --openai (OpenAI TTS voices, metered once, then cached)."""
    import asyncio as _aio
    import json as _json
    from .voice import wake_confirm  # noqa: F401  (faster-whisper before WinRT)
    from .voice.wake_eval import build_corpus, evaluate_pipeline, save_report
    from .voice.wakeword import make_detector, make_wake_detector
    from .config import load_secrets
    v = cfg.voice
    if a.openai:
        load_secrets(home)
        print("Using OpenAI TTS voices for extra speakers (metered, about US$0.30 the first time, then cached).")
    cost: dict = {}
    corpus = build_corpus(home / "wake" / "eval_cache", use_openai=a.openai, minutes=a.minutes,
                          progress=lambda s: print("  " + s, flush=True), cost=cost)
    pipes = {f"single-stage {v.wake_word}@{v.wake_threshold}": lambda: make_detector(v.wake_word, v.wake_threshold),
             "configured (" + ("AGC + " if v.wake_agc else "") + (f"two-stage gate {v.wake_gate} / instant "
              f"{max(v.wake_threshold, v.wake_accept)} / {v.wake_confirm_model}" if v.wake_confirm
              else f"single-stage @{v.wake_threshold}") + ")": lambda: make_wake_detector(v)}
    rows = []
    for name, make in pipes.items():
        print(f"running {name} ...", flush=True)
        rows.append(_aio.run(evaluate_pipeline(name, make, corpus, progress=lambda s: print("  " + s, flush=True))))
    out = Path(a.out) if a.out else home / "wake" / "eval_report.json"
    save_report(out, rows, {**corpus.info, "openai_cost": cost})
    print(f"\nSaved {out}. Synthetic voices only: confirm with your own voice using  jarvis wake test.")
    print(_json.dumps([{"pipeline": r["pipeline"], "detection": r["detection"]["rate"],
                        "false_per_hour": r["negatives"]["per_hour"]} for r in rows], indent=1))
    return 0


def _wake_train(a, cfg, home) -> int:
    """`jarvis wake train "<phrase>"`: local synthetic samples + the owner's recordings -> a CANDIDATE head in
    <state_dir>/wake/candidates (voice/wake_train). Nothing active changes without --apply; --revert undoes the last
    --apply (voice/wake_prod)."""
    from .identity import IdentityError
    from .voice import wake_prod
    from .voice.wake_train import local_voices, slug, train_phrase
    if getattr(a, "revert", False):
        try:
            r = wake_prod.revert(home, cfg)
        except (IdentityError, OSError) as e:
            print(f"not reverted: {e}", file=sys.stderr)
            return 1
        if r is None:
            print("Nothing to revert: no wake model was applied with `jarvis wake train --apply`.", file=sys.stderr)
            return 1
        now = "the stock Hey Jarvis" if r["model"] == "hey_jarvis" else Path(r["model"]).name
        print(f"Reverted: the wake model is now {now}. Restart JARVES to use it (jarvis stop, then jarvis).")
        return 0
    samples = Path(a.samples) if a.samples else None
    if samples is None and wake_prod.default_samples_dir(home).is_dir()             and any(wake_prod.default_samples_dir(home).glob("*.wav")):
        samples = wake_prod.default_samples_dir(home)           # the owner's recordings, no flag needed
    files = None
    if samples is not None:
        checks = wake_prod.check_samples(samples)
        sm = wake_prod.sample_summary(checks)
        print(f"Your recordings in {samples}: {sm['usable']} usable of {sm['files']}.")
        for c in checks:
            for msg in c.problems:
                print(f"  REJECTED {c.path.name}: {msg}")
            for msg in c.warnings:
                print(f"  warning  {c.path.name}: {msg}")
        if not sm["enough"]:
            print(f"  Fewer than {wake_prod.RECOMMENDED_SAMPLES} usable recordings: the model will lean on synthetic "
                  "voices. Record more phrases in a quiet room, 20-50 cm from the microphone.")
        files = [c.path for c in checks if c.ok]
        if a.check_samples:
            return 0 if sm["minimum"] else 1
        if a.samples and not files:
            print("No usable recording in --samples: nothing trained.", file=sys.stderr)
            return 1
    elif a.check_samples:
        print(f"No recordings found. Put WAV files of you saying the phrase in {wake_prod.default_samples_dir(home)}.",
              file=sys.stderr)
        return 1
    if not a.phrase:
        print('usage: jarvis wake train "hey max" --say "היי מקס" [--samples DIR] [--apply] | --revert | '
              '--check-samples', file=sys.stderr)
        return 2
    phrase = " ".join(a.phrase)
    voices = local_voices()
    print(f"Training a wake phrase for \"{phrase}\" on this device (free, local; nothing is played or sent).")
    print("Voices: " + (", ".join(f"{x.id} ({x.lang})" for x in voices) or "none") +
          (f"; your recordings: {len(files)} usable" if files is not None else ""))
    if not voices and not files:
        print("No local voice found: install a Windows speech voice (Settings > Time & language > Speech) or "
              "espeak-ng (Linux/Pi), or record yourself and pass --samples DIR.", file=sys.stderr)
        return 1
    active = home / "wake" / f"{slug(phrase)}.npz"
    if a.out:
        out = Path(a.out)
        if out.exists() and not a.apply:
            print(f"{out} exists and is not replaced without --apply (the old one is kept in history then).",
                  file=sys.stderr)
            return 1
    else:
        out = wake_prod.candidate_path(home, slug(phrase))      # never the active model: that needs --apply
    try:
        rep = train_phrase(phrase, out, say=a.say, samples=samples, voices=voices, seed=a.seed,
                           sample_files=files, progress=lambda s: print("  " + s, flush=True))
    except (RuntimeError, ImportError, OSError) as e:
        print(f"WAKE_TRAIN_FAILED: {e}", file=sys.stderr)
        return 1
    ev = rep["eval"]
    print(f"\nSaved the candidate {out} ({rep['seconds']} s, seed {rep['seed']}).")
    print(f"Held-out check ({rep['held_out_voice'] or 'random 20 %'}): recall {ev['recall']:.0%}, "
          f"{ev['false_wakes']} false wakes in {ev['hours'] * 60:.0f} min of synthetic speech/noise "
          f"(threshold {rep['threshold']}). Synthetic only: test it with your voice next.")
    print("Calibration (threshold vs misses and false wakes):")
    for line in wake_prod.format_curve(rep["curve"], rep["threshold"]):
        print(line)
    ow = rep["owner"]
    if ow["recordings"]:
        print(f"Your recordings wake it {ow['recall_at_threshold']:.0%} of the time at {rep['threshold']}"
              + (f" (held out, not trained on: {ow['held_out_recall_at_threshold']:.0%})"
                 if ow["held_out_recall_at_threshold"] is not None else "") + ".")
    nm = rep["near_miss"]
    print(f"Near-miss sentences: {len(nm['false_wakes'])} false wakes in {nm['rendered']} renderings "
          f"(worst score {nm['worst_score']}).")
    for w in nm["false_wakes"]:
        print(f"  WOULD WAKE on \"{w['text']}\" ({w['voice']}, score {w['score']})")
    for line in rep["next"]:
        print("  " + line)
    if not a.apply:
        print(f"\nNot applied. The active wake model is unchanged. Use it with: "
              f"jarvis wake train {' '.join(a.phrase)} --apply   (undo later with: jarvis wake train --revert)")
        return 0
    why = wake_prod.gate(rep)
    if why and not a.accept_risk:
        for w in why:
            print(f"not applied: {w}", file=sys.stderr)
        print("Record better samples or retrain; --accept-risk applies it anyway.", file=sys.stderr)
        return 1
    try:
        wake_prod.promote(home, cfg, out, phrase, rep["threshold"], active)
    except (IdentityError, OSError) as e:
        print(f"not applied: {e}", file=sys.stderr)
        return 1
    print(f"Applied: JARVES will wake on \"{phrase}\" after the next start (jarvis stop, then jarvis). "
          "Undo with: jarvis wake train --revert")
    return 0


def cmd_doctor(a, cfg, home) -> int:
    """Check every part and say exactly what to fix. Free and offline unless --live (then ~1 cent, asks first)."""
    import time as _time
    rows: list[tuple[str, str, str]] = []

    def add(level: str, what: str, detail: str = "") -> None:
        rows.append((level, what, detail))
        icon = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]", "info": "[ .. ]"}[level]
        print(f"{icon} {what}" + (f"\n         {detail}" if detail else ""), flush=True)

    print(f"JARVES doctor  (version {__version__}, state dir {home})\n")
    add("ok", f"connection mode: {cfg.ai.connection}")
    key = openai_key_present()
    if cfg.ai.connection in ("api_key", "combined"):
        add("ok" if key else "fail", "OpenAI API key " + ("found" if key else "MISSING"),
            "" if key else "run:  jarvis key")
    from .chatgpt_auth import ChatGPTAuth
    st = ChatGPTAuth(home).status()
    if cfg.ai.connection in ("chatgpt", "combined"):
        ok = st.get("plan_usage")
        add("ok" if ok else "fail", "ChatGPT plan " + (f"connected ({st.get('email') or 'signed in'})" if ok else "NOT connected"),
            "" if ok else "run:  jarvis chatgpt login")
    live = _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status")
    add("info", "Core is " + ("running" if live else "not running (start it with: jarvis)"))
    try:                                     # no silent limits: a used-up or nearly used-up daily cap is a finding
        from . import limits as _limits
        _vs = _limits.views_from_home(home, cfg.ai)
        for _v in _limits.worst(_vs):
            add("fail" if _v["level"] == "exhausted" else "warn", _v["text_en"], "fix:  " + _v["action_en"])
        if not _limits.worst(_vs):
            add("ok", "daily limits have room left (" + "; ".join(v["text_en"] for v in _vs) + ")")
    except Exception as e:
        add("warn", f"could not read the daily limits ({type(e).__name__})")

    # --- R-01: OS limits around approved helper commands and generated-Skill children
    try:
        from . import isolation
        from .health import read_snapshot
        snap = read_snapshot(home / "health.json") or {} if live else {}
        iso = isolation.row(cfg, (snap.get("components") or {}) if snap.get("age_s", 1e9) < 120 else {})
        add({"active": "ok", "degraded": "warn"}.get(iso["state"], "warn"),
            f"helper isolation: {iso['state']} ({iso['mechanism']}, {iso['source']})",
            iso["reason"] + ("" if iso["state"] != "unavailable" else
                             "  -> helper commands ask for the typed code until it is active"))
    except Exception as e:
        add("warn", f"helper isolation: could not check ({type(e).__name__})")

    # --- microphone: level while you stay quiet (digital silence = hardware/privacy problem)
    try:
        import numpy as np
        import sounddevice as sd
        from .app import _mic_name, _speaker_name
        add("info", f"default microphone: {_mic_name(cfg.voice.input_device)}   speaker: {_speaker_name(cfg.voice.output_device)}")
        rec = sd.rec(int(16000 * 1.5), samplerate=16000, channels=1, dtype="float32", device=cfg.voice.input_device)
        sd.wait()
        peak = float(np.abs(rec).max())
        if peak < 0.0005:
            add("fail", "microphone records SILENCE", "check the mic key/switch/shutter, Windows Settings > Privacy & "
                "security > Microphone (allow desktop apps), and the input device (voice.input_device)")
        else:
            add("ok", f"microphone hears sound (room peak {peak:.4f})")
        if a.sound:
            import numpy as np
            tone = (0.25 * np.sin(np.arange(int(24000 * 0.5)) * 2 * np.pi * 523 / 24000)).astype("float32")
            sd.play(tone, 24000, device=cfg.voice.output_device)
            sd.wait()
            add("info", "played a short beep on the speakers: did you hear it?")
    except Exception as e:
        add("fail", f"audio check failed: {type(e).__name__}: {str(e)[:100]}", "uv sync --extra voice; check the audio devices")

    # --- wake word
    try:
        from .voice.wakeword import make_detector
        ok, msg = make_detector(cfg.voice.wake_word, cfg.voice.wake_threshold).check()
        add("ok" if ok else "fail", "wake word model: " + msg, "" if ok else "uv sync --extra voice")
        add("info", f"wake threshold {cfg.voice.wake_threshold}: if \"Hey Jarvis\" is not recognised run  jarvis wake test")
    except Exception as e:
        add("fail", f"wake word: {type(e).__name__}: {str(e)[:100]}")

    # --- music
    if cfg.music.provider == "pear":
        import httpx
        try:
            httpx.get(cfg.music.pear_url + "/api/v1/song", timeout=2)
            paired = (home / "pear_client_id.paired").exists()
            add("ok" if paired else "warn", "Pear Desktop is running" + ("" if paired else " but NOT paired"),
                "" if paired else "run:  jarvis music pair  (and click Allow in Pear)")
        except httpx.HTTPError:
            add("warn", "Pear Desktop is not running (music unavailable)", "run:  jarvis music open")

    # --- optional real (billable) GPT-Live smoke test
    if a.live:
        if not key:
            add("fail", "live smoke test needs an API key", "run:  jarvis key")
        else:
            print("\nLIVE test: opens ONE real GPT-Live session for ~8 seconds, no audio. Cost: about 1 cent of your API credit.")
            if input("Type yes to run it: ").strip().lower() == "yes":
                asyncio.run(_live_smoke(cfg, add))
            else:
                add("info", "live test skipped")
    bad = [r for r in rows if r[0] == "fail"]
    print(f"\n{len(bad)} problem(s) found." if bad else "\nAll checks passed.")
    return 1 if bad else 0


async def _live_smoke(cfg, add) -> None:
    """Real session using the exact production config; reports delegation/validation errors the docs say arrive later."""
    import time as _time
    from .skills import SkillRegistry
    from .skills.builtin import ClockSkill
    from .voice.live import LiveVoice
    reg = SkillRegistry()
    reg.register(ClockSkill())
    problems: list[str] = []

    def on_health(status, detail, action=""):
        if status != "ok":
            problems.append(f"{detail} -> {action}")
    v = cfg.voice
    lv = LiveVoice(reg, lambda: "test", model=v.live_model, voice=v.voice, backend_model=cfg.ai.text_model,
                   web_search=cfg.ai.web_search, name=cfg.assistant_name, half_duplex=True, max_session_s=20,
                   audio_io=None, on_health=on_health)
    t0 = _time.monotonic()
    try:
        await lv.start()
        add("ok", f"GPT-Live connected in {(_time.monotonic() - t0):.1f} s ({v.live_model}, voice {v.voice})")
        await asyncio.sleep(4)                       # delegation settings are validated AFTER session.started
        await lv.stop()
        if problems:
            add("fail", "GPT-Live rejected a setting: " + problems[0])
        else:
            add("ok", f"backend delegation accepted ({cfg.ai.text_model}, web search {'on' if cfg.ai.web_search else 'off'})")
    except Exception as e:
        add("fail", f"GPT-Live could not start: {type(e).__name__}: {str(e)[:160]}",
            "check the key, project access to the live model, billing and the model name in config.toml")


def cmd_say(a, cfg, home) -> int:
    from .budget import Budget
    from .memory import MemoryStore
    from .voice.audio import SoundDeviceAudio
    from .voice.tts import make_tts
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    t = make_tts(cfg, Budget(MemoryStore(home / "memory.db"), cfg.ai))   # same mode + shared budget as the Core
    ok, q, d = t.check()
    if getattr(a, "verbose", False):
        print(f"speech engines: {d}")                      # the full table (every engine, incl. the ones that failed)
    if not ok:
        print(f"ERROR: no speech engine can speak. {d} -> run `jarvis key` (cloud voice) or add a Hebrew voice in "
              f"Settings > Time & language > Speech", file=sys.stderr)
        return 1
    text = " ".join(a.text)

    async def go():
        s, r = await t.synth(text)
        eng = next((e for e in t.usable if e.name == t.last_used), t.usable[0])
        print(f"Speaking with {eng.name} ({eng.check()[1]})")
        await SoundDeviceAudio(output_device=cfg.voice.output_device).play(s, r)
    try:
        asyncio.run(go())
    except Exception as e:
        print(f"ERROR: speech failed: {getattr(e, 'message', e)}. {getattr(e, 'action', '') or 'run `jarvis say --verbose`'}",
              file=sys.stderr)
        return 1
    return 0


def _history(cfg, home):
    from .store import Store
    return Store(home / "store.db", tier=cfg.ai.voice_service_tier, backend_model=cfg.ai.text_model)


def _log_files(home: Path) -> list[Path]:
    """jarves.log and its rotations, oldest first."""
    from .logview import log_files
    return log_files(home)


def cmd_cost(a, cfg, home) -> int:
    from .store import daily_costs, session_cost
    st = _history(cfg, home)
    try:
        days = daily_costs(st, cfg.pricing, a.days)
        print(f"JARVES cost ESTIMATE, last {a.days} days (prices: [pricing], checked 2026-10-19; your OpenAI bill "
              f"is the truth)")
        if not days:
            print("  no conversations recorded yet (history starts with this version; "
                  "`jarvis diagnose --import-log` reads older logs)")
            return 0
        print(f"  {'day':<11} {'conv':>4} {'voice min':>9} {'voice':>8} {'backend':>8} {'typed':>7} {'total':>8}")
        for d in days:
            print(f"  {d['day']:<11} {d['sessions']:>4} {d['voice_minutes']:>9.1f} ${d['voice']:>7.3f} "
                  f"${d['backend']:>7.4f} ${d['text']:>6.4f} ${d['total']:>7.3f}")
        print(f"  {'total':<11} {sum(d['sessions'] for d in days):>4} {sum(d['voice_minutes'] for d in days):>9.1f} "
              f"{'':>27} ${sum(d['total'] for d in days):>7.3f}")
        print("\n  last conversations:")
        for s in st.sessions(a.sessions):
            c = session_cost(st, s, cfg.pricing)
            when = datetime.fromtimestamp(s["started"]).strftime("%Y-%m-%d %H:%M")
            print(f"  {when}  {c['voice_minutes']:>5.1f} min  {c['backend_responses']:>3} backend calls  "
                  f"${c['total']:.3f}{'  (tokens estimated)' if c['estimated_tokens'] else ''}")
    finally:
        st.close()
    return 0


def cmd_diagnose(a, cfg, home) -> int:
    from . import diagnose, selfcheck
    if selfcheck.wants_selfcheck(a):                 # --now / --history / --schedule / --suggestion
        return selfcheck.cli_main(a, cfg, home)
    a.days = a.days or 30
    st = _history(cfg, home)
    try:
        if a.import_log is not None:
            files = [Path(a.import_log)] if a.import_log else _log_files(home)
            skip = st.newest_ts()
            n = sum(len(st.ingest_log(f, skip, backfill=True)) for f in files)
            print(f"imported {n} conversation(s) from {len(files)} log file(s)")
        found = diagnose.run(st, cfg, days=a.days)
        issues = st.issues("open")
        print(f"self-diagnosis: {len(found)} finding(s); {len(issues)} open issue(s)")
        for i in issues:
            print(f"  [{i['severity']:<6}] {i['title']}  (x{i['count']})")
        if issues:
            print("details: `jarvis dashboard` -> Issues")
    finally:
        st.close()
    return 0


def _cli_features(cfg, home):
    from .features import Features
    from .health import read_snapshot
    from .runtime.lock import held
    from .setup_wizard import load_addons
    load_addons()      # add-ons register their switches on import (scheduler, notes, backup...): same list as the Core

    def components() -> dict:
        if not held(home):                 # a fresh snapshot from a stopped Core is not live evidence
            return {}
        snap = read_snapshot(home / "health.json") or {}
        return (snap.get("components") or {}) if snap.get("age_s", 1e9) < 120 else {}
    return Features(cfg, home, components=components)


def cmd_help(a, cfg, home) -> int:
    """jarvis help [topic]: the feature guides as text (Hebrew + English). No topic = the list of topics."""
    from . import guides
    topic = " ".join(a.topic).strip()
    if not topic:
        print(guides.topics_text())
        return 0
    g = guides.find(topic)
    if g is None:
        print(f"unknown topic {topic!r}.\n", file=sys.stderr)
        print(guides.topics_text(), file=sys.stderr)
        return 1
    print(guides.help_text(g))
    return 0


def cmd_features(a, cfg, home) -> int:
    """jarvis features [list | on ID | off ID | reset ID]: the user's switches (the running Core follows them)."""
    from .features import CATALOG
    f = _cli_features(cfg, home)
    if a.action != "list":
        if not a.id or a.id not in CATALOG:
            print(f"unknown feature {a.id!r}. Known: {', '.join(CATALOG)}", file=sys.stderr)
            return 2
        from .features import APPLIES_EN, state_text, switch
        if a.action == "on":
            from .feature_setup import prepare
            ok, detail = prepare(a.id, f, home)
            if not ok:                      # best effort: the switch is still saved (same rule as the dashboard)
                print(f"{a.id}: {detail}", file=sys.stderr)
        s = f.reset(a.id) if a.action == "reset" else switch(f, a.id, a.action == "on", by="cli")
        print(f"{a.id}: {state_text(s, reason=clilang.reason)} (saved; takes effect {APPLIES_EN[s['applies']]})")
        return 0
    for s in f.list(wait=True):
        state = "ACTIVE" if s["active"] else ("off" if not s["enabled"] else "unavailable")
        print(f"{s['id']:<22} {state:<12} {s['risk']:<7} {s['title_en']}"
              + (f"  - {clilang.reason(s['reason'])}" if s["reason"] and not s["active"] else "")
              + (f"  [from {s['source']}]" if s["source"] != "default" else ""))
    try:                                   # R-01: not a switch; the OS limits the helper runs under
        from . import isolation
        iso = isolation.row(cfg, f.ctx.components())
        print(f"{'helper_isolation':<22} {iso['state'].upper():<12} {'status':<7} {iso['mechanism']}: {iso['reason']}"
              f"  [{iso['source']}]")
    except Exception as e:
        print(f"{'helper_isolation':<22} {'unknown':<12} {'status':<7} could not check ({type(e).__name__})")
    print("change: jarvis features on|off|reset ID   (or the dashboard Features page)")
    return 0


def cmd_dashboard(a, cfg, home) -> int:
    if not _cli_features(cfg, home).enabled("dashboard"):
        print("The dashboard is switched off. Turn it on with: jarvis features on dashboard", file=sys.stderr)
        return 1
    from .dashboard import Dashboard
    if a.lan and not _cli_features(cfg, home).enabled("dashboard_lan"):
        print("Home-network access is switched off. Turn it on with: jarvis features on dashboard_lan "
              "(then phones on your Wi-Fi can pair with a code; never reachable from the internet)", file=sys.stderr)
        return 1
    mobile_on, tls, bridge = bool(getattr(a, "mobile", False)), None, None
    tail, tls_src, only_tail = {}, "none", False
    if mobile_on:
        if not _cli_features(cfg, home).enabled("mobile_access"):
            print("Phone access is switched off. Turn it on with: jarvis features on mobile_access "
                  "(then phones on your Wi-Fi can pair with a QR code; never reachable from the internet)", file=sys.stderr)
            return 1
        from . import mobile as _mobile
        cert, key = _mobile.tls_paths(home)
        if getattr(a, "tls_selfsigned", False) and not cert.is_file():
            from .dashboard_auth import lan_ip
            _mobile.make_selfsigned(home, lan_ip())
        from . import tailnet as _tn
        tail = _tn.detect()                              # detection only: JARVES never installs or logs in to Tailscale
        tls, tls_src = _tn.choose_tls(home, tail)
        only_tail = bool(getattr(a, "tailnet_only", False)) or _mobile.MobileAccess(home).settings()["bind_tailnet_only"]
    try:
        dash = Dashboard(home, cfg, a.port, lan=a.lan, mobile_on=mobile_on, tls=tls, tailnet=tail, tls_source=tls_src,
                         bind_tailnet_only=only_tail)
    except OSError as e:
        print(f"cannot start the dashboard: {e}", file=sys.stderr)
        return 1
    print(f"JARVES dashboard (the link holds a private key for THIS computer, valid until the dashboard stops; do not share it):\n  {dash.url}",
          flush=True)
    if dash.lan_url:
        print(f"Home network: on your phone open {dash.lan_url} and enter the pairing code from the Devices page "
              "(on this computer). If Windows asks, allow PRIVATE networks only.", flush=True)
    if mobile_on:
        print(f"Phone access is ON ({'https' if tls else 'http, text only: voice needs TLS, use --tls-selfsigned'}) "
              f"on {dash.mobile.host}:{dash.port}. Press 'Pair a phone' on the dashboard Phone page for the QR code. "
              "Standalone dashboard: chat/voice answer only inside a running Core.", flush=True)
    if mobile_on and tail.get("present"):
        print(f"Tailscale: {dash.mobile.scheme}://{dash.mobile.tailnet_host()}:{dash.port}/m/ "
              f"({'listening on the tailnet address only' if only_tail else 'also reachable from your tailnet'}).", flush=True)
    elif mobile_on:
        print("Tailscale was not found on this computer (optional). Setup guide: docs/PHONE_ACCESS_TAILSCALE.md", flush=True)
    print("Ctrl+C to stop.", flush=True)
    if not a.no_open:
        import webbrowser
        page = str(getattr(a, "page", "") or "")
        webbrowser.open(dash.url + (page if re.fullmatch(r"#/[a-z0-9_/-]{1,40}", page) else ""))
    try:
        dash.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dash.stop()
    return 0


def cmd_update(a, cfg, home) -> int:
    """jarvis update [check | install | status]: notify + ask approval (D16); never installs on its own."""
    from .features import Features
    from .updates import UpdateChecker, UpdateError, launch_installer, set_channel, version_line
    if getattr(a, "channel", None):
        try:
            ch = set_channel(home, cfg, a.channel)
        except UpdateError as e:
            print(f"{e.code}: {e.message}")
            return 1
        print(clilang.fmt(f"ערוץ העדכונים: {'בטא (כל בנייה ירוקה של main)' if ch == 'beta' else 'יציב'}.",
                          f"Update channel: {'beta (every green main build)' if ch == 'beta' else 'stable'}."))
    u = UpdateChecker(home, cfg=cfg)
    print(version_line(home) + f"; update channel {u.channel}")
    if getattr(a, "rollback", False):
        return _update_rollback(a, cfg, home)
    if a.action == "status":
        s = u.status()
    elif not Features(cfg, home).enabled("update_check"):
        print("The update check is switched off. Turn it on with: jarvis features on update_check")
        return 1
    else:
        s = u.check(force=True)
    if s["error"]:
        print(f"Update check unavailable: {s['error']}")
        return 1
    if not s["available"]:
        print(f"Up to date (checked {s['checked_at'] or 'never'}).")
        return 0
    rel = s["latest"]
    print(f"New version {rel['version']} ({rel['published'] or ''})  {rel['page'] or ''}")
    if a.action != "install":
        print("Install it with:  jarvis update install   (asks first; your data is kept and backed up)")
        return 0
    print("This downloads the installer, checks its SHA256, backs up your data, stops JARVES and opens the setup "
          "window.")
    if not a.yes and input("Type yes to continue: ").strip().lower() != "yes":
        print("Not installed.")
        return 1
    try:
        r = u.prepare_install(approved=True)
    except UpdateError as e:
        print(f"{e.code}: {e.message}")
        return 1
    print(f"Verified {Path(r['installer']).name}; backup in {r['backup']}")
    if _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status") is not None:
        cmd_stop(a, cfg, home)
    try:
        launch_installer(r["installer"], verified=r, silent=a.silent, restart=a.silent)
    except UpdateError as e:
        print(f"{e.code}: {e.message}")
        return 1
    if a.silent:
        print("Setup runs silently and starts JARVES again at the end. If it fails to start, "
              "`jarvis update --rollback` goes back to the previous version.")
    else:
        print("Setup is open. Follow it; JARVES can start again at the end.")
    return 0


def _update_rollback(a, cfg, home) -> int:
    """jarvis update --rollback: reinstall the previous version and restore the data from before the update."""
    from .updates import UpdateError, rollback, rollback_info
    info = rollback_info(home)
    if not info["possible"]:
        print("Nothing to roll back to: no previous version is kept (an update has not been installed by JARVES yet).")
        return 1
    print(f"Rolling back to {info['version']}: restores your data from before the update ({info['backup'] or 'no backup'}),"
          " stops JARVES and starts setup.")
    if not a.keep_data:
        print("Anything you changed after the update is kept in a safety backup, not in the restored data.")
    if not a.yes and input("Type yes to continue: ").strip().lower() != "yes":
        print("Not rolled back.")
        return 1
    if _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status") is not None:
        cmd_stop(a, cfg, home)
    try:
        r = rollback(home, approved=True, restore_data=not a.keep_data, silent=not a.window, port=cfg.link.port)
    except UpdateError as e:
        print(f"{e.code}: {e.message}")
        return 1
    print(f"Verified {Path(r['installer']).name}" + (f"; data restored from {r['restored']}" if r["restored"] else "")
          + ". Setup is starting.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):      # Hebrew output on Windows consoles
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    from . import console_rtl
    console_rtl.install(sys.argv[1:] if argv is None else list(argv))   # Hebrew in consoles without bidi (--no-rtl-fix / JARVES_CONSOLE_RTL)
    ap = argparse.ArgumentParser(prog="jarves", description="JARVES assistant Core")
    ap.add_argument("--no-rtl-fix", action="store_true", help="print Hebrew in logical order (consoles that do their own bidi)")
    ap.add_argument("--config", type=Path, help="config.toml (default: <JARVES_HOME>/config.toml)")
    ap.add_argument("--profile", metavar="NAME", help="use this profile (its own memory, conversations, costs, "
                                                      "settings, keys); also JARVES_PROFILE. See: jarvis profile list")
    from .updates import version_line
    ap.add_argument("--version", action="version", version=version_line())
    sub = ap.add_subparsers(dest="cmd", required=True)
    def log_opts(p):
        p.add_argument("--log-level", type=str.upper, choices=logs.LEVELS, help="terminal log level")
        p.add_argument("--module", help="show only these modules, comma separated (e.g. voice,wake,ai)")
        p.add_argument("--hide", help="hide these modules, comma separated (e.g. link)")
    run = sub.add_parser("run", help="start the Core (Godot face connects to it)")
    log_opts(run)
    run.add_argument("--desktop", action="store_true", help="show loading progress, prepare voice, then open the room")
    run.add_argument("--background", action="store_true", help="no console, no window: tray + wake word (autostart)")
    run.add_argument("--show", action="store_true", help="with --background: also open the window")
    sta = sub.add_parser("start", help="start JARVES in the background (tray, wake word) and return")
    sta.add_argument("--show", action="store_true", help="also open the window")
    sta.add_argument("--wait", type=int, default=90, help="seconds to wait for the Core to answer")
    sub.add_parser("show", help="open the JARVES window (the Core keeps running)")
    sub.add_parser("hide", help="close the JARVES window (the Core keeps running)")
    tk = sub.add_parser("talk", help="start a live voice conversation now, without the wake word (tests voice)")
    tk.add_argument("--stop", action="store_true", help="end the open conversation")
    au = sub.add_parser("autostart", help="start JARVES invisibly at login: on / off / status")
    au.add_argument("action", choices=["on", "off", "status"])
    lg = sub.add_parser("logs", help="show the log (filter by --log-level / --module; -f to follow)")
    log_opts(lg)
    lg.add_argument("-f", "--follow", action="store_true")
    lg.add_argument("-n", "--lines", type=int, default=50)
    lg.add_argument("--until-stopped", action="store_true", help="finish following when the Core stops")
    sub.add_parser("stop", help="orderly shutdown of the running Core")
    stp = sub.add_parser("status", help="live health from the running Core, or the last stale snapshot",
                         description="Exit codes: 0 ready; 1 the Core is not running; 3 degraded (a component has a "
                                     "problem); 4 ready except that the Godot face is not connected (headless run).")
    stp.add_argument("--brief", action="store_true", help="only the one-line runtime summary")
    d = sub.add_parser("diag", help="write a redacted support bundle (zip)")
    d.add_argument("--out")
    d.add_argument("--lines", type=int, default=500)
    d.add_argument("--max-mb", type=float, default=5.0, help="size limit of the bundle (default 5 MB)")
    d.add_argument("--ci", action="store_true", help="CI: bundle of the TEST home + health summary into --out (a "
                                                     "folder), scanned for secrets; a hit = exit 2, nothing kept")
    m = sub.add_parser("memory", help="list / delete / clear what JARVES remembers")
    m.add_argument("action", choices=["list", "delete", "clear"])
    m.add_argument("id", nargs="?")
    m.add_argument("--yes", action="store_true")
    co = sub.add_parser("contacts", help="remember relationships locally / search opt-in Google Contacts")
    cs = co.add_subparsers(dest="action", required=True)
    cr = cs.add_parser("remember", help="save an explicitly provided relationship and email locally")
    cr.add_argument("label", help='relationship or alias, e.g. "my wife"')
    cr.add_argument("--email", required=True)
    cr.add_argument("--name", default="")
    cl = cs.add_parser("lookup", help="look up remembered relationship/name/email")
    cl.add_argument("query")
    cf = cs.add_parser("forget", help="delete one saved contact after confirmation")
    cf.add_argument("id", type=int, help="memory_id from lookup")
    cg = cs.add_parser("search", help="search Google Contacts (requires contacts consent and feature)")
    cg.add_argument("query")
    cg.add_argument("--limit", type=int, default=5)
    cal = sub.add_parser("calendar", help="find/create events or invite guests, with exact confirmation")
    cals = cal.add_subparsers(dest="action", required=True)
    cc = cals.add_parser("create", help="create an event; --guest sends a Calendar invitation after approval")
    cc.add_argument("--title", required=True)
    cc.add_argument("--day", required=True)
    cc.add_argument("--start", help="HH:MM; omitted = all day")
    cc.add_argument("--duration", type=int, default=60, help="minutes")
    cc.add_argument("--calendar", default="primary")
    cc.add_argument("--guest", action="append", default=[], help="plain email; repeat for more guests")
    for action in ("find", "share"):
        cp = cals.add_parser(action, help="find events" if action == "find" else "choose an event and invite guests")
        cp.add_argument("--day", default="week")
        cp.add_argument("--query", default="")
        if action == "share":
            cp.add_argument("--guest", action="append", required=True)
            cp.add_argument("--scope", choices=["this", "series"], help="required choice for a recurring event")
    mail = sub.add_parser("mail", help="draft mail and optionally send after exact confirmation")
    ms = mail.add_subparsers(dest="action", required=True)
    md = ms.add_parser("draft", help="create a Gmail draft; does not send")
    md.add_argument("--to", required=True)
    md.add_argument("--subject", required=True)
    md.add_argument("--body", required=True)
    md.add_argument("--send", action="store_true", help="also offer read-back and sending in this session; asks confirmation")
    mu = sub.add_parser("music", help="Pear Desktop pairing and manual control")
    mu.add_argument("action", choices=["open", "pair", "status", "play", "pause", "resume", "next"])
    mu.add_argument("query", nargs="?", default="")
    g = sub.add_parser("chatgpt", help="connect / disconnect your ChatGPT plan (Sign in with ChatGPT)")
    g.add_argument("action", choices=["login", "logout", "status", "models"])
    g.add_argument("--forget", action="store_true", help="logout: also forget this app registration")
    from .google_auth import add_cli as add_google_cli, cmd_google
    add_google_cli(sub)                       # WP-G: jarvis google login / status / logout
    from .backup import add_cli as add_backup_cli, cmd_backup, cmd_restore
    from .usage import add_cli as add_usage_cli, cmd_usage
    add_backup_cli(sub)                       # SW-V2 M5: jarvis backup / restore
    add_usage_cli(sub)                        # SW-V2 M5: jarvis usage
    from .browser import add_cli as add_browser_cli, cmd_browser
    add_browser_cli(sub)                      # SW-V3 P1.2: jarvis browser status / sites / allow / remove
    from .telegram_channel import add_cli as add_telegram_cli, cmd_telegram
    from .homeassistant import add_cli as add_home_cli, cmd_home
    from .instagram_channel import add_cli as add_instagram_cli, cmd_instagram
    add_instagram_cli(sub)                    # V3 P1: jarvis instagram start / status / test / logout (+ serve, refresh)
    add_telegram_cli(sub)                     # V3 P1: jarvis telegram start / status / test / logout
    add_home_cli(sub)                         # V3 P1: jarvis home start / status / test / logout
    from .connect import add_cli as add_connect_cli, cmd_connect
    add_connect_cli(sub)                      # owner request 20 Oct: jarvis connect (guided setup of every account)
    from .approvals import add_cli as add_approvals_cli, cmd_approvals
    add_approvals_cli(sub)                    # V3: jarvis approvals list / approve / deny / history
    wk = sub.add_parser("wake", help="calibrate the wake word to your voice and mic, or train a new wake phrase")
    wk.add_argument("action", choices=["test", "train", "eval"],
                    help="test = live check of your voice + mic with an exact fix; train = new phrase (candidate; --apply to use, --revert to undo); "
                         "eval = measured detection rate / false wakes per hour on a synthetic corpus")
    wk.add_argument("--list-mics", action="store_true", help="test: list the input devices and exit")
    wk.add_argument("--openai", action="store_true", help="eval: add OpenAI TTS voices (metered once, cached)")
    wk.add_argument("--minutes", type=float, default=24.0, help="eval: minutes of non-wake audio (default 24)")
    wk.add_argument("phrase", nargs="*", help="train: the new phrase in Latin letters, e.g. \"hey max\"")
    wk.add_argument("--seconds", type=int, default=20)
    wk.add_argument("--apply", action="store_true", help="test: save the recommended threshold; train: use the "
                                                         "new phrase from the next start")
    wk.add_argument("--revert", action="store_true", help="train: undo the last --apply (the previous model and setting; else the standard \"Hey Jarvis\") "
                                                          "(the trained file is kept)")
    wk.add_argument("--model", help="test: a trained .npz / .tflite instead of the current wake model")
    wk.add_argument("--false-wakes", type=int, default=0, metavar="MIN",
                    help="test: count false wakes over MIN minutes of normal room audio")
    wk.add_argument("--say", action="append", help="train: another way to say it (e.g. the Hebrew spelling); repeat")
    wk.add_argument("--samples", help="train: a folder of WAV recordings of YOU saying the phrase (recommended)")
    wk.add_argument("--out", help="train: output .npz (default <state_dir>/wake/candidates/<phrase>.npz)")
    wk.add_argument("--check-samples", action="store_true",
                    help="train: only check the quality of your recordings (default folder <state_dir>/wake/samples)")
    wk.add_argument("--seed", type=int, default=7, help="train: random seed (same seed + same samples = same model)")
    wk.add_argument("--accept-risk", action="store_true",
                    help="train: apply even if a near-miss sentence or your held-out recordings fail the check")
    dr = sub.add_parser("doctor", help="check microphone, wake word, keys, music; say what to fix")
    dr.add_argument("--sound", action="store_true", help="also play a beep on the speakers")
    dr.add_argument("--live", action="store_true", help="also open one real GPT-Live session (~1 cent, asks first)")
    su = sub.add_parser("setup", help="setup wizard: language, connection + key, name, wake phrase, voice, every "
                                      "add-on on/off (re-run any time to change)")
    su.add_argument("--if-needed", action="store_true", help="only on the first run")
    su.add_argument("--answers", metavar="FILE", help="non-interactive: apply a JSON answers file (no secrets in it)")
    su.add_argument("--defaults", action="store_true", help="non-interactive: take every recommended answer")
    su.add_argument("--lang", choices=["he", "en"], help="wizard language (default Hebrew)")
    su.add_argument("--restart", action="store_true", help="forget the progress of an interrupted run")
    su.add_argument("--yes", "-y", action="store_true", help="non-interactive: keep every existing setting and secret, "
                    "take the safe defaults for the rest (never overwrites)")
    su.add_argument("--no-rtl-fix", action="store_true", default=argparse.SUPPRESS,
                    help="print Hebrew in logical order (same as JARVES_CONSOLE_RTL=logical)")
    su.add_argument("--check-addons", action="store_true",
                    help="check that every add-on's code, models and data are in this install (no sensors, no network)")
    su.add_argument("--json", action="store_true", help="with --check-addons: machine-readable output")
    k = sub.add_parser("key", help="save your OpenAI API key in a private file (secrets.env)")
    k.add_argument("--remove", action="store_true")
    k.add_argument("--show-path", action="store_true")
    k.add_argument("--admin", action="store_true", help="the optional read-only Admin key (real monthly API bill)")
    ch = sub.add_parser("chat", aliases=["text"], help="silent text chat in this terminal with the running JARVES "
                        "(no microphone, no speech; /help inside; starts the Core if needed)")
    ch.add_argument("--silent", action="store_true", help="never speak, chime or light the face in this session "
                    "(automatic during quiet hours)")
    ch.add_argument("--verbose", "-v", action="store_true", help="show codes and details on tool and approval lines")
    ch.add_argument("--wait", type=float, default=60.0, help="seconds to wait for the Core to start")
    q = sub.add_parser("ask", help="ask one question (brain + Skills, no Godot)")
    ex = sub.add_parser("execute", help="run a text request with JARVES stopped; Google/memory/tools, terminal confirmations")
    ex.add_argument("text", nargs="+", help='request, e.g. "add a calendar event tomorrow at 10"')
    ex.add_argument("--interactive", "-i", action="store_true", help="keep this text session for follow-up answers; Enter exits")
    q.add_argument("text", nargs="+")
    s = sub.add_parser("say", help="speak a sentence with the configured voice")
    s.add_argument("text", nargs="+")
    s.add_argument("--verbose", "-v", action="store_true", help="show every speech engine and its state")
    co =sub.add_parser("cost", help="estimated cost per day and per conversation")
    co.add_argument("--days", type=int, default=14)
    co.add_argument("--sessions", type=int, default=10)
    db = sub.add_parser("dashboard", help="local web dashboard: conversations, costs, issues, memory, skills")
    db.add_argument("--port", type=int, default=0, help="default: a free port")
    db.add_argument("--no-open", action="store_true", help="do not open the browser")
    db.add_argument("--page", default="", help="open this dashboard page, e.g. #/setup (Setup and tour)")
    db.add_argument("--lan", action="store_true", help="also allow paired phones on the home network "
                                                       "(needs: jarvis features on dashboard_lan)")
    db.add_argument("--mobile", action="store_true", help="also serve the phone app (needs: jarvis features on "
                                                          "mobile_access); pair with a QR code")
    db.add_argument("--tailnet-only", action="store_true", help="with --mobile: listen on the Tailscale address only "
                                                                  "(never on all interfaces); needs Tailscale running")
    db.add_argument("--tls-selfsigned", action="store_true", help="with --mobile: make a self-signed certificate so the "
                                                                  "phone may use the microphone")
    fe = sub.add_parser("features", help="feature switches: list / on ID / off ID / reset ID; on installs supported "
                                        "locked prerequisites before enabling (stop Core for dependency installation)")
    fe.add_argument("action", nargs="?", default="list", choices=["list", "on", "off", "reset"])
    fe.add_argument("id", nargs="?")
    hp = sub.add_parser("help", help="explain a topic: jarvis help timers / google / music (no topic = the list)")
    hp.add_argument("topic", nargs="*", help="a topic id or a word in Hebrew or English")
    dg = sub.add_parser("diagnose", help="self-diagnosis: find problems in recent conversations")
    dg.add_argument("--days", type=int, default=None, help="look back N days (conversations: 30, self-check: 7)")
    dg.add_argument("--import-log", nargs="?", const="", default=None, metavar="PATH",
                    help="first read conversations from jarves.log (default: the current log and its rotations)")
    from .selfcheck import add_cli_args as add_selfcheck_args
    add_selfcheck_args(dg)                    # owner request 20 Oct: jarvis diagnose --now (self-check)
    up = sub.add_parser("update", help="check GitHub for a newer JARVES; install only after you approve")
    up.add_argument("action", nargs="?", default="check", choices=["check", "install", "status"])
    up.add_argument("--channel", choices=["stable", "beta"], help="choose the update channel: stable (default, "
                    "tested releases) or beta (every green main build, early); then check on it")
    up.add_argument("--yes", action="store_true", help="install / rollback: you already approved (no question)")
    up.add_argument("--silent", action="store_true", help="install: run setup silently (no window) and start JARVES again")
    up.add_argument("--rollback", action="store_true", help="go back to the previous version and restore the data "
                                                              "from before the update")
    up.add_argument("--keep-data", action="store_true", help="rollback: reinstall the program only, keep current data")
    up.add_argument("--window", action="store_true", help="rollback: show the setup window instead of running silently")
    from . import profiles
    profiles.add_cli(sub)                     # Issue #17: jarvis profile list / create / use / delete / export
    from . import wizard
    wizard.add_cli(sub)                       # owner issues #58/#60/#61: jarvis tour (Setup and tour wizard)
    from . import release_cmd
    release_cmd.add_cli(sub)                  # jarvis release signoff (stable release manual test sign-off)
    from . import release_notes
    release_notes.add_cli(sub)                # jarvis release-notes: what changed per version, HTML page
    from . import cli_parity
    cli_parity.add_cli(sub)                   # V4 P0.3: jarvis timers / lists / notes / jobs / reports
    from . import budget_cli
    budget_cli.add_cli(sub)                   # SW-V4 D (#14): jarvis budget status / test-cap / set / reset-ladder
    from . import recall
    from . import privacy as privacy_cli, phone_cli
    privacy_cli.add_cli(sub)                  # OD-12: jarvis privacy status | full-text on|off
    phone_cli.add_cli(sub)                    # V5: jarvis phone status | approvals | revoke-all | bind
    recall.add_cli(sub)                       # SW-V4 G: jarvis recall "query" [--since] [--json]
    from .diag import service as health_cli
    health_cli.add_cli(sub)                   # SW-V4 O: jarvis health [report|remediate|ticket|ai-review|canary|...]
    from . import selfspeed
    selfspeed.add_cli(sub)                    # owner request 21 Oct: jarvis selftest [--deep] / jarvis speedtest
    from . import issue_registry
    issue_registry.add_cli(sub)               # owner request 21 Oct: jarvis issues [list|show|report|ignore|fixed|add]
    from . import web_cli
    web_cli.add_cli(sub)                      # owner request 21 Oct: jarvis web (the dashboard Chat page)
    from . import whatsapp_admin
    whatsapp_admin.add_cli(sub)               # SW-V4 F: jarvis whatsapp status / link / mode / allow / kill ...
    from .orchestrator import surfaces as agents_cli
    agents_cli.add_cli(sub)                   # SW-V4 E: jarvis agents list | status | ask | tell | start | ...
    from .projectmgr import surfaces as project_cli
    project_cli.add_cli(sub)                  # project manager: jarvis project open | plan | status | add | talk | run ...
    from . import dialing
    dialing.add_cli(sub)                      # SW-V4 H: jarvis dial <name|number> [--yes-on-screen]
    from . import self_manage
    self_manage.add_cli(sub)                  # jarvis manage status|file|memory|feature|skill|setting|connect|history|undo
    a = ap.parse_args(argv)
    try:
        if a.profile:
            profiles.select(a.profile)        # this process and every child it starts use that profile
        home = state_dir()
        profiles.prepare_env(profiles.current_name())     # a non-default profile never inherits the parent's keys
    except profiles.ProfileError as e:
        print(f"PROFILE ERROR: {e}", file=sys.stderr)
        return 2
    load_secrets(home)
    from . import clilang
    clilang.set_home(home)
    try:
        cfg, _ = load_config(a.config)
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    return {"run": cmd_run, "stop": cmd_stop, "status": cmd_status, "diag": cmd_diag, "memory": cmd_memory, "music": cmd_music,
            "contacts": cmd_personal, "calendar": cmd_personal, "mail": cmd_personal,
            "execute": cmd_execute, "chat": cmd_chat, "text": cmd_chat,
            "ask": cmd_ask, "say": cmd_say, "chatgpt": cmd_chatgpt, "setup": cmd_setup, "logs": cmd_logs, "key": cmd_key,
            "wake": cmd_wake, "doctor": cmd_doctor, "cost": cmd_cost, "dashboard": cmd_dashboard,
            "diagnose": cmd_diagnose, "features": cmd_features, "help": cmd_help, "google": cmd_google,
            "backup": cmd_backup, "restore": cmd_restore, "usage": cmd_usage, "start": cmd_start,
            "talk": cmd_talk, "show": cmd_display, "hide": cmd_display, "autostart": cmd_autostart,
            "update": cmd_update, "browser": cmd_browser, "connect": cmd_connect, "telegram": cmd_telegram, "instagram": cmd_instagram, "home": cmd_home,
            "profile": profiles.cmd_profile, "approvals": cmd_approvals, "recall": recall.cmd_recall,
            "health": health_cli.cmd_health,
            "whatsapp": whatsapp_admin.cmd_whatsapp,
            "agents": agents_cli.cmd_agents, "project": project_cli.cmd_project,
            "dial": dialing.cmd_dial, "release": release_cmd.cmd_release, "tour": wizard.cmd_tour,
            "manage": self_manage.cmd_manage, "undo": self_manage.cmd_manage,
            **cli_parity.COMMANDS, **budget_cli.COMMANDS, **privacy_cli.COMMANDS, **phone_cli.COMMANDS, **selfspeed.COMMANDS,
            **issue_registry.COMMANDS, **web_cli.COMMANDS, **release_notes.COMMANDS}[a.cmd](a, cfg, home)


if __name__ == "__main__":
    sys.exit(main())
