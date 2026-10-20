"""User-editable persona/workspace files (OpenClaw-style), loaded into the model instructions.

Layout (<state_dir>/workspace/, created with Hebrew-aware defaults on first run; the user may edit any file in a
text editor or the dashboard):

    AGENTS.md     operating rules (how to use memory, what never to store)   user-only    backend
    SOUL.md       personality and tone                                       agent+confirm front + backend
    IDENTITY.md   name, how it is pronounced, vibe                           agent+confirm front + backend
    USER.md       stable facts/preferences about the user                    agent        front + backend
    MEMORY.md     curated long-term notes                                    agent        backend
    TOOLS.md      GENERATED: skills, tools, settings, voices                 read-only    backend
    SETUP.md      GENERATED: setup/install help and user approval boundaries read-only    backend
    FEATURES.md   GENERATED: complete feature catalog and readiness snapshot read-only    backend
    HEARTBEAT.md  periodic checklist (reserved; not run yet)                 user-only    not injected
    memory/YYYY-MM-DD.md  daily notes written by the curator (what was learned, from which conversation)
    .history/<FILE>/<stamp>.md  a backup of the previous version on EVERY write (undo / restore)

Atomic facts (remember / curator) live in SQLite (memory.py); these files hold free-form text. Prompt injection is
size-capped per file and in total, `<!-- comments -->` are dropped (the defaults are mostly comments, so a fresh
workspace changes nothing in the tuned persona), and files are re-read only when their mtime/size changes, so an
edit applies to the next backend request without a restart. A running GPT-Live session gets the change as a silent
note (`note_live`); the voice itself and the session's backend instructions refresh at the next session.

Plain-Python API for the dashboard: files(), read(), write(), diff(), history(), restore(), undo().
"""
from __future__ import annotations

import difflib
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Literal

from . import logs
from . import pronounce as _pron

Target = Literal["front", "backend"]
Writer = Literal["user", "agent"]


@dataclass(frozen=True)
class FileSpec:
    name: str
    title: str                 # what the file is for (shown in the dashboard and TOOLS.md)
    agent_edit: bool           # the assistant may change it (through the settings Skill)
    confirm: bool              # an assistant edit needs the user's approval first
    inject: tuple[str, ...]    # which instructions include it
    max_chars: int             # cap when injected (the file itself may be longer, up to MAX_FILE_CHARS)
    generated: bool = False    # rewritten by the Core; nobody edits it


SPECS: dict[str, FileSpec] = {s.name: s for s in (
    FileSpec("AGENTS.md", "operating rules", False, False, ("backend",), 2500),
    FileSpec("SOUL.md", "personality and tone", True, True, ("front", "backend"), 1500),
    FileSpec("IDENTITY.md", "name and identity", True, True, ("front", "backend"), 500),
    FileSpec("USER.md", "about the user", True, False, ("front", "backend"), 1500),
    FileSpec("PRONUNCIATIONS.md", "how to SAY names: 'word = spoken form'; say exactly that, also with prefixes ל/ו/ב/כ/מ/ה/ש",
             True, False, ("front",), 700),
    FileSpec("MEMORY.md", "long-term notes", True, False, ("backend",), 2500),
    FileSpec("SETUP.md", "installation and connection guide (generated; read full file for a topic)", False, False,
             ("backend",), 1700, True),
    FileSpec("FEATURES.md", "all supported features and readiness snapshot (generated)", False, False,
             ("backend",), 2000, True),
    FileSpec("TOOLS.md", "skills, settings and voices (generated)", False, False, ("backend",), 3000, True),
    FileSpec("HEARTBEAT.md", "periodic checklist (not active yet)", False, False, (), 0),
)}
TOTAL_CHARS = {"front": 2500, "backend": 9000}   # GPT-Live front instructions stay short (latency)
MAX_FILE_CHARS = 20_000                          # a write larger than this is refused (file kept unchanged)
MAX_AGENT_WRITE = 3_000                          # one assistant edit may write at most this much
HISTORY_KEEP = 50                                # backups kept per file
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\b(?:api[_ -]?key|password|passwd|token|secret)\s*[:=]\s*\S+"
                     r"|(?:סיסמה|סיסמא)\s*[:=]?\s*\S+|\b\d{13,19}\b", re.I)

DEFAULTS = {
    "AGENTS.md": """# כללי עבודה / Operating rules
<!-- This file is yours: JARVES reads it but never changes it. Text inside these comment marks is ignored. -->
- Facts the user asks to keep go to `remember`; stable details about the user may also go to USER.md
  (`workspace_note`). Never store passwords, codes, card or ID numbers, or anything the user calls private.
- SOUL.md and IDENTITY.md change only when the user asks, and only after the user approves on the screen.
- Never change settings outside the settings tools; secrets and API keys are not reachable at all.
- Some remembered facts were learned automatically from earlier conversations: use them naturally, and when
  the user says one is wrong, delete it (`forget`).
""",
    "SOUL.md": """# האופי שלי / Personality
<!-- How JARVES should sound and behave, in your words (Hebrew or English). Lines here are added to the built-in
personality and win when they conflict. Examples:
- תדבר קצת יותר לאט ובשקט.
- יותר הומור, פחות רצינות.
- Always answer in English.
-->
""",
    "IDENTITY.md": """# זהות / Identity
- שם: ג'ארוויס (נכתב JARVES; באמירה בעברית תמיד "ג'ארוויס")
- רובוט ביתי קטן על השולחן, עם פנים עגולות.
""",
    "USER.md": """# על המשתמש / About the user
<!-- Stable facts and preferences (name, how to address you, family, likes). JARVES may add lines here when you
ask; you can edit or delete anything. Example:
- קוראים לי איגל; תפנה אליי בלשון זכר.
-->
""",
    "MEMORY.md": """# הערות לטווח ארוך / Long-term notes
<!-- Free-form notes JARVES keeps for later (decisions, ongoing plans). Single facts live in the memory list
(`jarvis memory` / dashboard); conversation summaries in memory/YYYY-MM-DD.md. -->
""",
    "PRONUNCIATIONS.md": _pron.DEFAULT_TEXT,
    "HEARTBEAT.md": """# בדיקות תקופתיות / Heartbeat
<!-- Reserved: a short checklist JARVES will run periodically in a later version. Not active yet. -->
""",
}


class WorkspaceError(ValueError):
    """A refused read/write; the message says why (shown to the user / dashboard)."""


def _clean(text: str) -> str:
    """Injected form: comments dropped; a file with nothing but headings/blank lines counts as empty."""
    lines = [l.rstrip() for l in _COMMENT.sub("", text).splitlines()]
    body = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
    return "\n".join(l for l in lines if l.strip()).strip() if body else ""


def _cap(text: str, n: int) -> str:
    return text if len(text) <= n else text[: max(0, n - 20)].rstrip() + "\n...(truncated)"


class Workspace:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "memory").mkdir(exist_ok=True)
        for name, text in DEFAULTS.items():
            p = self.root / name
            if not p.exists():
                p.write_text(text, encoding="utf-8")
        if not (self.root / "TOOLS.md").exists():
            (self.root / "TOOLS.md").write_text("# Tools\n(generated when the Core starts)\n", encoding="utf-8")
        self._cache: dict[str, tuple[tuple[int, int], str]] = {}
        self.listeners: list[Callable[[str], None]] = []   # called with the file name after every write
        self.write_generated("SETUP.md", Path(__file__).with_name("setup_guide.md").read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ reading
    def path(self, name: str) -> Path:
        """Only the known files ("soul", "SOUL.md", "soul.md" all work); never an arbitrary path."""
        base = str(name).strip().upper()
        spec = SPECS.get((base[:-3] if base.endswith(".MD") else base) + ".md")
        if spec is None:
            raise WorkspaceError(f"unknown workspace file {name!r}; files: {', '.join(SPECS)}")
        return self.root / spec.name

    def spec(self, name: str) -> FileSpec:
        return SPECS[self.path(name).name]

    def read(self, name: str) -> str:
        p = self.path(name)
        try:
            st = p.stat()
        except FileNotFoundError:
            return ""
        key = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(p.name)
        if hit and hit[0] == key:
            return hit[1]
        text = p.read_text(encoding="utf-8", errors="replace")
        self._cache[p.name] = (key, text)
        return text

    def files(self) -> list[dict]:
        """Dashboard listing: name, purpose, flags, size, last change."""
        out = []
        for s in SPECS.values():
            p = self.root / s.name
            st = p.stat() if p.exists() else None
            out.append({"name": s.name, "title": s.title, "agent_edit": s.agent_edit, "confirm": s.confirm,
                        "user_edit": not s.generated, "generated": s.generated, "inject": list(s.inject),
                        "chars": len(self.read(s.name)), "max_inject_chars": s.max_chars,
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds") if st else None,
                        "versions": len(self.history(s.name))})
        return out

    def section(self, target: Target) -> str:
        """The workspace text for one set of instructions (front = GPT-Live voice, backend = reasoning model)."""
        parts, left = [], TOTAL_CHARS[target]
        for s in SPECS.values():
            if target not in s.inject or left <= 0:
                continue
            body = _clean(self.read(s.name))
            if not body:
                continue
            body = _cap(body, min(s.max_chars, left))
            parts.append(f"[{s.name} - {s.title}]\n{body}")
            left -= len(body)
        if not parts:
            return ""
        head = ("Workspace files (the user can edit them; they refine the instructions above and win when they "
                "conflict; never read them aloud verbatim):")
        return head + "\n" + "\n\n".join(parts)

    # ------------------------------------------------------------------ writing
    def validate(self, name: str, text: str, by: Writer) -> FileSpec:
        spec = self.spec(name)
        if spec.generated:
            raise WorkspaceError(f"{spec.name} is generated by JARVES and cannot be edited")
        if by == "agent" and not spec.agent_edit:
            raise WorkspaceError(f"{spec.name} can only be changed by the user")
        if "\x00" in text:
            raise WorkspaceError("binary content is not allowed")
        if len(text) > MAX_FILE_CHARS:
            raise WorkspaceError(f"{spec.name} would be {len(text)} characters; the limit is {MAX_FILE_CHARS}")
        if _SECRET.search(_COMMENT.sub("", text)):
            raise WorkspaceError("this looks like a password, key or card number; secrets are never stored here")
        return spec

    def write(self, name: str, text: str, *, by: Writer = "user", note: str = "") -> dict:
        """Replace a file (the previous version is backed up first). Returns {name, changed, version, diff}."""
        spec = self.validate(name, text, by)
        p = self.root / spec.name
        old = self.read(spec.name)
        if old == text:
            return {"name": spec.name, "changed": False, "version": None, "diff": ""}
        if by == "agent" and abs(len(text) - len(old)) > MAX_AGENT_WRITE:
            raise WorkspaceError(f"one change may add or remove at most {MAX_AGENT_WRITE} characters")
        version = self._backup(spec.name, old)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        self._cache.pop(spec.name, None)
        logs.info("workspace", "WORKSPACE_WRITE", "workspace file changed", file=spec.name, by=by,
                  chars=len(text), version=version, note=note[:80])
        for cb in list(self.listeners):
            try:
                cb(spec.name)
            except Exception:
                logs.warn("workspace", "WORKSPACE_LISTENER_FAILED", "a workspace change listener failed", exc=True)
        return {"name": spec.name, "changed": True, "version": version, "diff": self.diff(spec.name, old, text)}

    def append(self, name: str, line: str, *, by: Writer = "agent", section: str | None = None) -> dict:
        """Add one bullet line (optionally under a '## section' heading, created if missing)."""
        line = " ".join(line.split())
        if not line:
            raise WorkspaceError("nothing to add")
        old = self.read(self.path(name).name).rstrip("\n")
        bullet = line if line.startswith("- ") else f"- {line}"
        if section:
            head = f"## {section.strip()}"
            if head in old.splitlines():
                lines = old.splitlines()
                i = lines.index(head) + 1
                while i < len(lines) and not lines[i].startswith("#"):
                    i += 1
                while i > 0 and not lines[i - 1].strip():
                    i -= 1
                lines.insert(i, bullet)
                return self.write(name, "\n".join(lines) + "\n", by=by)
            return self.write(name, f"{old}\n\n{head}\n{bullet}\n", by=by)
        return self.write(name, f"{old}\n{bullet}\n", by=by)

    def write_generated(self, name: str, text: str) -> None:
        """Core-only: rewrite a generated file (TOOLS.md); no backup, no listeners."""
        spec = self.spec(name)
        if not spec.generated:
            raise WorkspaceError(f"{spec.name} is not a generated file")
        p = self.root / spec.name
        if self.read(spec.name) != text:
            p.write_text(text, encoding="utf-8")
            self._cache.pop(spec.name, None)

    @staticmethod
    def diff(name: str, old: str, new: str) -> str:
        return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"{name} (before)",
                                            f"{name} (after)", n=1))

    def preview(self, name: str, text: str) -> str:
        """Diff of a proposed write against the current file, without writing."""
        n = self.path(name).name
        return self.diff(n, self.read(n), text)

    # ------------------------------------------------------------------ history / undo
    def _hdir(self, name: str) -> Path:
        return self.root / ".history" / name

    def _backup(self, name: str, text: str) -> str:
        d = self._hdir(name)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        n, version = 0, stamp
        while (d / f"{version}.md").exists():
            n += 1
            version = f"{stamp}-{n}"
        (d / f"{version}.md").write_text(text, encoding="utf-8")
        for old in sorted(d.glob("*.md"), key=lambda p: (p.stat().st_mtime_ns, p.name))[:-HISTORY_KEEP]:
            old.unlink(missing_ok=True)
        return version

    def history(self, name: str) -> list[dict]:
        """Saved previous versions, newest first: [{version, chars, saved}]."""
        d = self._hdir(self.path(name).name)
        if not d.exists():
            return []
        items = sorted(d.glob("*.md"), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
        return [{"version": p.stem, "chars": p.stat().st_size,
                 "saved": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")} for p in items]

    def read_version(self, name: str, version: str) -> str:
        if not re.fullmatch(r"[0-9-]{8,24}", version):
            raise WorkspaceError("bad version id")
        p = self._hdir(self.path(name).name) / f"{version}.md"
        if not p.exists():
            raise WorkspaceError(f"no saved version {version}")
        return p.read_text(encoding="utf-8")

    def restore(self, name: str, version: str, *, by: Writer = "user") -> dict:
        """Bring back a saved version (the current text is itself backed up, so a restore can be undone)."""
        n = self.path(name).name
        text = self.read_version(n, version)
        spec = SPECS[n]
        if by == "agent" and not spec.agent_edit:
            raise WorkspaceError(f"{n} can only be changed by the user")
        return self.write(n, text, by=by, note=f"restore {version}")

    def undo(self, name: str, *, by: Writer = "user") -> dict:
        """Go back to the version before the last change."""
        n = self.path(name).name
        cur = self.read(n)
        for h in self.history(n):
            if self.read_version(n, h["version"]) != cur:
                return self.restore(n, h["version"], by=by)
        raise WorkspaceError(f"{n} has no earlier version")

    # ------------------------------------------------------------------ daily notes
    def daily_note(self, lines: Iterable[str], when: datetime | None = None) -> Path:
        when = when or datetime.now()
        p = self.root / "memory" / f"{when:%Y-%m-%d}.md"
        new = not p.exists()
        with p.open("a", encoding="utf-8") as f:
            if new:
                f.write(f"# {when:%Y-%m-%d}\n")
            for line in lines:
                f.write(f"- {when:%H:%M} {' '.join(line.split())}\n")
        return p

    def daily_notes(self, limit: int = 14) -> list[dict]:
        ps = sorted((self.root / "memory").glob("????-??-??.md"), reverse=True)[:limit]
        return [{"day": p.stem, "chars": p.stat().st_size} for p in ps]


# ---------------------------------------------------------------------- generated TOOLS.md
def tools_markdown(registry, settings_view: dict | None = None) -> str:
    """Capabilities list for TOOLS.md, generated from the live Skill registry (the agent's view of itself)."""
    lines = ["# כלים ויכולות / Tools and abilities", "<!-- GENERATED by JARVES at start-up; edits are overwritten. -->",
             "Skills and their tools (only these exist; anything else is not set up):"]
    by_skill: dict[str, list] = {}
    for t in registry.tools():
        skill = registry._tools[t.name][0].name
        by_skill.setdefault(skill, []).append(t)
    for skill, tools in by_skill.items():
        desc = registry.skills[skill].description
        lines.append(f"- {skill} ({desc}): " + ", ".join(
            f"{t.name}{' [asks first]' if t.permission == 'destructive' or t.confirm else ''}" for t in tools))
    if settings_view:
        lines.append("Settings you may change with set_setting / set_voice: " + ", ".join(
            f"{k} = {v['value']}" for k, v in settings_view.get("settings", {}).items()))
        if settings_view.get("voices"):
            lines.append("Voices (GPT-Live; a new voice starts with the next conversation): "
                         + ", ".join(settings_view["voices"]))
    lines.append("Workspace files you can read: " + ", ".join(SPECS) + "; you may edit "
                 + ", ".join(s.name for s in SPECS.values() if s.agent_edit)
                 + " (SOUL.md / IDENTITY.md only after the user approves).")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- Core hooks
def augment_instructions(agent, ws: Workspace) -> None:
    """Append the backend workspace section to a brain's instructions (AgentBrain / ChatGPTBrain both call
    self.instructions() for every request, so edits apply to the next request)."""
    base = agent.instructions

    def instructions() -> str:
        extra = ws.section("backend")
        return f"{base()}\n\n{extra}" if extra else base()
    agent.instructions = instructions


FRONT_FACTS, FRONT_FACTS_CHARS = 8, 600
FRONT_SELF = ("Also delegate (a word or two, like other quick actions) when the user asks to change your voice "
              "(\"תחליף קול\"), your personality or how you talk, to note something about them, or about your own "
              "settings and skills: the backend has tools for these. Delegate installation, setup, connection and "
              "feature-help questions too: the backend reads SETUP.md and FEATURES.md. Never guess setup steps. "
              "For camera connection/status or requests to open/enable the camera, delegate to the backend to run "
              "camera_status before making any connection claim. Never infer disconnected hardware from an "
              "unavailable describe/enroll tool or a feature flag. If status cannot be checked, say it is unknown; "
              "never substitute a claim that no camera is connected.")


def front_facts(memory) -> str:
    """A few remembered facts for the voice model itself, so it can use the user's name/preferences at once."""
    if memory is None:
        return ""
    rows = memory.for_prompt(FRONT_FACTS, min_confidence=0.6)
    text = "\n".join(f"- {r['text']}" for r in rows)
    return f"Things you know about the user (from memory):\n{_cap(text, FRONT_FACTS_CHARS)}" if text else ""


def attach_live(live, ws: Workspace, memory=None) -> None:
    """Make a LiveVoice include the front workspace section (+ a few remembered facts) in its instructions."""
    orig = getattr(live, "session_config", None)
    if orig is None:
        return

    def session_config() -> dict:
        cfg = orig()
        extra = "\n\n".join(x for x in (FRONT_SELF, ws.section("front"), front_facts(memory)) if x)
        if extra:
            cfg["instructions"] = f"{cfg.get('instructions', '')}\n\n{extra}"
        return cfg
    live.session_config = session_config
    live._workspace_front = ws.section("front")


async def note_live(live, ws: Workspace, name: str) -> bool:
    """Tell a running GPT-Live session about a changed persona file (silent context, like the clock note)."""
    if live is None or not getattr(live, "running", False) or getattr(live, "conn", None) is None:
        return False
    if "front" not in SPECS[name].inject:
        return False
    text = ws.section("front")
    if text == getattr(live, "_workspace_front", None):
        return False
    live._workspace_front = text
    note = f"[workspace update: {name} changed. From now on follow this]\n{text or '(the file is now empty)'}"
    try:
        await live.conn.session.thinking.append(content=_cap(note, 2500), delegation_id=None,
                                                event_id=f"jarves_ws_{int(time.time())}")
        return True
    except Exception as e:
        logs.warn("workspace", "WORKSPACE_LIVE_NOTE_FAILED", "live session not updated", error_type=type(e).__name__)
        return False
