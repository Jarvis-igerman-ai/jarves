"""Flake handling for CI: retry ONLY the failed test files, and only for the known Windows socket/load flakes.

    python core/tools/ci_flake.py failed-files --junit pytest-junit.xml     # prints the files to retry (exit 0), or
                                                                            # exits 3 when nothing is retry-worthy
    python core/tools/ci_flake.py report --first first.xml --second retry.xml --out flake-report.json

A failure is retry-worthy only when its message matches a KNOWN transient pattern (WinError 10048/10054/10061, address in
use, connection reset/refused, timed out waiting on a loaded runner). A real assertion failure is never retried; a file
that fails again after the retry fails the job. Every retry that turned green is recorded in flake-report.json (uploaded
as an artifact) so flakes stay visible and get fixed instead of being hidden. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

KNOWN = re.compile(
    r"WinError 10048|WinError 10054|WinError 10061|WinError 10053|Errno 98|Address already in use|address in use|"
    r"ConnectionResetError|ConnectionRefusedError|ConnectionAbortedError|condition not reached|"
    r"Timeout(Error)?: |timed out|TimeoutExpired|the semaphore timeout period has expired", re.I)
MAX_FILES = 6          # more failing files than this is a real regression, not a flake


def _file_of(case: ET.Element) -> str | None:
    for part in (case.get("file") or case.get("classname") or "").replace("\\", "/").replace("/", ".").split("."):
        if part.startswith("test_"):
            return part + ".py"
    return None


def failures(junit: Path) -> list[dict]:
    out = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        if bad is not None:
            text = (bad.get("message") or "") + "\n" + (bad.text or "")
            out.append({"file": _file_of(case), "test": f"{case.get('classname')}::{case.get('name')}",
                        "known_flake": bool(KNOWN.search(text))})
    return out


def retry_files(junit: Path) -> list[str] | None:
    """Files to re-run, or None when the failure is not retry-worthy (a real failure, or too many files)."""
    fails = failures(junit)
    if not fails or any(not f["known_flake"] or not f["file"] for f in fails):
        return None
    files = sorted({f["file"] for f in fails})
    return files if len(files) <= MAX_FILES else None


def report(first: Path, second: Path | None) -> dict:
    first_fail = failures(first)
    second_fail = {f["test"] for f in failures(second)} if second and second.is_file() else set()
    flaky = [f for f in first_fail if f["test"] not in second_fail]
    return {"flaky_passed_on_retry": flaky, "failed_again": sorted(second_fail), "first_failures": len(first_fail)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ci_flake.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("failed-files")
    f.add_argument("--junit", type=Path, required=True)
    r = sub.add_parser("report")
    r.add_argument("--first", type=Path, required=True)
    r.add_argument("--second", type=Path)
    r.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "failed-files":
        files = retry_files(a.junit) if a.junit.is_file() else None
        if not files:
            print("no retry-worthy failures (real failures are never retried)", file=sys.stderr)
            return 3
        print(" ".join(f"tests/{x}" for x in files))
        return 0
    doc = report(a.first, a.second)
    a.out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"flake report: {len(doc['flaky_passed_on_retry'])} test(s) passed only on retry, {len(doc['failed_again'])} failed again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
