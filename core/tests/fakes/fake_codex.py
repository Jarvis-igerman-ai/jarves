"""A FAKE `codex` CLI with an App Server that speaks the codex-cli 0.163.0 protocol (method names, required fields
and enums recorded in tests/fixtures/agents/codex_app_server.json): JSON-RPC 2.0 lines WITHOUT the "jsonrpc" field,
`initialize` then `initialized` (requests before it fail "Not initialized").

    fake_codex.py --version | app-server | app-server daemon version (exit 1: no daemon)
State in $FAKE_CODEX_DIR: threads.json = threads that exist from elsewhere (the owner's TUI / app: source vscode,
status notLoaded). Turn behaviour by markers in the input text: "RUN: <cmd>" = a command that needs approval (a
server REQUEST item/commandExecution/requestApproval; accept runs it, decline skips it), "SLOW" = waits for
turn/steer or turn/interrupt. $FAKE_CODEX_ACCOUNT=apiKey makes the account an API key.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

STATE = Path(os.environ.get("FAKE_CODEX_DIR") or Path.cwd() / ".fake_codex")
_out = threading.Lock()
# the recorded contract: a request missing a field the real 0.163.0 schema requires is refused (-32602)
FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "agents" / "codex_app_server.json")
                     .read_text(encoding="utf-8"))
PARAMS_TYPE = {"initialize": "InitializeParams", "thread/start": "ThreadStartParams",
               "thread/resume": "ThreadResumeParams", "thread/list": "ThreadListParams",
               "thread/read": "ThreadReadParams", "turn/start": "TurnStartParams", "turn/steer": "TurnSteerParams",
               "turn/interrupt": "TurnInterruptParams", "account/read": "GetAccountParams"}
DECISIONS = set(FIXTURE["enums"]["CommandExecutionApprovalDecision"])


def send(obj: dict) -> None:
    with _out:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def thread_obj(tid: str, cwd: str, source: str, status: dict, preview: str = "", turns=None) -> dict:
    now = int(time.time())
    return {"id": tid, "sessionId": tid, "cwd": cwd, "source": source, "status": status, "preview": preview,
            "name": preview[:30] or None, "createdAt": now - 60, "updatedAt": now, "cliVersion": "0.163.0",
            "ephemeral": False, "modelProvider": "openai", "projectId": "p", "turns": turns or [],
            "gitInfo": {"branch": "main"}}


class Server:
    def __init__(self) -> None:
        self.inited = False
        self.threads: dict[str, dict] = {}
        self.loaded: set[str] = set()
        self.pending: dict[int, threading.Event] = {}
        self.answers: dict[int, dict] = {}
        self.next_req = 1000
        self.turn_events: dict[str, threading.Event] = {}
        self.steers: dict[str, list[str]] = {}
        self.interrupts: set[str] = set()
        try:
            for t in json.loads((STATE / "threads.json").read_text(encoding="utf-8")):
                self.threads[t["id"]] = thread_obj(t["id"], t["cwd"], t.get("source", "vscode"), {"type": "notLoaded"},
                                                   t.get("preview", ""))
        except (OSError, ValueError):
            pass

    def handle(self, m: dict) -> None:
        if "method" not in m:                                   # a response to OUR server request
            ev = self.pending.get(m.get("id"))
            if ev is not None:
                res = m.get("result") or {}
                if res.get("decision") not in DECISIONS:        # contract: one of the recorded decision values
                    res = {"decision": "__invalid__"}
                self.answers[m["id"]] = res
                ev.set()
            return
        method, mid, p = m["method"], m.get("id"), m.get("params") or {}
        typ = PARAMS_TYPE.get(method)
        if typ and mid is not None:
            missing = [k for k in FIXTURE["types"][typ]["required"] if k not in p]
            unknown = [k for k in p if k not in FIXTURE["types"][typ]["properties"]]
            if missing or unknown:
                return send({"id": mid, "error": {"code": -32602, "message": f"invalid params: missing {missing} "
                                                                             f"unknown {unknown}"}})
        if method == "initialized":
            self.inited = True
            return
        if mid is None:
            return
        if method == "initialize":
            if self.inited:
                return send({"id": mid, "error": {"code": -32600, "message": "Already initialized"}})
            return send({"id": mid, "result": {"codexHome": str(STATE), "platformFamily": "windows",
                                               "platformOs": "windows", "userAgent": "fake/0.163.0"}})
        if not self.inited:
            return send({"id": mid, "error": {"code": -32600, "message": "Not initialized"}})
        fn = getattr(self, "m_" + method.replace("/", "_"), None)
        if fn is None:
            return send({"id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}})
        try:
            send({"id": mid, "result": fn(p)})
        except KeyError as e:
            send({"id": mid, "error": {"code": -32600, "message": f"thread not loaded: {e.args[0]}"}})
        except ValueError as e:
            send({"id": mid, "error": {"code": -32602, "message": str(e)}})

    # ------------------------------------------------------------------ methods
    def m_account_read(self, p):
        typ = os.environ.get("FAKE_CODEX_ACCOUNT", "chatgpt")
        acc = {"type": "apiKey"} if typ == "apiKey" else {"type": "chatgpt", "planType": "plus", "email": None}
        return {"account": acc, "requiresOpenaiAuth": True}

    def m_thread_list(self, p):
        rows = list(self.threads.values())
        if p.get("cwd"):
            rows = [t for t in rows if t["cwd"] == p["cwd"]]
        return {"data": rows[: int(p.get("limit") or 25)], "nextCursor": None}

    def m_thread_loaded_list(self, p):
        return {"data": sorted(self.loaded), "nextCursor": None}

    def m_thread_start(self, p):
        tid = str(uuid.uuid4())
        t = thread_obj(tid, p.get("cwd") or str(Path.cwd()), "appServer", {"type": "idle"})
        self.threads[tid] = t
        self.loaded.add(tid)
        threading.Thread(target=lambda: send({"method": "thread/started", "params": {"thread": t}}), daemon=True).start()
        return {"thread": t, "approvalPolicy": p.get("approvalPolicy", "on-request"), "approvalsReviewer": "user",
                "cwd": t["cwd"], "model": p.get("model") or "gpt-fake", "modelProvider": "openai",
                "sandbox": p.get("sandbox", "workspace-write")}

    def m_thread_resume(self, p):
        t = self.threads[p["threadId"]]
        self.loaded.add(t["id"])
        t["status"] = {"type": "idle"}
        send({"method": "thread/status/changed", "params": {"threadId": t["id"], "status": t["status"]}})
        return {"thread": t, "approvalPolicy": "on-request", "approvalsReviewer": "user", "cwd": t["cwd"],
                "model": "gpt-fake", "modelProvider": "openai", "sandbox": "workspace-write"}

    def m_thread_read(self, p):
        t = self.threads[p["threadId"]]
        return {"thread": t if p.get("includeTurns") else {**t, "turns": []}}

    def m_turn_start(self, p):
        tid = p["threadId"]
        if tid not in self.loaded:
            raise KeyError(tid)
        text = " ".join(x.get("text", "") for x in p.get("input") or [] if x.get("type") == "text")
        turn = {"id": str(uuid.uuid4()), "status": "inProgress", "items": [], "error": None}
        self.turn_events[turn["id"]] = threading.Event()
        threading.Thread(target=self._run_turn, args=(tid, turn["id"], text), daemon=True).start()
        return {"turn": turn}

    def m_turn_steer(self, p):
        tid, exp = p["threadId"], p["expectedTurnId"]
        if exp not in self.turn_events:
            raise ValueError("expectedTurnId does not match the active turn")
        self.steers.setdefault(exp, []).append(" ".join(x.get("text", "") for x in p.get("input") or []))
        self.turn_events[exp].set()
        return {"turnId": exp}

    def m_turn_interrupt(self, p):
        self.interrupts.add(p["turnId"])
        ev = self.turn_events.get(p["turnId"])
        if ev is not None:
            ev.set()
        return {}

    # ------------------------------------------------------------------ a turn
    def _status(self, tid: str, st: dict) -> None:
        self.threads[tid]["status"] = st
        send({"method": "thread/status/changed", "params": {"threadId": tid, "status": st}})

    def _item(self, tid, turn_id, item) -> None:
        now = int(time.time() * 1000)
        send({"method": "item/started", "params": {"threadId": tid, "turnId": turn_id, "item": item, "startedAtMs": now}})
        if item["type"] == "agentMessage":
            send({"method": "item/agentMessage/delta", "params": {"threadId": tid, "turnId": turn_id,
                                                                  "itemId": item["id"], "delta": item["text"][:10]}})
        send({"method": "item/completed", "params": {"threadId": tid, "turnId": turn_id, "item": item,
                                                     "completedAtMs": now}})

    def _run_turn(self, tid: str, turn_id: str, text: str) -> None:
        turn = {"id": turn_id, "status": "inProgress", "items": [], "error": None}
        self._status(tid, {"type": "active", "activeFlags": []})
        send({"method": "turn/started", "params": {"threadId": tid, "turn": turn}})
        final = f"Codex worked on: {text[:80]}"
        status = "completed"
        if "RUN:" in text:
            cmd = text.split("RUN:", 1)[1].strip().splitlines()[0]
            self.next_req += 1
            rid = self.next_req
            ev = threading.Event()
            self.pending[rid] = ev
            self._status(tid, {"type": "active", "activeFlags": ["waitingOnApproval"]})
            item_id = str(uuid.uuid4())
            send({"id": rid, "method": "item/commandExecution/requestApproval", "params": {
                "threadId": tid, "turnId": turn_id, "itemId": item_id, "startedAtMs": int(time.time() * 1000),
                "command": cmd, "cwd": self.threads[tid]["cwd"], "reason": "needs to run a command"}})
            ev.wait(300)
            decision = (self.answers.get(rid) or {}).get("decision")
            send({"method": "serverRequest/resolved", "params": {"threadId": tid, "requestId": rid}})
            self._status(tid, {"type": "active", "activeFlags": []})
            if decision == "accept":
                self._item(tid, turn_id, {"type": "commandExecution", "id": item_id, "command": cmd, "cwd": "",
                                          "commandActions": [], "status": "completed"})
                final = f"ran: {cmd}"
            else:
                final = f"declined: {cmd}"
        elif "SLOW" in text:
            self._item(tid, turn_id, {"type": "agentMessage", "id": str(uuid.uuid4()), "text": "working slowly"})
            self.turn_events[turn_id].wait(120)
            if turn_id in self.interrupts:
                status, final = "interrupted", "interrupted"
            else:
                final = "steered: " + "; ".join(self.steers.get(turn_id, []))
        if status == "completed":
            self._item(tid, turn_id, {"type": "fileChange", "id": str(uuid.uuid4()), "status": "completed",
                                      "changes": [{"path": "src/fake.py", "kind": "update"}]})
        self._item(tid, turn_id, {"type": "agentMessage", "id": str(uuid.uuid4()), "text": final})
        send({"method": "thread/tokenUsage/updated", "params": {"threadId": tid, "turnId": turn_id, "tokenUsage": {
            "total": {"inputTokens": 120, "cachedInputTokens": 0, "outputTokens": 30, "reasoningOutputTokens": 0,
                      "totalTokens": 150}, "last": {"inputTokens": 120, "cachedInputTokens": 0, "outputTokens": 30,
                                                    "reasoningOutputTokens": 0, "totalTokens": 150}}}})
        self.threads[tid]["turns"].append({"id": turn_id, "status": status, "items": [
            {"type": "agentMessage", "id": "m", "text": final}], "error": None})
        self._status(tid, {"type": "idle"})
        send({"method": "turn/completed", "params": {"threadId": tid, "turn": {**turn, "status": status}}})


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print("codex-cli 0.163.0")
        return 0
    if argv[:3] == ["app-server", "daemon", "version"]:
        print("Error: failed to connect to app-server-control.sock", file=sys.stderr)
        return 1
    if argv[:1] == ["app-server"]:
        srv = Server()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and "jsonrpc" in m:
                continue                                  # 0.163.0 omits the field; a client that sends it is wrong
            srv.handle(m)
        return 0
    print("fake codex: unsupported", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
