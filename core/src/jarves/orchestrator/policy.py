"""Orchestrator policy, enforced in code (never by prompt text).

- Project allow-list (`<home>/agents/projects.json`, owner decision OD-22): JARVES shows, asks and steers only
  sessions whose working folder is inside an allowed project; others are counted, not shown. Edited on the PC
  (`jarvis agents projects add|remove`), never by a voice tool.
- Risk of a worker's approval request: what JARVES must NEVER approve by itself (it approves nothing by itself:
  every worker approval goes to the owner's approvals popup) and what needs a typed confirmation code there:
  git push / merge / rebase onto shared branches, deletion, writes outside the worktree, privileged commands,
  paid-API / money switches, permission-skipping flags.
- One writer per repository folder / worktree: a second writer is refused (SECOND_WRITER) and a new worktree is
  offered.
- Launch flags JARVES never passes (permission skipping).
- Daily caps: JARVES-initiated turns (`agents.max_turns_per_day`, OD-24 = 20) and live "ask" probes.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_FLAGS = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                   "--dangerously-bypass-approvals-and-sandbox", "bypassPermissions", "danger-full-access",
                   "--full-auto", "--yolo")
# (pattern, class) - the class names the reason shown on the popup; any hit = typed confirmation code
_GIT = r"\bgit(?:\s+(?:-C|-c|--git-dir|--work-tree|--namespace)\s+\S+|\s+-\S+)*\s+"   # global options before the verb
RISKY = (
    (_GIT + r"push\b", "git push"),
    (_GIT + r"(?:merge|rebase)\b", "git merge / rebase"),
    (_GIT + r"(?:reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D|tag\s+-d|filter-branch)", "git history rewrite"),
    (r"\bgh\s+(?:pr\s+merge|release\s+create|repo\s+(?:delete|edit))", "GitHub merge / release / repo change"),
    (r"(?:^|[\s;&|(])(?:rm|rmdir|del|erase|rd|Remove-Item|ri|unlink|shred|truncate)(?:\s|$)", "deletion"),
    (r"\b(?:sudo|runas|doas|Start-Process\b[^\n]*-Verb\s+RunAs|reg\s+(?:add|delete)|bcdedit|diskpart|format\s+[a-z]:|"
     r"icacls|takeown|chown|chmod\s+-R|Set-ExecutionPolicy|netsh|sc\s+(?:create|delete|config))", "privileged command"),
    (r"(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|api[_-]?key|billing|--api-key|stripe|paypal|purchase|payment)",
     "paid API / money"),
    (r"(?:curl|wget|Invoke-WebRequest|iwr|irm)\b[^\n]*\|\s*(?:sh|bash|iex|Invoke-Expression|python)", "remote script"),
    (r"(?:npm|pnpm|yarn)\s+publish|twine\s+upload|cargo\s+publish|docker\s+push", "publish"),
    (r"(?:" + "|".join(re.escape(f) for f in FORBIDDEN_FLAGS) + ")", "permission bypass"),
)
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "fileChange", "apply_patch")


def norm(p: str | os.PathLike | None) -> str:
    if not p:
        return ""
    try:
        s = os.path.normcase(os.path.abspath(os.path.expanduser(str(p))))
    except (TypeError, ValueError):
        return ""
    return s.rstrip("\\/") or s


def inside(child: str, parent: str) -> bool:
    c, p = norm(child), norm(parent)
    if not c or not p:
        return False
    return c == p or c.startswith(p + os.sep)


@dataclass
class Project:
    name: str
    path: str
    aliases: tuple[str, ...] = ()
    allow_start: bool = True
    # which worker signals may interrupt the owner (UC5): approval, blocked, done, failed
    notify: tuple[str, ...] = ("approval", "blocked", "done", "failed")

    def matches(self, word: str) -> bool:
        w = _fold(word)
        return bool(w) and (w == _fold(self.name) or w in {_fold(a) for a in self.aliases}
                            or w == _fold(Path(self.path).name))


def _fold(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\"'׳״`]", "", s)
    return re.sub(r"\s+", " ", s)


class Projects:
    """The allow-list file. Missing file = nothing allowed (the empty state shows how to add one)."""

    def __init__(self, home: Path) -> None:
        self.path = Path(home) / "agents" / "projects.json"

    def load(self) -> list[Project]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for p in d.get("projects") or [] if isinstance(d, dict) else []:
            if isinstance(p, dict) and p.get("path"):
                notify = p.get("notify")
                out.append(Project(str(p.get("name") or Path(str(p["path"])).name), str(p["path"]),
                                   tuple(str(a) for a in p.get("aliases") or []), bool(p.get("allow_start", True)),
                                   tuple(str(n) for n in notify) if isinstance(notify, list) else
                                   Project.notify))
        return out

    def save(self, items: list[Project]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"projects": [{"name": p.name, "path": p.path, "aliases": list(p.aliases),
                                                 "allow_start": p.allow_start, "notify": list(p.notify)}
                                                for p in items]},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, path: str, name: str = "", aliases: list[str] | None = None, allow_start: bool = True) -> Project:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"not a folder: {p}")
        items = [x for x in self.load() if norm(x.path) != norm(str(p))]
        proj = Project(name or p.name, str(p), tuple(aliases or ()), allow_start)
        items.append(proj)
        self.save(items)
        return proj

    def remove(self, name_or_path: str) -> bool:
        items = self.load()
        keep = [x for x in items if not (x.matches(name_or_path) or norm(x.path) == norm(name_or_path))]
        if len(keep) == len(items):
            return False
        self.save(keep)
        return True

    def for_path(self, cwd: str | None) -> Project | None:
        best = None
        for p in self.load():
            if inside(cwd or "", p.path) and (best is None or len(norm(p.path)) > len(norm(best.path))):
                best = p
        return best

    def by_word(self, word: str) -> list[Project]:
        return [p for p in self.load() if p.matches(word)]


def classify(tool: str, command: str = "", cwd: str = "", worktree: str = "", file_path: str = "") -> tuple[str, list[str]]:
    """(risk, reasons) of a worker's approval request. risk "high" = needs the typed code on the popup."""
    reasons = [label for rx, label in RISKY if command and re.search(rx, command, re.I)]
    if file_path and worktree and not inside(file_path, worktree):
        reasons.append("write outside the worktree")
    if tool in WRITE_TOOLS and cwd and worktree and not inside(cwd, worktree):
        reasons.append("write outside the worktree")
    return ("high" if reasons else "medium"), sorted(set(reasons))


def check_flags(argv: list[str]) -> None:
    """Raise if a launch command would skip the provider's permissions (never allowed)."""
    joined = " ".join(str(a) for a in argv)
    for f in FORBIDDEN_FLAGS:
        if f in joined:
            raise PermissionError(f"refused: JARVES never launches an agent with {f}")


def writers_at(path: str, sessions: list[dict]) -> list[dict]:
    """Live sessions whose working folder IS this folder (a worktree is its own folder)."""
    target = norm(path)
    return [s for s in sessions if norm(s.get("cwd") or "") == target
            and s.get("state") not in ("STOPPED", "DISCONNECTED", "UNKNOWN")]
