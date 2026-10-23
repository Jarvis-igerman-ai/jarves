"""Project manager: JARVES splits a software project into small tasks and runs short-lived coding agents (Claude Code,
Codex, OpenCode, OpenRouter models) in parallel git worktrees, passing work forward by handoff. Plan:
docs/plans/PROJECT_MANAGER.md."""
from __future__ import annotations

from .. import features, hebrew
from ..capabilities import PI_ROADMAP

FEATURE = "project_manager"
SKILL = "project"
ABOUT = ("manages a software project for you: splits the goal into small tasks, runs coding agents (Claude Code, Codex, "
         "OpenCode) on them in parallel, each in its own short session, and tells you the status; you can add or change "
         "tasks, ask for details and talk to an agent. Never pushes or merges your branches")
EVIDENCE = ("core/tests/test_projectmgr.py (fake agent executables, real git worktrees) + docs/plans/PROJECT_MANAGER.md; "
            "real Claude Code / Codex / OpenCode runs are not verified yet")
features.register(FEATURE, "מנהל פרויקט תוכנה", "Software project manager", False, risk="medium", skills=[SKILL], applies="live",
                  about=ABOUT,
                  targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE},
                           "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                                              "blocker": "not tried on a Pi; coding agents run on the PC"}})
hebrew.TEXT.update({
    ABOUT: "מנהל בשבילך פרויקט תוכנה: מפרק את המטרה למשימות קטנות, מריץ עליהן סוכני קוד (Claude Code, Codex, OpenCode) "
           "במקביל, כל אחד בשיחה קצרה משלו, ומדווח מצב; אפשר להוסיף או לשנות משימות, לבקש פירוט ולדבר עם סוכן. "
           "אף פעם לא דוחף ולא ממזג את הענפים שלך",
    EVIDENCE: "core/tests/test_projectmgr.py (תוכניות סוכן מדומות, git worktrees אמיתיים) ו-docs/plans/PROJECT_MANAGER.md; "
              "הרצות אמיתיות של Claude Code / Codex / OpenCode עוד לא נבדקו",
    "not tried on a Pi; coding agents run on the PC": "לא נוסה על Pi; סוכני הקוד רצים על המחשב",
})
