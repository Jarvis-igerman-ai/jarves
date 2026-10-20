"""The JARVES plugin's hook command (Claude Code runs it for every hook event; exec form, JSON on stdin).

`python -c <boot> --home <profile home> --event <HookEvent>`: it turns the payload into an envelope and hands it to
the Core over the bridge (2-5 s budget; PermissionRequest waits for the owner's decision on the popup up to
agents.permission_wait_s). What it may print is only the documented hook output:
- Stop / SubagentStop: `{"decision":"block","reason": <the owner's queued messages>}` - delivered at this safe stop;
- UserPromptSubmit / SessionStart: `hookSpecificOutput.additionalContext`;
- PermissionRequest: `hookSpecificOutput.decision.behavior` allow / deny - ONLY when the owner decided; a timeout,
  no screen or no Core prints nothing, so Claude Code shows its own terminal dialog as usual.
When the Core is not running, the event is spooled to `<home>/agents/spool/` (replayed once on the next start) and
queued messages are still delivered from agents.db. It never fails the session: every error = exit 0, no output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

SPOOL_MAX = 500
MAX_STDIN = 1_000_000


def _out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def spool(home: Path, env: dict) -> None:
    d = Path(home) / "agents" / "spool"
    try:
        d.mkdir(parents=True, exist_ok=True)
        files = sorted(d.glob("*.json"))
        for old in files[: max(0, len(files) - SPOOL_MAX + 1)]:
            old.unlink(missing_ok=True)
        tmp = d / f"{env['message_id']}.tmp"
        tmp.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / f"{time.time():.6f}-{env['message_id']}.json")
    except OSError:
        pass


def offline_deliveries(home: Path, sid: str, consumer: str) -> tuple[str, list[str]]:
    from .db import AgentDB
    from .envelope import wrap_for_agent
    db = AgentDB(Path(home) / "agents.db")
    try:
        rows = db.claim(sid, consumer)
        if not rows:
            return "", []
        text = wrap_for_agent(rows)
        ids = [r["message_id"] for r in rows]
        db.ack(ids, delivery="stop_hook_offline")
        return text, ids
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarves-hook", add_help=False)
    ap.add_argument("--home", required=True)
    ap.add_argument("--event", default="")
    try:
        a, _ = ap.parse_known_args(argv)
    except SystemExit:
        return 0
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN) if sys.stdin else b""
        payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        if not isinstance(payload, dict):
            return 0
        return run(Path(a.home), a.event or str(payload.get("hook_event_name") or ""), payload)
    except Exception:
        return 0


def run(home: Path, event: str, payload: dict) -> int:
    from . import bridge, claude, procs
    from .envelope import make
    payload = {**payload, "hook_event_name": payload.get("hook_event_name") or event}
    psid = str(payload.get("session_id") or "")
    if not psid:
        return 0
    kind, data, sev = claude.hook_to_event(payload)
    sid = f"claude:{psid}"
    env = make(kind, provider="claude", session_id=sid, payload=data, origin="agent", severity=sev,
               request_id=uuid.uuid4().hex[:16] if kind == "approval_requested" else "").to_dict()
    anc = procs.ancestors()
    ppid = anc[0] if anc else os.getppid()
    wait = 5.0
    if kind == "approval_requested":
        wait = 200.0                                         # the Core answers within agents.permission_wait_s
    reply = None
    try:
        c = bridge.BridgeClient.connect(home, timeout=2.0)
    except bridge.BridgeError:
        c = None
    if c is not None:
        try:
            reply = c.request("hook", timeout=wait, envelope=env, event=event, ppid=ppid, ancestors=anc,
                              cwd=str(payload.get("cwd") or ""))
        except bridge.BridgeError:
            reply = None
        finally:
            c.close()
    if reply is None:                                        # no Core: keep the event, deliver what is queued
        spool(home, {**env, "_ppid": ppid, "_anc": anc, "_event": event})
        if event in ("Stop", "UserPromptSubmit"):
            text, _ = offline_deliveries(home, sid, f"hook-{ppid}")
            reply = {"ok": True, "deliver": text} if text else None
        if reply is None:
            return 0
    if not reply.get("ok"):
        return 0
    text = str(reply.get("deliver") or "")
    if event in ("Stop", "SubagentStop") and text:
        _out({"decision": "block", "reason": text})
    elif event == "UserPromptSubmit" and text:
        _out({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}})
    elif event == "SessionStart" and reply.get("context"):
        _out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": str(reply["context"])}})
    elif event == "PermissionRequest" and reply.get("decision") in ("allow", "deny"):
        dec: dict = {"behavior": reply["decision"]}
        if reply["decision"] == "deny":
            dec["message"] = str(reply.get("message") or "The owner denied this on the JARVES approvals popup.")
        _out({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": dec}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
