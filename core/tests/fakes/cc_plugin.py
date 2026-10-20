"""Shared by the fake Claude Code processes: run the REAL JARVES plugin the way Claude Code does (recorded contract:
tests/fixtures/agents/claude_code.json + code.claude.com/docs/en/hooks, channels-reference).

- hooks: read <plugin>/hooks/hooks.json, run the command in exec form (command + args, no shell), the event payload
  as JSON on stdin (common fields session_id, transcript_path, cwd, hook_event_name, permission_mode), parse a
  single JSON object from stdout;
- MCP: start <plugin>/.mcp.json's server as a child, JSON-RPC 2.0 lines, `initialize` asking for the newest
  revision (2026-07-28) like the v2 runtime, `notifications/initialized`, `tools/list`, `tools/call`; server
  notifications are collected.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path


def hook_payload(event: str, session_id: str, cwd: str, **fields) -> dict:
    return {"session_id": session_id, "transcript_path": str(Path(cwd) / ".fake" / f"{session_id}.jsonl"),
            "cwd": cwd, "hook_event_name": event, "permission_mode": "default", **fields}


def run_hook(plugin_dir: str, event: str, payload: dict, timeout: float = 240) -> dict | None:
    hooks = json.loads((Path(plugin_dir) / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    out = None
    for block in hooks.get(event, []):
        for h in block["hooks"]:
            argv = [h["command"], *h.get("args", [])]
            p = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                               timeout=timeout, cwd=payload.get("cwd") or None)
            txt = (p.stdout or "").strip()
            if txt:
                try:
                    out = json.loads(txt)
                except ValueError:
                    out = {"_raw": txt[:200]}
    return out


class Mcp:
    def __init__(self, plugin_dir: str, cwd: str, env: dict | None = None) -> None:
        cfg = json.loads((Path(plugin_dir) / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["jarves"]
        e = dict(os.environ)
        e.update(env or {})
        self.proc = subprocess.Popen([cfg["command"], *cfg.get("args", [])], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                     cwd=cwd, env=e)
        self._id = 0
        self._replies: dict = {}
        self.notifications: list[dict] = []
        self.on_notification = None
        self._cv = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            with self._cv:
                if "id" in m and "method" not in m:
                    self._replies[m["id"]] = m
                else:
                    self.notifications.append(m)
                self._cv.notify_all()
            if "method" in m and self.on_notification is not None:
                try:
                    self.on_notification(m)
                except Exception:
                    pass

    def send(self, obj: dict) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        with self._cv:
            self._id += 1
            rid = self._id
        self.send({"id": rid, "method": method, "params": params or {}})
        with self._cv:
            if not self._cv.wait_for(lambda: rid in self._replies, timeout):
                raise TimeoutError(method)
            return self._replies.pop(rid)

    def notify(self, method: str, params: dict) -> None:
        self.send({"method": method, "params": params})

    def handshake(self) -> dict:
        r = self.request("initialize", {"protocolVersion": "2026-07-28", "capabilities": {},
                                        "clientInfo": {"name": "fake-claude-code", "version": "2.1.304"}})
        self.notify("notifications/initialized", {})
        return r

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
