"""One command before every push: the same fast checks CI's `validate` (quick) job runs, in about 2-3 minutes.

    python core/tools/prepush.py                 # all checks, PASS/FAIL table, exit 1 on any failure
    python core/tools/prepush.py --fast          # skip the slower ones (locales, CI workflow tests, network guard)
    python core/tools/prepush.py --changed-only  # only checks the changed files (vs the main branch) can affect
    python core/tools/prepush.py --only perf dash
    python core/tools/prepush.py --install-hook  # optional git pre-push hook (never installed automatically)
    python core/tools/prepush.py --uninstall-hook

Checks: lint (log_lint), guides (guides + CLI parity), locales (Hebrew texts, dashboard locales), docs (links, DOC_MAP),
perf (prompt+tools size budget), ci (workflow contract tests), net (network guard), dash (every dashboard GET endpoint
timed against `ci_max` of dashboard_api_max_ms in core/perf_budgets.toml). The checks use the Python running this
script (the project virtualenv). `git push --no-verify` always bypasses the hook. See docs/PREPUSH.md.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent
MARK = "# jarves-prepush-hook"
DASH_PAGES = ("/", "/api/overview", "/api/sessions", "/api/costs?days=30", "/api/issues", "/api/skills",
              "/api/memory", "/api/settings")


@dataclass
class Check:
    name: str
    title: str
    tests: tuple[str, ...] = ()          # pytest files relative to core/
    cmd: tuple[str, ...] = ()            # alternative: a command (relative to core/)
    slow: bool = False                   # skipped by --fast
    always_on_code: bool = False         # --changed-only: also runs when any core/src or core/tools file changed


CHECKS: tuple[Check, ...] = (
    Check("lint", "log message lint", cmd=("tools/log_lint.py",), always_on_code=True),
    Check("guides", "guides + CLI parity", tests=("tests/test_guides.py", "tests/test_cli_parity.py")),
    Check("locales", "Hebrew texts + dashboard locales",
          tests=("tests/test_hebrew_texts.py", "tests/test_dashboard_locales.py"), slow=True),
    Check("docs", "docs links + DOC_MAP", tests=("tests/test_docs_links.py",)),
    Check("perf", "prompt+tools size budget", tests=("tests/test_p_perf.py",), always_on_code=True),
    Check("ci", "CI workflow contracts",
          tests=("tests/test_ci_workflows.py", "tests/test_ci_pipeline.py", "tests/test_ci_speed_tools.py",
                 "tests/test_ci_comment.py", "tests/test_prepush.py"), slow=True),
    Check("net", "network guard", tests=("tests/test_netguard.py",), slow=True),
    Check("dash", "dashboard endpoint timing vs ci budget", cmd=("tools/prepush.py", "--dash-probe"),
          always_on_code=True),
)


@dataclass
class Result:
    check: Check
    ok: bool
    seconds: float
    first_error: str = ""
    rerun: str = ""
    skipped: bool = False
    extra: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- parsing / formatting (unit tested)

def first_error(output: str) -> str:
    """First useful error line of a tool/pytest output ('' when there is none). Truncated to 200 characters."""
    lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]
    for pat in (r"^E\s+\S", r"^FAILED ", r"^ERROR ", r"(?i)\b(error|violation|exceed|over the|fail)"):
        for ln in lines:
            if re.search(pat, ln):
                return re.sub(r"^E\s+", "", ln).strip()[:200]
    return (lines[-1].strip()[:200]) if lines else ""


def pytest_failed_test(output: str) -> str:
    m = re.search(r"^FAILED (\S+)", output, re.M)
    return m.group(1) if m else ""


def dash_budget(budgets_file: Path | None = None, klass: str = "ci") -> float:
    doc = tomllib.loads((budgets_file or CORE / "perf_budgets.toml").read_text(encoding="utf-8"))
    return float(doc["metrics"]["dashboard_api_max_ms"][f"{klass}_max"])


def judge_timings(timings: dict[str, float], budget_ms: float) -> tuple[bool, str]:
    """Every endpoint must be at or under the budget. Returns (ok, one-line message naming the slowest endpoints)."""
    if not timings:
        return False, "no dashboard endpoint was timed"
    worst = sorted(timings.items(), key=lambda kv: kv[1], reverse=True)
    over = [f"{p} {ms:.0f} ms" for p, ms in worst if ms > budget_ms]
    if over:
        return False, f"over the {budget_ms:.0f} ms ci budget: " + ", ".join(over[:3])
    return True, f"slowest {worst[0][0]} {worst[0][1]:.0f} ms (budget {budget_ms:.0f} ms)"


def format_table(results: list[Result]) -> str:
    w = max([len(r.check.name) for r in results] + [5])
    rows = [f"{'check'.ljust(w)}  result  {'time':>6}  detail"]
    for r in results:
        state = "SKIP" if r.skipped else ("PASS" if r.ok else "FAIL")
        detail = r.first_error if not r.ok and not r.skipped else r.check.title
        if r.ok and r.extra:
            detail = r.extra[0]
        rows.append(f"{r.check.name.ljust(w)}  {state:<6}  {r.seconds:5.1f}s  {detail}")
    bad = [r for r in results if not r.ok and not r.skipped]
    if bad:
        rows.append("")
        rows.append("re-run a failed check:")
        rows.extend(f"  {r.rerun}" for r in bad)
    rows.append("")
    rows.append("PREPUSH FAILED: do not push on a red local gate" if bad else "PREPUSH PASSED")
    return "\n".join(rows)


def select(changed: list[str] | None, fast: bool, only: list[str] | None) -> list[Check]:
    """Which checks run. `changed` None = everything; otherwise only the ones the changed files can affect."""
    out = []
    for c in CHECKS:
        if only and c.name not in only:
            continue
        if fast and c.slow and not only:
            continue
        out.append(c)
    if changed is None:
        return out
    if not changed:
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ci_affected
    picked = ci_affected.affected(changed)
    if picked is None:
        return out
    code = any(f.replace("\\", "/").startswith(("core/src/", "core/tools/")) for f in changed)
    sel = []
    for c in out:
        names = {Path(t).name for t in c.tests}
        if names & set(picked) or (c.always_on_code and code):
            sel.append(c)
    return sel


def hook_text(python: str) -> str:
    py = python.replace("\\", "/")
    return (f"#!/bin/sh\n{MARK}\n"
            "# Runs the local gate before a push. Bypass once with: git push --no-verify (or JARVES_PREPUSH_SKIP=1).\n"
            '[ -n "$JARVES_PREPUSH_SKIP" ] && exit 0\n'
            'root=$(git rev-parse --show-toplevel) || exit 0\n'
            f'exec "{py}" "$root/core/tools/prepush.py" --changed-only --fast\n')


# ---------------------------------------------------------------- running

def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(CORE / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _command(c: Check) -> list[str]:
    if c.tests:
        return [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-o", "pythonpath=src", *c.tests]
    return [sys.executable, *c.cmd]


def run_check(c: Check) -> Result:
    cmd = _command(c)
    t = time.perf_counter()
    p = subprocess.run(cmd, cwd=CORE, env=_env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    shown = ["python" if i == 0 else a for i, a in enumerate(cmd) if a != "no:cacheprovider" and a != "-p"]
    rerun = "cd core && " + " ".join(shown)
    ok = p.returncode == 0
    err = ""
    if not ok:
        err = first_error(out)
        test = pytest_failed_test(out)
        if test and test not in err:
            err = f"{test}: {err}" if err else test
    extra = [ln for ln in out.splitlines() if ln.startswith("dashboard:")][:1] if ok else []
    return Result(c, ok, time.perf_counter() - t, err[:200], rerun, extra=extra)


def changed_files(base: str | None = None) -> list[str]:
    for b in ([base] if base else ["jarves/main", "origin/main", "main"]):
        r = subprocess.run(["git", "diff", "--name-only", f"{b}...HEAD"], cwd=REPO, capture_output=True, text=True)
        if r.returncode == 0:
            dirty = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO, capture_output=True,
                                   text=True).stdout
            return sorted({x for x in (r.stdout + dirty).splitlines() if x.strip()})
    return []


def dash_probe() -> int:
    """Start the dashboard in a temp home (same helpers as jarves.speedtest) and time every GET endpoint."""
    import http.client
    home = Path(tempfile.mkdtemp(prefix="jarves-prepush-"))
    os.environ["JARVES_HOME"] = str(home)
    from jarves import dashboard
    from jarves.config import Config
    (home / "workspace").mkdir(exist_ok=True)
    d = dashboard.Dashboard(home, Config()).start()
    timings: dict[str, float] = {}
    try:
        cookie = f"{dashboard.COOKIE}={d.url.split('t=')[1]}"
        for path in DASH_PAGES:
            c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=15)
            t = time.perf_counter()
            c.request("GET", path, headers={"Host": f"127.0.0.1:{d.port}", "Cookie": cookie})
            r = c.getresponse()
            r.read()
            timings[path] = (time.perf_counter() - t) * 1000
            c.close()
            if r.status >= 500:
                print(f"dashboard {path} returned HTTP {r.status}")
                return 1
    finally:
        try:
            d.stop()
        except Exception:  # noqa: BLE001 - best effort shutdown of a throwaway dashboard
            pass
    ok, msg = judge_timings({p: m for p, m in timings.items() if p != "/"}, dash_budget())
    print("dashboard: " + msg)
    for p, m in timings.items():
        print(f"  {m:7.1f} ms  {p}")
    return 0 if ok else 1


def hook_path() -> Path:
    r = subprocess.run(["git", "rev-parse", "--git-path", "hooks/pre-push"], cwd=REPO, capture_output=True,
                       text=True, check=True)
    p = Path(r.stdout.strip())
    return p if p.is_absolute() else REPO / p


def install_hook() -> int:
    p = hook_path()
    if p.exists() and MARK not in p.read_text(encoding="utf-8", errors="replace"):
        print(f"{p} exists and is not ours; not touching it")
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(hook_text(sys.executable), encoding="utf-8", newline="\n")
    p.chmod(0o755)
    print(f"installed {p} (runs --changed-only --fast; bypass with git push --no-verify)")
    return 0


def uninstall_hook() -> int:
    p = hook_path()
    if p.exists() and MARK in p.read_text(encoding="utf-8", errors="replace"):
        p.unlink()
        print(f"removed {p}")
    else:
        print("no JARVES pre-push hook installed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fast", action="store_true", help="skip the slower checks")
    ap.add_argument("--changed-only", action="store_true", help="only checks the changed files can affect")
    ap.add_argument("--base", help="base ref for --changed-only (default jarves/main, origin/main, main)")
    ap.add_argument("--only", nargs="+", choices=[c.name for c in CHECKS])
    ap.add_argument("--install-hook", action="store_true")
    ap.add_argument("--uninstall-hook", action="store_true")
    ap.add_argument("--dash-probe", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.dash_probe:
        return dash_probe()
    if a.install_hook:
        return install_hook()
    if a.uninstall_hook:
        return uninstall_hook()
    checks = select(changed_files(a.base) if a.changed_only else None, a.fast, a.only)
    if not checks:
        print("PREPUSH PASSED (nothing to check for these changes)")
        return 0
    results = []
    for c in checks:
        print(f"... {c.name}: {c.title}", flush=True)
        results.append(run_check(c))
    print()
    print(format_table(results))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
