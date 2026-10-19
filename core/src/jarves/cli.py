"""jarves command line: run the Core and offline troubleshooting tools (work even when the Core is down)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, logs
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


def cmd_run(a, cfg, home) -> int:
    from .app import Core
    from .runtime.lock import AlreadyRunning, InstanceLock
    background = getattr(a, "background", False)
    if background:
        _quiet_streams()
    _apply_log_args(a, cfg)
    mode = "background" if background else ("desktop" if getattr(a, "desktop", False) else "foreground")
    lock = InstanceLock(home)
    try:
        lock.acquire(mode)              # one Core per user: autostart, `jarvis start` and `jarvis` never double up
    except AlreadyRunning as e:
        print(f"{e}. Use `jarvis status`, `jarvis show` or `jarvis stop`.", file=sys.stderr)
        if getattr(a, "show", False):
            _ws_call(home, cfg.link.port, {"type": "runtime.set", "payload": {"display": "show"}}, "runtime.state")
        return EXIT_ALREADY_RUNNING
    if not background:
        print(f"JARVES Core starting. Terminal log: {cfg.log.console_level}"
              + (f", modules {','.join(cfg.log.modules)}" if cfg.log.modules else "")
              + (f", hiding {','.join(cfg.log.hide)}" if cfg.log.hide else "")
              + ". Stop: Ctrl+C or `jarvis stop`.", file=sys.stderr)
    core = Core(cfg, home, console_logs=not background, runtime_mode=mode)
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
        r.fields = {k: v for k, v in d.items() if k not in ("ts", "level", "component", "code", "msg", "session", "cid")}
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


def cmd_status(a, cfg, home) -> int:
    live = _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status")
    if live is None:
        from .runtime import read_state
        snap = read_snapshot(home / "health.json")
        print("Core is NOT running (no answer on the local link).")
        prefs = read_state(home).get("prefs") or {}
        if prefs:
            print(f"Saved choices: window {'hidden' if prefs.get('display_hidden') else 'shown'}, mic "
                  f"{'muted' if prefs.get('mic_muted') else 'on'}, alerts {'paused' if prefs.get('alerts_paused') else 'on'}")
        if snap:
            print(f"Last snapshot is {snap['age_s']} s old (stale, not current truth):")
            print(json.dumps(snap, indent=1, ensure_ascii=False))
        return 1
    print(runtime_summary(live.get("runtime")))
    if not getattr(a, "brief", False):
        print(json.dumps(live, indent=1, ensure_ascii=False))
    return 0 if live["core"] == "ready" else 3


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
    Features(cfg, home).set("autostart", a.action == "on", by="cli")     # the dashboard shows the same choice
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
    return private_line(line)


def cmd_diag(a, cfg, home) -> int:
    """User-initiated, redacted support bundle (no secrets, no transcripts, no audio)."""
    from . import diagbundle                    # SW-V2 M5: sanitized, size-bounded bundle
    out = Path(a.out or home / f"jarves-diag-{datetime.now():%Y%m%d-%H%M%S}.zip")
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
        print("deleted" if m.delete(int(a.id)) else "not found")
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
    reg.confirm = confirm

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
        return 0 if result.ok else 1
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


CONNECTIONS = {"1": "chatgpt", "2": "combined", "3": "api_key"}
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
    """First-run wizard: choose who pays for AI, connect ChatGPT if needed."""
    from .chatgpt_auth import ChatGPTAuth
    path = a.config or home / "config.toml"
    if a.if_needed and (home / "setup_done").exists():
        conn = cfg.ai.connection
        if conn in ("chatgpt", "combined") and not ChatGPTAuth(home).status()["plan_usage"]:
            print("ChatGPT is not connected, so JARVES starts with offline commands only.\n"
                  "Reconnect any time:  jarvis chatgpt login   (or change mode: jarvis setup)")
        if conn in ("combined", "api_key") and not openai_key_present():
            print("No OpenAI API key saved, so live voice is off.  Add it:  jarvis key")
        return 0
    print("How should JARVES connect?\n"
          "  1  My ChatGPT plan          (no extra cost, simple voice)\n"
          "  2  ChatGPT plan + API key   (best voice, voice billed to the key)\n"
          "  3  API key only             (best everything, billed to the key)")
    choice = input("Choose 1, 2 or 3 [1]: ").strip() or "1"
    conn = CONNECTIONS.get(choice)
    if conn is None:
        print("Please run again and type 1, 2 or 3.")
        return 1
    set_connection(path, conn)
    if conn in ("combined", "api_key") and not openai_key_present():
        if _ask_key(home) != 0:
            print("No key saved. JARVES runs offline until you run:  jarvis key")
    if conn in ("combined", "api_key"):
        print(SPEND_LIMIT_ADVICE)
    if conn in ("chatgpt", "combined") and not ChatGPTAuth(home).status()["plan_usage"]:
        print("Now sign in to ChatGPT in the browser window and click Allow.")
        a.action, a.forget = "login", False
        cfg.ai.connection = conn
        if cmd_chatgpt(a, cfg, home) != 0:
            return 1
    (home / "setup_done").write_text(conn, encoding="utf-8")
    print(f"Done: connection = {conn}. Change it any time with:  jarvis setup")
    return 0


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
    if not key.startswith("sk-admin-"):
        print("That does not look like an OpenAI admin key (it should start with sk-admin-). Nothing saved.")
        return 1
    print(f"saved to {save_secret('OPENAI_ADMIN_KEY', key, home)} (private to your Windows user, not in the project)")
    return 0


def cmd_key(a, cfg, home) -> int:
    """Store / remove the OpenAI API key (or with --admin the read-only Admin key) in <state_dir>/secrets.env."""
    path = secrets_file(home)
    name = "OPENAI_ADMIN_KEY" if getattr(a, "admin", False) else "OPENAI_API_KEY"
    if a.remove:
        if path.exists():
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


def cmd_wake(a, cfg, home) -> int:
    """Calibrate "Hey Jarvis" to YOUR voice and mic: live score bar, then a recommended threshold."""
    import time as _time
    from .voice.audio import AudioUnavailable, StreamingAudio
    from .voice.wakeword import WakeWordUnavailable, level_meter, make_detector
    from .identity import Identity
    Identity(home, cfg).apply_to_config(cfg)            # the chosen wake model / phrase (dashboard, wake train)
    v = cfg.voice
    if a.action == "train":
        return _wake_train(a, cfg, home)
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
        print(f'Say "{phrase}" 3-4 times with a pause between, normally, from where you will sit.  ({a.seconds} s)\n')
        mic = StreamingAudio(v.input_device, v.output_device, rate=16000)
        peaks: list[float] = []
        cur_peak, best, loud = 0.0, 0.0, 0.0
        t_end = _time.monotonic() + a.seconds
        gap = 0
        try:
            async for chunk in mic.mic_chunks(16000, 2560):
                det.feed(chunk)
                sc, lv = det.last_score, level_meter(chunk)
                loud = max(loud, lv)
                if sc > 0.1:
                    cur_peak, gap = max(cur_peak, sc), 0
                else:
                    gap += 1
                    if gap > 8 and cur_peak > 0:                     # ~0.6 s of quiet closes one utterance
                        peaks.append(cur_peak)
                        cur_peak = 0.0
                best = max(best, sc)
                bar = "#" * int(sc * 30)
                mark = "  <-- heard it" if sc >= v.wake_threshold else ""
                print(f"\r mic {'|' * int(lv * 12):<12} wake {sc:4.2f} {bar:<30} best {best:4.2f}{mark}   ", end="", flush=True)
                if _time.monotonic() >= t_end:
                    break
        except AudioUnavailable as e:
            print(f"\n{e.code}: {e.message}. {e.action}")
            return 1
        finally:
            mic.close()
        if cur_peak > 0:
            peaks.append(cur_peak)
        print("\n")
        if loud < 0.05:
            print("The microphone is (almost) silent: check Windows Settings > Privacy & security > Microphone, the "
                  "selected input device, and that it is not muted.  Run `jarvis doctor`.")
            return 1
        if not peaks or best < 0.15:
            print(f"Never heard it (best score {best:.2f}). Try: say it clearer as \"HEY JAR-VIS\", closer to the mic, or "
                  "pick another microphone (voice.input_device in config.toml).")
            return 1
        rec = round(min(0.5, max(0.15, 0.6 * min(peaks))), 2)
        print(f"Heard {len(peaks)} attempt(s), scores {', '.join(f'{x:.2f}' for x in peaks)}.")
        print(f"Recommended wake_threshold = {rec}   (now {v.wake_threshold}).  Lower = easier to trigger, but more false wakes.")
        if a.apply and a.model:
            Identity(home, cfg).set_wake(a.model, threshold=rec, by="cli")
            print(f"Saved: {Path(a.model).name} is now the wake phrase (threshold {rec}). Restart JARVES to use it.")
        elif a.apply:
            set_toml_value(Path(a.config) if a.config else home / "config.toml", "voice", "wake_threshold", str(rec))
            print("Saved. Restart JARVES to use it.")
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


def _wake_train(a, cfg, home) -> int:
    """`jarvis wake train "<phrase>"`: local synthetic samples -> trained head in <state_dir>/wake (voice/wake_train)."""
    from .identity import Identity, IdentityError
    from .voice.wake_train import local_voices, slug, train_phrase
    if not a.phrase:
        print('usage: jarvis wake train "hey max" --say "היי מקס" [--samples DIR] [--apply]', file=sys.stderr)
        return 2
    phrase = " ".join(a.phrase)
    voices = local_voices()
    print(f"Training a wake phrase for \"{phrase}\" on this device (free, local; nothing is played or sent).")
    print("Voices: " + (", ".join(f"{x.id} ({x.lang})" for x in voices) or "none") +
          (f"; your recordings: {a.samples}" if a.samples else ""))
    if not voices and not a.samples:
        print("No local voice found: install a Windows speech voice (Settings > Time & language > Speech) or "
              "espeak-ng (Linux/Pi), or record yourself and pass --samples DIR.", file=sys.stderr)
        return 1
    out = Path(a.out) if a.out else home / "wake" / f"{slug(phrase)}.npz"
    try:
        rep = train_phrase(phrase, out, say=a.say, samples=Path(a.samples) if a.samples else None, voices=voices,
                           progress=lambda s: print("  " + s, flush=True))
    except (RuntimeError, ImportError, OSError) as e:
        print(f"WAKE_TRAIN_FAILED: {e}", file=sys.stderr)
        return 1
    ev = rep["eval"]
    print(f"\nSaved {out} ({rep['seconds']} s).")
    print(f"Held-out check ({rep['held_out_voice'] or 'random 20 %'}): recall {ev['recall']:.0%}, "
          f"{ev['false_wakes']} false wakes in {ev['hours'] * 60:.0f} min of synthetic speech/noise "
          f"(threshold {rep['threshold']}). Synthetic only: test it with your voice next.")
    for line in rep["next"]:
        print("  " + line)
    if a.apply:
        try:
            Identity(home, cfg).set_wake(str(out), phrase, rep["threshold"], by="cli")
        except IdentityError as e:
            print(f"not applied: {e}", file=sys.stderr)
            return 1
        print(f"Applied: JARVES will wake on \"{phrase}\" after the next start (jarvis stop, then jarvis).")
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
    print(f"speech: {q} ({d})")
    if not ok:
        return 1

    async def go():
        s, r = await t.synth(" ".join(a.text))
        await SoundDeviceAudio(output_device=cfg.voice.output_device).play(s, r)
    asyncio.run(go())
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
    from . import diagnose
    st = _history(cfg, home)
    try:
        if a.import_log is not None:
            files = [Path(a.import_log)] if a.import_log else _log_files(home)
            skip = st.newest_ts()
            n = sum(len(st.ingest_log(f, skip)) for f in files)
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

    def components() -> dict:
        if not held(home):                 # a fresh snapshot from a stopped Core is not live evidence
            return {}
        snap = read_snapshot(home / "health.json") or {}
        return (snap.get("components") or {}) if snap.get("age_s", 1e9) < 120 else {}
    return Features(cfg, home, components=components)


def cmd_features(a, cfg, home) -> int:
    """jarvis features [list | on ID | off ID | reset ID]: the user's switches (the running Core follows them)."""
    from .features import CATALOG
    f = _cli_features(cfg, home)
    if a.action != "list":
        if not a.id or a.id not in CATALOG:
            print(f"unknown feature {a.id!r}. Known: {', '.join(CATALOG)}", file=sys.stderr)
            return 2
        if a.action == "on":
            from .feature_setup import prepare
            ok, detail = prepare(a.id, f, home)
            if not ok:
                print(f"{a.id}: {detail}", file=sys.stderr)
                return 1
        s = f.reset(a.id) if a.action == "reset" else f.set(a.id, a.action == "on", by="cli")
        when = {"live": "now", "next_session": "from the next conversation", "restart": "at the next Core start"}
        print(f"{a.id}: {'on' if s['enabled'] else 'off'} (takes effect {when[s['applies']]})"
              + ("" if s["available"] or not s["enabled"] else f"; not available: {s['reason']}"))
        return 0
    for s in f.list():
        state = "ACTIVE" if s["active"] else ("off" if not s["enabled"] else "unavailable")
        print(f"{s['id']:<22} {state:<12} {s['risk']:<7} {s['title_en']}"
              + (f"  - {s['reason']}" if s["reason"] and not s["active"] else "")
              + (f"  [from {s['source']}]" if s["source"] != "default" else ""))
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
    try:
        dash = Dashboard(home, cfg, a.port, lan=a.lan)
    except OSError as e:
        print(f"cannot start the dashboard: {e}", file=sys.stderr)
        return 1
    print(f"JARVES dashboard (the link holds a one-time key for THIS computer, do not share it):\n  {dash.url}",
          flush=True)
    if dash.lan_url:
        print(f"Home network: on your phone open {dash.lan_url} and enter the pairing code from the Devices page "
              "(on this computer). If Windows asks, allow PRIVATE networks only.", flush=True)
    print("Ctrl+C to stop.", flush=True)
    if not a.no_open:
        import webbrowser
        webbrowser.open(dash.url)
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
    from .updates import UpdateChecker, UpdateError, launch_installer, version_line
    u = UpdateChecker(home, cfg=cfg)
    print(version_line())
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
    launch_installer(r["installer"])
    print("Setup is open. Follow it; JARVES can start again at the end.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):      # Hebrew output on Windows consoles
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="jarves", description="JARVES assistant Core")
    ap.add_argument("--config", type=Path, help="config.toml (default: <JARVES_HOME>/config.toml)")
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
    au = sub.add_parser("autostart", help="start JARVES invisibly at login: on / off / status")
    au.add_argument("action", choices=["on", "off", "status"])
    lg = sub.add_parser("logs", help="show the log (filter by --log-level / --module; -f to follow)")
    log_opts(lg)
    lg.add_argument("-f", "--follow", action="store_true")
    lg.add_argument("-n", "--lines", type=int, default=50)
    lg.add_argument("--until-stopped", action="store_true", help="finish following when the Core stops")
    sub.add_parser("stop", help="orderly shutdown of the running Core")
    stp = sub.add_parser("status", help="live health from the running Core, or the last stale snapshot")
    stp.add_argument("--brief", action="store_true", help="only the one-line runtime summary")
    d = sub.add_parser("diag", help="write a redacted support bundle (zip)")
    d.add_argument("--out")
    d.add_argument("--lines", type=int, default=500)
    d.add_argument("--max-mb", type=float, default=5.0, help="size limit of the bundle (default 5 MB)")
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
    wk = sub.add_parser("wake", help="calibrate the wake word to your voice and mic, or train a new wake phrase")
    wk.add_argument("action", choices=["test", "train"])
    wk.add_argument("phrase", nargs="*", help="train: the new phrase in Latin letters, e.g. \"hey max\"")
    wk.add_argument("--seconds", type=int, default=20)
    wk.add_argument("--apply", action="store_true", help="test: save the recommended threshold; train: use the "
                                                         "new phrase from the next start")
    wk.add_argument("--model", help="test: a trained .npz / .tflite instead of the current wake model")
    wk.add_argument("--false-wakes", type=int, default=0, metavar="MIN",
                    help="test: count false wakes over MIN minutes of normal room audio")
    wk.add_argument("--say", action="append", help="train: another way to say it (e.g. the Hebrew spelling); repeat")
    wk.add_argument("--samples", help="train: a folder of WAV recordings of YOU saying the phrase (recommended)")
    wk.add_argument("--out", help="train: output .npz (default <state_dir>/wake/<phrase>.npz)")
    dr = sub.add_parser("doctor", help="check microphone, wake word, keys, music; say what to fix")
    dr.add_argument("--sound", action="store_true", help="also play a beep on the speakers")
    dr.add_argument("--live", action="store_true", help="also open one real GPT-Live session (~1 cent, asks first)")
    su = sub.add_parser("setup", help="choose how JARVES connects (ChatGPT plan / API key) and sign in")
    su.add_argument("--if-needed", action="store_true", help="only on the first run")
    k = sub.add_parser("key", help="save your OpenAI API key in a private file (secrets.env)")
    k.add_argument("--remove", action="store_true")
    k.add_argument("--show-path", action="store_true")
    k.add_argument("--admin", action="store_true", help="the optional read-only Admin key (real monthly API bill)")
    q = sub.add_parser("ask", help="ask one question (brain + Skills, no Godot)")
    ex = sub.add_parser("execute", help="run a text request with JARVES stopped; Google/memory/tools, terminal confirmations")
    ex.add_argument("text", nargs="+", help='request, e.g. "add a calendar event tomorrow at 10"')
    ex.add_argument("--interactive", "-i", action="store_true", help="keep this text session for follow-up answers; Enter exits")
    q.add_argument("text", nargs="+")
    s = sub.add_parser("say", help="speak a sentence with the configured voice")
    s.add_argument("text", nargs="+")
    co = sub.add_parser("cost", help="estimated cost per day and per conversation")
    co.add_argument("--days", type=int, default=14)
    co.add_argument("--sessions", type=int, default=10)
    db = sub.add_parser("dashboard", help="local web dashboard: conversations, costs, issues, memory, skills")
    db.add_argument("--port", type=int, default=0, help="default: a free port")
    db.add_argument("--no-open", action="store_true", help="do not open the browser")
    db.add_argument("--lan", action="store_true", help="also allow paired phones on the home network "
                                                       "(needs: jarvis features on dashboard_lan)")
    fe = sub.add_parser("features", help="feature switches: list / on ID / off ID / reset ID; on installs supported "
                                        "locked prerequisites before enabling (stop Core for dependency installation)")
    fe.add_argument("action", nargs="?", default="list", choices=["list", "on", "off", "reset"])
    fe.add_argument("id", nargs="?")
    dg = sub.add_parser("diagnose", help="self-diagnosis: find problems in recent conversations")
    dg.add_argument("--days", type=int, default=30)
    dg.add_argument("--import-log", nargs="?", const="", default=None, metavar="PATH",
                    help="first read conversations from jarves.log (default: the current log and its rotations)")
    up = sub.add_parser("update", help="check GitHub for a newer JARVES; install only after you approve")
    up.add_argument("action", nargs="?", default="check", choices=["check", "install", "status"])
    up.add_argument("--yes", action="store_true", help="install: you already approved (no question)")
    a = ap.parse_args(argv)
    home = state_dir()
    load_secrets(home)
    try:
        cfg, _ = load_config(a.config)
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    return {"run": cmd_run, "stop": cmd_stop, "status": cmd_status, "diag": cmd_diag, "memory": cmd_memory, "music": cmd_music,
            "contacts": cmd_personal, "calendar": cmd_personal, "mail": cmd_personal,
            "execute": cmd_execute,
            "ask": cmd_ask, "say": cmd_say, "chatgpt": cmd_chatgpt, "setup": cmd_setup, "logs": cmd_logs, "key": cmd_key,
            "wake": cmd_wake, "doctor": cmd_doctor, "cost": cmd_cost, "dashboard": cmd_dashboard,
            "diagnose": cmd_diagnose, "features": cmd_features, "google": cmd_google,
            "backup": cmd_backup, "restore": cmd_restore, "usage": cmd_usage, "start": cmd_start,
            "show": cmd_display, "hide": cmd_display, "autostart": cmd_autostart,
            "update": cmd_update}[a.cmd](a, cfg, home)


if __name__ == "__main__":
    sys.exit(main())
