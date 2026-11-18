"""Daily installed-beta run that leaves records (JAR-456). Standard library only.

    python core/tools/beta_daily.py run   --home DIR [--jarvis PATH]
    python core/tools/beta_daily.py check --home DIR   # exit 0 = healthy

run: never installs (that is the owner's `jarvis update auto`). It asks `jarvis update check --json`
  and always writes <home>/beta_records/install-<ts>.json (ok = newest, no error). Only when newest:
  S01-S05 (PASS = exit 0) into scenarios-<ts>.json, then one demo record plus an SVG transcript per
  ticket. JAR-214/JAR-320 run their regression test from this checkout (no pytest = SKIPPED);
  JAR-321 reuses S04. Tails are redacted, stdin is closed, 300 s per step, 1800 s per run.
check: newest_beta_installed_age_h (future records ignored), scenario_pass_pct (last 26 h) and
  passing demos_24h_with_image. ok = install under 26 h, pass % known, a passing installed demo.
Each record is its own file (unique temp file + os.replace). Caller: autopilot beta-verify task.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

CORE = Path(__file__).resolve().parents[1]
INSTALL = ("update", "check", "--json")  # read-only; installing is `jarvis update auto` (owner switch), never this tool
# Unattended daily, so device- and popup-free only: no `doctor` (it records the microphone and beeps), selftest
# without --deep/--audio (audio_roundtrip runs only with --audio), cached update status, local Core status, the
# throw-away-database limits proof and the read-only feature list.
SCENARIOS = (("S01", ("update", "status")), ("S02", ("selftest",)), ("S03", ("status",)),
             ("S04", ("budget", "test-limits")), ("S05", ("features", "list")))


def core_python() -> str:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        if (CORE / rel).is_file():
            return str(CORE / rel)
    return sys.executable


PY_TEST = (core_python(), "-m", "pytest", "-q")
DEMOS = (("JAR-214", "source-tests", PY_TEST + ("tests/test_workflow_hooks.py::ConcurrentSubagentStartTests",)),
         ("JAR-320", "source-tests", PY_TEST + ("tests/test_workflow_http.py::WorkflowHTTPTests::"
                                                "test_state_changing_routes_relock_after_expiry_and_ignore_other_sessions",)),
         ("JAR-321", "installed", "S04"))  # the S04 `budget test-limits` result, not a second run
MAX_INSTALL_AGE_H = 26
STEP_TIMEOUT_S = 300
RUN_BUDGET_S = 1800  # whole run; steps after the budget are recorded SKIPPED, not run
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def real_runner(jarvis: str = "jarvis", timeout: int = STEP_TIMEOUT_S):
    exe = shutil.which(jarvis) or jarvis  # Windows jarvis.cmd/.bat shims are not found by shell=False otherwise

    def run(argv):
        argv = [exe if a == "jarvis" else a for a in argv]
        try:  # stdin closed: a prompting step fails at once instead of hanging until the timeout
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=str(CORE), stdin=subprocess.DEVNULL)
        except FileNotFoundError as exc:
            return 127, f"not found: {exc}"
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout} s"
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    return run


def _redact(text: str) -> str:
    """The product's own log redaction (secrets, user names in paths, e-mails); fail closed without it."""
    try:
        if str(CORE / "src") not in sys.path:
            sys.path.append(str(CORE / "src"))
        from jarves.logs import mask_emails, redact
    except ImportError:
        return "<output withheld: redaction unavailable>"
    return mask_emails(redact(text))


def _tail(text: str, lines: int = 20) -> list[str]:
    return [_CTRL.sub("", _ANSI.sub("", s))[:200] for s in _redact(str(text)).splitlines()[-lines:]]


def _write(folder: Path, name: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, folder / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def render_svg(title: str, lines: list[str]) -> str:
    rows = [_CTRL.sub("", _ANSI.sub("", str(s)))[:120] for s in [title, ""] + list(lines)[:40]]
    text = "".join(f'<text x="10" y="{20 + 16 * i}">{escape(r)}</text>' for i, r in enumerate(rows))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="980" height="{30 + 16 * len(rows)}" '
            f'font-family="monospace" font-size="13"><rect width="100%" height="100%" fill="#111"/>'
            f'<g fill="#eee">{text}</g></svg>\n')


def run_daily(home, run, now: float | None = None, clock=time.monotonic, budget_s: float = RUN_BUDGET_S) -> dict:
    now = time.time() if now is None else now
    start = clock()
    folder = Path(home) / "beta_records"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(now)}-{uuid.uuid4().hex[:8]}"  # unique: two runs in one second never overwrite each other
    rc, out = run(["jarvis", *INSTALL])
    newest = rc == 0 and _newest(out)
    newer = rc == 0 and _newest(out, want_available=True)  # a newer beta shipped: supersedes older ok records
    install = {"at": now, "ok": newest, "newer_available": newer, "rc": rc, "tail": _tail(out), "installer": "jarvis update auto (owner switch)"}
    _write(folder, f"install-{stamp}.json", json.dumps(install))
    if not newest:
        return {"install": install}
    scenarios, outputs = [], {}
    for sid, args in SCENARIOS:
        if clock() - start > budget_s:
            scenarios.append({"id": sid, "cmd": " ".join(args), "result": "SKIPPED", "rc": None,
                              "tail": [f"run budget of {budget_s:g} s used up"]})
            continue
        code, text = outputs[sid] = run(["jarvis", *args])
        scenarios.append({"id": sid, "cmd": " ".join(args), "result": "PASS" if code == 0 else "FAIL",
                          "rc": code, "tail": _tail(text)})
    _write(folder, f"scenarios-{stamp}.json", json.dumps({"at": now, "scenarios": scenarios}))
    for ticket, target, argv in DEMOS:
        if isinstance(argv, str):  # reuses a scenario result; a SKIPPED scenario leaves no demo record
            if argv not in outputs:
                continue
            code, text = outputs[argv]
        elif clock() - start > budget_s:
            break  # no demo record at all: an unrun demo must not look like a result
        else:
            code, text = run(list(argv))
        missing = target == "source-tests" and "No module named pytest" in text
        result = "PASS" if code == 0 else "SKIPPED" if missing else "FAIL"
        image = f"demo-{ticket}-{stamp}.svg"
        _write(folder, image, render_svg(f"{ticket} demo ({target}): rc={code}", _tail(text, 40)))
        _write(folder, f"demo-{ticket}-{stamp}.json", json.dumps(
            {"at": now, "ticket": ticket, "target": target, "result": result, "image": image}))
    return {"install": install, "scenarios": scenarios}


def _newest(out: str, want_available: bool = False) -> bool:
    """The last line of `update check --json` has no error and `available` == want_available."""
    try:
        data = json.loads((out.strip().splitlines() or [""])[-1])
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("error") is None and data.get("available") is want_available


def _load(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    at = data.get("at") if isinstance(data, dict) else None
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
        return None
    return data


def check(home, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    folder = Path(home) / "beta_records"
    last_ok, last_newer, scen, demos, installed, not_passing, bad = None, None, None, 0, 0, 0, 0
    fresh = lambda at, hours: 0 <= now - at <= hours * 3600  # noqa: E731
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = _load(path)
        rows = data.get("scenarios") if data else None
        if data is None or (path.name.startswith("scenarios-") and not (isinstance(rows, list) and rows)):
            bad += 1
        elif path.name.startswith("install-"):
            if data["at"] > now:
                continue  # clock-skewed record: it must not hide a stale install
            if data.get("ok") is True:
                last_ok = max(last_ok or data["at"], data["at"])
            elif data.get("newer_available") is True:
                last_newer = max(last_newer or data["at"], data["at"])
        elif path.name.startswith("scenarios-"):
            if fresh(data["at"], MAX_INSTALL_AGE_H) and (scen is None or data["at"] >= scen["at"]):
                scen = data
        elif path.name.startswith("demo-") and fresh(data["at"], 24) and data.get("image") \
                and (folder / Path(str(data["image"])).name).is_file():
            if data.get("result") == "PASS":
                demos += 1
                installed += data.get("target") == "installed"
            else:
                not_passing += 1
    pct = None
    if scen:
        rows = scen["scenarios"]
        pct = round(100.0 * sum(isinstance(r, dict) and r.get("result") == "PASS" for r in rows) / len(rows), 1)
    if last_ok is not None and last_newer is not None and last_newer > last_ok:
        last_ok = None  # the good install was followed by a check that found a newer beta
    hours = None if last_ok is None else (now - last_ok) / 3600
    ok = (hours is not None and 0 <= hours < MAX_INSTALL_AGE_H and pct is not None  # unrounded age
          and installed > 0)  # source-test demos alone never make the installed beta look healthy
    return {"ok": ok, "newest_beta_installed_age_h": None if hours is None else round(hours, 1),
            "scenario_pass_pct": pct, "demos_24h_with_image": demos, "demos_24h_installed_pass": installed,
            "demos_24h_not_passing": not_passing, "unreadable": bad}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["run", "check"])
    ap.add_argument("--home", required=True, help="folder that holds beta_records/")
    ap.add_argument("--jarvis", default="jarvis", help="installed jarvis command")
    args = ap.parse_args(argv)
    if args.action == "run":
        run_daily(args.home, real_runner(args.jarvis))
    report = check(args.home)
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
