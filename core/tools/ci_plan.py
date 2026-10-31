"""CI planner: which jobs a change needs, and how to split the Python tests into balanced shards.

Pure standard library (the `changes` job runs it before any dependency is installed) and offline, so it is unit-tested.

    python core/tools/ci_plan.py classify --event pull_request [--files-from -] [--durations FILE] [--reused]
    python core/tools/ci_plan.py shard-files --plan PLAN.json --os windows --shard 2/3
    python core/tools/ci_plan.py durations-merge --junit DIR_OR_FILE... --into core/tests/durations.json

`classify` prints key=value lines for $GITHUB_OUTPUT (code, full, perf, installer, cad, blender, godot, whatsapp, e2e,
plan). The shard plan is balanced by MEASURED seconds per test file (durations file) and falls back to the number of
test functions for files that have no measurement yet. Docs/CLAUDE.md-only changes need no job; guides-only changes run
only the Linux Core shard; a merge commit whose tree was already proven on its PR (`--reused`) needs nothing.
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
BROWSER_FILES = ("test_browser.py", "test_canary.py", "test_dashboard_a11y.py")   # same list as tests/conftest.py
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


LIGHT_OFF = ("full", "perf", "installer")      # a LIGHT run (PR into the `next` batch branch) never builds or times these
SMOKE = ("test_guides.py", "test_cli_parity.py")
ALL_TESTS_TRIGGER = re.compile(r"^core/(tests/(conftest|_tools)\.py|pyproject\.toml|uv\.lock)$")


def affected_tests(files: list[str], tests_dir: Path = TESTS) -> list[str] | None:
    """Test files a change can affect (light CI, docs/RELEASE_TRAIN.md): the changed test files themselves, tests named
    after a changed module (core/src/jarves/foo.py -> test_foo*.py, core/tools/foo.py likewise) and every test that
    mentions the module name. None = cannot tell (conftest, lock files, packaging...): run everything."""
    present = {p.name for p in tests_dir.glob("test_*.py") if p.name not in BROWSER_FILES}
    out: set[str] = set()
    mods: set[str] = set()
    for f in (x.strip() for x in files):
        if not f or DOCS.match(f):
            continue
        if ALL_TESTS_TRIGGER.match(f) or SELF.match(f) or f.startswith(("packaging/", ".github/")):
            return None
        m = re.match(r"^core/tests/(test_[^/]+\.py)$", f)
        if m:
            if m.group(1) in present:
                out.add(m.group(1))
            continue
        m = re.match(r"^core/(?:src/jarves|tools)/(?:.*/)?([A-Za-z0-9_]+)\.(?:py|toml|js)$", f)
        if m:
            mods.add(m.group(1))
        elif f.startswith("core/"):
            return None
    for name in present:
        stem = name[len("test_"):-3]
        if any(stem == m or stem.startswith(m + "_") for m in mods):
            out.add(name)
    if mods:
        pat = re.compile(r"\b(" + "|".join(re.escape(m) for m in sorted(mods)) + r")\b")
        for name in present - out:
            try:
                if pat.search((tests_dir / name).read_text(encoding="utf-8", errors="replace")):
                    out.add(name)
            except OSError:
                pass
    if not out:
        out = {n for n in SMOKE if n in present}
    return sorted(out)


def classify(files: list[str], event: str = "pull_request", reused: bool = False, light: bool = False) -> dict[str, bool]:
    """Which jobs a change needs. Everything runs on main pushes, tags and manual runs, unless `reused`. `light` (a PR
    into the `next` batch branch): quick Linux job on the affected tests plus the gates of the touched areas; the
    installer, performance gate and Windows shards run once per batch on `next`, not per PR."""
    if light and not reused and event == "pull_request":
        out = classify(files, event)
        return {**out, **{k: False for k in LIGHT_OFF}}
    if reused:
        return {k: False for k in FLAGS}
    if event != "pull_request":
        return {k: True for k in FLAGS}
    files = [f.strip() for f in files if f.strip()]
    if any(SELF.match(f) for f in files):
        return {k: True for k in FLAGS}
    rest = [f for f in files if not DOCS.match(f)]
    out = {k: any(re.match(p, f) for f in files) for k, p in RULES.items()}
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


def plan(tests_dir: Path = TESTS, durations: dict[str, float] | None = None, windows: int = 3, linux: int = 1) -> dict:
    w = file_weights(tests_dir, durations)
    win, lin = split(w, windows), split(w, linux)
    return {"windows": win, "linux": lin,
            "expected_s": {"windows": [round(sum(w[f] for f in s)) for s in win],
                           "linux": [round(sum(w[f] for f in s)) for s in lin]}}


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
    c.add_argument("--durations", type=Path, default=TESTS / "durations.json")
    c.add_argument("--reused", action="store_true", help="the tree was already proven on its PR: nothing to run")
    c.add_argument("--light", action="store_true", help="PR into the `next` batch branch: affected tests + touched-area gates only")
    s = sub.add_parser("shard-files")
    s.add_argument("--plan", type=Path, required=True)
    s.add_argument("--os", required=True)
    s.add_argument("--shard", required=True)
    d = sub.add_parser("durations-merge")
    d.add_argument("--junit", nargs="+", type=Path, required=True)
    d.add_argument("--into", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "classify":
        if a.files_from:
            files = (sys.stdin.read() if a.files_from == "-" else Path(a.files_from).read_text(encoding="utf-8")).splitlines()
        else:
            files = _git_files() if a.event == "pull_request" else []
        for k, v in classify(files, a.event, a.reused, a.light).items():
            print(f"{k}={'true' if v else 'false'}")
        doc = plan(durations=load_durations(a.durations))
        aff = affected_tests(files) if a.light and a.event == "pull_request" and not a.reused else None
        if aff is not None:                    # light: ONE Linux shard with only the affected files
            doc = {"windows": [[], [], []], "linux": [aff], "expected_s": {"windows": [0, 0, 0], "linux": [0]}, "light": True}
        print("plan=" + json.dumps(doc, separators=(",", ":")))
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
            fresh[k] = max(fresh.get(k, 0.0), v)      # Windows is the long pole: the slowest measurement wins
    merged = merge_durations(load_durations(a.into), fresh)
    a.into.parent.mkdir(parents=True, exist_ok=True)
    a.into.write_text(json.dumps(dict(sorted(merged.items())), indent=1) + "\n", encoding="utf-8")
    print(f"durations: {len(fresh)} files measured from {len(paths)} junit file(s) -> {a.into}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
