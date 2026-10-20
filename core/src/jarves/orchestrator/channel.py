"""The JARVES plugin's stdio MCP server (Claude Code starts it with the plugin, one per session).

- Tools (always): `jarves_reply` (answer a question JARVES relayed, by correlation_id), `jarves_report_status`
  (current task, files, blocker, next step), `jarves_heartbeat`, `jarves_inbox` (pull the owner's queued messages).
- Channel (research preview, only when the session was started with
  `--dangerously-load-development-channels plugin:jarves@jarves-local`): declares
  `capabilities.experimental["claude/channel"]` and pushes the owner's messages live as
  `notifications/claude/channel`; optional permission relay (`claude/channel/permission`, agents.channel_permission_relay)
  forwards `notifications/claude/channel/permission_request` to the owner's popup and answers
  `notifications/claude/channel/permission` with the owner's verdict only.
- Protocol: JSON-RPC 2.0 lines; the server answers a protocol revision older than 2026-07-28 (that revision cannot
  carry channel messages, docs/en/mcp).
Whether the parent session really loaded the channel is checked from its command line (the flag naming this
plugin); otherwise JARVES queues messages for the next Stop hook instead (Claude Code would drop them silently).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SUPPORTED = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = ("JARVES is the owner's local assistant. Messages from it arrive as <channel source=\"...jarves...\" "
                "correlation_id=\"...\">: they relay the owner's questions or instructions (never permission "
                "approvals). Answer questions with the jarves_reply tool, passing the correlation_id, in a few short "
                "sentences: current task, last activity, files/commits, blocker, next step; then continue your work. "
                "You may call jarves_report_status when you finish a milestone or get blocked.")
TOOLS = [
    {"name": "jarves_reply", "description": "Answer a question the owner sent through JARVES (pass its "
     "correlation_id). Short: current task, last activity, files/commits, blocker, next step.",
     "inputSchema": {"type": "object", "properties": {
         "correlation_id": {"type": "string"}, "text": {"type": "string"},
         "status": {"type": "string", "description": "working | blocked | done | waiting"}},
         "required": ["correlation_id", "text"]}},
    {"name": "jarves_report_status", "description": "Tell the owner (through JARVES) where you are: a milestone, a "
     "blocker, a test/CI result or completion.",
     "inputSchema": {"type": "object", "properties": {
         "summary": {"type": "string"}, "current_task": {"type": "string"}, "blocker": {"type": "string"},
         "next_step": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}},
         "status": {"type": "string", "description": "working | blocked | done | failed | needs_input"},
         "test_result": {"type": "string"}}, "required": ["summary"]}},
    {"name": "jarves_heartbeat", "description": "A short liveness note for JARVES (no answer needed).",
     "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}}},
    {"name": "jarves_inbox", "description": "Fetch messages the owner queued for this session through JARVES.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def parent_cmdline(pids: list[int]) -> str:
    """The command lines of the ancestor processes (the Claude Code process is one of the first few)."""
    pids = [int(p) for p in pids if p][:4]
    if not pids:
        return ""
    try:
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            filt = " OR ".join(f"ProcessId={p}" for p in pids)
            p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                f"(Get-CimInstance Win32_Process -Filter '{filt}').CommandLine"],
                               capture_output=True, text=True, timeout=30, creationflags=flags)
            return p.stdout or ""
        return "\n".join(Path(f"/proc/{p}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
                         for p in pids if Path(f"/proc/{p}/cmdline").exists())
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def channel_flag_in(cmdline: str) -> bool:
    return ("--dangerously-load-development-channels" in cmdline or "--channels" in cmdline) and "jarves" in cmdline


class Server:
    def __init__(self, home: Path, *, relay: bool | None = None, out=None, inp=None, ppid: int | None = None,
                 channel_live: bool | None = None) -> None:
        self.home = Path(home)
        self.out = out or sys.stdout
        self.inp = inp or sys.stdin
        self._wlock = threading.Lock()
        from .procs import ancestors
        self.ancestors = [ppid] if ppid is not None else ancestors()
        self.ppid = self.ancestors[0] if self.ancestors else os.getppid()
        self.cwd = os.getcwd()
        self.channel_live = channel_live
        self.relay = relay
        self.client = None
        self._stop = threading.Event()
        self.session_id = ""

    # ------------------------------------------------------------------ MCP wire
    def write(self, obj: dict) -> None:
        obj = {"jsonrpc": "2.0", **obj}
        with self._wlock:
            self.out.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.out.flush()

    def notify(self, method: str, params: dict) -> None:
        self.write({"method": method, "params": params})

    def serve(self) -> int:
        if self.channel_live is None:
            self.channel_live = channel_flag_in(parent_cmdline(self.ancestors))
        if self.relay is None:
            self.relay = self._relay_setting()
        threading.Thread(target=self._bridge_loop, name="jarves-channel-bridge", daemon=True).start()
        for line in self.inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                try:
                    self.handle(msg)
                except Exception as e:
                    if "id" in msg:
                        self.write({"id": msg["id"], "error": {"code": -32603, "message": type(e).__name__}})
        self._stop.set()
        if self.client is not None:
            self.client.close()
        return 0

    def _relay_setting(self) -> bool:
        try:
            import tomllib
            p = self.home / "config.toml"
            d = tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            return bool((d.get("agents") or {}).get("channel_permission_relay", False))
        except Exception:
            return False

    def handle(self, msg: dict) -> None:
        method, mid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            asked = str(params.get("protocolVersion") or "")
            ver = asked if asked in SUPPORTED else SUPPORTED[0]
            exp: dict = {"claude/channel": {}}
            if self.relay:
                exp["claude/channel/permission"] = {}
            self.write({"id": mid, "result": {"protocolVersion": ver,
                                              "capabilities": {"experimental": exp, "tools": {}},
                                              "serverInfo": {"name": "jarves", "version": "1.0.0"},
                                              "instructions": INSTRUCTIONS}})
        elif method == "tools/list":
            self.write({"id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            text = self.call_tool(str(params.get("name") or ""), params.get("arguments") or {})
            self.write({"id": mid, "result": {"content": [{"type": "text", "text": text}]}})
        elif method == "ping":
            self.write({"id": mid, "result": {}})
        elif method == "notifications/claude/channel/permission_request":
            threading.Thread(target=self._relay_permission, args=(params,), daemon=True).start()
        elif mid is not None and method:
            self.write({"id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}})

    # ------------------------------------------------------------------ tools
    def _request(self, op: str, timeout: float = 10.0, **kw) -> dict | None:
        c = self.client
        if c is None or c.closed:
            return None
        from .bridge import BridgeError
        try:
            return c.request(op, timeout=timeout, **kw)
        except BridgeError:
            return None

    def call_tool(self, name: str, args: dict) -> str:
        if name not in {t["name"] for t in TOOLS}:
            return "unknown tool"
        r = self._request("tool", tool=name, args=args, ppid=self.ppid, ancestors=self.ancestors, cwd=self.cwd)
        if r is None:
            return "JARVES is not running right now; nothing was sent."
        return str(r.get("text") or ("sent" if r.get("ok") else r.get("error") or "not sent"))

    # ------------------------------------------------------------------ bridge (JARVES side)
    def _bridge_loop(self) -> None:
        from .bridge import BridgeClient, BridgeError
        delay = 1.0
        while not self._stop.is_set():
            try:
                c = BridgeClient.connect(self.home, timeout=3.0)
            except BridgeError:
                self._stop.wait(delay)
                delay = min(delay * 2, 60.0)
                continue
            delay = 1.0
            closed = threading.Event()

            def on_push(m: dict, closed=closed) -> None:
                if m.get("push") == "closed":
                    closed.set()
                elif m.get("push") == "message":
                    self._deliver(m)
            c.listen(on_push)
            self.client = c
            r = self._request("hello", role="channel", ppid=self.ppid, ancestors=self.ancestors, cwd=self.cwd,
                              channel_live=bool(self.channel_live), relay=bool(self.relay))
            if r and r.get("session_id"):
                self.session_id = str(r["session_id"])
            while not self._stop.is_set() and not closed.is_set():
                closed.wait(1.0)
            self.client = None
            c.close()

    def _deliver(self, m: dict) -> None:
        meta = {k: str(v) for k, v in (m.get("meta") or {}).items() if k.replace("_", "").isalnum()}
        self.notify("notifications/claude/channel", {"content": str(m.get("content") or ""), "meta": meta})
        self._request("ack", message_ids=list(m.get("message_ids") or []), delivery="channel")

    def _relay_permission(self, params: dict) -> None:
        rid = str(params.get("request_id") or "")
        r = self._request("permission_relay", timeout=200.0, request_id=rid, ppid=self.ppid, ancestors=self.ancestors, cwd=self.cwd,
                          tool_name=str(params.get("tool_name") or ""),
                          description=str(params.get("description") or ""),
                          input_preview=str(params.get("input_preview") or ""))
        if r and r.get("decision") in ("allow", "deny"):
            self.notify("notifications/claude/channel/permission", {"request_id": rid, "behavior": r["decision"]})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarves-channel", add_help=False)
    ap.add_argument("--home", required=True)
    a, _ = ap.parse_known_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")          # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8")           # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    return Server(Path(a.home)).serve()


if __name__ == "__main__":
    sys.exit(main())
