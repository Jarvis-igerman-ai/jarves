"""First-run local Claude instructions and observer hooks, with backups.

Default is review only. --apply is an explicit local owner installation; it
never changes tool permissions, global Claude settings or billing configuration.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .hooks import EVENTS, console_command

START, END = "<!-- JARVES-WORKFLOW-START -->", "<!-- JARVES-WORKFLOW-END -->"
OBSOLETE = ("5 nightly", "five nightly", "no idle agents", "Keep 5-8", "one AskUserQuestion batch", "agentN_STATE.md")
# Remove only complete, recognised standalone rules. A substring in a paragraph
# (especially a STATE.md reference) cannot establish that its other rules retired.
RETIRED_RULE = re.compile(
    r"(?:Require (?:5|five) nightly runs for a JARVES release|"
    r"Keep 5-8 (?:sub-agents|agents) busy|No idle agents|"
    r"(?:At most )?one AskUserQuestion batch(?: \(<= ?4\))? a day)[.!]?", re.I)


# Events whose hook output Claude Code does not use: run in the background, never block a tool call.
# SessionStart/SubagentStart return context and Stop returns a decision, so they stay synchronous.
ASYNC_EVENTS = frozenset({"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
                          "Notification", "SessionEnd", "SubagentStop"})


def local_instructions(home: Path, root: Path | None = None, python: str = "python") -> str:
    # AGENTS.md and OPERATING_MODEL.md are already imported by the tracked CLAUDE.md.
    prefix = console_command(root, home, python) if root else "python core/tools/workflow_console.py"
    return (f"{START}\n"
            "# Local workflow connection\n"
            f"Private workflow directory: {home}\n"
            "Claude Code is the manager and uses its native subagents. Codex can use the same task/evidence CLI.\n"
            "Run the workflow CLI with exactly this prefix (the bare `python` may be a broken Store alias): "
            f"`{prefix} status`.\n"
            "When the owner is present, use the live question inbox interactively without the old once-a-day cap.\n"
            "When absent, preserve pending questions and checkpoint only their dependent work; continue other authorised tasks.\n"
            "No answer is never approval. Stop/merge/CI green do not mean verified or released.\n"
            "Keep native hooks enabled for telemetry; use CLI for semantic task/evidence updates.\n"
            f"{END}\n")


def settings_with_hooks(settings: dict, root: Path, home: Path, python: str) -> dict:
    result = json.loads(json.dumps(settings))
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("existing hooks settings is not an object")
    argv = [python, str(root / "core" / "tools" / "workflow_hook.py"), "--home", str(home), "--repo-root", str(root)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    for event in EVENTS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError(f"existing {event} hook list is invalid")
        # Idempotent own-entry replacement; unrelated hooks and permissions stay byte-semantically intact.
        retained = []
        for entry in entries:
            remaining = [h for h in entry.get("hooks", []) if not (
                isinstance(h, dict) and h.get("type") == "command"
                and str(root / "core" / "tools" / "workflow_hook.py") in h.get("command", ""))]
            if len(remaining) == len(entry.get("hooks", [])):
                retained.append(entry)
            elif remaining:
                entry["hooks"] = remaining
                retained.append(entry)
        entries[:] = retained
        hook = {"type": "command", "command": command, "timeout": 3}
        if event in ASYNC_EVENTS:
            hook["async"] = True
        entries.append({"hooks": [hook]})
    return result


def prepare(root: Path, home: Path, python: str = sys.executable) -> list[tuple[Path, str, str]]:
    root, home = root.resolve(), home.resolve()
    if not (root / "AGENTS.md").is_file() or not (root / "core" / "tools" / "workflow_hook.py").is_file():
        raise ValueError("select the JARVES checkout containing the new workflow code")
    local = root / "CLAUDE.local.md"
    old = local.read_text(encoding="utf-8") if local.exists() else ""
    # Preserve unrelated lines, including lines beside a retired standalone rule.
    # Ambiguous legacy wording remains visible for the owner's dry-run review.
    if old and START not in old:
        retained = "".join(line for line in old.splitlines(True)
                           if not RETIRED_RULE.fullmatch(line.strip().removeprefix("- ").removeprefix("* ")))
        new = (retained.rstrip() + "\n\n" if retained.strip() else "") + local_instructions(home, root, python)
    elif START in old:
        before, _, rest = old.partition(START)
        _, marker, after = rest.partition(END)
        if not marker:
            raise ValueError("local workflow instruction block is malformed")
        new = before + local_instructions(home, root, python) + after.lstrip("\n")
    else:
        new = local_instructions(home, root, python)
    settings_file = root / ".claude" / "settings.local.json"
    raw = settings_file.read_text(encoding="utf-8") if settings_file.exists() else "{}\n"
    settings = json.loads(raw)
    if not isinstance(settings, dict):
        raise ValueError("existing Claude local settings is not an object")
    updated = json.dumps(settings_with_hooks(settings, root, home, python), ensure_ascii=False, indent=2) + "\n"
    return [(local, old, new), (settings_file, raw, updated)]


def install(root: Path, home: Path, *, apply=False, python=sys.executable) -> dict:
    changes = prepare(root, home, python)
    report = {"applied": False, "files": [], "global_instructions": "not changed; inspect with Claude /memory"}
    for path, old, new in changes:
        report["files"].append({"path": str(path), "changed": old != new,
                                "old_rule_warnings": [word for word in OBSOLETE if word.lower() in old.lower()],
                                "diff": "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                                     fromfile=path.name + ":before", tofile=path.name + ":after"))})
    if not apply:
        return report
    claude = shutil.which("claude")
    if not claude:
        raise ValueError("Claude Code CLI not found; no local instructions/settings changed")
    checked = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=10, check=True)
    version = re.search(r"(\d+)\.(\d+)\.(\d+)", checked.stdout)
    if not version or tuple(map(int, version.groups())) < (2, 1, 271):
        raise ValueError("observer hook compatibility requires Claude Code 2.1.271+; no settings changed")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backups = home / "instruction-backups" / stamp
    backups.mkdir(parents=True, exist_ok=False)
    for path, old, new in changes:
        if old == new:
            continue
        if path.exists():
            (backups / path.name).write_text(old, encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".workflow.tmp")
        temporary.write_text(new, encoding="utf-8")
        temporary.replace(path)
    # Ignore machine-local connection paths and hook settings without modifying tracked .gitignore.
    gitdir = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
                            capture_output=True, text=True, check=True, timeout=3).stdout.strip()
    exclude = Path(gitdir) if Path(gitdir).is_absolute() else root / gitdir
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    for pattern in ("/CLAUDE.local.md", "/.claude/settings.local.json"):
        if pattern not in text.splitlines():
            text = text.rstrip("\n") + "\n" + pattern + "\n"
    exclude.write_text(text, encoding="utf-8")
    report.update(applied=True, backups=str(backups), claude_version=checked.stdout.strip())
    return report
