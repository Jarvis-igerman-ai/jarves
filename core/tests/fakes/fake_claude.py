"""A FAKE `claude` CLI (Claude Code 2.1.304 shapes, tests/fixtures/agents/claude_code.json) for the orchestrator
tests. Real process, real stdio, no network. State lives in $FAKE_CLAUDE_DIR:

    fake_claude.py --version
    fake_claude.py agents --json            live sessions = live/<pid>.json files whose process is alive
    fake_claude.py plugin marketplace add|remove ... / plugin install|uninstall ... / plugin list --json
    fake_claude.py -p --input-format stream-json --output-format stream-json --verbose [--plugin-dir P]
                   [--resume ID] [--permission-mode M] [--model M]
        one session: system/init, then per user line an assistant message + a result (total_cost_usd running total,
        usage); runs the plugin's SessionStart / PostToolUse / PermissionRequest / Stop / SessionEnd hooks like
        Claude Code (a Stop "block" reason becomes the next turn); control_request interrupt -> control_response.
        Markers in a user message: "APPROVE: <command>" = a Bash call that needs permission (PermissionRequest hook
        decides; no decision = denied, as in -p Manual mode), "SLOW" = a long turn that an interrupt ends.
    anything else = an interactive session: hands over to fake_claude_host.py.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cc_plugin  # noqa: E402

STATE = Path(os.environ.get("FAKE_CLAUDE_DIR") or Path.cwd() / ".fake_claude")
VERSION = "2.1.304"
CAPS = ["interrupt_receipt_v1", "interrupt_cancel_queued_v1", "msg_lifecycle_v1"]


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def register(entry: dict) -> Path:
    d = STATE / "live"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{entry['pid']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry), encoding="utf-8")
    os.replace(tmp, p)
    return p


def agents_json() -> int:
    rows = []
    for p in sorted((STATE / "live").glob("*.json")) if (STATE / "live").is_dir() else []:
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _alive(int(e.get("pid") or 0)):
            rows.append(e)
    print(json.dumps(rows, indent=2))
    return 0


def plugin(args: list[str]) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    log = STATE / "plugin_calls.jsonl"
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(args) + "\n")
    st = STATE / "plugins.json"
    cur = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {"marketplaces": [], "installed": []}
    if args[:2] == ["marketplace", "add"]:
        cur["marketplaces"].append(args[2])
        print("Successfully added marketplace: jarves-local")
    elif args[:2] == ["marketplace", "remove"]:
        cur["marketplaces"] = []
        print("Removed marketplace")
    elif args[:1] == ["install"]:
        if not cur["marketplaces"]:
            print("Marketplace not found", file=sys.stderr)
            return 1
        cur["installed"].append(args[1])
        print(f"Installed {args[1]}")
    elif args[:1] == ["uninstall"]:
        cur["installed"] = [x for x in cur["installed"] if x != args[1]]
        print(f"Uninstalled {args[1]}")
    elif args[:1] == ["list"]:
        print(json.dumps([{"id": x, "enabled": True} for x in cur["installed"]]))
    st.write_text(json.dumps(cur), encoding="utf-8")
    return 0


class Session:
    def __init__(self, argv: list[str]) -> None:
        def opt(name, default=""):
            return argv[argv.index(name) + 1] if name in argv else default
        self.plugin_dir = opt("--plugin-dir")
        self.session_id = opt("--resume") or str(uuid.uuid4())
        self.resumed = "--resume" in argv
        self.mode = opt("--permission-mode", "default")
        self.model = opt("--model", "claude-fake")
        self.cwd = os.getcwd()
        self.cost = 0.0
        self.turns = 0
        self.interrupted = threading.Event()
        self._out = threading.Lock()
        self.queue: list[str] = []
        self.cv = threading.Condition()
        self.closed = False

    def emit(self, obj: dict) -> None:
        with self._out:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    def hook(self, event: str, **fields):
        if not self.plugin_dir:
            return None
        return cc_plugin.run_hook(self.plugin_dir, event, cc_plugin.hook_payload(event, self.session_id, self.cwd,
                                                                                  **fields))

    def run(self) -> int:
        self.reg = register({"pid": os.getpid(), "sessionId": self.session_id, "cwd": self.cwd, "kind": "interactive",
                             "startedAt": int(time.time() * 1000), "name": f"{Path(self.cwd).name}-p",
                             "status": "idle"})
        self.emit({"type": "system", "subtype": "init", "session_id": self.session_id, "cwd": self.cwd,
                   "apiKeySource": "ANTHROPIC_API_KEY" if os.environ.get("FAKE_CLAUDE_API_KEY") else "none",
                   "permissionMode": self.mode, "claude_code_version": VERSION, "capabilities": CAPS,
                   "model": self.model, "tools": ["Bash", "Edit", "Read"], "mcp_servers": [],
                   "plugins": [{"name": "jarves", "path": self.plugin_dir}] if self.plugin_dir else [],
                   "uuid": str(uuid.uuid4())})
        self.hook("SessionStart", source="resume" if self.resumed else "startup", model=self.model)
        threading.Thread(target=self._stdin, daemon=True).start()
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.queue or self.closed)
                if not self.queue and self.closed:
                    break
                text = self.queue.pop(0)
            self.turn(text)
        self.hook("SessionEnd", reason="other")
        try:
            self.reg.unlink()
        except OSError:
            pass
        return 0

    def _stdin(self) -> None:
        for line in sys.stdin:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("type") == "user":
                c = (m.get("message") or {}).get("content")
                text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                with self.cv:
                    self.queue.append(text)
                    self.cv.notify_all()
            elif m.get("type") == "control_request":
                if (m.get("request") or {}).get("subtype") == "interrupt":
                    self.interrupted.set()
                self.emit({"type": "control_response", "response": {"subtype": "success",
                                                                     "request_id": m.get("request_id"),
                                                                     "response": {"still_queued": []}}})
        with self.cv:
            self.closed = True
            self.cv.notify_all()

    def say(self, text: str) -> None:
        self.emit({"type": "assistant", "session_id": self.session_id, "parent_tool_use_id": None,
                   "uuid": str(uuid.uuid4()), "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})

    def turn(self, text: str) -> None:
        self._set_status("busy")
        self.interrupted.clear()
        reply = f"Working on: {text[:120]}"
        m = re.search(r"APPROVE:\s*(.+)", text)
        if m:
            cmd = m.group(1).strip().splitlines()[0]
            out = self.hook("PermissionRequest", tool_name="Bash", tool_input={"command": cmd, "description": "run"})
            dec = (((out or {}).get("hookSpecificOutput") or {}).get("decision") or {}).get("behavior")
            reply = f"ran: {cmd}" if dec == "allow" else f"denied: {cmd}"
        elif "correlation_id=" in text:
            cid = re.search(r"correlation_id=([A-Za-z0-9_-]+)", text).group(1)
            reply = f"[{cid}] Status: editing core/app.py for the task; next step: run the targeted tests."
        elif "SLOW" in text:
            self.say("starting a long step")
            self.interrupted.wait(60)
            reply = "(interrupted)" if self.interrupted.is_set() else "long step done"
        else:
            self.hook("PostToolUse", tool_name="Edit", tool_input={"file_path": str(Path(self.cwd) / "core" / "app.py")},
                      tool_response={"ok": True}, tool_use_id="t1", duration_ms=5)
        self.say(reply)
        self.turns += 1
        self.cost += 0.01
        self.emit({"type": "result", "subtype": "success", "is_error": False, "result": reply,
                   "session_id": self.session_id, "total_cost_usd": round(self.cost, 4), "num_turns": self.turns,
                   "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0,
                             "cache_creation_input_tokens": 0}, "permission_denials": [], "uuid": str(uuid.uuid4())})
        out = self.hook("Stop", stop_hook_active=False, last_assistant_message=reply)
        self._set_status("idle")
        if (out or {}).get("decision") == "block" and out.get("reason"):
            with self.cv:
                self.queue.insert(0, out["reason"])
                self.cv.notify_all()

    def _set_status(self, st: str) -> None:
        try:
            e = json.loads(self.reg.read_text(encoding="utf-8"))
            e["status"] = st
            register(e)
        except (OSError, ValueError):
            pass


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print(f"{VERSION} (Claude Code)")
        return 0
    if argv[:2] == ["agents", "--json"]:
        return agents_json()
    if argv[:1] == ["plugin"]:
        return plugin(argv[1:])
    if "-p" in argv or "--print" in argv:
        return Session(argv).run()
    import fake_claude_host
    return fake_claude_host.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
