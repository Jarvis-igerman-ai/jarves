"""Synthetic journey canaries: the nightly hook for SW-V4 package O (Self-Diagnostics 2.0).

    python tools/synthetic_canaries.py [--out canaries.json]

A canary is a function `() -> None` that exercises one user journey on a CI test profile with fakes and raises on a
failure. Package O registers its journeys (jarves/diag/canary.py, profile "ci") in CANARIES (name -> callable).
A journey that cannot run on this machine raises an exception with `blocked = True` (e.g. no headless browser):
it is reported as BLOCKED with its reason, never as a pass and never as a failure. Exit 0 when no canary failed,
1 when any failed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

CANARIES: dict[str, Callable[[], None]] = {}    # package O: registered below


class CanaryBlocked(Exception):
    blocked = True


def _journey(name: str) -> Callable[[], None]:
    def run_one() -> None:
        from jarves.diag import canary
        rep = asyncio.run(canary.run_all("ci", only=[name]))
        r = rep["results"][0]
        if r["status"] == canary.BLOCKED:
            raise CanaryBlocked(r["detail"])
        if r["status"] != canary.PASS:
            raise AssertionError(r["detail"])
    run_one.__name__ = f"canary_{name}"
    return run_one


def register_package_o() -> None:
    from jarves.diag.canary import JOURNEYS
    for name in JOURNEYS:
        CANARIES.setdefault(name, _journey(name))


def run(canaries: dict[str, Callable[[], None]]) -> dict:
    results = []
    for name, fn in canaries.items():
        t = time.perf_counter()
        try:
            fn()
            results.append({"name": name, "ok": True, "ms": round((time.perf_counter() - t) * 1000, 1)})
        except Exception as e:                        # a canary reports, it never takes the runner down
            row = {"name": name, "ok": False, "error": type(e).__name__, "ms": round((time.perf_counter() - t) * 1000, 1)}
            if getattr(e, "blocked", False):
                row.update(ok=None, blocked=True, reason=str(e)[:200])
            else:
                row["detail"] = str(e)[:200]
            results.append(row)
    return {"registered": len(canaries), "passed": sum(r["ok"] is True for r in results),
            "failed": sum(r["ok"] is False for r in results), "blocked": sum(bool(r.get("blocked")) for r in results),
            "results": results, "note": "" if canaries else "no canaries registered yet (package O)"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    register_package_o()
    report = run(CANARIES)
    print(json.dumps(report))
    if a.out:
        a.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
