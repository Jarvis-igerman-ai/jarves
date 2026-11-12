"""`jarvis qa-daily install|check|status` (JAR-232): the installed-beta check the owner (or QA) runs once a day.
Nothing schedules it: it is a manual routine, `install` first, then `check` once setup has finished.

install: `jarvis install-latest --channel beta` (backup first, signed silent setup, doctor + selftest + status, automatic
         rollback when a check fails; all of that stays in jarves.install_latest).
check:   reads that report, runs the top scenarios in fresh processes, writes a short demo transcript and one local
         bug draft per failure (P1 = install/doctor/selftest/version, or a report that is missing, unfinished, not
         from today or not a beta; P2 = a scenario) under <home>/qa/daily/<day>/. Paths in drafts are home-relative.
Both refuse unless the owner switch `qa_installs_enabled` is on (beta, OFF by default). Text is redacted (secrets,
user name in paths, e-mails, phone numbers) and nothing leaves the PC: no upload, no GitHub filing.
The demo is a text transcript; a screen recording is not made (UNVERIFIED on the owner PC).
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import logs

FEATURE = "qa_installs_enabled"
SCENARIOS = (("status", ["status"]), ("features", ["features"]), ("about", ["about"]), ("health", ["health"]))
OFF_MSG = ("The daily installed-beta check is OFF. The owner turns it on with: "
           "jarvis features on qa_installs_enabled (beta: jarvis features beta on first).")
_CHECK = re.compile(r"^\s*\[(PASS|FAIL)\]\s+([\w-]+)(?::\s*(.*))?$")


@dataclass
class Deps:
    home: Path
    report: Callable[[], str | None]                       # the last install-latest report text, or None
    run_cli: Callable[[list[str]], tuple[int, str]]        # a fresh `jarvis ...` process: (exit code, output)
    day: str = ""


def _clean(text: str, limit: int = 400) -> str:
    return logs.redact_pii(logs.redact(str(text or "")))[-limit:]


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()            # install-latest report times are UTC


def check(deps: Deps) -> dict:
    rel = f"qa/daily/{deps.day or _today()}"                          # home-relative: no user name in drafts
    out = Path(deps.home) / rel
    out.mkdir(parents=True, exist_ok=True)
    demo, bugs = out / "demo.txt", []
    lines = ["JARVES daily installed-beta check (local only)"]

    def bug(prio: str, name: str, detail: str) -> None:
        bugs.append({"priority": prio, "check": name, "title": f"{prio}: daily beta check '{name}' failed",
                     "detail": _clean(detail), "evidence": f"{rel}/demo.txt", "local_only": True})

    report, day = deps.report(), out.name
    head = report.splitlines()[0] if report else ""
    if report:
        lines += ["$ jarvis install-latest --report", _clean(report, 2000)]
    if not report:
        bug("P1", "install-latest", "no install-latest report; run: jarvis qa-daily install")
    elif not head.startswith(("PASS", "FAIL")):                    # PENDING or DRY RUN: nothing was judged yet
        bug("P1", "install-latest", f"install not finished ({head.split('  ')[0]}): {head}")
    elif day not in head:
        bug("P1", "install-latest", f"report is from an earlier day, not {day}: {head}")
    elif not re.search(r"^version: .*-> \S*-beta", report, re.M):
        bug("P1", "install-latest", "installed version is not a beta; run: jarvis qa-daily install")
    else:
        for ln in report.splitlines():
            m = _CHECK.match(ln)
            if m and m.group(1) == "FAIL":
                bug("P1", m.group(2), m.group(3) or "")
        if report.startswith("FAIL") and not bugs:
            bug("P1", "install-latest", report.splitlines()[0])
    for name, argv in SCENARIOS:
        code, text = deps.run_cli(argv)
        lines += [f"$ jarvis {' '.join(argv)}  (exit {code})", _clean(text, 600)]
        if code != 0:
            bug("P2", name, f"exit {code}: {text}")
    demo.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "bugs.json").write_text(json.dumps(bugs, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": not bugs, "bugs": bugs, "dir": rel}


def _enabled(cfg, home) -> bool:
    from .features import Features
    return Features(cfg, home).enabled(FEATURE)


def cmd_qa_daily(a, cfg, home) -> int:
    if not _enabled(cfg, home):
        print(OFF_MSG)
        return 2
    action = getattr(a, "action", "status")
    if action == "install":
        from . import install_latest
        ns = argparse.Namespace(channel="beta", dry_run=bool(getattr(a, "dry_run", False)), report=False, finish=None)
        return install_latest.cmd_install_latest(ns, cfg, home)
    if action == "check":
        from .install_latest import REPORT_FILE, _cli_subprocess
        rp = Path(home) / "updates" / REPORT_FILE
        res = check(Deps(home, lambda: rp.read_text(encoding="utf-8-sig") if rp.is_file() else None, _cli_subprocess,
                         _today()))
        for b in res["bugs"]:
            print(f"  [{b['priority']}] {b['check']}: {b['detail'][:160]}")
        print(("PASS" if res["ok"] else "FAIL") + f"  qa-daily  demo + bug drafts (local only): <data folder>/{res['dir']}")
        return 0 if res["ok"] else 1
    days = sorted(p for p in (Path(home) / "qa" / "daily").glob("*/bugs.json"))
    if not days:
        print("qa-daily is ON. No check yet: run jarvis qa-daily install, then jarvis qa-daily check.")
        return 0
    bugs = json.loads(days[-1].read_text(encoding="utf-8"))
    print(f"qa-daily is ON. Last check {days[-1].parent.name}: {'FAIL' if bugs else 'PASS'} ({len(bugs)} bug drafts)")
    return 0


def add_cli(sub) -> None:
    p = sub.add_parser("qa-daily", help="installed-beta check, run by hand once a day: install the newest beta silently (backup + "
                                        "rollback), run checks and top scenarios, local demo + P1/P2 bug drafts")
    p.add_argument("action", nargs="?", choices=["install", "check", "status"], default="status")
    p.add_argument("--dry-run", action="store_true", help="install: only print the plan, change nothing")


COMMANDS = {"qa-daily": cmd_qa_daily}

# ---------------------------------------------------------------------- catalog row
from . import features as feat, hebrew    # noqa: E402

ABOUT = ("the installed-beta check the owner runs once a day by hand: installs the newest beta silently (backup + rollback), runs doctor, selftest "
         "and top scenarios, writes a local demo transcript and P1/P2 bug drafts; nothing leaves the PC")
EVIDENCE = "UNIT test_qa_daily.py (off by default, P1/P2 drafts, redaction, fakes only; no real install)"
BLOCKER = "never run on the owner PC"
PI = "the installer is Windows only"
hebrew.TEXT.update({
    ABOUT: "בדיקת הבטא המותקנת שהבעלים מריץ ידנית פעם ביום: מתקינה בשקט את הבטא החדשה (גיבוי וחזרה), מריצה doctor, selftest ותרחישים "
           "מרכזיים, כותבת תמליל הדגמה מקומי וטיוטות באגים P1/P2; שום דבר לא יוצא מהמחשב",
    EVIDENCE: "בדיקות יחידה test_qa_daily.py (כבוי כברירת מחדל, טיוטות P1/P2, הסתרת פרטים, דמה בלבד; בלי התקנה אמיתית)",
    BLOCKER: "מעולם לא הורץ במחשב של הבעלים",
    PI: "תוכנית ההתקנה היא ל-Windows בלבד",
    OFF_MSG: "הבדיקה היומית של הבטא המותקנת כבויה. הבעלים מפעיל אותה עם: "
             "jarvis features on qa_installs_enabled (בטא: קודם jarvis features beta on).",
})
feat.register(FEATURE, "בדיקה יומית של הבטא המותקנת", "Daily installed-beta check", False, risk="medium",
              about=ABOUT, stage="beta",
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["installs signed betas silently"], settings="jarvis features on qa_installs_enabled")
hebrew.TEXT["installs signed betas silently"] = "מתקין בשקט גרסאות בטא חתומות"
