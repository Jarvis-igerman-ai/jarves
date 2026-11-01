"""Factory chaos simulation: run every test_sim_* scenario and print PASS/FAIL per scenario.

    python core/tools/factory_sim.py [-k NAME] [-v]

Everything is simulated (temporary workflow home, fake clock, fake Core bridge): it never touches the real
workflow ledger, starts no process, uses no network and no real Claude. Scenarios that need the manager wrapper
C:\\work\\claude-j-run.py are reported NOT RUN when that file is missing. Exit code 0 = no scenario failed.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "core" / "tests" / "test_workflow_factory_sim.py"
WRAPPER_TESTS = {"test_sim_stop_hook_idle_nudge_rules", "test_sim_supervisor_dead_watchdog", "test_sim_end_to_end_day"}
LABEL = {"PASSED": "PASS", "FAILED": "FAIL", "ERROR": "FAIL", "SKIPPED": "NOT RUN (wrapper missing)", "XFAIL": "XFAIL (known bug)",
         "XPASS": "FAIL (unexpected pass)"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-k", default="", help="only scenarios whose name contains this text")
    parser.add_argument("-v", action="store_true", help="show the full pytest output (incl. the day timeline)")
    args = parser.parse_args(argv)
    cmd = [sys.executable, "-m", "pytest", str(TEST), "-rA", "-s", "-p", "no:cacheprovider", "--tb=short",
           "-o", f"pythonpath={(ROOT / 'core' / 'src').as_posix()}"]
    if args.k:
        cmd += ["-k", args.k]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
                          env={**os.environ, "PYTHONPATH": str(ROOT / "core" / "src")})
    out = proc.stdout + proc.stderr
    if args.v:
        print(out)
    failed = 0
    for line in out.splitlines():
        match = re.search(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) .*::(test_sim_\w+)", line)
        if match:
            result, name = match.groups()
            if result == "PASSED" and "WRAPPER-MISSING" in out and name in WRAPPER_TESTS:
                result = "SKIPPED"
            failed += result in {"FAILED", "ERROR", "XPASS"}
            print(f"{LABEL[result]:<22} {name[len('test_sim_'):]}")
    if not args.v and "DAY SIMULATION TIMELINE" in out:
        block = out[out.index("DAY SIMULATION TIMELINE"):].splitlines()
        print("\n" + "\n".join(line for line in block[:11] if not re.match(r"\.|PASSED|FAILED|=", line)))
    if not re.search(r"::test_sim_", out):
        print("no scenario ran:\n" + out[-800:])
        return 1
    print("\nALL SCENARIOS OK" if not failed else f"\n{failed} SCENARIO(S) FAILED (run with -v)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
