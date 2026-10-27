"""`jarvis install-latest [--channel beta|stable] [--dry-run]`: the owner's whole "install the newest build and test it"
routine in one command (docs/plans/LEAN_PROCESS_V2_2026-10-27.md, item 5).

Two phases, because setup force-stops every process that runs from {app}\\python (jarves.iss StopScript):

  begin (runs from the installed program): backup now -> find the newest signed installer of the channel
    (UpdateChecker.check) -> verify signature, SHA256 and size and take the pre-update backup (UpdateChecker.prepare_install,
    the same code as `jarvis update install`; launch_installer re-checks the SHA256 right before the start) -> stop the Core
    if it ran -> silent setup (/VERYSILENT /SUPPRESSMSGBOXES /NORESTART, plus /RESTARTJARVES=1 when the Core ran). If the
    start fails, the Core is started again and a clean report is printed. Then a tiny PowerShell waiter (outside {app})
    waits for the setup PROCESS to exit and starts phase two.
  finish (`install-latest --finish STATE`, started by the waiter from the NEW program, never while setup runs): waits for
    the new Core when it ran, then runs version check, doctor, selftest and status in fresh processes, rolls back by itself
    when a check fails, writes the ONE short report to <home>/updates/install-latest-report.txt and prints it.
  `jarvis install-latest --report` prints the last report again (the first process is gone after setup stopped it).

Everything that touches the machine is injected through `Deps`, so the tests use fakes (no network, no installer, frozen
clock). Nothing here downloads or verifies by itself: that stays in jarves.updates. A dry run changes nothing except the
update-check cache file (the check is read-only on the network).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import logs

CHANNELS = ("stable", "beta")
STATE_FILE = "install-latest-state.json"
REPORT_FILE = "install-latest-report.txt"
PID_FILE = "install-latest.pid"                   # setup process id written by the parent; "cancel" = setup never started
SETUP_WAIT_S = 1200


@dataclass
class Deps:
    """Everything the flow does to the outside world. Real wiring: `real_deps`; tests pass fakes."""
    home: Path
    channel: str                                              # the channel in force now
    set_channel: Callable[[str], None]
    backup_now: Callable[[], Path]
    check: Callable[[str], dict]                              # channel -> UpdateChecker.status()-shaped dict
    prepare: Callable[[str], dict]                            # channel -> {installer, backup, version, signed, sha256}
    core_running: Callable[[], bool]
    detect_running: Callable[[bool], dict]
    record_was_running: Callable[[dict], None]
    stop_core: Callable[[], None]
    start_core: Callable[[], None]
    launch: Callable[[dict, dict, bool], int]                 # (prepared, was_running, restart) -> setup process id
    wait_installer: Callable[[], bool]                        # True when the setup process has exited
    wait: Callable[[bool], str]                               # (core_ran) -> 'done' | 'failed' | 'timeout'
    run_cli: Callable[[list[str]], tuple[int, str]]           # the NEW program, fresh process: (exit code, output)
    rollback: Callable[[], dict]
    spawn_finisher: Callable[[Path], None] | None = None      # (state file) start the waiter; None: finish in this process
    publish_pid: Callable[[int | None], None] = lambda pid: None   # tell the waiter the setup pid (None = cancelled)
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


@dataclass
class Report:
    ok: bool = False
    changed: bool = False                  # an installer was started
    pending: bool = False                  # phase two (finish) is still to run in another process
    version: str = ""                      # the version asked for
    from_version: str = ""
    revert_id: str = ""                    # the backup that `jarvis update --rollback` restores
    rolled_back: bool = False
    dry_run: bool = False
    stopped_at: str = ""                   # first step that failed
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    at: str = ""

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        return ok

    def to_dict(self) -> dict:
        return {k: ([list(c) for c in v] if k == "checks" else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "Report":
        r = cls()
        for k, v in d.items():
            if k in r.__dict__:
                setattr(r, k, [tuple(c) for c in v] if k == "checks" else v)
        return r

    def render(self) -> str:
        """ONE short report: version, revert id, how to roll back, pass/fail per check."""
        head = ("DRY RUN" if self.dry_run else "PENDING" if self.pending else "PASS" if self.ok else "FAIL") \
            + f"  install-latest  {self.at}"
        lines = [head]
        if self.version:
            lines.append(f"version: {self.from_version or '?'} -> {self.version}")
        if self.revert_id:
            lines.append(f"revert id: {self.revert_id}")
        for name, ok, detail in self.checks:
            lines.append(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        if self.rolled_back:
            lines.append("rolled back to the previous version (data from before the update restored)")
        elif self.changed:
            lines.append("roll back:  jarvis update --rollback --yes")
        return "\n".join(lines)


def _first_line(out: str) -> str:
    for ln in out.splitlines():
        if ln.strip():
            return ln.strip()[:160]
    return ""


def _updates(deps: Deps) -> Path:
    return Path(deps.home) / "updates"


def _save_report(deps: Deps, rep: Report) -> None:
    try:
        _updates(deps).mkdir(parents=True, exist_ok=True)
        (_updates(deps) / REPORT_FILE).write_text(rep.render() + "\n", encoding="utf-8")
    except OSError:
        pass


def _err(e: BaseException) -> str:
    return f"{e.code}: {e.message}" if hasattr(e, "code") and hasattr(e, "message") else type(e).__name__


def _fail_start(deps: Deps, rep: Report, e: BaseException, core_up: bool) -> Report:
    """The installer never started: put the Core back and say so cleanly."""
    rep.add("silent install", False, _err(e) + "; nothing was installed")
    rep.stopped_at = "install"
    if core_up:
        try:
            deps.start_core()
            rep.add("restart Core", True, "started again with the current version")
        except Exception as e2:                           # noqa: BLE001
            rep.add("restart Core", False, f"{type(e2).__name__}; start it with: jarvis start")
    _save_report(deps, rep)
    return rep


def begin(deps: Deps, channel: str | None = None, dry_run: bool = False) -> Report:
    rep = Report(dry_run=dry_run, at=deps.now().strftime("%Y-%m-%d %H:%M UTC"))
    ch = channel or deps.channel
    if ch not in CHANNELS:
        rep.add("channel", False, f"must be one of {', '.join(CHANNELS)}")
        return rep
    if not dry_run and ch != deps.channel:
        deps.set_channel(ch)                              # same switch as `jarvis update --channel`
    s = deps.check(ch)
    rep.add(f"find newest {ch} installer", not s.get("error"), str(s.get("error") or ""))
    if s.get("error"):
        rep.stopped_at = "check"
        return rep
    if not s.get("available"):
        rep.add("up to date", True, "nothing newer on this channel")
        rep.ok = True
        return rep
    rel = s.get("latest") or {}
    rep.version = str(rel.get("version") or "")
    rep.from_version = str(s.get("current") or "")
    shown = sorted({rep.version, str(rel.get("display") or rep.version)})
    if dry_run:
        for step in ("backup now", f"download {rep.version} and verify signature, SHA256 and size",
                     "stop the Core if it runs; silent setup (/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
                     " /RESTARTJARVES=1 when the Core ran)", "wait for the setup process to exit",
                     "version + doctor + selftest + status in the new program",
                     "roll back automatically if a check fails"):
            rep.add("would: " + step, True)
        rep.ok = True
        return rep
    try:
        rep.add("backup now", True, deps.backup_now().name)
    except Exception as e:                                # noqa: BLE001  (BackupError and OS errors alike)
        rep.add("backup now", False, getattr(e, "code", type(e).__name__))
        rep.stopped_at = "backup"
        return rep
    try:
        prep = deps.prepare(ch)
    except Exception as e:                                # noqa: BLE001  (UpdateError and anything else: no traceback)
        rep.add("verify installer (signature, SHA256)", False, _err(e))
        rep.stopped_at = "verify"
        return rep
    rep.add("verify installer (signature, SHA256)", True, f"{Path(prep['installer']).name}, "
                                                           f"{'signed' if prep.get('signed') else 'unsigned'}")
    rep.revert_id = Path(str(prep.get("backup") or "")).name
    core_up = deps.core_running()
    was = deps.detect_running(core_up)                    # read BEFORE anything is stopped (#293)
    try:
        deps.record_was_running(was)
    except OSError:
        pass
    up = _updates(deps)
    for name in (REPORT_FILE, STATE_FILE, PID_FILE):      # nothing of an older run may be mistaken for this one
        try:
            (up / name).unlink(missing_ok=True)
        except OSError:
            pass
    state = {"report": {**rep.to_dict(), "changed": True}, "shown": shown, "core_up": core_up,
             "installer": str(prep["installer"])}
    spawned = False
    if deps.spawn_finisher is not None:                   # the waiter exists BEFORE setup can stop this process
        try:
            up.mkdir(parents=True, exist_ok=True)
            sp = up / STATE_FILE
            sp.write_text(json.dumps(state), encoding="utf-8")
            deps.spawn_finisher(sp)
            spawned = True
        except Exception:                                 # noqa: BLE001  (no waiter: finish here, still after setup)
            spawned = False
    try:
        if core_up:
            deps.stop_core()
        pid = deps.launch(prep, was, core_up)             # restart flag only when the Core ran
    except Exception as e:                                # noqa: BLE001  (UpdateError, OSError from the process start)
        if spawned:
            try:
                deps.publish_pid(None)                    # the waiter ends without judging anything
            except Exception:                             # noqa: BLE001
                pass
        return _fail_start(deps, rep, e, core_up)
    rep.changed = True
    if spawned:
        try:
            deps.publish_pid(pid)
        except Exception:                                 # noqa: BLE001  (the waiter also finds setup by its path)
            pass
        rep.pending = True
        return rep
    return finish(deps, state)


def finish(deps: Deps, state: dict, installer_running: bool = False) -> Report:
    """Phase two. Never judges or rolls back while the setup process is still running."""
    from .updates import UpdateError
    rep = Report.from_dict(state["report"])
    rep.pending = False
    shown, core_up = set(state.get("shown") or []), bool(state.get("core_up"))
    if installer_running or not deps.wait_installer():
        rep.add("silent install", False, "setup is still running after the time limit; nothing was judged or rolled "
                                         "back. Check later: jarvis update status")
        rep.stopped_at = "install"
        _save_report(deps, rep)
        return rep
    end = deps.wait(core_up) if core_up else "done"
    install_ok = rep.add("silent install", end == "done" or (end == "timeout" and not core_up),
                         {"done": "setup finished" + (" and the new Core is healthy" if core_up else ""),
                          "failed": "setup or health check failed",
                          "timeout": "setup finished but the new Core did not report ready in time"}[end])
    ok = install_ok
    if install_ok or end == "timeout":                    # a timeout is judged by the checks below
        code, out = deps.run_cli(["update", "status"])
        ver_ok = code == 0 and any(v and v in out for v in shown)
        ok = rep.add("installed version", ver_ok, _first_line(out) if not ver_ok else rep.version) and ok
        for name, argv in (("doctor", ["doctor"]), ("selftest", ["selftest"]), ("status", ["status", "--brief"])):
            code, out = deps.run_cli(argv)
            good = code == 0 or (name == "status" and (code == 4 or (code == 1 and not core_up)))
            ok = rep.add(name, good, "" if good else f"exit {code}: {_first_line(out)}") and ok
    if not ok:
        try:
            if deps.core_running():
                deps.stop_core()
            deps.rollback()
            rep.rolled_back = True
            rep.add("automatic rollback", True, "previous version + data from before the update")
        except UpdateError as e:
            rep.add("automatic rollback", False, f"{e.code}: {e.message}; run: jarvis update --rollback --yes")
        except Exception as e:                            # noqa: BLE001
            rep.add("automatic rollback", False, f"{type(e).__name__}; run: jarvis update --rollback --yes")
        rep.stopped_at = rep.stopped_at or "checks"
    rep.ok = ok
    logs.info("updates", "INSTALL_LATEST", "install-latest finished", ok=ok, version=rep.version)
    _save_report(deps, rep)
    return rep


def run(deps: Deps, channel: str | None = None, dry_run: bool = False) -> Report:
    """Both phases (finish runs inline when `spawn_finisher` is None: dev checkout, tests)."""
    return begin(deps, channel, dry_run)


# ---------------------------------------------------------------------- real wiring
def _cli_subprocess(argv: list[str]) -> tuple[int, str]:
    """The installed program, started fresh (new code, not this old process). Silent: no window, no console flash."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run([sys.executable, "-m", "jarves", *argv], capture_output=True, text=True, timeout=600,
                       creationflags=flags, errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


_QUOTES = "'‘’‚‛"                # PowerShell treats all four as a single quote


def _ps(s) -> str:
    """A PowerShell single-quoted string: every quote character is doubled, so a path cannot end the string."""
    return "'" + "".join(c * 2 if c in _QUOTES else c for c in str(s).replace("\r", "").replace("\n", "")) + "'"


def finisher_script(state: Path, python: str, report: Path, pid_file: Path, installer: str) -> str:
    """PowerShell run OUTSIDE {app}, started BEFORE setup: wait for the parent to publish the setup pid (or find the setup
    process by its path), wait for that process to exit, then start phase two from the NEW program. A pid file saying
    `cancel` ends it silently (setup never started). If the new version has no `--finish` (first install of this
    feature) it leaves an honest report: the old one was deleted before launch. Write the file as UTF-8 WITH BOM."""
    lines = [
        f"$pidFile = {_ps(pid_file)}",
        f"$inst = {_ps(installer)}",
        "$p = 0",
        "$end = (Get-Date).AddSeconds(180)",
        "while ((Get-Date) -lt $end -and $p -eq 0) {",
        "  if (Test-Path -LiteralPath $pidFile) {",
        "    $t = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)",
        "    if ($t -and $t.Trim() -eq 'cancel') { exit 0 }",
        "    $n = 0; if ($t -and [int]::TryParse($t.Trim(), [ref]$n) -and $n -gt 0) { $p = $n; break }",
        "  }",
        "  $q = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $inst } | Select-Object -First 1",
        "  if ($q) { $p = $q.Id; break }",
        "  Start-Sleep -Milliseconds 500",
        "}",
        f"if ($p -gt 0) {{ Wait-Process -Id $p -Timeout {SETUP_WAIT_S} -ErrorAction SilentlyContinue }}",
        "$left = ($p -gt 0) -and [bool](Get-Process -Id $p -ErrorAction SilentlyContinue)",
        f"$a = @('-X','utf8','-m','jarves','install-latest','--finish',{_ps(state)})",
        "if ($left) { $a += '--installer-running' }",
        f"& {_ps(python)} @a",
        f"if ($LASTEXITCODE -ne 0 -and -not (Test-Path -LiteralPath {_ps(report)})) {{",
        "  'FAIL  install-latest  setup ran, but the installed version cannot finish the checks. Run: jarvis doctor; "
        "jarvis selftest; jarvis update status. Roll back: jarvis update --rollback --yes' | Set-Content "
        f"-LiteralPath {_ps(report)} -Encoding UTF8 }}",
    ]
    return "\r\n".join(lines) + "\r\n"


def real_deps(a, cfg, home: Path) -> Deps:
    from . import backup, cli, runmarks, update_progress as up
    from .updates import (UpdateChecker, is_installed_build, launch_installer, record_was_running, rollback,
                          set_channel)
    box: dict = {}
    proc: dict = {}

    def checker(ch: str) -> UpdateChecker:
        return box.setdefault(ch, UpdateChecker(home, cfg=cfg, channel=ch))

    def check(ch: str) -> dict:
        s = checker(ch).check(force=True)
        return {**s, "current": checker(ch).current}

    def running() -> bool:
        return cli._ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status") is not None

    def launch(prep: dict, was: dict, restart: bool) -> int:
        up.write(home, "stopping")

        def popen(args, **kw):                            # keep the handle: the checks wait for this process to exit
            proc["p"] = subprocess.Popen(args, **kw)
            return proc["p"]
        launch_installer(prep["installer"], verified=prep, silent=True, restart=restart, was_running=was, popen=popen)
        return proc["p"].pid

    def wait_installer() -> bool:
        p = proc.get("p")
        if p is None:
            return True                                   # phase two in a new process: the waiter already waited
        try:
            p.wait(timeout=SETUP_WAIT_S)
            return True
        except subprocess.TimeoutExpired:
            return False

    def spawn(state: Path) -> None:
        upd = Path(home) / "updates"
        inst = json.loads(state.read_text(encoding="utf-8")).get("installer", "")
        script = upd / "install-latest-finish.ps1"
        script.write_text(finisher_script(state, sys.executable, upd / REPORT_FILE, upd / PID_FILE, inst),
                          encoding="utf-8-sig")          # Windows PowerShell 5.1 reads a BOM-less file as ANSI
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-File", str(script)], creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=str(upd))                   # outside {app}: setup may replace that folder

    def publish_pid(pid: int | None) -> None:
        f = Path(home) / "updates" / PID_FILE
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text("cancel" if pid is None else str(int(pid)), encoding="ascii")
        os.replace(tmp, f)
        if pid is not None:
            proc.pop("p", None)                           # the waiter owns the wait from here on

    def start_core() -> None:
        cli.cmd_start(argparse.Namespace(show=False), cfg, home)

    return Deps(home=home, channel=UpdateChecker(home, cfg=cfg).channel,
                set_channel=lambda ch: set_channel(home, cfg, ch),
                backup_now=lambda: backup.create(home, "manual"),
                check=check, prepare=lambda ch: checker(ch).prepare_install(approved=True),
                core_running=running,
                detect_running=lambda up_: runmarks.detect_running(home, core_up=up_),
                record_was_running=lambda w: record_was_running(home, w),
                stop_core=lambda: cli.cmd_stop(a, cfg, home), start_core=start_core, launch=launch,
                wait_installer=wait_installer,
                wait=lambda core_ran: up.watch(home, lambda _l: None, timeout_s=420.0),
                run_cli=_cli_subprocess,
                rollback=lambda: rollback(home, approved=True, restore_data=True, silent=True, port=cfg.link.port),
                spawn_finisher=spawn if is_installed_build() else None, publish_pid=publish_pid)


def wait_report(home: Path, since: float, *, timeout_s: float = 1500.0, sleep=time.sleep, clock=time.time) -> str | None:
    """The first process is normally stopped by setup; when it is not (dev checkout), it prints the report on arrival."""
    p = Path(home) / "updates" / REPORT_FILE
    end = clock() + timeout_s
    while clock() < end:
        try:
            if p.stat().st_mtime >= since:
                return p.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        sleep(3)
    return None


def cmd_install_latest(a, cfg, home) -> int:
    rp = Path(home) / "updates" / REPORT_FILE
    if getattr(a, "report", False):
        try:
            print(rp.read_text(encoding="utf-8").strip())
            return 0
        except OSError:
            print("No install-latest report yet.")
            return 1
    deps = real_deps(a, cfg, home)
    if getattr(a, "finish", None):                        # phase two, started by the waiter from the new program
        try:
            state = json.loads(Path(a.finish).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("install-latest --finish: state file missing or unreadable")
            return 2
        rep = finish(deps, state, installer_running=bool(getattr(a, "installer_running", False)))
        print(rep.render())
        return 0 if rep.ok else 1
    t0 = time.time()
    rep = begin(deps, channel=getattr(a, "channel", None), dry_run=bool(getattr(a, "dry_run", False)))
    print(rep.render())
    if rep.pending:
        print(f"Setup is running silently. The checks run after it ends; the report is written to {rp}\n"
              "(read it with: jarvis install-latest --report). If this window closes, that is normal.")
        text = wait_report(home, t0)
        if text:
            print(text)
            return 0 if text.startswith("PASS") else 1
        return 0
    return 0 if rep.ok else 1


def add_cli(sub) -> None:
    p = sub.add_parser("install-latest", help="back up, install the newest signed build of a channel silently, run "
                                              "doctor + selftest + status, roll back if a check fails")
    p.add_argument("--channel", choices=list(CHANNELS), help="beta (every green main build) or stable; default: "
                   "the channel in force (a different one is switched like `jarvis update --channel`)")
    p.add_argument("--dry-run", action="store_true", help="only check and print the plan; nothing is installed, "
                   "stopped or switched")
    p.add_argument("--report", action="store_true", help="print the report of the last run")
    p.add_argument("--finish", metavar="STATE", help=argparse.SUPPRESS)           # internal: phase two
    p.add_argument("--installer-running", action="store_true", help=argparse.SUPPRESS)


COMMANDS = {"install-latest": cmd_install_latest}
