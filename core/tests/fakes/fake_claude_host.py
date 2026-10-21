"""A FAKE interactive Claude Code session the owner "started by hand" (not launched by JARVES). It runs the REAL
JARVES plugin the way Claude Code 2.1.304 does (tests/fixtures/agents/claude_code.json): registers itself so the fake
`claude agents --json` lists it, starts the plugin's MCP server as its own child (initialize asks for revision
2026-07-28 like the v2 runtime), and runs the plugin hooks on command. Started with
`--dangerously-load-development-channels plugin:jarves@jarves-local` it behaves as a channel-enabled session (the
plugin checks that flag on its parent's command line).

Driven by JSON lines on stdin, reports JSON lines on stdout (`{"ev": ...}`):
  {"do":"prompt","text":...}            UserPromptSubmit (+ status busy)
  {"do":"tool","file":...}              PostToolUse Edit
  {"do":"permission","command":...}     Notification permission_prompt + PermissionRequest; reports the decision
  {"do":"stop","text":...}              Stop; a "block" reason is reported and (auto-reply) answered via jarves_reply
  {"do":"report","summary":...,...}     tools/call jarves_report_status
  {"do":"hook","event":E,"payload":{}}  any hook with a hand-made payload (spoof tests)
  {"do":"end"}                          SessionEnd, unregister, exit
Channel pushes (notifications/claude/channel) are reported as {"ev":"channel",...}; with FAKE_AUTO_REPLY=1 the fake
answers each one with jarves_reply (like Claude following the channel instructions).
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
import fake_claude  # noqa: E402

_out = threading.Lock()


def report(**kw) -> None:
    with _out:
        sys.stdout.write(json.dumps(kw) + "\n")
        sys.stdout.flush()


def main(argv: list[str]) -> int:
    def opt(name, default=""):
        return argv[argv.index(name) + 1] if name in argv else default
    sid = opt("--session-id") or str(uuid.uuid4())
    plugin_dir = opt("--plugin-dir")
    cwd = os.getcwd()
    auto = os.environ.get("FAKE_AUTO_REPLY") == "1"
    entry = {"pid": os.getpid(), "sessionId": sid, "cwd": cwd, "kind": "interactive",
             "startedAt": int(time.time() * 1000), "name": opt("--name") or f"{Path(cwd).name}-2f", "status": "idle"}
    reg = fake_claude.register(entry)

    def status(st: str, waiting: str = "") -> None:
        e = dict(entry, status=st)
        if waiting:
            e["waitingFor"] = waiting
        fake_claude.register(e)

    def hook(event: str, **fields):
        return cc_plugin.run_hook(plugin_dir, event, cc_plugin.hook_payload(event, sid, cwd, **fields))

    mcp = cc_plugin.Mcp(plugin_dir, cwd)

    def on_note(m: dict) -> None:
        if m.get("method") == "notifications/claude/channel":
            p = m.get("params") or {}
            report(ev="channel", content=p.get("content"), meta=p.get("meta"))
            cid = (p.get("meta") or {}).get("correlation_id") or ""
            if auto and cid and "asks" in (p.get("content") or ""):
                r = mcp.request("tools/call", {"name": "jarves_reply", "arguments": {
                    "correlation_id": cid, "text": "Live: refactoring the bridge; tests next.", "status": "working"}})
                report(ev="replied", via="channel", result=r.get("result"))
    mcp.on_notification = on_note
    init = mcp.handshake()
    tools = mcp.request("tools/list")
    report(ev="mcp", protocol=init["result"]["protocolVersion"],
           capabilities=init["result"]["capabilities"], tools=[t["name"] for t in tools["result"]["tools"]])
    out = hook("SessionStart", source="startup", model="claude-fake")
    report(ev="session_start", pid=os.getpid(), session_id=sid, output=out)
    for line in sys.stdin:
        try:
            c = json.loads(line)
        except ValueError:
            continue
        do = c.get("do")
        if do == "prompt":
            status("busy")
            out = hook("UserPromptSubmit", prompt=c.get("text", ""))
            report(ev="prompt", output=out)
        elif do == "tool":
            out = hook("PostToolUse", tool_name="Edit", tool_input={"file_path": c.get("file", "x.py")},
                       tool_response={"ok": True}, tool_use_id="t", duration_ms=3)
            report(ev="tool", output=out)
        elif do == "permission":
            status("waiting", "permission prompt")
            hook("Notification", message="Claude needs your permission to use Bash", title="Permission needed",
                 notification_type="permission_prompt")
            t0 = time.time()
            out = hook("PermissionRequest", tool_name=c.get("tool", "Bash"),
                       tool_input={"command": c.get("command", ""), "description": "run a command"})
            dec = (((out or {}).get("hookSpecificOutput") or {}).get("decision") or {})
            status("busy")
            report(ev="permission", decision=dec.get("behavior"), message=dec.get("message"),
                   seconds=round(time.time() - t0, 2))
        elif do == "stop":
            out = hook("Stop", stop_hook_active=False, last_assistant_message=c.get("text", "done"))
            report(ev="stop", output=out)
            reason = (out or {}).get("reason") or ""
            if (out or {}).get("decision") == "block":
                status("busy")
                cids = re.findall(r"correlation_id=([A-Za-z0-9_-]+)\] The owner asks", reason)
                for cid in cids if auto else []:
                    r = mcp.request("tools/call", {"name": "jarves_reply", "arguments": {
                        "correlation_id": cid, "text": "At my stop: tests pass; next I commit.", "status": "working"}})
                    report(ev="replied", via="stop", result=r.get("result"))
                out2 = hook("Stop", stop_hook_active=True, last_assistant_message="answered the owner")
                report(ev="stop2", output=out2)
            status("idle")
        elif do == "report":
            r = mcp.request("tools/call", {"name": "jarves_report_status", "arguments": c.get("args") or {}})
            report(ev="reported", result=r.get("result"))
        elif do == "inbox":
            r = mcp.request("tools/call", {"name": "jarves_inbox", "arguments": {}})
            report(ev="inbox", result=r.get("result"))
        elif do == "reply":
            r = mcp.request("tools/call", {"name": "jarves_reply", "arguments": c.get("args") or {}})
            report(ev="reply_result", result=r.get("result"))
        elif do == "hook":
            payload = {**cc_plugin.hook_payload(c["event"], sid, cwd), **(c.get("payload") or {})}
            out = cc_plugin.run_hook(plugin_dir, c["event"], payload)
            report(ev="hook", event=c["event"], output=out)
        elif do == "end":
            hook("SessionEnd", reason="prompt_input_exit")
            break
    mcp.close()
    try:
        reg.unlink()
    except OSError:
        pass
    report(ev="ended")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
