"""Daily installed-beta run that leaves records (JAR-456). Standard library only.

    python core/tools/beta_daily.py run   --home DIR [--jarvis PATH]   # record install, S01-S05, demos
    python core/tools/beta_daily.py check --home DIR                   # exit 0 = fresh install, known pass %, demos

This tool never installs and never answers an install question. The install itself is the owner-approved path:
`jarvis update auto on` (signed release, quiet moment, notice and backup first). `run` asks the read-only
`jarvis update check` whether the installed build is the newest and writes <home>/beta_records/install-<ts>.json
(ok = "Up to date", else the waiting version) every time. Only when it is the newest it runs S01-S05 (PASS = exit
code 0) into scenarios-<ts>.json and writes one demo per ticket (JAR-214, JAR-320, JAR-321): demo-<ticket>-<ts>.json
plus an SVG image of the command transcript. JAR-214 and JAR-320 have no installed-product command, so their demo
runs the regression test from this checkout with the core venv Python (`target: source-tests`); no pytest = SKIPPED.
`check` prints last_install_age_h (newest-build records only), scenario_pass_pct (latest run), demos_24h_with_image
(passing demos with their image), demos_24h_not_passing and unreadable (malformed record files, skipped).
ok = install record under 26 h, pass % known and at least one passing demo with image.
Every record is its own file written via a unique temp file and os.replace, so runs never race on a shared file.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

CORE = Path(__file__).resolve().parents[1]
INSTALL = ("update", "check")  # read-only; installing is `jarvis update auto` (owner switch), never this tool
SCENARIOS = (("S01", ("doctor",)), ("S02", ("selftest",)), ("S03", ("status",)),
             ("S04", ("budget", "test-limits")), ("S05", ("features", "diff")))


def core_python() -> str:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        if (CORE / rel).is_file():
            return str(CORE / rel)
    return sys.executable


PY_TEST = (core_python(), "-m", "pytest", "-q")
DEMOS = (("JAR-214", "source-tests", PY_TEST + ("tests/test_workflow_hooks.py::ConcurrentSubagentStartTests",)),
         ("JAR-320", "source-tests", PY_TEST + ("tests/test_workflow_http.py::WorkflowHTTPTests::"
                                                "test_state_changing_routes_relock_after_expiry_and_ignore_other_sessions",)),
         ("JAR-321", "installed", ("jarvis", "budget", "test-limits")))
MAX_INSTALL_AGE_H = 26
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def real_runner(jarvis: str = "jarvis", timeout: int = 1800):
    def run(argv):
        argv = [jarvis if a == "jarvis" else a for a in argv]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=str(CORE))
        except FileNotFoundError as exc:
            return 127, f"not found: {exc}"
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout} s"
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    return run


def _tail(text: str, lines: int = 20) -> list[str]:
    return [_CTRL.sub("", _ANSI.sub("", s))[:200] for s in str(text).splitlines()[-lines:]]


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


def run_daily(home, run, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    folder = Path(home) / "beta_records"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(now)}-{uuid.uuid4().hex[:8]}"  # unique: two runs in one second never overwrite each other
    rc, out = run(["jarvis", *INSTALL])
    newest = rc == 0 and "Up to date" in out
    install = {"at": now, "ok": newest, "rc": rc, "tail": _tail(out), "installer": "jarvis update auto (owner switch)"}
    _write(folder, f"install-{stamp}.json", json.dumps(install))
    if not newest:
        return {"install": install}
    scenarios = []
    for sid, args in SCENARIOS:
        code, text = run(["jarvis", *args])
        scenarios.append({"id": sid, "cmd": " ".join(args), "result": "PASS" if code == 0 else "FAIL",
                          "rc": code, "tail": _tail(text)})
    _write(folder, f"scenarios-{stamp}.json", json.dumps({"at": now, "scenarios": scenarios}))
    for ticket, target, argv in DEMOS:
        code, text = run(list(argv))
        missing = target == "source-tests" and "No module named pytest" in text
        result = "PASS" if code == 0 else "SKIPPED" if missing else "FAIL"
        image = f"demo-{ticket}-{stamp}.svg"
        _write(folder, image, render_svg(f"{ticket} demo ({target}): rc={code}", _tail(text, 40)))
        _write(folder, f"demo-{ticket}-{stamp}.json", json.dumps(
            {"at": now, "ticket": ticket, "target": target, "result": result, "image": image}))
    return {"install": install, "scenarios": scenarios}


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
    last_ok, scen, demos, not_passing, bad = None, None, 0, 0, 0
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = _load(path)
        rows = data.get("scenarios") if data else None
        if data is None or (path.name.startswith("scenarios-") and not (isinstance(rows, list) and rows)):
            bad += 1
        elif path.name.startswith("install-") and data.get("ok") is True:
            last_ok = max(last_ok or data["at"], data["at"])
        elif path.name.startswith("scenarios-") and (scen is None or data["at"] >= scen["at"]):
            scen = data
        elif path.name.startswith("demo-") and 0 <= now - data["at"] <= 86400 and data.get("image") \
                and (folder / Path(str(data["image"])).name).is_file():
            if data.get("result") == "PASS":
                demos += 1
            else:
                not_passing += 1
    pct = None
    if scen:
        rows = scen["scenarios"]
        pct = round(100.0 * sum(isinstance(r, dict) and r.get("result") == "PASS" for r in rows) / len(rows), 1)
    hours = None if last_ok is None else (now - last_ok) / 3600
    ok = hours is not None and 0 <= hours < MAX_INSTALL_AGE_H and pct is not None and demos > 0  # unrounded age
    return {"ok": ok, "last_install_age_h": None if hours is None else round(hours, 1), "scenario_pass_pct": pct,
            "demos_24h_with_image": demos, "demos_24h_not_passing": not_passing, "unreadable": bad}


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
