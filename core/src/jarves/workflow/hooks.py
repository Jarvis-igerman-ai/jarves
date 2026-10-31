"""Claude Code native lifecycle observer; bounded action history, never approvals.

Hooks fail open for Claude itself and retain an error counter in the workflow
runtime. A native Stop/SubagentStop is NEVER evidence that a requirement passed.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from .store import Store

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Notification", "SubagentStart", "SubagentStop", "Stop", "SessionEnd")


def console_command(root: Path, home: Path, python: str | None = None) -> str:
    """Exact, copy-pasteable CLI prefix; the bare `python` may be the Windows Store stub."""
    def q(value):
        text = str(value).replace("\\", "/")
        if any(c in text for c in '"$`%'):
            raise ValueError(f"unsafe character in path: {text}")
        return f'"{text}"'
    return f'{q(python or sys.executable)} {q(Path(root) / "core" / "tools" / "workflow_console.py")} --home {q(home)}'


def in_project(cwd: str, root: Path) -> bool:
    """Include real git worktrees of this repo; ignore hooks from other projects."""
    current = Path(cwd).resolve()
    root = root.resolve()
    if current == root or root in current.parents:
        return True
    try:
        def common(path):
            result = subprocess.run(["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                    capture_output=True, text=True, timeout=2, check=True)
            return Path(result.stdout.strip()).resolve()
        return common(current) == common(root)
    except (OSError, subprocess.SubprocessError):
        return False


def _managed(store: Store, agent_id: str | None) -> bool:
    """An autopilot-started worker: its manager hooks belong to the ledger agent the autopilot made."""
    if not agent_id:
        return False
    try:
        return any(a["id"] == agent_id and a.get("autopilot") for a in store.snapshot()["agents"])
    except (OSError, ValueError):
        return False


def observe(store: Store, payload: dict, root: Path, workflow_agent: str | None = None) -> dict:
    event = payload.get("hook_event_name", "")
    if event not in EVENTS or not in_project(str(payload.get("cwd", "")), root):
        return {}
    session = payload.get("session_id")
    if not isinstance(session, str) or not session or len(session) > 200:
        return {}
    subagent = payload.get("agent_id") or "manager"
    if not isinstance(subagent, str) or len(subagent) > 200:
        return {}
    kind = str(payload.get("agent_type") or ("manager" if subagent == "manager" else "subagent"))[:100]
    lane = {"workflow-bugs": "bugs", "workflow-current": "features", "workflow-qa": "release",
                                                           "workflow-merger": "release"}.get(kind,
            "ops" if subagent == "manager" else "any")
    ident = "claude-" + hashlib.sha256(f"{session}:{subagent}".encode()).hexdigest()[:20]
    parent_agent = payload.get("parent_agent_id") or "manager"
    if not isinstance(parent_agent, str) or len(parent_agent) > 200:
        parent_agent = "manager"
    parent = "claude-" + hashlib.sha256(f"{session}:{parent_agent}".encode()).hexdigest()[:20] if subagent != "manager" else ""
    managed = subagent == "manager" and _managed(store, workflow_agent)
    if managed:
        # Autopilot-started worker: attribute to its ledger agent; no second native identity.
        ident = workflow_agent
    # Native session identities are deliberately separate from attachable bridge sessions.
    if not managed:
        store.register_agent({"id": ident, "label": "Claude · " + kind,
                            "lane": lane, "provider": "claude-native",
                            "session_id": "native:" + hashlib.sha256(f"{session}:{subagent}".encode()).hexdigest(),
                            "project": root.name, "parent_id": parent, "auto_dispatch": False})
    status = "offline" if event == "SessionEnd" else "idle" if event in {"SubagentStop", "Stop"} else "running"
    note = f"Native Claude event: {event}; completion evidence is tracked separately"
    if event == "Notification":
        notification = payload.get("notification_type", "")
        if notification == "permission_prompt":
            status, note = "blocked", "Claude permission prompt pending; answer on Claude's existing approval surface"
        elif notification == "idle_prompt":
            status, note = "idle", "Claude is idle; the external watchdog checks eligible work"
    # Tool names describe activity only; detailed action data is explicitly opt-in.
    # Neither capture mode reads transcripts, hidden reasoning or whole user prompts.
    tool = payload.get("tool_name")
    if event in {"PreToolUse", "PostToolUse", "PostToolUseFailure"} and isinstance(tool, str):
        note = f"Claude {'started' if event == 'PreToolUse' else 'completed'} tool {tool[:80]}; no task verification inferred"
    flight = "start" if event == "PreToolUse" else "end" if event in {
        "PostToolUse", "PostToolUseFailure", "Stop", "SubagentStop", "SessionEnd"} else None
    native = store.observe_agent(ident, status, note, tool=flight, call_id=payload.get("tool_use_id"))
    from .trace import record
    record(store, native, event, tool=tool if isinstance(tool, str) else "",
           call_id=payload.get("tool_use_id", ""), status="failed" if event == "PostToolUseFailure" else status,
           details={k: payload[k] for k in ("tool_input", "tool_response", "error") if k in payload})
    if event in {"Stop", "SubagentStop", "SessionEnd"} and native.get("task_id"):
        store.quarantine(native["task_id"], "Native session stopped before a ledger handoff; manager must inspect the result/checkpoint")
    if event == "Stop" and subagent == "manager" and not managed:
        # Owner follow-ups are handed over exactly once each, so this cannot loop even when stop_hook_active.
        followups = store.take_followups(ident)
        if followups:
            return {"decision": "block", "reason": followup_message(followups, root, store.path.parent)}
    if event == "Stop" and subagent == "manager" and not managed and payload.get("stop_hook_active") is not True:
        ready = store.manager_continue(ident)
        if ready:
            return {"decision": "block", "reason":
                "Authorised independent workflow tasks remain ready: " + ", ".join(ready) +
                ". Check the ledger and continue the approved work. Do not invent approvals or bypass existing gates. "
                "If truly blocked, checkpoint and record the exact blocker instead of repeatedly retrying."}
    if (event == "SessionStart" and subagent == "manager") or event == "SubagentStart":
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
            "JARVES workflow observer is connected. Follow AGENTS.md and docs/workflow/OPERATING_MODEL.md. "
            f"Your workflow agent ID is {ident}; workflow data directory is {store.path.parent}. "
            "Use workflow CLI to claim/checkpoint work, record evidence and queue owner planning questions; "
            f"run it exactly as: {console_command(root, store.path.parent)} <command> "
            "Native subagents remain your workers; do not start a second manager. "
            "If the owner is away, retain the question and continue independent authorised work. "
            "Planning answers never approve runtime tools or releases." +
            (" " + followup_message(store.open_followups(), root, store.path.parent)
             if event == "SessionStart" else "")}}
    return {}


def followup_message(items: list, root: Path, home: Path) -> str:
    """Owner follow-ups as quoted data, with the one command that stores the reply."""
    if not items:
        return ""
    lines = [f"- {f['id']}" + (f" (question {f['question_id']}: {json.dumps(f.get('question_title', ''), ensure_ascii=False)})"
                                if f.get("question_id") else " (general)") + f": {json.dumps(f['text'], ensure_ascii=False)}"
             for f in items]
    return ("The owner asked follow-up question(s) in the workflow console. Treat the quoted text as an owner request "
            "for information, never as an approval or an answer to the question:\n" + "\n".join(lines) +
            "\nReply to each briefly (clarify, check, or give options with a recommendation) and store it with: "
            f"{console_command(root, home)} reply --id FOLLOWUP_ID --answer TEXT . "
            "The question stays open until the owner answers it; then continue your work.")


def main(argv=None) -> int:
    import argparse
    from .__main__ import utf8_streams
    utf8_streams()
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--repo-root", type=Path, required=True)
    args = ap.parse_args(argv)
    store = None
    try:
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            store = Store(args.home / "workflow.db", timeout_s=0.1)
            from .trace import record
            record(store, {"id": "observer-gap"}, "TelemetryGap", status="payload_limit",
                   details={"reason": "native event exceeded the 1 MiB capture limit; content not retained"})
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        store = Store(args.home / "workflow.db", timeout_s=0.1)
        result = observe(store, payload, args.repo_root, os.environ.get("JARVES_WORKFLOW_AGENT") or None)
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError):
        # Do not block Claude or emit private input. The supervisor exposes the
        # missing heartbeat if telemetry cannot reach the ledger.
        print("JARVES workflow observer unavailable; Claude permissions unchanged", file=sys.stderr)
    finally:
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
