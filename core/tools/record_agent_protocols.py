"""Record the protocol SHAPES of the installed Claude Code and Codex for the orchestrator's contract tests (SW-V4 E).

    python tools/record_agent_protocols.py            # Codex schema (free) + `claude agents --json` keys (free)
    python tools/record_agent_protocols.py --live     # also one tiny `claude -p` stream-json turn (haiku; on the
                                                      # subscription: a few tenths of a cent of estimated cost)

Writes core/tests/fixtures/agents/codex_app_server.json and claude_code.json: method names, required fields and the
keys real messages carried - never values (no prompts, paths, e-mails, tokens). The fakes in core/tests/fakes/ are
checked against these files (tests/test_agents_contract.py).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "agents"
CODEX_TYPES = ("InitializeParams", "InitializeResponse", "ThreadStartParams", "ThreadStartResponse", "ThreadResumeParams",
               "ThreadListParams", "ThreadListResponse", "ThreadReadParams", "TurnStartParams", "TurnStartResponse",
               "TurnSteerParams", "TurnInterruptParams", "CommandExecutionRequestApprovalParams",
               "CommandExecutionRequestApprovalResponse", "FileChangeRequestApprovalParams",
               "FileChangeRequestApprovalResponse", "ThreadTokenUsageUpdatedNotification", "TurnCompletedNotification",
               "TurnStartedNotification", "ItemCompletedNotification", "ItemStartedNotification",
               "ThreadStatusChangedNotification", "AgentMessageDeltaNotification", "GetAccountParams",
               "GetAccountResponse", "ThreadStartedNotification", "ErrorNotification")
USED_CLIENT = ("initialize", "thread/start", "thread/resume", "thread/list", "thread/read", "thread/loaded/list",
               "turn/start", "turn/steer", "turn/interrupt", "account/read")
USED_NOTIF = ("thread/started", "thread/status/changed", "thread/tokenUsage/updated", "turn/started", "turn/completed",
              "item/started", "item/completed", "item/agentMessage/delta", "error")
USED_SERVER_REQ = ("item/commandExecution/requestApproval", "item/fileChange/requestApproval")


def _methods(d: dict) -> list[str]:
    out = []
    for v in d.get("oneOf", []):
        out += v.get("properties", {}).get("method", {}).get("enum", [])
    return out


def codex_fixture(exe: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([exe, "app-server", "generate-json-schema", "--out", tmp], check=True, capture_output=True,
                       timeout=120)
        root = Path(tmp)
        load = lambda n: json.loads(next(root.rglob(f"{n}.json")).read_text(encoding="utf-8"))  # noqa: E731
        allv2 = json.loads((root / "codex_app_server_protocol.v2.schemas.json").read_text(encoding="utf-8"))
        defs = allv2.get("definitions", {})
        types = {}
        for n in CODEX_TYPES:
            d = load(n)
            types[n] = {"required": sorted(d.get("required") or []), "properties": sorted(d.get("properties") or {})}
        enums = {"ThreadStatus": sorted(v["properties"]["type"]["enum"][0] for v in defs["ThreadStatus"]["oneOf"]),
                 "TurnStatus": sorted(defs["TurnStatus"]["enum"]),
                 "ThreadActiveFlag": sorted(defs["ThreadActiveFlag"]["enum"]),
                 "ThreadItemType": sorted(v["properties"]["type"]["enum"][0] for v in defs["ThreadItem"]["oneOf"]),
                 "AccountType": sorted(v["properties"]["type"]["enum"][0] for v in defs["Account"]["oneOf"]),
                 "CommandExecutionApprovalDecision": sorted(
                     x["enum"][0] for x in load("CommandExecutionRequestApprovalResponse")["definitions"]
                     ["CommandExecutionApprovalDecision"]["oneOf"] if "enum" in x),
                 "FileChangeApprovalDecision": sorted(
                     x["enum"][0] for x in load("FileChangeRequestApprovalResponse")["definitions"]
                     ["FileChangeApprovalDecision"]["oneOf"] if "enum" in x)}
        thread_req = sorted(defs["Thread"].get("required") or [])
        cr, sn, sr = _methods(load("ClientRequest")), _methods(load("ServerNotification")), _methods(load("ServerRequest"))
    ver = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    live = _codex_live(exe)
    return {"recorded": time.strftime("%Y-%m-%d"), "version": ver,
            "client_requests_used": [m for m in USED_CLIENT if m in cr], "missing_client_requests":
            [m for m in USED_CLIENT if m not in cr], "notifications_used": [m for m in USED_NOTIF if m in sn],
            "server_requests_used": [m for m in USED_SERVER_REQ if m in sr], "types": types, "enums": enums,
            "thread_required": thread_req, "live": live}


def _codex_live(exe: str) -> dict:
    p = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, encoding="utf-8")
    out: dict = {}

    def rpc(i, method, params):
        p.stdin.write(json.dumps({"id": i, "method": method, "params": params}) + "\n")
        p.stdin.flush()
        notes = []
        while True:
            m = json.loads(p.stdout.readline())
            if m.get("id") == i:
                return m, notes
            notes.append(m.get("method"))
    try:
        r, notes = rpc(1, "initialize", {"clientInfo": {"name": "jarves_recorder", "title": "rec", "version": "1"}})
        out["initialize_result_keys"] = sorted(r["result"])
        out["notifications_after_initialize"] = sorted(set(n for n in notes if n))
        p.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        p.stdin.flush()
        r, _ = rpc(2, "account/read", {"refreshToken": False})
        acc = r["result"].get("account") or {}
        out["account_result_keys"] = sorted(r["result"])
        out["account_keys"] = sorted(acc)
        out["account_type"] = acc.get("type")
        r, _ = rpc(3, "thread/list", {"limit": 3})
        out["thread_list_result_keys"] = sorted(r["result"])
        out["thread_keys"] = sorted(set().union(*[set(t) for t in r["result"]["data"]])) if r["result"]["data"] else []
        out["thread_status_seen"] = sorted({(t.get("status") or {}).get("type", "") for t in r["result"]["data"]})
        r, _ = rpc(4, "thread/read", {"threadId": "00000000-0000-0000-0000-000000000000"})
        out["unknown_thread_error_code"] = (r.get("error") or {}).get("code")
    finally:
        p.stdin.close()
        p.wait(20)
    return out


def claude_fixture(exe: str, live: bool) -> dict:
    ver = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    rows = json.loads(subprocess.run([exe, "agents", "--json"], capture_output=True, text=True, timeout=60).stdout or "[]")
    d = {"recorded": time.strftime("%Y-%m-%d"), "version": ver,
         "agents_json_keys": sorted(set().union(*[set(r) for r in rows])) if rows else [],
         "agents_json_status_seen": sorted({r.get("status") for r in rows if r.get("status")}),
         # documented (code.claude.com/docs/en/hooks, channels-reference), checked 2026-10-20
         "hook_common_fields": ["cwd", "hook_event_name", "permission_mode", "session_id", "transcript_path"],
         "hook_events_used": ["SessionStart", "UserPromptSubmit", "PostToolUse", "Notification", "PermissionRequest",
                              "Stop", "SubagentStop", "StopFailure", "SessionEnd"],
         "hook_fields": {"SessionStart": ["source", "model"], "UserPromptSubmit": ["prompt"],
                         "PostToolUse": ["tool_name", "tool_input", "tool_response", "tool_use_id"],
                         "Notification": ["message", "title", "notification_type"],
                         "PermissionRequest": ["tool_name", "tool_input"],
                         "Stop": ["stop_hook_active", "last_assistant_message"], "SessionEnd": ["reason"]},
         "channel": {"capability": "claude/channel", "permission_capability": "claude/channel/permission",
                     "notification": "notifications/claude/channel", "params": ["content", "meta"],
                     "permission_request": "notifications/claude/channel/permission_request",
                     "permission_request_params": ["request_id", "tool_name", "description", "input_preview"],
                     "permission_verdict": "notifications/claude/channel/permission",
                     "permission_verdict_params": ["request_id", "behavior"], "request_id_alphabet": "a-km-z{5}",
                     "refused_protocol_revision": "2026-07-28"}}
    if live:
        d["stream_json"] = _claude_live(exe)
    return d


def _claude_live(exe: str) -> dict:
    work = tempfile.mkdtemp(prefix="jarves_rec_")
    p = subprocess.Popen([exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                          "--model", "haiku", "--no-session-persistence", "--setting-sources", "project",
                          "--strict-mcp-config", "--permission-mode", "default"], cwd=work, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": "Reply with exactly: OK"},
                              "parent_tool_use_id": None, "session_id": ""}) + "\n")
    p.stdin.flush()
    seen: dict = {}
    t0 = time.time()
    while time.time() - t0 < 180:
        line = p.stdout.readline()
        if not line:
            break
        m = json.loads(line)
        key = f"{m.get('type')}/{m.get('subtype') or ''}"
        seen.setdefault(key, sorted(m))
        if key == "system/init":
            seen["init_api_key_source"] = m.get("apiKeySource")          # "none" = the subscription login
            seen["init_capabilities"] = sorted(m.get("capabilities") or [])
        if m.get("type") == "result":
            seen["result_cost_present"] = "total_cost_usd" in m
            break
    p.stdin.write(json.dumps({"type": "control_request", "request_id": "rec-1", "request": {"subtype": "interrupt"}})
                  + "\n")
    p.stdin.flush()
    line = p.stdout.readline()
    try:
        seen["control_response"] = sorted(json.loads(line)) if line else []
    except ValueError:
        pass
    p.stdin.close()
    p.wait(30)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also one tiny claude -p turn (subscription)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    cx = shutil.which("codex") or shutil.which("codex.exe")
    if cx:
        (OUT / "codex_app_server.json").write_text(json.dumps(codex_fixture(cx), indent=1, sort_keys=True) + "\n",
                                                   encoding="utf-8")
        print("codex fixture written")
    cl = shutil.which("claude.cmd") or shutil.which("claude")
    if cl:
        (OUT / "claude_code.json").write_text(json.dumps(claude_fixture(cl, a.live), indent=1, sort_keys=True) + "\n",
                                              encoding="utf-8")
        print("claude fixture written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
