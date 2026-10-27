"""`jarvis install-latest [--channel beta|stable] [--dry-run]`: the owner's whole "install the newest build and test it"
routine in one command (docs/plans/LEAN_PROCESS_V2_2026-10-27.md, item 5).

Order: backup now -> find the newest signed installer of the channel (UpdateChecker.check) -> verify signature, SHA256 and
size and take the pre-update backup (UpdateChecker.prepare_install, the same code as `jarvis update install`) -> stop the
Core if it ran -> silent setup (/VERYSILENT /SUPPRESSMSGBOXES /NORESTART, plus /RESTARTJARVES=1 when the Core ran;
launch_installer) -> doctor + selftest + status + version, each run by the NEW program in a fresh process -> ONE short
report. Any failed check starts the same rollback as `jarvis update --rollback` (previous version + data from before).

Everything that touches the machine is injected through `Deps`, so the tests use fakes (no network, no installer, frozen
clock). Nothing here downloads or verifies by itself: that stays in jarves.updates. A dry run changes nothing except the
update-check cache file (the check is read-only on the network).
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import logs

CHANNELS = ("stable", "beta")


@dataclass
class Deps:
    """Everything the flow does to the outside world. Real wiring: `real_deps`; tests pass fakes."""
    channel: str                                              # the channel in force now
    set_channel: Callable[[str], None]
    backup_now: Callable[[], Path]
    check: Callable[[str], dict]                              # channel -> UpdateChecker.status()-shaped dict
    prepare: Callable[[str], dict]                            # channel -> {installer, backup, version, signed, sha256}
    core_running: Callable[[], bool]
    detect_running: Callable[[bool], dict]
    record_was_running: Callable[[dict], None]
    stop_core: Callable[[], None]
    launch: Callable[[dict, dict, bool], None]                # (prepared, was_running, restart)
    wait: Callable[[bool], str]                               # (core_ran) -> 'done' | 'failed' | 'timeout'
    run_cli: Callable[[list[str]], tuple[int, str]]           # the NEW program, fresh process: (exit code, output)
    rollback: Callable[[], dict]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    say: Callable[[str], None] = print


@dataclass
class Report:
    ok: bool = False
    changed: bool = False                  # an installer was started
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

    def render(self) -> str:
        """ONE short report: version, revert id, how to roll back, pass/fail per check."""
        head = ("DRY RUN" if self.dry_run else "PASS" if self.ok else "FAIL") + f"  install-latest  {self.at}"
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


def run(deps: Deps, channel: str | None = None, dry_run: bool = False) -> Report:
    from .updates import UpdateError
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
    shown = {rep.version, str(rel.get("display") or rep.version)}
    if dry_run:
        for step in ("backup now", f"download {rep.version} and verify signature, SHA256 and size",
                     "stop the Core if it runs; silent setup (/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
                     " /RESTARTJARVES=1 when the Core ran)", "doctor + selftest + status + version check",
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
    except UpdateError as e:
        rep.add("verify installer (signature, SHA256)", False, f"{e.code}: {e.message}")
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
    if core_up:
        deps.stop_core()
    try:
        deps.launch(prep, was, core_up)                   # restart flag only when the Core ran
    except UpdateError as e:
        rep.add("silent install", False, f"{e.code}: {e.message}")
        rep.stopped_at = "install"
        return rep
    rep.changed = True
    end = deps.wait(core_up)
    install_ok = rep.add("silent install", end == "done" or (end == "timeout" and not core_up),
                         {"done": "new version started and healthy", "failed": "setup or health check failed",
                          "timeout": "no sign of the new Core within the time limit" if core_up
                          else "setup finished (Core was not running, so it was not started)"}[end])
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
    return rep


# ---------------------------------------------------------------------- real wiring
def _cli_subprocess(argv: list[str]) -> tuple[int, str]:
    """The installed program, started fresh (new code, not this old process). Silent: no window, no console flash."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run([sys.executable, "-m", "jarves", *argv], capture_output=True, text=True, timeout=600,
                       creationflags=flags, errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def real_deps(a, cfg, home: Path) -> Deps:
    from . import backup, cli, runmarks, update_progress as up
    from .updates import (UpdateChecker, launch_installer, record_was_running, rollback,
                          set_channel)
    box: dict = {}

    def checker(ch: str) -> UpdateChecker:
        return box.setdefault(ch, UpdateChecker(home, cfg=cfg, channel=ch))

    def check(ch: str) -> dict:
        s = checker(ch).check(force=True)
        return {**s, "current": checker(ch).current}

    def stop_core() -> None:
        cli.cmd_stop(a, cfg, home)

    def running() -> bool:
        return cli._ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status") is not None

    def launch(prep: dict, was: dict, restart: bool) -> None:
        up.write(home, "stopping")
        launch_installer(prep["installer"], verified=prep, silent=True, restart=restart, was_running=was)

    def do_rollback() -> dict:
        return rollback(home, approved=True, restore_data=True, silent=True, port=cfg.link.port)

    return Deps(channel=UpdateChecker(home, cfg=cfg).channel,
                set_channel=lambda ch: set_channel(home, cfg, ch),
                backup_now=lambda: backup.create(home, "manual"),
                check=check, prepare=lambda ch: checker(ch).prepare_install(approved=True),
                core_running=running,
                detect_running=lambda up_: runmarks.detect_running(home, core_up=up_),
                record_was_running=lambda w: record_was_running(home, w),
                stop_core=stop_core, launch=launch,
                wait=lambda core_ran: up.watch(home, lambda _l: None, timeout_s=420.0 if core_ran else 90.0),
                run_cli=_cli_subprocess, rollback=do_rollback)


def cmd_install_latest(a, cfg, home) -> int:
    t0 = time.monotonic()
    rep = run(real_deps(a, cfg, home), channel=getattr(a, "channel", None), dry_run=bool(getattr(a, "dry_run", False)))
    print(rep.render())
    logs.info("updates", "INSTALL_LATEST_DONE", "install-latest report", seconds=round(time.monotonic() - t0, 1))
    return 0 if rep.ok else 1


def add_cli(sub) -> None:
    p = sub.add_parser("install-latest", help="back up, install the newest signed build of a channel silently, run "
                                              "doctor + selftest + status, roll back if a check fails")
    p.add_argument("--channel", choices=list(CHANNELS), help="beta (every green main build) or stable; default: "
                   "the channel in force (a different one is switched like `jarvis update --channel`)")
    p.add_argument("--dry-run", action="store_true", help="only check and print the plan; nothing is installed, "
                   "stopped or switched")


COMMANDS = {"install-latest": cmd_install_latest}
