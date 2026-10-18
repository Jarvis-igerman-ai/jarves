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
from .config import ConfigError, load_config, openai_key_present, state_dir
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


def cmd_run(a, cfg, home) -> int:
    from .app import Core
    core = Core(cfg, home)
    try:
        asyncio.run(core.run())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"Core failed to start: {e}", file=sys.stderr)
        return 2
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


def _git_rev() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


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
        z.writestr("recent.log", "\n".join(logs.redact(l) for l in lines))
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


def cmd_music(a, cfg, home) -> int:
    from .skills.music import MusicError, PearDesktopPlayer
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
    from .brain import AgentBrain, Brain
    from .memory import MemoryStore
    from .skills import SkillRegistry
    from .skills.builtin import ClockSkill, MemorySkill
    from .skills.music import MusicSkill, PearDesktopPlayer
    logs.setup(home / "logs", cfg.log.level, console=False)
    mem = MemoryStore(home / "memory.db")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    reg.register(MemorySkill(mem))
    reg.register(MusicSkill(PearDesktopPlayer(cfg.music.pear_url, home / "pear_client_id"), cfg.music.max_volume))
    agent = AgentBrain(cfg.ai, reg, mem, cfg.assistant_name) if cfg.ai.provider == "openai" else None
    r = asyncio.run(Brain(reg, agent).respond(" ".join(a.text)))
    print(f"[{r.source}{'/' + r.tool if r.tool else ''} {r.code}] {r.text}")
    return 0 if r.ok else 1


def cmd_say(a, cfg, home) -> int:
    from .voice.audio import SoundDeviceAudio
    from .voice.tts import FallbackTTS, tts_chain
    logs.setup(home / "logs", cfg.log.level, console=False)
    t = FallbackTTS(tts_chain(cfg.voice))
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
    sub.add_parser("run", help="start the Core (Godot face connects to it)")
    sub.add_parser("status", help="live health from the running Core, or the last stale snapshot")
    d = sub.add_parser("diag", help="write a redacted support bundle (zip)")
    d.add_argument("--out")
    d.add_argument("--lines", type=int, default=500)
    m = sub.add_parser("memory", help="list / delete / clear what JARVES remembers")
    m.add_argument("action", choices=["list", "delete", "clear"])
    m.add_argument("id", nargs="?")
    m.add_argument("--yes", action="store_true")
    mu = sub.add_parser("music", help="Pear Desktop pairing and manual control")
    mu.add_argument("action", choices=["pair", "status", "play", "pause", "resume", "next"])
    mu.add_argument("query", nargs="?", default="")
    q = sub.add_parser("ask", help="ask one question (brain + Skills, no Godot)")
    q.add_argument("text", nargs="+")
    s = sub.add_parser("say", help="speak a sentence with the configured voice")
    s.add_argument("text", nargs="+")
    a = ap.parse_args(argv)
    home = state_dir()
    try:
        cfg, _ = load_config(a.config)
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    return {"run": cmd_run, "status": cmd_status, "diag": cmd_diag, "memory": cmd_memory, "music": cmd_music,
            "ask": cmd_ask, "say": cmd_say}[a.cmd](a, cfg, home)


if __name__ == "__main__":
    sys.exit(main())
