"""Find the PR group that broke a failed batch run on `next` without re-running everything (docs/RELEASE_TRAIN.md).

    python core/tools/bisect_batch.py plan --failed test_a.py test_b.py [--base main --head next]
    python core/tools/bisect_batch.py run  --failed test_a.py --test-cmd "uv run pytest -q {files}" [--base main --head next]

A batch is the list of first-parent merge commits `base..head` (one per PR). `plan` lists the SUSPECT groups: those whose
changed files can affect a failing test file (core/tools/ci_plan.py affected_tests), in merge order, plus the binary-search
probes. `run` checks out the probes in a throw-away worktree and runs ONLY the failing test files at each probe, so a
bad batch of 20 PRs needs about log2(20) small runs, not 20 full ones. It prints the first bad group; the owner/agent reverts
or fixes that PR on `next` and the batch run is simply repeated. Pure helpers are unit-tested without git.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_plan  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def groups(log_lines: list[str]) -> list[dict]:
    """Parse `git log --first-parent --reverse --format=%H%x09%s` (oldest first) into [{sha, subject, files: []}]."""
    out = []
    for ln in log_lines:
        if ln.strip():
            sha, _, subj = ln.partition("\t")
            out.append({"sha": sha, "subject": subj, "files": []})
    return out


def suspects(batch: list[dict], failed: list[str], tests_dir: Path = ci_plan.TESTS) -> list[dict]:
    """Groups whose changed files can affect any failing test (unknowable = suspect)."""
    bad = set(failed)
    out = []
    for g in batch:
        aff = ci_plan.affected_tests(g["files"], tests_dir)
        if aff is None or bad & set(aff):
            out.append(g)
    return out


def first_bad(candidates: list[dict], is_bad: Callable[[dict], bool]) -> tuple[dict | None, int]:
    """Binary search for the first candidate at which `is_bad` becomes true (state at a group = the tree after it).
    Returns (group, number of probes). None when even the last candidate is good (flaky or an interaction)."""
    if not candidates:
        return None, 0
    probes = 1
    if not is_bad(candidates[-1]):
        return None, probes
    lo, hi = 0, len(candidates) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        probes += 1
        if is_bad(candidates[mid]):
            hi = mid
        else:
            lo = mid + 1
    return candidates[lo], probes


def _git(*a: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


def load_batch(base: str, head: str) -> list[dict]:
    g = groups(_git("log", "--first-parent", "--reverse", "--format=%H%x09%s", f"{base}..{head}").splitlines())
    for x in g:
        x["files"] = _git("diff", "--name-only", f"{x['sha']}^1", x["sha"]).splitlines()
    return g


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bisect_batch.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=["plan", "run"])
    ap.add_argument("--failed", nargs="+", required=True, help="failing test files, e.g. test_updates.py")
    ap.add_argument("--base", default="main")
    ap.add_argument("--head", default="next")
    ap.add_argument("--test-cmd", default="python -m pytest -q {files}", help="run in the probe worktree's core/ dir")
    a = ap.parse_args(argv)
    batch = load_batch(a.base, a.head)
    sus = suspects(batch, a.failed)
    print(f"batch {a.base}..{a.head}: {len(batch)} PR group(s), {len(sus)} suspect(s) for {', '.join(a.failed)}")
    for g in sus:
        print(f"  {g['sha'][:10]}  {g['subject']}")
    if a.mode == "plan" or not sus:
        return 0
    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "wt"
        _git("worktree", "add", "--detach", str(wt), sus[-1]["sha"])
        try:
            def is_bad(g: dict) -> bool:
                _git("checkout", "--detach", g["sha"], cwd=wt)
                cmd = a.test_cmd.format(files=" ".join(a.failed))
                rc = subprocess.run(cmd, shell=True, cwd=wt / "core").returncode
                print(f"  probe {g['sha'][:10]}: {'FAIL' if rc else 'pass'}")
                return rc != 0
            bad, n = first_bad(sus, is_bad)
        finally:
            _git("worktree", "remove", "--force", str(wt))
    if bad is None:
        print("The failure does not reproduce on the last suspect: flaky, or an interaction of several groups. Re-run the batch once.")
        return 1
    print(f"FIRST BAD GROUP after {n} probe(s): {bad['sha'][:10]} {bad['subject']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
