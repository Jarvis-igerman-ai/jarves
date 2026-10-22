"""CI pytest wrapper: run the suite; if test FILES failed, rerun ONLY those files once and record flake-report.json.

A file that fails twice stays failed (exit 1). A file that passes on retry is a flake: it is written to
flake-report.json (test id, first failure message, passed_on_retry) and the run is green but reported.
Usage: python tools/pytest_ci.py [pytest args...]   (junit goes to pytest-junit.xml / pytest-junit-retry.xml)"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPORT = Path("flake-report.json")


def failures(junit: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not junit.exists():
        return out
    for tc in ET.parse(junit).getroot().iter("testcase"):
        bad = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
        if bad is not None:
            tid = f"{tc.get('classname', '').replace('.', '/')}::{tc.get('name')}"
            out[tid] = (bad.get("message") or "")[:300]
    return out


def file_of(tid: str) -> str:
    return tid.split("::")[0] + ".py"


def main(argv: list[str]) -> int:
    base = [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider"]
    if "-m" not in argv:
        base += ["-m", "not quarantine"]            # quarantined tests run in their own non-blocking job
    r1 = subprocess.run(base + ["--junitxml=pytest-junit.xml", *argv])
    if r1.returncode == 0:
        REPORT.write_text("[]", encoding="utf-8")
        return 0
    first = failures(Path("pytest-junit.xml"))
    files = sorted({file_of(t) for t in first})
    if not files:                                   # collection/internal error: nothing to retry
        return r1.returncode
    print(f"::warning::retrying once, only these files: {files}")
    r2 = subprocess.run(base + ["--junitxml=pytest-junit-retry.xml", *files])
    second = failures(Path("pytest-junit-retry.xml"))
    report = [{"test": t, "first_failure": m, "passed_on_retry": t not in second} for t, m in first.items()]
    REPORT.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return r2.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
