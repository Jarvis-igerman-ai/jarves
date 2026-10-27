"""Coding-agent orchestrator (SW-V4 package E, issue #49): JARVES as the owner's manager of Claude Code and Codex
sessions - including sessions the owner started by hand - by voice, CLI and the dashboard.

Modules: envelope (the #49 message envelope, states, codes, untrusted-text rules), db (agents.db: registry, durable
inbox/outbox, timeline, approvals map, audit, day counters), policy (allow-list, risk, one writer, caps), bridge (the
one local named pipe / Unix socket with a per-instance token), claude (discovery, hooks mapping, the plugin, managed
`claude -p` sessions), codex (Codex App Server client), hook + channel (the plugin's processes), service (the
Orchestrator: every operation as call(op, ...)), surfaces (voice tools, `jarvis agents`, dashboard routes).
Research and the capability matrix: docs/reviews/SWV4_E_ORCHESTRATOR_RESEARCH.md; report: SWV4_E_ORCHESTRATOR.md.
"""
from __future__ import annotations

import shutil

from .. import features, hebrew
from ..capabilities import PI_ROADMAP

FEATURE = "agent_orchestrator"
SKILL = "agents"

ABOUT = ("manages your Claude Code and Codex coding sessions - also ones you started yourself: which are running, "
         "what each is doing, ask and tell them things, start new ones, and route their approval requests to your "
         "screen; only projects on your allow-list; subscription sign-ins only")
EVIDENCE = ("core/tests/test_agents_*.py (real processes: fake Claude Code host + fake Codex App Server speaking the "
            "recorded protocols, the real named pipe, the real approvals popup path) + "
            "docs/reviews/SWV4_E_ORCHESTRATOR.md; live: Claude Code 2.1.304 / Codex 0.163.0 probes")
PI_BLOCKER = "the Unix-socket bridge runs in Linux CI; Claude Code / Codex on a Pi not tried yet"
NO_AGENT = "neither Claude Code (claude) nor Codex (codex) was found on this computer"
features.register(FEATURE, "מנהל סוכני קוד (Claude Code / Codex)", "Coding-agent orchestrator", False,
                  probe=lambda c: _probe(c), slow=True, risk="medium", skills=[SKILL], applies="live", about=ABOUT,
                  targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE},
                           "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                                              "blocker": PI_BLOCKER}})
hebrew.TEXT.update({
    ABOUT: "מנהל את סשני הקוד שלך ב-Claude Code וב-Codex - גם כאלה שהפעלת בעצמך: מי פועל, מה כל אחד עושה, לשאול "
           "ולהגיד להם דברים, להפעיל חדשים, ולהעביר את בקשות האישור שלהם למסך שלך; רק פרויקטים מרשימת המותרים; "
           "רק התחברות עם מנוי",
    EVIDENCE: "core/tests/test_agents_*.py (תהליכים אמיתיים: מארח Claude Code מדומה ושרת Codex App Server מדומה "
              "שמדברים בפרוטוקולים המוקלטים, ה-named pipe האמיתי, מסלול חלון האישור האמיתי) ו-"
              "docs/reviews/SWV4_E_ORCHESTRATOR.md; בדיקה חיה: Claude Code 2.1.304 / Codex 0.163.0",
    PI_BLOCKER: "הגשר ב-Unix socket רץ ב-CI של לינוקס; Claude Code / Codex על Pi עוד לא נוסו",
    NO_AGENT: "לא נמצאו Claude Code (claude) או Codex (codex) במחשב הזה",
})


OC_ABOUT = ("shows your OpenCode sessions through its local server and, only after you approve each one, starts a "
            "session in its own worktree, sends a prompt or stops it; loopback address and server password only")
OC_EVIDENCE = ("core/tests/test_agents_opencode.py (fake OpenCode server with response shapes from "
               "opencode.ai/docs/server and /sdk; no real server contacted)")
features.register("agent_opencode", "OpenCode - צפייה והפעלה באישור", "OpenCode (view and start with approval)", False, risk="high",
                  skills=[], applies="live", about=OC_ABOUT,
                  targets={"windows": {"status": "implemented_unverified", "evidence": OC_EVIDENCE},
                           "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                                              "blocker": "OpenCode server on a Pi not tried yet"}})
hebrew.TEXT.update({
    OC_ABOUT: "מציג את סשני OpenCode שלך דרך השרת המקומי שלו, ורק אחרי שאתה מאשר כל פעולה מפעיל סשן בעץ עבודה "
              "נפרד, שולח הוראה או עוצר אותו; רק כתובת מקומית וסיסמת שרת",
    OC_EVIDENCE: "core/tests/test_agents_opencode.py (שרת OpenCode מדומה עם מבנה תשובות מהתיעוד; לא נוצר קשר עם "
                 "שרת אמיתי)",
    "OpenCode server on a Pi not tried yet": "שרת OpenCode על Pi עוד לא נוסה",
})


def _probe(c) -> tuple[bool, str]:
    cfg = getattr(getattr(c, "cfg", None), "agents", None)
    if (cfg is not None and (cfg.claude_command or cfg.codex_command)) or any(
            shutil.which(n) for n in ("claude", "claude.cmd", "codex", "codex.exe")):
        return True, ""
    from .codex import resolve_command
    return (True, "") if resolve_command() else (False, NO_AGENT)


def attach(core) -> None:
    from .surfaces import attach as _attach
    _attach(core)
