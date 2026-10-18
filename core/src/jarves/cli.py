"""jarves command line: run the Core and offline troubleshooting tools (work even when the Core is down)."""
from __future__ import annotations

import argparse
import asyncio
import json
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


def cmd_run(a, cfg, home) -> int:
    from .app import Core
    _apply_log_args(a, cfg)
    print(f"JARVES Core starting. Terminal log: {cfg.log.console_level}"
          + (f", modules {','.join(cfg.log.modules)}" if cfg.log.modules else "")
          + (f", hiding {','.join(cfg.log.hide)}" if cfg.log.hide else "")
          + ". Stop: Ctrl+C or `jarvis stop`.", file=sys.stderr)
    core = Core(cfg, home)
    try:
        asyncio.run(core.run())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"Core failed to start: {e}", file=sys.stderr)
        return 2
    return 0


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


def cmd_status(a, cfg, home) -> int:
    live = _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status")
    if live is None:
        snap = read_snapshot(home / "health.json")
        print("Core is NOT running (no answer on the local link).")
        if snap:
            print(f"Last snapshot is {snap['age_s']} s old (stale, not current truth):")
            print(json.dumps(snap, indent=1, ensure_ascii=False))
        return 1
    print(json.dumps(live, indent=1, ensure_ascii=False))
    return 0 if live["core"] == "ready" else 3


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
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _private_line(line: str) -> str:
    """Support bundles are for sharing: mask personal content even if the log was written with privacy off."""
    try:
        return json.dumps(logs.scrub(json.loads(line), private=True), ensure_ascii=False)
    except ValueError:
        return logs.redact(line)


def cmd_diag(a, cfg, home) -> int:
    """User-initiated, redacted support bundle (no secrets, no transcripts, no audio)."""
    out = Path(a.out or home / f"jarves-diag-{datetime.now():%Y%m%d-%H%M%S}.zip")
    snap = _ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status") or read_snapshot(home / "health.json")
    info = {"version": __version__, "commit": _git_rev(), "created": datetime.now(timezone.utc).isoformat(),
            "python": sys.version.split()[0], "platform": platform.platform(), "openai_key_present": openai_key_present(),
            "config": json.loads(cfg.model_dump_json())}
    lines = []
    logf = home / "logs" / "jarves.log"
    if logf.exists():
        lines = logf.read_text(encoding="utf-8", errors="replace").splitlines()[-a.lines:]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", logs.redact(json.dumps(info, indent=1, ensure_ascii=False)))
        z.writestr("health.json", logs.redact(json.dumps(snap, indent=1, ensure_ascii=False)))
        z.writestr("recent.log", "\n".join(_private_line(l) for l in lines))
    print(f"wrote {out} ({len(lines)} log lines, redacted). Review it before sharing.")
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
    reg.register(MusicSkill(PearDesktopPlayer(cfg.music.pear_url, home / "pear_client_id"), cfg.music.max_volume))
    agent = make_agent(cfg, reg, mem, home)
    r = asyncio.run(Brain(reg, agent).respond(" ".join(a.text)))
    print(f"[{r.source}{'/' + r.tool if r.tool else ''} {r.code}] {r.text}")
    return 0 if r.ok else 1


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


def cmd_key(a, cfg, home) -> int:
    """Store / remove the OpenAI API key in <state_dir>/secrets.env."""
    path = secrets_file(home)
    if a.remove:
        if path.exists():
            lines = [l for l in path.read_text(encoding="utf-8-sig").splitlines()
                     if l.partition("=")[0].strip() != "OPENAI_API_KEY"]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("key removed (restart JARVES)")
        return 0
    if a.show_path:
        print(path)
        return 0
    rc = _ask_key(home)
    if rc == 0:
        print("Restart JARVES to use it:  jarvis stop  then  jarvis")
    return rc


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


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):      # Hebrew output on Windows consoles
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="jarves", description="JARVES assistant Core")
    ap.add_argument("--config", type=Path, help="config.toml (default: <JARVES_HOME>/config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    def log_opts(p):
        p.add_argument("--log-level", type=str.upper, choices=logs.LEVELS, help="terminal log level")
        p.add_argument("--module", help="show only these modules, comma separated (e.g. voice,wake,ai)")
        p.add_argument("--hide", help="hide these modules, comma separated (e.g. link)")
    log_opts(sub.add_parser("run", help="start the Core (Godot face connects to it)"))
    lg = sub.add_parser("logs", help="show the log (filter by --log-level / --module; -f to follow)")
    log_opts(lg)
    lg.add_argument("-f", "--follow", action="store_true")
    lg.add_argument("-n", "--lines", type=int, default=50)
    lg.add_argument("--until-stopped", action="store_true", help="finish following when the Core stops")
    sub.add_parser("stop", help="orderly shutdown of the running Core")
    sub.add_parser("status", help="live health from the running Core, or the last stale snapshot")
    d = sub.add_parser("diag", help="write a redacted support bundle (zip)")
    d.add_argument("--out")
    d.add_argument("--lines", type=int, default=500)
    m = sub.add_parser("memory", help="list / delete / clear what JARVES remembers")
    m.add_argument("action", choices=["list", "delete", "clear"])
    m.add_argument("id", nargs="?")
    m.add_argument("--yes", action="store_true")
    mu = sub.add_parser("music", help="Pear Desktop pairing and manual control")
    mu.add_argument("action", choices=["open", "pair", "status", "play", "pause", "resume", "next"])
    mu.add_argument("query", nargs="?", default="")
    g = sub.add_parser("chatgpt", help="connect / disconnect your ChatGPT plan (Sign in with ChatGPT)")
    g.add_argument("action", choices=["login", "logout", "status", "models"])
    g.add_argument("--forget", action="store_true", help="logout: also forget this app registration")
    su = sub.add_parser("setup", help="choose how JARVES connects (ChatGPT plan / API key) and sign in")
    su.add_argument("--if-needed", action="store_true", help="only on the first run")
    k = sub.add_parser("key", help="save your OpenAI API key in a private file (secrets.env)")
    k.add_argument("--remove", action="store_true")
    k.add_argument("--show-path", action="store_true")
    q = sub.add_parser("ask", help="ask one question (brain + Skills, no Godot)")
    q.add_argument("text", nargs="+")
    s = sub.add_parser("say", help="speak a sentence with the configured voice")
    s.add_argument("text", nargs="+")
    a = ap.parse_args(argv)
    home = state_dir()
    load_secrets(home)
    try:
        cfg, _ = load_config(a.config)
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    return {"run": cmd_run, "stop": cmd_stop, "status": cmd_status, "diag": cmd_diag, "memory": cmd_memory, "music": cmd_music,
            "ask": cmd_ask, "say": cmd_say, "chatgpt": cmd_chatgpt, "setup": cmd_setup, "logs": cmd_logs, "key": cmd_key}[a.cmd](a, cfg, home)


if __name__ == "__main__":
    sys.exit(main())
