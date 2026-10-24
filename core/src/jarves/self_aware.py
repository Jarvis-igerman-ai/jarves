"""Self-awareness (issue #61): JARVES can say what he is, what changed, where the project is going and take an
improvement ticket.

    jarvis about            version, channel, build, where installed, data folder, what is on/off
    jarvis whatsnew         the changes of this version (release_notes data; --version V for another one)
    jarvis roadmap          short status per milestone from ROADMAP.md (shipped next to the program by the installer)
    jarvis ticket TEXT      record an improvement ticket locally and show the exact text; nothing is sent
    jarvis ticket send ID   show it again and send it only after you approve (issue_registry.report: popup or --yes)

No new store and no new sender: tickets are issue_registry records (area "ticket"), sending is issue_registry.report.
The same facts are read aloud by three voice tools (about_jarves, whats_new, roadmap_status). Offline, stdlib only.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

from . import __version__, features as feat, release_notes, updates
from .capabilities import PI_ROADMAP

FEATURE = "self_awareness"
MAX_LINES = 12
TICKET_SEVERITY = "P3"


# ---------------------------------------------------------------------- facts
def install_path() -> str:
    return str(Path(__file__).resolve().parents[1])


def about_facts(cfg, home: Path, states: list[dict] | None = None) -> dict:
    b = updates.build_info()
    on = [s["id"] for s in states or [] if s.get("active")]
    off = [s["id"] for s in states or [] if not s.get("active")]
    return {"version": updates.version_line(home), "channel": b["channel"], "commit": (b.get("commit") or "")[:12],
            "built": b.get("built") or "", "installed": updates.is_installed_build(), "install_path": install_path(),
            "data_dir": str(home), "on": on, "off": off}


def about_text(facts: dict) -> list[str]:
    lines = [facts["version"],
             f"Install:  {facts['install_path']} ({'installed build' if facts['installed'] else 'source checkout'})",
             f"Data:     {facts['data_dir']}"]
    if facts["on"] or facts["off"]:
        lines.append(f"Features: {len(facts['on'])} on, {len(facts['off'])} off or unavailable "
                     "(list: jarvis features; guides: jarvis tour)")
        lines.append("  on:  " + (", ".join(facts["on"][:MAX_LINES]) + (" ..." if len(facts["on"]) > MAX_LINES else "") or "-"))
        lines.append("  off: " + (", ".join(facts["off"][:MAX_LINES]) + (" ..." if len(facts["off"]) > MAX_LINES else "") or "-"))
    lines.append("More: jarvis whatsnew | jarvis roadmap | jarvis ticket \"idea\" | jarvis --help")
    return lines


def whatsnew_lines(version: str | None = None, limit: int = MAX_LINES) -> tuple[bool, list[str]]:
    """(found, lines): the items of one version, 'Kind: text'. Not found -> (False, [reason])."""
    secs = release_notes.sections()
    if not secs:
        return False, ["No release notes found in this install."]
    sec = release_notes.pick(secs, version or release_notes.stable_of(__version__))
    if sec is None:
        return False, [f"No release notes for {version}."]
    head = f"Version {sec['version']}" + (f" ({sec['status']})" if sec["status"] else "")
    items = [f"- {i['kind']}{' (' + i['scope'] + ')' if i['scope'] else ''}: {_short(i['text'])}" for i in sec["items"][:limit]]
    more = len(sec["items"]) - limit
    return True, [head, *items] + ([f"... and {more} more: jarvis release-notes --open"] if more > 0 else [])


def _short(t: str, n: int = 160) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0] + "…"


def find_roadmap() -> str:
    for d in (Path.cwd(), *Path(__file__).resolve().parents):
        for f in (d / "ROADMAP.md", d / "docs" / "ROADMAP.md"):
            if f.is_file():
                try:
                    return f.read_text(encoding="utf-8")
                except OSError:
                    return ""
    return ""


def _plain(s: str) -> str:
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return re.sub(r"[*`]", "", s).strip()


def roadmap_lines(text: str | None = None) -> tuple[bool, list[str]]:
    """The first milestone table of ROADMAP.md as 'Name: status', plain and short."""
    text = find_roadmap() if text is None else text
    if not text:
        return False, ["No roadmap file in this install (the installer ships ROADMAP.md next to the program)."]
    rows: list[str] = []
    heading = ""
    for line in text.splitlines():
        if line.startswith("## ") and rows:
            break
        if line.startswith("## ") and not heading:
            heading = _plain(line[3:])
        if line.startswith("|") and not re.match(r"^\|[\s:|-]+\|?\s*$", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0].lower() != "milestone":
                rows.append(f"- {_plain(cells[0])}: {_short(_plain(cells[1]), 200)}")
    if not rows:
        return False, ["The roadmap has no milestone table in this install."]
    return True, [heading or "Roadmap", *rows, "Full plan: docs/ROADMAP.md; only docs/NOW.md authorizes work."]


# ---------------------------------------------------------------------- tickets (issue_registry)
def clean_ticket(text: str) -> str:
    from . import issue_registry as ir
    return ir.clean_text(text, 100)


def record_ticket(home: Path, text: str):
    """Record (or re-record: same text, same id) an improvement ticket locally. Nothing leaves the computer."""
    from . import issue_registry as ir
    title = clean_ticket(text)
    if not title:
        return None
    code = "TICKET_" + hashlib.sha256(title.lower().encode()).hexdigest()[:8].upper()
    return ir.Registry(home).record("ticket", code, TICKET_SEVERITY, {"kind": "improvement"}, title=title,
                                    title_he=title if re.search(r"[֐-׿]", title) else "", source="ticket")


def cmd_ticket(a, cfg, home, *, out: Callable[..., None] = print, ask=input) -> int:
    from . import issue_registry as ir
    words = list(a.text or [])
    if words and words[0] == "send":
        if len(words) != 2 or not ir._ID.match(words[1]):
            out("usage: jarvis ticket send JI-xxxxxxxx   (the id comes from jarvis ticket \"idea\")")
            return 2
        sub = type("A", (), {"action": "report", "target": words[1], "yes": a.yes, "json": False})()
        return ir.cmd_issues(sub, cfg, home, out=out, ask=ask)
    if words and words[0] == "list":
        rows = [r for r in ir.Registry(home).list(None) if r.get("area") == "ticket"]
        out(f"{len(rows)} ticket(s)" + ("" if rows else " - make one: jarvis ticket \"your idea\""))
        for r in rows:
            out(f"  {r['id']}  {r['status']:<9} {r['title']}")
        return 0
    text = " ".join(words).strip()
    if not text:
        out("usage: jarvis ticket \"short improvement idea\" | list | send ID")
        return 2
    r = record_ticket(home, text)
    if r is None:
        out("the text is empty or looks like it holds a path, address or secret; write a short plain sentence")
        return 2
    b = ir.bundle(r)
    out(f"Ticket {r['id']} saved locally. NOTHING is sent. This exact text would be sent to {ir.REPO} (label {ir.LABEL}):\n\n"
        f"# {b['title']}\n\n{b['body']}\n\nTo send it: jarvis ticket send {r['id']}   (asks you first)")
    return 0


# ---------------------------------------------------------------------- CLI
def cmd_about(a, cfg, home, *, out=print) -> int:
    states: list[dict] = []
    try:
        from .cli import _cli_features
        states = _cli_features(cfg, home).list(wait=False)
    except Exception:
        pass
    for line in about_text(about_facts(cfg, home, states)):
        out(line)
    return 0


def cmd_whatsnew(a, cfg, home, *, out=print) -> int:
    ok, lines = whatsnew_lines(getattr(a, "version", None))
    for line in lines:
        out(line)
    return 0 if ok else 1


def cmd_roadmap(a, cfg, home, *, out=print) -> int:
    ok, lines = roadmap_lines()
    for line in lines:
        out(line)
    return 0 if ok else 1


def add_cli(sub) -> None:
    sub.add_parser("about", help="what this JARVES is: version, channel, build, install and data folders, what is on/off")
    w = sub.add_parser("whatsnew", help="what changed in this version (release notes; --version V for another)")
    w.add_argument("--version", default=None)
    sub.add_parser("roadmap", help="short milestone status from ROADMAP.md")
    t = sub.add_parser("ticket", help="improvement ticket: jarvis ticket \"idea\" (saved locally, nothing sent) | list | "
                                      "send ID (shows the exact text, asks first)")
    t.add_argument("text", nargs="*", help="the idea, or: list | send ID")
    t.add_argument("--yes", action="store_true", help="send: approve without the typed confirmation")


COMMANDS = {"about": cmd_about, "whatsnew": cmd_whatsnew, "roadmap": cmd_roadmap, "ticket": cmd_ticket}


# ---------------------------------------------------------------------- voice tools
def make_skill(home: Path, cfg_fn: Callable[[], object], lang: Callable[[], str]):
    from pydantic import BaseModel

    from .skills import Skill, Tool, ToolResult

    class _NoArgs(BaseModel):
        pass

    def spoken(lines: list[str], n: int = 4) -> str:
        return ". ".join(re.sub(r"^- ", "", x) for x in lines[:n])

    class SelfAwareSkill(Skill):
        name = "self_awareness"
        description = "what JARVES is, what is new, the roadmap"

        def tools(self) -> list:
            async def about(_: _NoArgs) -> ToolResult:
                f = about_facts(cfg_fn(), home)
                return ToolResult(True, f"{f['version']}. Data folder {f['data_dir']}.", {"channel": f["channel"]}, code="ABOUT_SAID")

            async def whatsnew(_: _NoArgs) -> ToolResult:
                ok, lines = whatsnew_lines(limit=3)
                return ToolResult(ok, spoken(lines), {"dashboard": "#/release-notes"}, code="WHATSNEW_SAID" if ok else "WHATSNEW_NONE")

            async def roadmap(_: _NoArgs) -> ToolResult:
                ok, lines = roadmap_lines()
                return ToolResult(ok, spoken(lines[:4]), {}, code="ROADMAP_SAID" if ok else "ROADMAP_NONE")
            return [Tool("about_jarves", "'what version are you', 'מה הגרסה שלך'.", about, _NoArgs, permission="read"),
                    Tool("whats_new", "'what is new', 'מה חדש'.", whatsnew, _NoArgs, permission="read"),
                    Tool("roadmap_status", "'what is on the roadmap', 'מה בתוכנית'.", roadmap, _NoArgs, permission="read")]

    return SelfAwareSkill()


INSTRUCTION = ("About yourself: for 'what version are you / מה הגרסה' call about_jarves, for 'what is new / מה חדש' call whats_new, "
               "for 'what is the plan / roadmap' call roadmap_status; say the result briefly. A new improvement ticket is "
               "made only with `jarvis ticket` on the terminal, never silently.")


def attach(core) -> None:
    core.registry.register(make_skill(core.home, lambda: core.cfg, lambda: getattr(core.cfg.voice, "language", "he")))
    agent = getattr(core, "agent", None)
    if agent is not None and hasattr(agent, "instructions"):
        base = agent.instructions

        def instructions() -> str:
            try:
                on = core.features.active(FEATURE)
            except Exception:
                on = False
            return f"{base()}\n\n{INSTRUCTION}" if on else base()
        agent.instructions = instructions


# ---------------------------------------------------------------------- feature
feat.register(FEATURE, "מודעות עצמית", "Self-awareness", True, skills=["self_awareness"],
              about="tells you his version, what changed, the roadmap, and takes improvement tickets you approve",
              targets={"windows": {"status": "implemented_unverified", "evidence": "UNIT test_self_aware.py",
                                   "blocker": "not yet tried by the owner on a real install"},
                       "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                                          "blocker": "pure Python; never run on a Raspberry Pi (no device in the test setup)"}},
              permissions=["read"], settings="jarvis about, whatsnew, roadmap, ticket")
