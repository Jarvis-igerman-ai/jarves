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


def default_home() -> Path:
    return Path(os.environ.get("JARVES_WORKFLOW_HOME") or (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "jarves" / "workflow"))


def schedule_command() -> str:
    """Daily 03:00 in the PC's local (Israel) time; the owner or manager runs it once."""
    return ('schtasks /Create /SC DAILY /ST 03:00 /TN "JARVES factory sim" /F /TR '
            f'"\\"{sys.executable}\\" \\"{Path(__file__).resolve()}\\" --nightly"')


def record(home: str, results: list, failed: int, tail: str) -> None:
    """Write <home>/factory_sim_last.json (read by the workflow dashboard) and, on failure, file one issue."""
    import json
    import time
    folder = Path(home) if home else default_home()
    folder.mkdir(parents=True, exist_ok=True)
    failing = [r["name"] for r in results if r["result"].startswith("FAIL")] or (["no scenario ran"] if failed else [])
    data = {"at": time.time(), "ok": not failed, "failed": failing, "scenarios": results}
    (folder / "factory_sim_last.json").write_text(json.dumps(data), encoding="utf-8")
    if failed:
        file_issue(failing, tail)


def _summary(tail: str, limit: int = 600) -> str:
    """Short, redacted summary for a public issue: no raw pytest output, no paths, no key-like strings."""
    import re
    lines = [ln.strip() for ln in (tail or "").splitlines()
             if re.match(r"^(FAILED|ERROR) |^=+ .*(passed|failed|error)", ln.strip())]
    text = "\n".join(lines[-5:]) or "(see local run for details)"
    text = re.sub(r"[A-Za-z]:[\\/][^\s:]*", "<path>", text)
    text = re.sub(r"\b(sk-|ghp_|gho_|ghs_|ghu_|github_pat_|AKIA|xox[bp]-)[A-Za-z0-9_-]{8,}", "<redacted>", text)
    return text[:limit]


def file_issue(failing: list, tail: str, repo: str = "Jarvis-igerman-ai/jarves") -> None:
    title = "Factory nightly simulation failed: " + ", ".join(failing)[:120]
    try:
        found = subprocess.run(["gh", "issue", "list", "--repo", repo, "--state", "open", "--search",
                                "Factory nightly simulation failed in:title", "--json", "number"],
                               capture_output=True, text=True, timeout=60)
        if found.returncode != 0:
            print("duplicate check failed (gh issue list); not filing an issue")
            return
        if found.stdout.strip() not in ("", "[]"):
            print("an open nightly-failure issue already exists; not filing another")
            return
        body = ("The nightly factory simulation (core/tools/factory_sim.py --nightly) failed.\n\nFailing: "
                + ", ".join(failing) + "\n\nSummary:\n```\n" + _summary(tail) + "\n```\nRe-run: python core/tools/factory_sim.py -v")
        made = subprocess.run(["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body,
                               "--label", "showstopper", "--label", "P1"],
                              capture_output=True, text=True, timeout=60)
        if made.returncode != 0:
            print("issue create failed: " + (made.stderr or "").strip()[:200])
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"could not file the issue: {exc}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-k", default="", help="only scenarios whose name contains this text")
    parser.add_argument("-v", action="store_true", help="show the full pytest output (incl. the day timeline)")
    parser.add_argument("--nightly", action="store_true",
                        help="record the result for the workflow dashboard; a failure also opens a showstopper issue")
    parser.add_argument("--home", default="", help="workflow home (default: the private jarves workflow directory)")
    parser.add_argument("--schedule-command", action="store_true",
                        help="print the Windows Task Scheduler command for the 03:00 nightly run and exit (installs nothing)")
    args = parser.parse_args(argv)
    if args.schedule_command:
        print(schedule_command())
        return 0
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
    results: list[dict] = []
    for line in out.splitlines():
        match = re.search(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) .*::(test_sim_\w+)", line)
        if match:
            result, name = match.groups()
            if result == "PASSED" and "WRAPPER-MISSING" in out and name in WRAPPER_TESTS:
                result = "SKIPPED"
            failed += result in {"FAILED", "ERROR", "XPASS"}
            results.append({"name": name[len("test_sim_"):], "result": LABEL[result]})
            print(f"{LABEL[result]:<22} {name[len('test_sim_'):]}")
    if not args.v and "DAY SIMULATION TIMELINE" in out:
        block = out[out.index("DAY SIMULATION TIMELINE"):].splitlines()
        print("\n" + "\n".join(line for line in block[:11] if not re.match(r"\.|PASSED|FAILED|=", line)))
    if not re.search(r"::test_sim_", out):
        print("no scenario ran:\n" + out[-800:])
        if args.nightly:
            record(args.home, [], 1, out[-800:])
        return 1
    if args.nightly:
        record(args.home, results, failed, out[-800:] if failed else "")
    print("\nALL SCENARIOS OK" if not failed else f"\n{failed} SCENARIO(S) FAILED (run with -v)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
