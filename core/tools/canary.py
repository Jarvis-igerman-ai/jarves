"""Nightly synthetic user-journey canaries (SW-V4 package O, Self-Diagnostics 2.0).

    python tools/canary.py [--profile ci|owner] [--only voice_stack,scheduler] [--report out/canary.json]

Each journey runs in its own throw-away home (never the real memory): see jarves/diag/canary.py. Exit 0 when no
journey FAILED (BLOCKED rows carry their reason and do not fail), 1 otherwise. The Core's scheduler runs the same
journeys at 03:30 on the owner's PC (profile "owner") and stores the result for the Health page.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", choices=["ci", "owner"], default="ci")
    ap.add_argument("--only", default="", help="comma-separated journey names")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--browser-channel", default=None, help="msedge on Windows; default bundled Chromium")
    a = ap.parse_args(argv)
    from jarves.config import state_dir
    from jarves.diag import canary
    only = [x for x in a.only.split(",") if x] or None
    real = state_dir() if a.profile == "owner" else None
    rep = asyncio.run(canary.run_all(a.profile, real_home=real, only=only, browser_channel=a.browser_channel))
    for r in rep["results"]:
        print(f"{r['status']:<8} {r['name']:<16} {r['ms']:>8.0f} ms  {r['detail']}")
    print(f"canary: {rep['passed']} PASS, {rep['failed']} FAIL, {rep['blocked']} BLOCKED")
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return 1 if rep["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
