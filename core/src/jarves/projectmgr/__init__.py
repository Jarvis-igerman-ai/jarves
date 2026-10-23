"""Project manager: JARVES splits a software project into small tasks and runs short-lived coding agents (Claude Code,
Codex, OpenCode, OpenRouter models) in parallel git worktrees, passing work forward by handoff. Plan:
docs/plans/PROJECT_MANAGER.md."""
from __future__ import annotations

from .. import features, hebrew
from ..capabilities import PI_ROADMAP

FEATURE = "project_manager"
SKILL = "project"
ABOUT = ("orchestrates coding agents (Claude Code, Codex, OpenCode) on a new or already-started software project: "
         "the work is split your way, by its suggestion, by consulting several agents, or given to one lead agent with "
         "its own sub-agents; tasks run in parallel in short sessions and it tells you the status; add, change or split "
         "tasks, ask for details and talk to an agent at any time. Never pushes; merges into your branch only when you "
         "approve")
EVIDENCE = ("core/tests/test_projectmgr.py (fake agent executables, real git worktrees) + docs/plans/PROJECT_MANAGER.md; "
            "real Claude Code / Codex / OpenCode runs are not verified yet")
features.register(FEATURE, "מנהל פרויקט תוכנה", "Software project manager", False, risk="medium", skills=[SKILL], applies="live",
                  about=ABOUT,
                  targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE},
                           "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                                              "blocker": "not tried on a Pi; coding agents run on the PC"}})
hebrew.TEXT.update({
    ABOUT: "מתזמר סוכני קוד (Claude Code, Codex, OpenCode) על פרויקט תוכנה חדש או כזה שכבר התחיל: העבודה מתפצלת "
           "כמו שתגיד, לפי הצעה שלו, בהתייעצות עם כמה סוכנים, או ניתנת לסוכן מוביל אחד עם תת-סוכנים משלו; משימות רצות "
           "במקביל בשיחות קצרות והוא מדווח מצב; בכל רגע אפשר להוסיף, לשנות או לפצל משימות, לבקש פירוט ולדבר עם סוכן. "
           "אף פעם לא דוחף; ממזג לענף שלך רק כשאתה מאשר",
    EVIDENCE: "core/tests/test_projectmgr.py (תוכניות סוכן מדומות, git worktrees אמיתיים) ו-docs/plans/PROJECT_MANAGER.md; "
              "הרצות אמיתיות של Claude Code / Codex / OpenCode עוד לא נבדקו",
    "not tried on a Pi; coding agents run on the PC": "לא נוסה על Pi; סוכני הקוד רצים על המחשב",
})
