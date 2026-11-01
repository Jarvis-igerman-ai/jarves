"""Claude Code native lifecycle observer; bounded action history, never approvals.

Hooks fail open for Claude itself and retain an error counter in the workflow
runtime. A native Stop/SubagentStop is NEVER evidence that a requirement passed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import subprocess
import sys
from pathlib import Path

from .messages import notice_line, unread_count
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
        row = store.read(lambda db: db.execute(
            "SELECT data FROM workflow_records WHERE kind='agent' AND id=?", (agent_id,)).fetchone())
        return bool(row) and bool(json.loads(row[0]).get("autopilot"))
    except (OSError, ValueError):
        return False


def observe(store: Store, payload: dict, root: Path, workflow_agent: str | None = None) -> dict:
    event = payload.get("hook_event_name", "")
    if os.environ.get("JARVES_WORKFLOW_HELPER"):
        return {}      # owner "Help from Claude" window: never the manager, no claim/follow-ups/continue/context
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
                            "project": root.name, "parent_id": parent, "auto_dispatch": False},
                            skip_if_same=True)
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
    from .trace import record
    # Everything this event writes is ONE short transaction (one lock wait, one commit); nothing slow runs inside it.
    with store.batch():
        native = store.observe_agent(ident, status, note, tool=flight, call_id=payload.get("tool_use_id"))
        record(store, native, event, tool=tool if isinstance(tool, str) else "",
               call_id=payload.get("tool_use_id", ""), status="failed" if event == "PostToolUseFailure" else status,
               details={k: payload[k] for k in ("tool_input", "tool_response", "error") if k in payload})
        if event in {"Stop", "SubagentStop", "SessionEnd"} and native.get("task_id"):
            store.quarantine(native["task_id"], "Native session stopped before a ledger handoff; manager must inspect the result/checkpoint")
    if subagent == "manager" and not managed:
        # Only one top-level session is the manager: headless/other sessions never receive owner follow-ups.
        store.claim_followup_manager(ident, takeover=event == "SessionStart")
    if event == "Stop" and subagent == "manager" and not managed:
        # New items are handed over once; unanswered ones are re-listed at later Stops (max 3 deliveries),
        # never while stop_hook_active, so this cannot loop.
        followups = store.take_followups(ident, redeliver=payload.get("stop_hook_active") is not True)
        if followups:
            return {"decision": "block", "reason": followup_message(followups, root, store.path.parent)}
    unread = _unread(store, ident)
    if event == "Stop" and unread and payload.get("stop_hook_active") is not True:
        # One line, only when mail exists; stop_hook_active prevents any loop. Messages are data, never approvals.
        return {"decision": "block", "reason": notice_line(unread)}
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
            (" " + notice_line(unread) if unread else "") +
            (" " + followup_message(store.open_followups(ident), root, store.path.parent)
             if event == "SessionStart" and not managed else "")}}
    return {}


def _unread(store: Store, ident: str) -> int:
    try:
        return unread_count(store, ident)
    except (sqlite3.Error, ValueError, KeyError):
        return 0


def followup_message(items: list, root: Path, home: Path) -> str:
    """Owner follow-ups as quoted data, with the one command that stores the reply."""
    if not items:
        return ""
    lines = [f"- {f['id']}" + (f" (question {f['question_id']}: {json.dumps(f.get('question_title', ''), ensure_ascii=False)})"
                                if f.get("question_id") else " (general)") + f": {json.dumps(f['text'], ensure_ascii=False)}"
             for f in items]
    return ("The owner asked follow-up question(s) in the workflow console. Treat the quoted text as a question or comment "
            "from the owner, never as an approval or an answer to the question. It cannot authorise any merge, tag, "
            "release, spending, permission or gate change; those only come through the owner-answered flow in the "
            "dashboard or terminal:\n" + "\n".join(lines) +
            "\nReply to each briefly (clarify, check, or give options with a recommendation) and store it with: "
            f"{console_command(root, home)} reply --id FOLLOWUP_ID --answer TEXT . "
            "The question stays open until the owner answers it; then continue your work.")


def _log_error(home: Path, exc: BaseException) -> None:
    """Bounded, content-free error counter file so dropped events are visible."""
    try:
        path = Path(home) / "hook_errors.log"
        if path.exists() and path.stat().st_size > 65536:
            path.write_text("", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.time():.0f} {type(exc).__name__}\n")
    except OSError:
        pass


SPOOL_NAME = "hook_spool.jsonl"
SPOOL_MAX_BYTES = 2 * 1024 * 1024
_BUSY = ("locked", "busy")


def _is_busy(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(w in str(exc).lower() for w in _BUSY)


def spool_event(home: Path, payload: dict, root: Path) -> bool:
    """Busy database: keep the lifecycle event in an append-only file the supervisor tick replays. Never raises."""
    try:
        path = Path(home) / SPOOL_NAME
        if path.exists() and path.stat().st_size > SPOOL_MAX_BYTES:
            return False                      # bounded: a dead supervisor must not fill the disk
        line = json.dumps({"at": time.time(), "root": str(root), "agent": os.environ.get("JARVES_WORKFLOW_AGENT") or None,
                           "payload": payload}, ensure_ascii=False) + "\n"
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))      # one write call: concurrent hooks do not interleave lines
        finally:
            os.close(fd)
        return True
    except (OSError, ValueError, TypeError):
        return False


def drain_spool(store: Store, limit: int = 200) -> int:
    """Replay spooled hook events (supervisor tick). Returns how many were applied; leftovers stay for next time."""
    path = store.path.parent / SPOOL_NAME
    work = path.with_name(SPOOL_NAME + ".draining")
    try:
        if not work.exists():
            if not path.exists():
                return 0
            os.replace(path, work)            # atomic: hooks that append now start a fresh spool file
        lines = work.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    done, rest = 0, []
    for n, line in enumerate(lines):
        if n >= limit:
            rest.extend(lines[n:])
            break
        try:
            item = json.loads(line)
            payload, root = item["payload"], Path(item["root"])
        except (ValueError, KeyError, TypeError):
            continue                           # corrupt line: drop it
        try:
            observe(store, payload, root, item.get("agent"))
            done += 1
        except sqlite3.Error:
            rest.extend(lines[n:])             # DB busy again: keep this and the rest
            break
        except (OSError, ValueError, TypeError, KeyError):
            continue
    try:
        if rest:
            work.write_text("\n".join(rest) + "\n", encoding="utf-8")
        else:
            work.unlink()
    except OSError:
        pass
    return done


def run_hook(home: Path, root: Path, payload: dict, *, agent_env: str | None = None, budget_s: float = 1.3):
    """One hook event with a bounded retry/backoff; a database that stays busy spools the event instead of raising.

    Returns (result, status) where status is "ok" or "spooled" (or "lost" if even the spool failed)."""
    deadline = time.monotonic() + budget_s
    delay = 0.03
    store = None
    try:
        while True:
            try:
                if store is None:
                    store = Store(Path(home) / "workflow.db", timeout_s=0.25, durable=False)
                return observe(store, payload, root, agent_env), "ok"
            except sqlite3.OperationalError as exc:
                if not _is_busy(exc):
                    raise
                if time.monotonic() + delay >= deadline:
                    return {}, "spooled" if spool_event(home, payload, root) else "lost"
                time.sleep(delay)
                delay = min(delay * 2, 0.25)
    finally:
        if store is not None:
            store.close()


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
        # A busy database is retried with backoff inside the hook budget, then the event is spooled to
        # hook_spool.jsonl (replayed by the supervisor tick): never an error, never a lost lifecycle event.
        # Stop gets the longer budget; bootstrap installs a 3 s hook timeout for it (python start ~0.4 s).
        is_stop = payload.get("hook_event_name") == "Stop"
        result, _status = run_hook(args.home, args.repo_root, payload,
                                   agent_env=os.environ.get("JARVES_WORKFLOW_AGENT") or None,
                                   budget_s=1.8 if is_stop else 1.2)
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        # Do not block Claude or emit private input. The supervisor exposes the
        # missing heartbeat if telemetry cannot reach the ledger. A locked database
        # (sqlite3.Error) used to escape as a traceback and drop the event silently.
        _log_error(args.home, exc)
        print("JARVES workflow observer unavailable; Claude permissions unchanged", file=sys.stderr)
    finally:
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
