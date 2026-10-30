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

from .hooks import EVENTS

START, END = "<!-- JARVES-WORKFLOW-START -->", "<!-- JARVES-WORKFLOW-END -->"
OBSOLETE = ("5 nightly", "five nightly", "no idle agents", "Keep 5-8", "one AskUserQuestion batch", "STATE.md")


def local_instructions(home: Path) -> str:
    return (f"{START}\n@AGENTS.md\n@docs/workflow/OPERATING_MODEL.md\n\n"
            "# Local workflow connection\n"
            f"Private workflow directory: {home}\n"
            "Claude Code is the manager and uses its native subagents. Codex can use the same task/evidence CLI.\n"
            "Get current state with `python core/tools/workflow_console.py status` using --home above.\n"
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
        entries[:] = [e for e in entries if not any(
            h.get("type") == "command" and str(root / "core" / "tools" / "workflow_hook.py") in h.get("command", "")
            for h in e.get("hooks", []) if isinstance(h, dict))]
        entries.append({"hooks": [{"type": "command", "command": command, "timeout": 3}]})
    return result


def prepare(root: Path, home: Path, python: str = sys.executable) -> list[tuple[Path, str, str]]:
    root, home = root.resolve(), home.resolve()
    if not (root / "AGENTS.md").is_file() or not (root / "core" / "tools" / "workflow_hook.py").is_file():
        raise ValueError("select the JARVES checkout containing the new workflow code")
    local = root / "CLAUDE.local.md"
    old = local.read_text(encoding="utf-8") if local.exists() else ""
    # Remove only recognisable superseded workflow paragraphs. Preserve unrelated
    # personal instructions; the diff and backup make every removed rule visible.
    if old and START not in old:
        paragraphs = re.split(r"\n\s*\n", old.strip())
        retained = [p for p in paragraphs if not any(word.lower() in p.lower() for word in OBSOLETE)]
        new = ("\n\n".join(retained).rstrip() + "\n\n" if retained else "") + local_instructions(home)
    elif START in old:
        before, _, rest = old.partition(START)
        _, marker, after = rest.partition(END)
        if not marker:
            raise ValueError("local workflow instruction block is malformed")
        new = before + local_instructions(home) + after.lstrip("\n")
    else:
        new = local_instructions(home)
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
