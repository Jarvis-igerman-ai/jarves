"""Synthetic journey canaries: the nightly hook for SW-V4 package O (Self-Diagnostics 2.0).

    python tools/synthetic_canaries.py [--out canaries.json]

A canary is a function `() -> None` that exercises one user journey on a CI test profile with fakes and raises on a
failure. Package O registers its canaries in CANARIES (name -> callable); this package only provides the runner, the
JSON report and the nightly wiring. Exit 0 when every registered canary passes (also when none is registered yet: the
report says so explicitly), 1 when any fails.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

CANARIES: dict[str, Callable[[], None]] = {}    # package O: register journeys here


def run(canaries: dict[str, Callable[[], None]]) -> dict:
    results = []
    for name, fn in canaries.items():
        t = time.perf_counter()
        try:
            fn()
            results.append({"name": name, "ok": True, "ms": round((time.perf_counter() - t) * 1000, 1)})
        except Exception as e:                        # a canary reports, it never takes the runner down
            results.append({"name": name, "ok": False, "error": type(e).__name__,
                            "ms": round((time.perf_counter() - t) * 1000, 1)})
    return {"registered": len(canaries), "passed": sum(r["ok"] for r in results),
            "failed": sum(not r["ok"] for r in results), "results": results,
            "note": "" if canaries else "no canaries registered yet (package O)"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    report = run(CANARIES)
    print(json.dumps(report))
    if a.out:
        a.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
