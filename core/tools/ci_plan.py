"""CI planner: which jobs a change needs, and how to split the Python tests into balanced shards.

Pure standard library (the `changes` job runs it before any dependency is installed) and offline, so it is unit-tested.

    python core/tools/ci_plan.py classify --event pull_request [--files-from -] [--durations FILE] [--reused]
    python core/tools/ci_plan.py shard-files --plan PLAN.json --os windows --shard 2/3
    python core/tools/ci_plan.py durations-merge --junit DIR_OR_FILE... --into core/tests/durations-windows.json
    python core/tools/ci_plan.py summary --plan-line "plan={...}"   # one markdown line for the job summary

`classify` prints key=value lines for $GITHUB_OUTPUT (code, full, perf, installer, cad, blender, godot, whatsapp, e2e,
plan). The shard plan is balanced by MEASURED seconds per test file (durations file) and falls back to the number of
test functions for files that have no measurement yet. Docs/CLAUDE.md-only changes need no job; guides-only changes run
only the Linux Core shards; a merge commit whose tree was already proven on its PR (`--reused`) needs nothing.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "core" / "tests"
BROWSER_FILES = ("test_browser.py", "test_canary.py", "test_dashboard_a11y.py", "test_chat_markdown_browser.py",
                 "test_mobile_phone_e2e.py")   # same list as tests/conftest.py (test_ci_speed_tools checks it)
SECONDS_PER_TEST = 0.5          # weight of a test file that has no measurement yet (when nothing is measured at all)
FLAGS = ("code", "full", "perf", "installer", "cad", "blender", "godot", "whatsapp", "e2e")

DOCS = re.compile(r"^(docs/|[^/]+\.md$|LICENSE|\.gitignore|\.github/(dependabot\.yml|secret_scanning\.yml|ISSUE_TEMPLATE/|PULL_REQUEST_TEMPLATE))")
GUIDES = re.compile(r"^core/src/jarves/guides/data/[^/]+\.toml$")
SELF = re.compile(r"^(\.github/workflows/jarves-checks\.yml|core/tools/ci_(plan|flake|reuse)\.py|core/tests/conftest\.py)$")
RULES = {
    "perf": r"^(core/(src/|tools/(bench_startup|perf_gate)|perf_budgets|pyproject|uv\.lock))",
    "installer": r"^(packaging/|core/(src/|pyproject|uv\.lock)|godot/|jarvis\.cmd)",
    "cad": r"^cad/",
    "blender": r"^(blender/|cad/|godot/)",
    "godot": r"^(godot/|blender/)",
    "whatsapp": r"^(bridge/|packaging/bridge_pin\.json|scripts/build_whatsapp_bridge\.ps1)",
    "e2e": r"^(godot/|core/(src/|pyproject|uv\.lock)|core/tests/test_face_protocol_godot\.py)",
}
# S2 (#160, owner standing decision 25 Oct 2026 via the coordinator): on a PULL REQUEST the installer chain (build, 4
# smoke profiles, upgrade/rollback: 6 Windows jobs, ~3-4 min of wall time) runs only when packaging itself changes. The
# merge-queue group (merge_group) keeps the full RULES["installer"] rule, so any group touching core/src, packaging/,
# pyproject/uv.lock, godot/ or jarvis.cmd builds and smoke-tests the installer BEFORE it merges; main reuses that proof.
PR_INSTALLER = r"^(packaging/|core/(pyproject\.toml|uv\.lock)|godot/|jarvis\.cmd|scripts/build_)"


def classify(files: list[str], event: str = "pull_request", reused: bool = False, draft: bool = False,
             reuse_tests_only: bool = False) -> dict[str, bool]:
    """Which jobs a change needs. Everything runs on main pushes, tags and manual runs, unless `reused`. A DRAFT pull
    request gets only the fast checks (classify + validation): the full run starts when it is marked ready (#89)."""
    if reused or draft:
        return {k: False for k in FLAGS}
    if reuse_tests_only:
        # Same exact content tree already passed *all PR tests*, but PR did not build
        # an installer. The main run must still produce+smoke one for release/package.
        # Never skip an installer on the strength of a test-only artifact.
        if event != "push":
            raise ValueError("reuse_tests_only requires a main push")
        return {k: k == "installer" for k in FLAGS}
    # a merge-queue group (merge_group) is classified like its PR: its commit is the merge of the queue base and the PR,
    # so `git diff HEAD^1 HEAD` is the PR's own change. A docs-only group skips the heavy jobs; the push run on main
    # then reuses the previous proof by the docs-free tree hash (ci_reuse.py). Code always gets the full matrix.
    if event not in ("pull_request", "merge_group"):
        return {k: True for k in FLAGS}
    files = [f.strip() for f in files if f.strip()]
    if any(SELF.match(f) for f in files):
        return {k: True for k in FLAGS}
    rest = [f for f in files if not DOCS.match(f)]
    out = {k: any(re.match(p, f) for f in files) for k, p in RULES.items()}
    if event == "pull_request":
        out["installer"] = any(re.match(PR_INSTALLER, f) for f in files)
    out["code"] = bool(rest)
    out["full"] = any(not GUIDES.match(f) for f in rest)      # guides-only: just the Linux shard
    if not out["full"]:
        out = {**out, **{k: False for k in RULES}}
    return out


# ---------------------------------------------------------------------------------------------------- shard planning
def count_tests(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return 1
    n = sum(1 for x in ast.walk(tree) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name.startswith("test"))
    return max(1, n)


def file_weights(tests_dir: Path, durations: dict[str, float] | None = None) -> dict[str, float]:
    """{test file name: seconds}. Measured seconds when known, else test count x the measured average per test so
    measured and unmeasured files stay comparable."""
    durations = durations or {}
    files = sorted(p for p in tests_dir.glob("test_*.py") if p.name not in BROWSER_FILES)
    measured = [durations[p.name] / count_tests(p) for p in files if p.name in durations]
    per_test = (sum(measured) / len(measured)) if measured else SECONDS_PER_TEST
    return {p.name: float(durations[p.name]) if p.name in durations else count_tests(p) * per_test for p in files}


def split(weights: dict[str, float], n: int) -> list[list[str]]:
    """Greedy longest-first into n shards (deterministic); each shard lists its slowest file first."""
    shards: list[list[str]] = [[] for _ in range(n)]
    load = [0.0] * n
    for name, w in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        i = load.index(min(load))
        shards[i].append(name)
        load[i] += w
    return shards


def plan(tests_dir: Path = TESTS, durations: dict[str, float] | None = None, windows: int = 3, linux: int = 3,
         linux_durations: dict[str, float] | None = None) -> dict:
    """Balance each OS from its own measurements; a legacy single map still works for callers."""
    lin_durations = durations if linux_durations is None else linux_durations
    win_weights = file_weights(tests_dir, durations)
    lin_weights = file_weights(tests_dir, lin_durations)
    win, lin = split(win_weights, windows), split(lin_weights, linux)
    return {"windows": win, "linux": lin,
            "expected_s": {"windows": [round(sum(win_weights[f] for f in s)) for s in win],
                           "linux": [round(sum(lin_weights[f] for f in s)) for s in lin]},
            # files planned from the test-count fallback: their real time is unknown, so a long one unbalances a shard
            "unmeasured": {"windows": sorted(f for f in win_weights if f not in (durations or {})),
                           "linux": sorted(f for f in lin_weights if f not in (lin_durations or {}))}}


def summary(plan_doc: dict) -> str:
    """One markdown line for the job summary: expected seconds per shard and how many files had no measurement."""
    parts = []
    for os_name in ("windows", "linux"):
        loads = plan_doc.get("expected_s", {}).get(os_name, [])
        missing = plan_doc.get("unmeasured", {}).get(os_name, [])
        parts.append(f"{os_name} shards expected {loads} s, {len(missing)} unmeasured file(s)"
                     + (f" ({', '.join(missing[:8])}{', ...' if len(missing) > 8 else ''})" if missing else ""))
    return "shard plan: " + "; ".join(parts)


def shard_files(plan_doc: dict, os_name: str, shard: str) -> list[str]:
    idx, n = (int(x) for x in shard.split("/"))
    shards = plan_doc["windows" if os_name.startswith("win") else "linux"]
    if len(shards) != n:
        raise SystemExit(f"plan has {len(shards)} {os_name} shards, asked for {shard}")
    return shards[idx - 1]


# -------------------------------------------------------------------------------------------------- durations data
def junit_file_times(xml_path: Path) -> dict[str, float]:
    """Seconds per test file from a pytest junit xml (classname `tests.test_x[.Class]` or the `file` attribute)."""
    out: dict[str, float] = {}
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        name = None
        for part in (case.get("file") or case.get("classname") or "").replace("\\", "/").replace("/", ".").split("."):
            if part.startswith("test_"):
                name = part + ".py"
                break
        if name:
            out[name] = out.get(name, 0.0) + float(case.get("time") or 0)
    return out


def report_shard_timings(xml_files: list[Path]) -> str:
    """Surface measured shard skew for tuning, without weakening any CI gate."""
    measured = [(p, junit_file_times(p)) for p in xml_files]
    shard_seconds = [round(sum(times.values())) for _, times in measured if times]
    if len(shard_seconds) < 2 or min(shard_seconds) == 0:
        return "shard timings: insufficient measurements to compare"
    high, low = max(shard_seconds), min(shard_seconds)
    imbalance = high / low
    heaviest: dict[str, float] = {}
    for _, times in measured:
        for name, seconds in times.items():
            heaviest[name] = max(heaviest.get(name, 0), seconds)
    top = ", ".join(f"{f} {s:.0f}s" for f, s in sorted(heaviest.items(), key=lambda t: -t[1])[:4])
    status = "REBALANCE CANDIDATE" if imbalance > 1.30 and high - low > 60 else "balanced"
    return (f"shard timing {status}: {len(shard_seconds)} shards, measured test seconds "
            f"{sorted(shard_seconds)}, skew {imbalance:.2f}x; slow files: {top}")


def merge_durations(old: dict[str, float], new: dict[str, float], keep: float = 0.5) -> dict[str, float]:
    """Blend so one slow run does not whip the shards around: keep*old + (1-keep)*new."""
    out = dict(old)
    for k, v in new.items():
        out[k] = round(keep * old[k] + (1 - keep) * v, 2) if k in old else round(v, 2)
    return out


def load_durations(path: Path | None) -> dict[str, float]:
    if path and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {k: float(v) for k, v in data.items() if k.endswith(".py")}
        except (ValueError, OSError):
            return {}
    return {}


# ------------------------------------------------------------------------------------------------------------- cli
def _git_files() -> list[str]:
    r = subprocess.run(["git", "diff", "--name-only", "HEAD^1", "HEAD"], capture_output=True, text=True, check=True)
    return r.stdout.splitlines()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ci_plan.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--event", default="pull_request")
    c.add_argument("--files-from", help="file with one path per line, '-' for stdin; default: git diff HEAD^1 HEAD")
    c.add_argument("--durations", type=Path, default=TESTS / "durations-windows.json")
    c.add_argument("--linux-durations", type=Path, default=TESTS / "durations-linux.json")
    c.add_argument("--reused", action="store_true", help="the tree was already proven on its PR: nothing to run")
    c.add_argument("--reuse-tests-only", action="store_true", help="exact green PR tests exist; main still builds installer")
    c.add_argument("--draft", action="store_true", help="a draft pull request: only the fast checks run")
    s = sub.add_parser("shard-files")
    s.add_argument("--plan", type=Path, required=True)
    s.add_argument("--os", required=True)
    s.add_argument("--shard", required=True)
    d = sub.add_parser("durations-merge")
    d.add_argument("--junit", nargs="+", type=Path, required=True)
    d.add_argument("--into", type=Path, required=True)
    m = sub.add_parser("summary")
    m.add_argument("--plan-line", required=True, help="the `plan=...` line printed by classify")
    a = ap.parse_args(argv)
    if a.cmd == "summary":
        print(summary(json.loads(a.plan_line.partition("=")[2] or "{}")))
        return 0
    if a.cmd == "classify":
        if a.files_from:
            files = (sys.stdin.read() if a.files_from == "-" else Path(a.files_from).read_text(encoding="utf-8")).splitlines()
        else:
            files = _git_files() if a.event in ("pull_request", "merge_group") else []
        for k, v in classify(files, a.event, a.reused, a.draft, a.reuse_tests_only).items():
            print(f"{k}={'true' if v else 'false'}")
        print("plan=" + json.dumps(plan(durations=load_durations(a.durations), linux_durations=load_durations(a.linux_durations)), separators=(",", ":")))
        return 0
    if a.cmd == "shard-files":
        print(",".join(shard_files(json.loads(a.plan.read_text(encoding="utf-8")), a.os, a.shard)))
        return 0
    paths: list[Path] = []
    for p in a.junit:
        paths += sorted(p.rglob("*.xml")) if p.is_dir() else [p]
    fresh: dict[str, float] = {}
    for p in paths:
        for k, v in junit_file_times(p).items():
            fresh[k] = max(fresh.get(k, 0.0), v)      # caller supplies artifacts from ONE platform only
    merged = merge_durations(load_durations(a.into), fresh)
    a.into.parent.mkdir(parents=True, exist_ok=True)
    a.into.write_text(json.dumps(dict(sorted(merged.items())), indent=1) + "\n", encoding="utf-8")
    print(f"durations: {len(fresh)} files measured from {len(paths)} junit file(s) -> {a.into}")
    print(report_shard_timings(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
