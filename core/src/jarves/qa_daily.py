"""`jarvis qa-daily install|check|status` (JAR-232): the installed-beta check the owner (or QA) runs once a day.
Daily: while the switch is on, the Core calls `tick` every hour (`start`); once per UTC day it starts `qa-daily install`
in a hidden detached process at a quiet moment (auto_update.core_busy; setup stops the Core), then runs `check`
once today's report is final. When install-latest ends without setup (nothing newer, or it stopped early) it writes no
report file, so `install` keeps that outcome in qa/daily/<day>/install.txt. `run` = one tick.

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
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import logs

FEATURE = "qa_installs_enabled"
SCENARIOS = (("status", ["status"]), ("features", ["features"]), ("about", ["about"]), ("health", ["health"]))
OFF_MSG = ("The daily installed-beta check is OFF. The owner turns it on with: "
           "jarvis features on qa_installs_enabled (beta: jarvis features beta on first).")
WINDOWS_ONLY = "The daily installed-beta check runs on Windows only: the signed installer does not exist here."
_CHECK = re.compile(r"^\s*\[(PASS|FAIL)\]\s+([^:]+?)(?::\s*(.*))?$")


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


def _beta_installed(deps: Deps, lines: list[str]) -> bool:
    """Nothing newer on the beta channel says nothing about what is installed: ask `update status`."""
    code, out = deps.run_cli(["update", "status"])
    lines += [f"$ jarvis update status  (exit {code})", _clean(out, 300)]
    first = (out or "").lstrip().splitlines()[0] if (out or "").strip() else ""   # "JARVES <installed> (...)"; a
    return code == 0 and bool(re.match(r"JARVES \d+\.\d+\.\d+-beta\.\d+\b", first))  # "New version" line is not it


def check(deps: Deps) -> dict:
    rel = f"qa/daily/{deps.day or _today()}"                          # home-relative: no user name in drafts
    out = Path(deps.home) / rel
    out.mkdir(parents=True, exist_ok=True)
    demo, bugs = out / "demo.txt", []
    lines = ["JARVES daily installed-beta check (local only)"]

    def bug(prio: str, name: str, detail: str) -> None:
        name = _clean(name, 80)                                       # from the report text: redact it too
        bugs.append({"priority": prio, "check": name, "title": f"{prio}: daily beta check '{name}' failed",
                     "detail": _clean(detail), "evidence": f"{rel}/demo.txt", "local_only": True})

    report, day = deps.report(), out.name
    head = report.splitlines()[0] if report else ""
    if report:
        lines += ["$ jarvis install-latest --report", _clean(report, 2000)]
    if not report:
        log = _tail(out / SPAWN_LOG)                                   # the detached child's own output, if any
        bug("P1", "install-latest", "no install-latest report; run: jarvis qa-daily install"
            + (f"; qa-daily install output: {log}" if log else ""))
    elif not head.startswith(("PASS", "FAIL")):                    # PENDING or DRY RUN: nothing was judged yet
        bug("P1", "install-latest", f"install not finished ({head.split('  ')[0]}): {head}")
    elif not _of_day(head, day):
        bug("P1", "install-latest", f"report is not from {day} (or the next UTC morning): {head}")   # earlier or later
    elif not re.search(r"^version: .*-> \S*-beta", report, re.M) and not report.startswith("FAIL") \
            and not (re.search(r"^\s*\[PASS\] up to date", report, re.M) and _beta_installed(deps, lines)):
        bug("P1", "install-latest", "installed version is not a beta; run: jarvis qa-daily install")
    else:
        for ln in report.splitlines():
            m = _CHECK.match(ln)
            if m and m.group(1) == "FAIL":
                bug("P1", m.group(2), m.group(3) or "")
        if report.startswith("FAIL") and not bugs:
            bug("P1", "install-latest", report.splitlines()[0])
    own = [(n, [n], "P1") for n in ("doctor", "selftest")             # no setup ran (up to date, early stop)
           if not re.search(rf"^\s*\[(?:PASS|FAIL)\] {n}\b", report or "", re.M)]
    for name, argv, prio in own + [(n, a, "P2") for n, a in SCENARIOS]:
        code, text = deps.run_cli(argv)
        lines += [f"$ jarvis {' '.join(argv)}  (exit {code})", _clean(text, 600)]
        if code != 0:
            bug(prio, name, f"exit {code}: {text}")
    demo.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "bugs.json").write_text(json.dumps(bugs, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": not bugs, "bugs": bugs, "dir": rel}


def _of_day(head: str, d: str) -> bool:
    """install-latest stamps its own start, seconds after our marker: an install begun just before UTC midnight
    carries the next day's date, so a header of day d or d+1 belongs to the attempt of day d."""
    nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    return d in head or nxt in head


def _open_day(root: Path, today: str) -> str:
    """The day of the newest install attempt not yet checked (it may have begun before UTC midnight), else today.
    Only yesterday can still be open: an older unchecked attempt is abandoned, never a reason to skip today."""
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    days = sorted(m.parent.name for m in root.glob("*/install.started")
                  if m.parent.name in (yesterday, today) and not (m.parent / "tick.checked").is_file())
    return days[-1] if days else today


INSTALL_WAIT_S = 5400 + 600                                         # install_latest.PENDING_S + margin
TICK_S = 3600.0


def _supported(platform: str | None = None) -> bool:
    """The signed installer is Windows only (catalog: the Pi is not_applicable); `enabled` does not check targets."""
    return (platform or sys.platform) == "win32"


CORRUPT = -1.0
MTIME_SLACK_S = 2.0                                                   # FAT/exFAT file times are 2 s coarse


def _marker_time(path: Path) -> float | None:
    """The start time in an install.started marker; None when empty, CORRUPT when unreadable or not a number."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return float(raw) if raw else None
    except (OSError, ValueError):
        return CORRUPT


def tick(home, *, enabled, day, report, spawn, do_check, clock=time.time, busy=lambda: [],
         supported=lambda: True) -> str:
    """One daily step: off | unsupported | done | busy | installing | spawn_failed | waiting | checking | checked.
    Install and check at most
    once a day; the silent install (it stops the Core) waits for a quiet moment, like auto_update."""
    if not enabled():
        return "off"
    if not supported():
        return "unsupported"
    root, today = Path(home) / "qa" / "daily", day()
    d = _open_day(root, today)                                        # begun before UTC midnight: finish it first
    out = root / d
    done = out / "tick.checked"                                       # ours: a manual `check` does not end the day
    if done.is_file():
        return "done"
    marker = out / "install.started"
    if not marker.is_file():                                          # an earlier (e.g. stable) report today is not ours
        if busy():
            return "busy"
        out.mkdir(parents=True, exist_ok=True)
        started = clock()
        try:                                                          # exclusive: a manual `run` cannot spawn twice
            with open(marker, "x", encoding="utf-8") as f:
                f.write(str(started))
        except FileExistsError:
            return "waiting"
        try:
            spawn(["qa-daily", "install"])
        except Exception as e:                                        # the day's draft carries the real cause
            rec = out / INSTALL_FILE
            if rec.is_file() and rec.stat().st_mtime >= started - MTIME_SLACK_S:  # the child stopped early (exit 1):
                return "installing"                                   # its own record is the result, keep it
            rec.write_text(f"FAIL  install-latest  {d} (not started)\n"
                           f"  [FAIL] start qa-daily install: {type(e).__name__}: {e}\n", encoding="utf-8")
            return "spawn_failed"
        return "installing"
    since = _marker_time(marker)
    if since is None or (since == CORRUPT and d == today):            # crashed before the clock was written, or a
        since = clock()                                               # broken marker today: an install may still run
        marker.write_text(str(since), encoding="utf-8")
    elif since == CORRUPT:                                            # yesterday's: long over, judge it now
        since = 0.0
    text = report(d, since) or ""                                     # only what was written after this attempt began
    final = text.startswith(("PASS", "FAIL")) and _of_day(text.splitlines()[0], d)
    if not final and clock() - since <= INSTALL_WAIT_S:
        return "waiting"
    with _check_lock(out / CHECK_LOCK) as held:                       # another tick (Core or manual run) checks
        if not held:
            return "checking"
        if done.is_file() or str(done) in _ended:                     # it ended the day while we waited for the lock
            return "done"
        try:
            do_check(d)                                               # never reported: check writes the P1 draft
        except Exception as e:                                        # status must show this day, not the one before
            _write_check_failed(out, d, e)
            raise
        finally:                                                      # a check that raises still ends the day (once)
            _ended.add(str(done))                                     # this process never re-checks the day
            try:                                                      # a failed write must not hide do_check's error
                done.write_text(str(clock()), encoding="utf-8")
            except OSError as e:
                logs.warn("updates", "QA_DAILY_DONE_WRITE_FAILED", "could not end the day",
                          error_type=type(e).__name__)
    return "checked"


CHECK_LOCK = "check.lock"
_ended: set[str] = set()
_lock_warned = False


@contextlib.contextmanager
def _check_lock(path: Path):
    """Yield True while this tick holds the day's check lock: an OS file lock (runtime/lock.py), so it is never
    removed while held and the OS frees it when a Core dies mid-check. The file itself stays."""
    global _lock_warned
    from .runtime.lock import _try_lock, _unlock
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:                                              # e.g. access denied: not checkable now
        if not _lock_warned:                                          # once per Core run, not every hourly tick
            _lock_warned = True
            logs.warn("updates", "QA_DAILY_LOCK_FAILED", "cannot open the daily check lock",
                      error_type=type(e).__name__)
        yield False
        return
    try:
        held = _try_lock(fd)
        try:
            yield held
        finally:
            if held:
                _unlock(fd)
    finally:
        os.close(fd)


def _write_check_failed(out: Path, d: str, e: Exception) -> None:
    detail = _clean(f"{type(e).__name__}: {e}")
    bugs = [{"priority": "P1", "check": "qa-daily check", "title": "P1: daily beta check 'qa-daily check' failed",
             "detail": detail, "evidence": f"qa/daily/{d}/bugs.json", "local_only": True}]
    try:                                                              # best effort: the caller re-raises e, so the
        (out / "bugs.json").write_text(json.dumps(bugs, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:                                                   # Core loop logs QA_DAILY_TICK_FAILED once
        pass


INSTALL_FILE = "install.txt"                                         # begin() ended without setup: no report file


def _report_reader(home, day: str, since: float = 0.0):
    """The newest (by file time) of our own install record for the day (up to date / stopped early) and
    install-latest's report file; with `since`, only one written after that install attempt began."""
    from .install_latest import REPORT_FILE
    paths = (Path(home) / "qa" / "daily" / day / INSTALL_FILE, Path(home) / "updates" / REPORT_FILE)

    def read() -> str | None:
        fresh = [(p.stat().st_mtime, -i, str(p)) for i, p in enumerate(paths)       # a tie: our own record
                 if p.is_file() and p.stat().st_mtime >= since - MTIME_SLACK_S]
        return Path(max(fresh)[2]).read_text(encoding="utf-8-sig") if fresh else None
    return read


def _since(home, day: str) -> float:
    m = Path(home) / "qa" / "daily" / day / "install.started"
    return max(_marker_time(m) or 0.0, 0.0) if m.is_file() else 0.0


SPAWN_LOG = "spawn.log"                                               # the detached child's stdout + stderr
START_WAIT_S = 5.0


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - limit))
            return _clean(f.read().decode("utf-8", "replace"))
    except OSError:
        return ""


def _spawn_hidden(argv: list[str], log: Path | None = None, wait_s: float = START_WAIT_S) -> None:
    """Start detached and hidden; its output goes to `log`. A child that exits non-zero at once (e.g. Python
    cannot import jarves) raises with that output, so tick records a start failure, not a missing report."""
    from .install_latest import cli_argv
    # DETACHED_PROCESS already means no console; CREATE_NO_WINDOW is a harmless fallback (ignored with it)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    sink = open(log, "wb") if log else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(cli_argv(argv), stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
                                creationflags=flags, close_fds=True)
    finally:
        if log:
            sink.close()                                              # the child keeps its own handle
    try:
        code = proc.wait(timeout=wait_s)
    except subprocess.TimeoutExpired:
        return                                                        # still running: the install is under way
    if code != 0:
        raise RuntimeError(f"child exited {code} at start: {_tail(log) if log else 'no output kept'}")


def _tick_real(cfg, home, busy=lambda: []) -> str:
    from .install_latest import _cli_subprocess
    day = _today()
    out = Path(home) / "qa" / "daily" / day

    def do_check(d: str) -> dict:
        return check(Deps(home, _report_reader(home, d, _since(home, d)), _cli_subprocess, d))
    return tick(home, enabled=lambda: _enabled(cfg, home), day=lambda: day,
                report=lambda d, since: _report_reader(home, d, since)(),
                spawn=lambda argv: _spawn_hidden(argv, out / SPAWN_LOG),
                busy=busy, supported=_supported, do_check=do_check)


def start(core, *, first_delay_s: float = 600.0, every_s: float = TICK_S) -> threading.Thread:
    """A daemon thread in the Core, like auto_update.start; does nothing while the switch is off."""
    stop = threading.Event()

    def run() -> None:
        wait = first_delay_s
        while not stop.wait(wait):
            wait = every_s
            try:
                from .auto_update import core_busy
                r = _tick_real(core.cfg, core.home, busy=lambda: core_busy(core))
                if r not in ("off", "unsupported", "done", "waiting", "busy"):
                    logs.info("updates", "QA_DAILY_TICK", "daily installed-beta check step", step=r)
            except Exception as e:                                    # never stops the Core
                logs.warn("updates", "QA_DAILY_TICK_FAILED", "daily beta check step failed", error_type=type(e).__name__)
    t = threading.Thread(target=run, name="jarves-qa-daily", daemon=True)
    t.stop = stop                                                     # type: ignore[attr-defined]
    t.start()
    return t


def _enabled(cfg, home) -> bool:
    from .features import Features
    return Features(cfg, home).enabled(FEATURE)


def cmd_qa_daily(a, cfg, home) -> int:
    if not _enabled(cfg, home):
        print(OFF_MSG)
        return 2
    action = getattr(a, "action", "status")
    if action in ("install", "check") and not _supported():          # `run` goes through tick's own guard
        print(WINDOWS_ONLY)
        return 2
    if action == "install":
        from . import install_latest
        dry = bool(getattr(a, "dry_run", False))
        ns = argparse.Namespace(channel="beta", dry_run=dry, report=False, finish=None)
        buf, day = io.StringIO(), _open_day(Path(home) / "qa" / "daily", _today())
        with contextlib.redirect_stdout(buf):
            code = install_latest.cmd_install_latest(ns, cfg, home)
        text = buf.getvalue()
        print(text, end="")
        if not dry and text.startswith(("PASS", "FAIL")):                # no setup ran, so no report file is written
            (Path(home) / "qa" / "daily" / day).mkdir(parents=True, exist_ok=True)
            (Path(home) / "qa" / "daily" / day / INSTALL_FILE).write_text(text, encoding="utf-8")
        return code
    if action == "run":                                               # by hand: the owner chose the moment, so no
        step = _tick_real(cfg, home)                                  # quiet-moment check (the Core's tick has one)
        print(f"qa-daily run: {step}")
        return 1 if step == "spawn_failed" else 0
    if action == "check":
        from .install_latest import _cli_subprocess
        day = _open_day(Path(home) / "qa" / "daily", _today())          # the same day `install` and the tick use
        res = check(Deps(home, _report_reader(home, day, _since(home, day)), _cli_subprocess, day))
        for b in res["bugs"]:
            print(f"  [{b['priority']}] {b['check']}: {b['detail'][:160]}")
        print(("PASS" if res["ok"] else "FAIL") + f"  qa-daily  demo + bug drafts (local only): <data folder>/{res['dir']}")
        return 0 if res["ok"] else 1
    days = sorted(p for p in (Path(home) / "qa" / "daily").glob("*/bugs.json"))
    if not days:
        print("qa-daily is ON. No check yet: the Core runs it once a day (or now: jarvis qa-daily run).")
        return 0
    bugs = json.loads(days[-1].read_text(encoding="utf-8"))
    print(f"qa-daily is ON. Last check {days[-1].parent.name}: {'FAIL' if bugs else 'PASS'} ({len(bugs)} bug drafts)")
    return 0


def add_cli(sub) -> None:
    p = sub.add_parser("qa-daily", help="daily installed-beta check (the Core runs it once a day when on; `run` = one step): install the newest beta silently (backup + "
                                        "rollback), run checks and top scenarios, local demo + P1/P2 bug drafts")
    p.add_argument("action", nargs="?", choices=["install", "check", "run", "status"], default="status")
    p.add_argument("--dry-run", action="store_true", help="install: only print the plan, change nothing")


COMMANDS = {"qa-daily": cmd_qa_daily}

# ---------------------------------------------------------------------- catalog row
from . import features as feat, hebrew    # noqa: E402

ABOUT = ("the installed-beta check the Core runs once a day while on: installs the newest beta silently (backup + rollback), runs doctor, selftest "
         "and top scenarios, writes a local demo transcript and P1/P2 bug drafts; nothing leaves the PC")
EVIDENCE = "UNIT test_qa_daily.py (off by default, P1/P2 drafts, redaction, fakes only; no real install)"
BLOCKER = "never run on the owner PC"
PI = "the installer is Windows only"
hebrew.TEXT.update({
    ABOUT: "בדיקת הבטא המותקנת שהליבה מריצה פעם ביום כשהיא מופעלת: מתקינה בשקט את הבטא החדשה (גיבוי וחזרה), מריצה doctor, selftest ותרחישים "
           "מרכזיים, כותבת תמליל הדגמה מקומי וטיוטות באגים P1/P2; שום דבר לא יוצא מהמחשב",
    EVIDENCE: "בדיקות יחידה test_qa_daily.py (כבוי כברירת מחדל, טיוטות P1/P2, הסתרת פרטים, דמה בלבד; בלי התקנה אמיתית)",
    BLOCKER: "מעולם לא הורץ במחשב של הבעלים",
    PI: "תוכנית ההתקנה היא ל-Windows בלבד",
    WINDOWS_ONLY: "הבדיקה היומית של הבטא המותקנת רצה רק ב-Windows: תוכנית ההתקנה החתומה לא קיימת כאן.",
    OFF_MSG: "הבדיקה היומית של הבטא המותקנת כבויה. הבעלים מפעיל אותה עם: "
             "jarvis features on qa_installs_enabled (בטא: קודם jarvis features beta on).",
})
feat.register(FEATURE, "בדיקה יומית של הבטא המותקנת", "Daily installed-beta check", False, risk="medium",
              about=ABOUT, stage="beta",
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["installs signed betas silently"], settings="jarvis features on qa_installs_enabled")
hebrew.TEXT["installs signed betas silently"] = "מתקין בשקט גרסאות בטא חתומות"
