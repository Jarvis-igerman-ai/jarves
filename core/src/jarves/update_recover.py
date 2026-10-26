"""#261: recovery when the new program is dead after an update (a schema bump plus a program that cannot start).

The actual recovery cannot live in the Core (it is the thing that is dead): it is `installer_state.py recover-program`
(standard library only, run with the kept OLD python by `jarvis-rollback.cmd` / the Start-menu item "Recover previous
JARVES"). It restores the verified data backup and the previous program together, and only after the owner approves
(data is never changed without a yes). This module is the Core-side face: `jarvis recover-update` shows what the recovery
would do (a plan, nothing changed) and where the recovery entry is. It adds nothing to the prompt."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from . import clilang
from . import features as feat
from . import hebrew

FEATURE = "update_recover"
ENTRY = "jarvis-rollback.cmd"


def app_dir() -> Path:
    return Path(os.environ.get("JARVES_APP") or Path(__file__).resolve().parents[3]).resolve()


def _load_installer_state(app: Path):
    path = app / "installer_state.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("jarves_installer_state", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def plan(app: Path, home: Path, *, run=None) -> dict:
    """What the recovery would do, changing nothing (approved is never passed here)."""
    mod = _load_installer_state(app)
    if mod is None:
        return {"status": "not_installed", "message": "This is not an installed build: there is nothing to recover."}
    return mod.recover_program(app, home, app / "install-state.json", approved=False, run=run)


def add_cli(sub) -> None:
    p = sub.add_parser("recover-update", help="after an update that left JARVES unable to start: show whether the previous "
                                              "program and your data can be put back (jarvis-rollback.cmd does it, after you approve)")
    p.add_argument("--app", help="the installed app folder (default: this install)")


def cmd_recover_update(a, cfg, home) -> int:
    app = Path(a.app) if getattr(a, "app", None) else app_dir()
    try:
        r = plan(app, Path(home))
    except (ValueError, OSError) as e:
        print(clilang.fmt(f"שגיאה: {type(e).__name__}", f"ERROR: {type(e).__name__}"))
        return 1
    print(r["message"])
    if r["status"] == "needs_approval":
        print(clilang.fmt(f"להחזרה: הריצו {ENTRY} (Start, JARVES, Recover previous JARVES). זה שואל קודם.",
                          f"To recover: run {ENTRY} (Start menu, JARVES, Recover previous JARVES). It asks first."))
    return 0


COMMANDS = {"recover-update": cmd_recover_update}

EVIDENCE = "UNIT test_installer_recover.py (fake process, fake data restore, frozen clock); no real VM run yet"
BLOCKER = "VM case 3b+3c (broken python after a schema bump) not yet run with this entry (owner/VM run pending)"
ABOUT = ("when a new version cannot start after an update, one Start-menu entry (jarvis-rollback.cmd) puts back the previous "
         "program and your data from before the update together, after you approve; it needs no working JARVES")
hebrew.TEXT.update({
    ABOUT: "כשגרסה חדשה לא עולה אחרי עדכון, פריט אחד בתפריט התחל (jarvis-rollback.cmd) מחזיר יחד את התוכנה הקודמת ואת הנתונים "
           "שלפני העדכון, אחרי שאישרתם; לא צריך ש-JARVES יעבוד",
    EVIDENCE: "בדיקות יחידה test_installer_recover.py (תהליך מזויף, שחזור נתונים מזויף, שעון קפוא); עדיין לא רצה במכונה וירטואלית אמיתית",
    BLOCKER: "מקרים 3b+3c במכונה הווירטואלית (פייתון שבור אחרי שינוי סכמה) עוד לא הורצו עם הכניסה הזו (ממתין להרצה)",
    "restores your data and the previous program from the update restore point": "משחזר את הנתונים והתוכנה הקודמת מנקודת השחזור של העדכון",
    "the Pi runs from a git checkout, not an installed build": "ה-Pi רץ מ-git checkout ולא מגרסה מותקנת",
})

feat.register(FEATURE, "החזרה אחרי עדכון שבור", "Update recovery", True, risk="high", about=ABOUT, stage="beta",
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable",
                                          "blocker": "the Pi runs from a git checkout, not an installed build"}},
              permissions=["restores your data and the previous program from the update restore point"],
              settings="jarvis recover-update")
