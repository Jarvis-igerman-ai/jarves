"""Claude Code adapter (verified against Claude Code 2.1.304, docs/reviews/SWV4_E_ORCHESTRATOR_RESEARCH.md).

- discovery of live sessions, including ones the owner started by hand: `claude agents --json` (the documented,
  supported read of session state; read-only, no turn, never touches the session);
- hook payload -> envelope mapping for the JARVES plugin's hooks;
- the plugin itself (generated into `<home>/agents/claude-plugin/`: a local marketplace `jarves-local` with the
  plugin `jarves`: hooks + the stdio MCP server of channel.py) and its install / uninstall through `claude plugin`;
- sessions JARVES starts: `claude -p --input-format stream-json --output-format stream-json --verbose` in the
  provider's normal permission mode, with the plugin loaded by `--plugin-dir` (its PermissionRequest hook routes
  approvals to the owner's popup); user turns on stdin, `control_request interrupt`, `result` cost/usage.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .. import logs
from . import policy
from .envelope import clean, summary

MARKETPLACE = "jarves-local"
PLUGIN = "jarves"
PLUGIN_ID = f"{PLUGIN}@{MARKETPLACE}"
PLUGIN_VERSION = "1.0.0"
LIST_TIMEOUT_S = 20.0
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Notification", "PermissionRequest", "Stop",
               "SubagentStop", "StopFailure", "SessionEnd")
HOOK_TIMEOUTS = {"PermissionRequest": 150, "Stop": 20, "UserPromptSubmit": 15}
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def resolve_command(configured: list[str] | None = None) -> list[str] | None:
    """The claude CLI as an argv prefix: agents.claude_command when set, else the PATH (claude.cmd / claude.exe on
    Windows: never the .ps1 shim, which needs a PowerShell host)."""
    if configured:
        return list(configured)
    for name in (("claude.cmd", "claude.exe", "claude") if sys.platform == "win32" else ("claude",)):
        p = shutil.which(name)
        if p and not p.lower().endswith(".ps1"):
            return [p]
    return None


def _run(argv: list[str], timeout: float, cwd: str | None = None, stdin: str | None = None) -> tuple[int, str, str]:
    p = subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, cwd=cwd, creationflags=_NOWIN)
    return p.returncode, p.stdout or "", p.stderr or ""


def version(cmd: list[str]) -> str:
    try:
        rc, out, _ = _run([*cmd, "--version"], 20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.strip().split(" ")[0] if rc == 0 else ""


STATUS_MAP = {"busy": "RUNNING", "idle": "IDLE", "waiting": "WAITING_INPUT"}
BG_STATE_MAP = {"working": "RUNNING", "blocked": "WAITING_INPUT", "done": "IDLE", "failed": "STOPPED",
                "stopped": "STOPPED"}


def map_live(row: dict) -> dict:
    """One `claude agents --json` entry -> registry fields."""
    st = STATUS_MAP.get(str(row.get("status") or ""), "")
    waiting = str(row.get("waitingFor") or "")
    if st == "WAITING_INPUT" and waiting == "permission prompt":
        st = "WAITING_APPROVAL"
    if not st:
        st = BG_STATE_MAP.get(str(row.get("state") or ""), "UNKNOWN" if row.get("pid") is None else "RUNNING")
    return {"provider_session_id": str(row.get("sessionId") or row.get("id") or f"pid-{row.get('pid')}"),
            "cwd": str(row.get("cwd") or ""), "kind": str(row.get("kind") or "interactive"),
            "name": clean(row.get("name") or "", 80), "pid": row.get("pid"),
            "started_at": (float(row["startedAt"]) / 1000.0) if row.get("startedAt") else None,
            "state": st, "waiting_for": waiting, "bg_id": str(row.get("id") or "")}


def list_live(cmd: list[str], timeout: float = LIST_TIMEOUT_S) -> tuple[list[dict] | None, str]:
    """(sessions, error). None = the CLI could not be asked (missing / failed): the caller keeps the last known."""
    try:
        rc, out, err = _run([*cmd, "agents", "--json"], timeout)
    except FileNotFoundError:
        return None, "PROVIDER_MISSING"
    except (OSError, subprocess.SubprocessError) as e:
        return None, type(e).__name__
    if rc != 0:
        return None, clean(err, 160) or f"exit {rc}"
    try:
        rows = json.loads(out.strip() or "[]")
    except ValueError:
        return None, "claude agents --json printed no JSON"
    if not isinstance(rows, list):
        return None, "unexpected claude agents --json shape"
    return [map_live(r) for r in rows if isinstance(r, dict)], ""


# ---------------------------------------------------------------------- hooks -> envelope
def hook_to_event(p: dict) -> tuple[str, dict, str]:
    """A hook's stdin payload -> (envelope kind, allow-listed payload, severity). Text is cleaned (untrusted)."""
    ev = str(p.get("hook_event_name") or "")
    base = {"event": ev, "cwd": str(p.get("cwd") or ""), "permission_mode": str(p.get("permission_mode") or "")}
    if ev == "SessionStart":
        return "hello", {**base, "source": str(p.get("source") or ""), "model": str(p.get("model") or ""),
                         "name": clean(p.get("session_title") or "", 80)}, "info"
    if ev == "UserPromptSubmit":
        # the owner's own prompt: only its length and a short summary of the task (the prompt itself stays local)
        return "progress", {**base, "status": "prompt", "summary": summary(p.get("prompt") or "", 160)}, "info"
    if ev in ("PostToolUse", "PostToolUseFailure"):
        ti = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
        files = [str(ti[k]) for k in ("file_path", "path", "notebook_path") if ti.get(k)]
        cmd = str(ti.get("command") or "")
        return "progress", {**base, "tool": str(p.get("tool_name") or ""), "files": files,
                            "command": clean(cmd, 300, keep_code=True) if cmd else "",
                            "duration_ms": p.get("duration_ms"),
                            "status": "tool_failed" if ev == "PostToolUseFailure" else "tool"}, "info"
    if ev == "Notification":
        nt = str(p.get("notification_type") or "")
        kind = "need_input" if nt in ("permission_prompt", "idle_prompt", "agent_needs_input", "elicitation_dialog") \
            else ("completed" if nt == "agent_completed" else "progress")
        return kind, {**base, "notification_type": nt, "title": clean(p.get("title") or "", 120),
                      "text": clean(p.get("message") or "", 400)}, "notice" if kind == "need_input" else "info"
    if ev == "PermissionRequest":
        ti = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
        return "approval_requested", {**base, "tool_name": str(p.get("tool_name") or ""),
                                      "command": clean(ti.get("command") or "", 1000, keep_code=True),
                                      "description": clean(ti.get("description") or "", 300),
                                      "files": [str(ti[k]) for k in ("file_path", "path") if ti.get(k)],
                                      "input_preview": clean(json.dumps(ti, ensure_ascii=False)[:1500], 1000,
                                                             keep_code=True)}, "warning"
    if ev in ("Stop", "SubagentStop"):
        return "status_snapshot", {**base, "status": "turn_done" if ev == "Stop" else "subagent_done",
                                   "summary": summary(p.get("last_assistant_message") or "", 400),
                                   "stop_hook_active": bool(p.get("stop_hook_active"))}, "info"
    if ev == "StopFailure":
        return "error", {**base, "error": str(p.get("error") or "unknown"),
                         "text": clean(p.get("last_assistant_message") or "", 200)}, "warning"
    if ev == "SessionEnd":
        return "goodbye", {**base, "reason": str(p.get("reason") or "")}, "info"
    return "heartbeat", base, "info"


# ---------------------------------------------------------------------- the plugin
def _boot(module: str, src: str) -> str:
    """`python -c` bootstrap: the JARVES source on sys.path, then the module's main()."""
    return (f"import sys; sys.path.insert(0, {src!r}); "
            f"from jarves.orchestrator.{module} import main; sys.exit(main(sys.argv[1:]))")


def plugin_root(home: Path) -> Path:
    return Path(home) / "agents" / "claude-plugin"


def write_plugin(home: Path, python: str | None = None) -> Path:
    """Generate the local marketplace + plugin (idempotent). Returns the plugin folder (for --plugin-dir)."""
    root = plugin_root(home)
    pdir = root / PLUGIN
    py = python or sys.executable
    src = str(Path(__file__).resolve().parents[2])
    h = str(Path(home).resolve())
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (pdir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (pdir / "hooks").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": MARKETPLACE, "owner": {"name": "JARVES (local)"},
        "plugins": [{"name": PLUGIN, "source": f"./{PLUGIN}", "version": PLUGIN_VERSION,
                     "description": "Lets your local JARVES assistant see this session's status, send it the "
                                    "owner's questions and route its approval prompts to the owner."}]},
        indent=2), encoding="utf-8")
    (pdir / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": PLUGIN, "version": PLUGIN_VERSION,
        "description": "JARVES bridge: status hooks, owner questions at safe stops, approvals on the owner's "
                       "screen, and an optional live channel.", "author": {"name": "JARVES"}}, indent=2),
        encoding="utf-8")
    hooks: dict = {}
    for ev in HOOK_EVENTS:
        entry = {"type": "command", "command": py, "args": ["-c", _boot("hook", src), "--home", h, "--event", ev],
                 "timeout": HOOK_TIMEOUTS.get(ev, 10)}
        block = {"hooks": [entry]}
        if ev == "PostToolUse":
            block["matcher"] = "Write|Edit|MultiEdit|NotebookEdit|Bash"
        hooks[ev] = [block]
    (pdir / "hooks" / "hooks.json").write_text(json.dumps({"description": "JARVES status + approvals bridge",
                                                           "hooks": hooks}, indent=2), encoding="utf-8")
    (pdir / ".mcp.json").write_text(json.dumps({"mcpServers": {"jarves": {
        "command": py, "args": ["-c", _boot("channel", src), "--home", h]}}}, indent=2), encoding="utf-8")
    return pdir


def install_plugin(cmd: list[str], home: Path) -> dict:
    pdir = write_plugin(home)
    steps = []
    for argv in ([*cmd, "plugin", "marketplace", "add", str(plugin_root(home))],
                 [*cmd, "plugin", "install", PLUGIN_ID, "--scope", "user"]):
        try:
            rc, out, err = _run(argv, 120)
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "error": type(e).__name__, "steps": steps}
        steps.append({"argv": [Path(argv[0]).name, *argv[len(cmd):]], "rc": rc,
                      "out": clean(out or err, 300)})
        already = "already" in (out + err).lower()
        if rc != 0 and not already:
            return {"ok": False, "error": clean(err or out, 300), "steps": steps}
    logs.info("agents", "AGENTS_PLUGIN_INSTALLED", "JARVES Claude Code plugin installed", plugin=PLUGIN_ID)
    return {"ok": True, "plugin": PLUGIN_ID, "dir": str(pdir), "steps": steps,
            "channel_flag": f"claude --dangerously-load-development-channels plugin:{PLUGIN_ID}"}


def uninstall_plugin(cmd: list[str], home: Path) -> dict:
    steps = []
    for argv in ([*cmd, "plugin", "uninstall", PLUGIN_ID, "--scope", "user"],
                 [*cmd, "plugin", "marketplace", "remove", MARKETPLACE]):
        try:
            rc, out, err = _run(argv, 120)
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "error": type(e).__name__, "steps": steps}
        steps.append({"argv": [Path(argv[0]).name, *argv[len(cmd):]], "rc": rc, "out": clean(out or err, 300)})
    shutil.rmtree(plugin_root(home), ignore_errors=True)
    logs.info("agents", "AGENTS_PLUGIN_REMOVED", "JARVES Claude Code plugin removed", plugin=PLUGIN_ID)
    return {"ok": all(s["rc"] == 0 or "not" in s["out"].lower() for s in steps), "steps": steps}


def plugin_installed(cmd: list[str]) -> tuple[bool | None, str]:
    try:
        rc, out, err = _run([*cmd, "plugin", "list", "--json"], 60)
    except (OSError, subprocess.SubprocessError) as e:
        return None, type(e).__name__
    if rc != 0:
        try:
            rc, out, err = _run([*cmd, "plugin", "list"], 60)
        except (OSError, subprocess.SubprocessError) as e:
            return None, type(e).__name__
    return (PLUGIN_ID in out or f'"{PLUGIN}"' in out), ""


# ---------------------------------------------------------------------- sessions JARVES starts
class ManagedClaude:
    """One `claude -p` stream-json process = one session JARVES started (or resumed). Thread-based reader; the
    callbacks run on the reader thread: on_event(kind, data)."""

    def __init__(self, cmd: list[str], cwd: str, *, plugin_dir: str | None, on_event: Callable[[str, dict], None],
                 resume: str = "", model: str = "", env: dict | None = None) -> None:
        argv = [*cmd, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "default"]
        if plugin_dir:
            argv += ["--plugin-dir", plugin_dir]
        if resume:
            argv += ["--resume", resume]
        if model:
            argv += ["--model", model]
        policy.check_flags(argv)
        self.argv, self.cwd, self.on_event = argv, cwd, on_event
        self.env = env
        self.proc: subprocess.Popen | None = None
        self.session_id = resume
        self.busy = False
        self.started = 0.0
        self._wlock = threading.Lock()
        self.exit_code: int | None = None
        self._cr = 0

    def start(self) -> "ManagedClaude":
        env = dict(os.environ)
        env.update(self.env or {})
        self.proc = subprocess.Popen(self.argv, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                     env=env, creationflags=_NOWIN)
        self.started = time.time()
        threading.Thread(target=self._read, name="agents-claude-out", daemon=True).start()
        threading.Thread(target=self._drain_err, name="agents-claude-err", daemon=True).start()
        return self

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _write(self, obj: dict) -> bool:
        if not self.alive():
            return False
        try:
            with self._wlock:
                self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def send(self, text: str) -> bool:
        ok = self._write({"type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None,
                          "session_id": self.session_id or ""})
        if ok:
            self.busy = True
        return ok

    def interrupt(self) -> bool:
        self._cr += 1
        return self._write({"type": "control_request", "request_id": f"jarves-int-{self._cr}",
                            "request": {"subtype": "interrupt"}})

    def close(self, grace_s: float = 10.0) -> int | None:
        if self.proc is None:
            return None
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()               # end of input = the session ends after its turn
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
        self.exit_code = self.proc.returncode
        return self.exit_code

    def _drain_err(self) -> None:
        try:
            for _ in self.proc.stderr:
                pass
        except (OSError, ValueError):
            pass

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
                if isinstance(m, dict):
                    self._dispatch(m)
        except (OSError, ValueError):
            pass
        rc = self.proc.wait()
        self.exit_code = rc
        self.busy = False
        self.on_event("exit", {"exit_code": rc})

    def _dispatch(self, m: dict) -> None:
        t, st = m.get("type"), m.get("subtype")
        if m.get("session_id") and not self.session_id:
            self.session_id = str(m["session_id"])
        if t == "system" and st == "init":
            self.session_id = str(m.get("session_id") or self.session_id)
            auth = "subscription" if str(m.get("apiKeySource") or "none") == "none" else "api_key"
            self.on_event("init", {"session_id": self.session_id, "auth": auth, "model": str(m.get("model") or ""),
                                   "permission_mode": str(m.get("permissionMode") or ""),
                                   "version": str(m.get("claude_code_version") or ""),
                                   "capabilities": list(m.get("capabilities") or [])})
        elif t == "assistant":
            parts = (m.get("message") or {}).get("content") or []
            text = " ".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
            tools = [str(p.get("name") or "") for p in parts if isinstance(p, dict) and p.get("type") == "tool_use"]
            if m.get("parent_tool_use_id"):
                return                                         # a subagent's message: not the session's own words
            self.on_event("assistant", {"text": text, "tools": tools})
        elif t == "result":
            self.busy = False
            u = m.get("usage") or {}
            tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                      "cache_creation_input_tokens"))
            self.on_event("result", {"is_error": bool(m.get("is_error")), "text": str(m.get("result") or ""),
                                     "cost_usd": float(m.get("total_cost_usd") or 0.0), "tokens_total": tokens,
                                     "turns": int(m.get("num_turns") or 0),
                                     "denials": len(m.get("permission_denials") or []),
                                     "subtype": str(st or "")})
        elif t == "control_response":
            self.on_event("control", {"ok": (m.get("response") or {}).get("subtype") == "success"})
        elif t == "system" and st in ("api_retry", "permission_denied"):
            self.on_event(st, {"error": str(m.get("error") or ""), "tool": str(m.get("tool_name") or "")})
