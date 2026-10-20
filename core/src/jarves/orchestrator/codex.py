"""Codex adapter: a client of the Codex App Server (verified against codex-cli 0.163.0: JSON-RPC 2.0 without the
"jsonrpc" field, one JSON object per line on stdio; `initialize` then the `initialized` notification).

JARVES spawns `codex app-server` (or, when the owner's shared daemon runs, `codex app-server proxy` to it) and:
- reads the account mode (`account/read`: `chatgpt` = subscription; `apiKey` -> AUTH_REQUIRED, never a silent
  switch to API billing, owner decision OD-21);
- lists threads (`thread/list`: also threads started elsewhere, from the state DB; status `notLoaded` = not live in
  this server -> observe only, UNSUPPORTED_ATTACH);
- starts / resumes threads and turns (`thread/start`, `thread/resume`, `turn/start`), steers (`turn/steer`) and
  interrupts (`turn/interrupt`), and follows the events (`turn/started|completed`, `item/started|completed`,
  `thread/status/changed`, `thread/tokenUsage/updated`, `error`);
- answers the server's approval REQUESTS (`item/commandExecution/requestApproval`, `item/fileChange/requestApproval`)
  only after the owner decided on the approvals popup: `accept` or `decline` (never `acceptForSession`).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from .. import logs
from . import policy
from .envelope import clean

CLIENT_INFO = {"name": "jarves_orchestrator", "title": "JARVES orchestrator", "version": "1.0"}
REQUEST_TIMEOUT_S = 60.0
APPROVAL_METHODS = ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                    "execCommandApproval", "applyPatchApproval")
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def resolve_command(configured: list[str] | None = None) -> list[str] | None:
    if configured:
        return list(configured)
    for name in (("codex.exe", "codex.cmd", "codex") if sys.platform == "win32" else ("codex",)):
        p = shutil.which(name)
        if p and not p.lower().endswith(".ps1"):
            return [p]
    if sys.platform == "win32":
        p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "OpenAI", "Codex", "bin", "codex.exe")
        if os.path.exists(p):
            return [p]
    return None


def version(cmd: list[str]) -> str:
    try:
        p = subprocess.run([*cmd, "--version"], capture_output=True, text=True, timeout=20, creationflags=_NOWIN)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (p.stdout or "").strip().split(" ")[-1] if p.returncode == 0 else ""


def daemon_running(cmd: list[str]) -> bool:
    """Is the owner's shared app-server daemon up (`codex app-server daemon version` answers)?"""
    try:
        p = subprocess.run([*cmd, "app-server", "daemon", "version"], capture_output=True, text=True, timeout=20,
                           creationflags=_NOWIN)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


STATUS_MAP = {"notLoaded": "DISCOVERED", "idle": "IDLE", "systemError": "STOPPED"}


def thread_state(status: Any) -> tuple[str, str]:
    """A Thread.status -> (lifecycle state, waiting_for)."""
    if isinstance(status, dict):
        t = str(status.get("type") or "")
        if t == "active":
            flags = list(status.get("activeFlags") or [])
            if "waitingOnApproval" in flags:
                return "WAITING_APPROVAL", "approval"
            if "waitingOnUserInput" in flags:
                return "WAITING_INPUT", "input"
            return "RUNNING", ""
        return STATUS_MAP.get(t, "UNKNOWN"), ""
    return "UNKNOWN", ""


class AppServer:
    """One app-server child process. `on_notification(method, params)` and `on_server_request(id, method, params)`
    run on the reader thread; answer server requests with `respond(id, result)`."""

    def __init__(self, cmd: list[str], *, proxy: bool = False,
                 on_notification: Callable[[str, dict], None] | None = None,
                 on_server_request: Callable[[Any, str, dict], None] | None = None,
                 on_exit: Callable[[int], None] | None = None, cwd: str | None = None) -> None:
        argv = [*cmd, "app-server", "proxy"] if proxy else [*cmd, "app-server"]
        policy.check_flags(argv)
        self.argv, self.cwd = argv, cwd
        self.on_notification = on_notification or (lambda m, p: None)
        self.on_server_request = on_server_request or (lambda i, m, p: None)
        self.on_exit = on_exit or (lambda rc: None)
        self.proc: subprocess.Popen | None = None
        self._next = 0
        self._pending: dict[int, dict] = {}
        self._cv = threading.Condition()
        self._wlock = threading.Lock()
        self.init: dict = {}
        self.account: dict = {}

    def start(self) -> "AppServer":
        self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                     cwd=self.cwd, creationflags=_NOWIN)
        threading.Thread(target=self._read, name="agents-codex-out", daemon=True).start()
        self.init = self.request("initialize", {"clientInfo": CLIENT_INFO})
        self.notify("initialized")
        return self

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _send(self, obj: dict) -> None:
        if not self.alive():
            raise RpcError(-1, "app-server not running")
        with self._wlock:
            self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"method": method, **({"params": params} if params is not None else {})})

    def request(self, method: str, params: dict | None = None, timeout: float = REQUEST_TIMEOUT_S) -> dict:
        with self._cv:
            self._next += 1
            rid = self._next
        self._send({"id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        with self._cv:
            while rid not in self._pending:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise RpcError(-2, f"{method}: no answer in {timeout:.0f} s")
                if not self.alive() and rid not in self._pending:
                    raise RpcError(-1, "app-server exited")
                self._cv.wait(min(left, 0.5))
            msg = self._pending.pop(rid)
        if "error" in msg:
            e = msg["error"] or {}
            raise RpcError(int(e.get("code") or -32000), clean(e.get("message") or "error", 300))
        return msg.get("result") or {}

    def respond(self, rid: Any, result: dict) -> None:
        self._send({"id": rid, "result": result})

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                if "method" in m and "id" in m:              # a server REQUEST (approvals, user input)
                    try:
                        self.on_server_request(m["id"], str(m["method"]), m.get("params") or {})
                    except Exception:
                        logs.error("agents", "AGENTS_CODEX_REQUEST_FAILED", "codex server request handler failed",
                                   exc=True)
                elif "method" in m:
                    try:
                        self.on_notification(str(m["method"]), m.get("params") or {})
                    except Exception:
                        logs.error("agents", "AGENTS_CODEX_EVENT_FAILED", "codex event handler failed", exc=True)
                elif "id" in m:
                    with self._cv:
                        self._pending[m["id"]] = m
                        self._cv.notify_all()
        except (OSError, ValueError):
            pass
        rc = self.proc.wait()
        with self._cv:
            self._cv.notify_all()
        self.on_exit(rc)

    def close(self, grace_s: float = 5.0) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(grace_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass

    # ------------------------------------------------------------------ typed calls
    def read_account(self) -> dict:
        r = self.request("account/read", {"refreshToken": False})
        acc = r.get("account") or {}
        self.account = {"type": str(acc.get("type") or ""), "plan": str(acc.get("planType") or ""),
                        "requires_auth": bool(r.get("requiresOpenaiAuth"))}
        return self.account

    def list_threads(self, cwd: str | None = None, limit: int = 25) -> list[dict]:
        params: dict = {"limit": int(limit)}
        if cwd:
            params["cwd"] = cwd
        return list((self.request("thread/list", params).get("data")) or [])

    def loaded(self) -> list[str]:
        return [str(x) for x in (self.request("thread/loaded/list", {}).get("data") or [])]

    def start_thread(self, cwd: str, *, model: str = "", sandbox: str = "workspace-write",
                     approval: str = "on-request") -> dict:
        params = {"cwd": cwd, "approvalPolicy": approval, "sandbox": sandbox, "serviceName": "jarves"}
        if model:
            params["model"] = model
        policy.check_flags([json.dumps(params)])
        return self.request("thread/start", params).get("thread") or {}

    def resume_thread(self, thread_id: str) -> dict:
        return self.request("thread/resume", {"threadId": thread_id, "approvalPolicy": "on-request",
                                              "sandbox": "workspace-write"}).get("thread") or {}

    def read_thread(self, thread_id: str, turns: bool = True) -> dict:
        return self.request("thread/read", {"threadId": thread_id, "includeTurns": bool(turns)}).get("thread") or {}

    def start_turn(self, thread_id: str, text: str) -> dict:
        return self.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": text}]}) \
            .get("turn") or {}

    def steer(self, thread_id: str, turn_id: str, text: str) -> str:
        r = self.request("turn/steer", {"threadId": thread_id, "expectedTurnId": turn_id,
                                        "input": [{"type": "text", "text": text}]})
        return str(r.get("turnId") or turn_id)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})


def thread_view(t: dict) -> dict:
    """A Thread -> registry fields (untrusted text cleaned)."""
    st, waiting = thread_state(t.get("status"))
    git = t.get("gitInfo") if isinstance(t.get("gitInfo"), dict) else {}
    return {"provider_session_id": str(t.get("id") or ""), "cwd": str(t.get("cwd") or ""),
            "name": clean(t.get("name") or t.get("preview") or "", 80), "state": st, "waiting_for": waiting,
            "kind": str(t.get("source") if isinstance(t.get("source"), str) else "thread"),
            "started_at": float(t.get("createdAt") or 0) or None, "updated_at": float(t.get("updatedAt") or 0) or None,
            "branch": str(git.get("branch") or ""), "version": str(t.get("cliVersion") or "")}


def last_agent_text(thread: dict) -> str:
    for turn in reversed(thread.get("turns") or []):
        for item in reversed(turn.get("items") or []):
            if isinstance(item, dict) and item.get("type") == "agentMessage" and item.get("text"):
                return str(item["text"])
    return ""
