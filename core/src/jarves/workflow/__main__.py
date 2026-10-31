"""Workflow CLI. Direct entry point works with the Python standard library only."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .store import Store


def default_profile() -> Path:
    if os.environ.get("JARVES_HOME"):
        return Path(os.environ["JARVES_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "jarves"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "jarves"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Live workflow: durable tasks, owner questions, evidence and an external supervisor")
    ap.add_argument("action", choices=["serve", "supervise", "open", "status", "seed", "sync", "agent", "report", "next",
                                        "create", "ask", "ask-more", "reply", "answer", "reopen", "task", "transition", "check", "authorize", "recover", "dispatch", "bootstrap", "trace", "retro", "autopilot"])
    ap.add_argument("rest", nargs="*", help="autopilot: status | set KEY VALUE | stop | start | once")
    ap.add_argument("--home", type=Path, help="private workflow data directory; default <profile>/workflow")
    ap.add_argument("--profile-home", type=Path, default=default_profile(), help="existing JARVES orchestrator profile")
    ap.add_argument("--port", type=int, default=8188)
    ap.add_argument("--no-sync", action="store_true", help="supervisor: no periodic public GitHub metadata reads")
    ap.add_argument("--no-open", action="store_true", help="print the private UI link without opening a browser")
    ap.add_argument("--repo-root", type=Path, default=Path.cwd(), help="bootstrap: current JARVES checkout")
    ap.add_argument("--apply", action="store_true", help="bootstrap: install reviewed project-local instructions/hooks, with backups")
    for name in ("id", "agent", "session-id", "provider", "project", "title", "context", "checkpoint", "answer", "state",
                 "implementation", "validation", "delivery", "criterion", "evidence", "note", "task", "version", "code-revision", "artifact-ref",
                 "why", "recommendation", "safe-default", "link"):
        ap.add_argument("--" + name, default="")
    ap.add_argument("--lane", choices=["bugs", "features", "release", "ops", "any"], default="any")
    ap.add_argument("--priority", type=int, default=2)
    ap.add_argument("--required-validation", choices=["automated", "app", "device"], default="app")
    ap.add_argument("--choices", default="[]", help='ask: options, JSON list of text or {"label","description","recommended"}; '
                    'description = what changes if chosen; recommended = listed first with a badge')
    ap.add_argument("--multi", action="store_true", help="ask: the owner may pick several options")
    ap.add_argument("--minutes", type=int, default=0, help="ask: owner minutes needed to decide")
    ap.add_argument("--acceptance", action="append", default=[], help="one acceptance criterion; repeatable")
    ap.add_argument("--resource", action="append", default=[], help="exclusive resource, e.g. windows-gui")
    ap.add_argument("--depends-on", action="append", default=[])
    ap.add_argument("--auto-dispatch", action="store_true", help="opt in this attached session to already authorised task assignment")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--failed", action="store_true")
    ap.add_argument("--capture", choices=["off", "metadata", "details"], help="trace: explicit local capture mode")
    ap.add_argument("--since", type=float, default=0, help="trace/retro: first UNIX timestamp")
    ap.add_argument("--until", type=float, help="trace/retro: last UNIX timestamp")
    ap.add_argument("--before", type=int, default=0, help="trace: next page cursor")
    return ap


def utf8_streams() -> None:
    """Piped Windows consoles default to a legacy code page; JSON output contains Hebrew."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


OWNER_ONLY_MESSAGE = "owner questions are answered in the dashboard or in your own terminal"


def owner_only_guard(action: str) -> str:
    """Owner questions are for the owner: refuse non-interactive callers (agents, pipes, scripts)."""
    if os.environ.get("JARVES_WORKFLOW_AGENT"):
        raise PermissionError(f"{OWNER_ONLY_MESSAGE} ('{action}' refused: agent environment); the question stays open")
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        raise PermissionError(f"{OWNER_ONLY_MESSAGE} ('{action}' refused: stdin and stdout must both be a terminal); the question stays open")
    return "terminal"


def main(argv=None) -> int:
    utf8_streams()
    _av = list(sys.argv[1:] if argv is None else argv)
    if _av[:1] == ["autopilot"] and _av[1:2] in (["grant"], ["revoke"], ["grant-status"]):
        from .autopilot_grant import run as grant_run
        return grant_run(_av[1:])
    a = parser().parse_args(argv)
    if not 0 <= a.port <= 65535:
        parser().error("invalid port")
    home = (a.home or a.profile_home / "workflow").resolve()
    from .server import access_token, private_directory
    private_directory(home)
    if a.action == "bootstrap":
        from .bootstrap import install
        try:
            print(json.dumps(install(a.repo_root, home, apply=a.apply), ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
    if a.action == "serve":
        from .server import serve
        serve(home, a.port, profile_home=a.profile_home.resolve())
        return 0
    if a.action == "supervise":
        from .control import supervisor
        supervisor(home, a.profile_home.resolve(), a.port, sync_github=not a.no_sync, update_enabled=not a.no_sync)
        return 0
    if a.action == "open":
        # Kept out of event logs and process arguments. Never use this link as a public share URL.
        link = f"http://127.0.0.1:{a.port}/#token={access_token(home)}"
        print(link)
        if not a.no_open:
            import webbrowser
            webbrowser.open(link)
        return 0
    store = Store(home / "workflow.db")
    try:
        if a.action == "status":
            from .autopilot import agent_retention_s
            result = store.snapshot(retention_s=agent_retention_s(home))
        elif a.action in {"trace", "retro"}:
            from .trace import history, retro_bundle, settings
            filters = {"agent_id": a.agent, "task_id": a.task, "since": a.since, "until": a.until}
            if a.action == "retro":
                result = retro_bundle(store, **filters)
            elif a.capture:
                result = settings(store, a.capture)
            else:
                result = history(store, before=a.before, **filters)
        elif a.action == "autopilot":
            from .autopilot import command
            result = command(store, home, (a.rest or ["status"])[0], a.rest[1:])
        elif a.action == "seed":
            from .sync import PLAN
            result = store.seed_plan(json.loads(PLAN.read_text(encoding="utf-8")))
            result = {"created": result["created"]}
        elif a.action == "sync":
            from .sync import sync
            result = sync(store)
        elif a.action == "agent":
            result = store.register_agent({"id": a.agent, "label": a.title or a.agent, "lane": a.lane,
                                           "session_id": a.session_id, "provider": a.provider, "project": a.project,
                                           "auto_dispatch": a.auto_dispatch})
        elif a.action == "report":
            result = store.heartbeat(a.agent, note=a.note, progress=a.progress)
        elif a.action == "next":
            result = store.claim(a.agent, task_id=a.task or None)
            if result:
                from .control import task_context
                result = task_context(store, result)
        elif a.action == "create":
            result = store.create_task({**({"id": a.id} if a.id else {}), "title": a.title, "version": a.version or "V5",
                                       "lane": "features" if a.lane == "any" else a.lane,
                                       "priority": a.priority, "required_validation": a.required_validation,
                                       "acceptance": [{"label": s} for s in a.acceptance],
                                       "resources": a.resource, "depends_on": a.depends_on})
        elif a.action == "ask":
            result = store.ask({"task_id": a.task, "title": a.title, "context": a.context,
                                "choices": json.loads(a.choices), "checkpoint": a.checkpoint, "multi": a.multi,
                                "minutes": a.minutes, "why": a.why, "recommendation": a.recommendation,
                                "safe_default": a.safe_default, "link": a.link})
        elif a.action == "ask-more":
            # Owner follow-up; the native manager receives it at its next Stop hook. Never an answer.
            result = store.ask_more(a.note, question_id=a.id, task_id=a.task)
        elif a.action == "reply":
            result = store.reply_followup(a.id, a.answer)
        elif a.action in {"answer", "reopen"}:
            channel = owner_only_guard(a.action)
            if a.action == "answer":
                result = store.answer(a.id, a.answer, answered_by="owner (terminal)", channel=channel)
            else:
                result = store.reopen(a.id, by="owner (terminal)", channel=channel)
        elif a.action == "task":
            result = next((t for t in store.snapshot()["tasks"] if t["id"] == (a.id or a.task)), None)
            if result is None:
                raise ValueError("task not found")
            from .control import task_context
            result = task_context(store, result)
        elif a.action == "check":
            result = store.record_check(a.task, a.criterion, a.evidence, passed=not a.failed)
        elif a.action in {"transition", "recover", "authorize"}:
            changes = {key: getattr(a, key) for key in ("state", "implementation", "validation", "delivery", "code_revision", "artifact_ref") if getattr(a, key)}
            if a.checkpoint:
                changes["checkpoint"] = a.checkpoint
            if a.action == "recover":
                changes.update(state="queued", recovery_confirmed=True)
            if a.action == "authorize":
                changes.update(execution_allowed=True)
            result = store.transition(a.task, changes)
        elif a.action == "dispatch":
            from .control import dispatch
            store.tick()
            result = dispatch(store, a.profile_home)
        else:
            raise ValueError("unsupported operation")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("error") else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
