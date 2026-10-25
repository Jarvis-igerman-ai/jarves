"""Read-only, bounded preflight for overlapping file edits in concurrent GitHub PRs.

Run BEFORE editing shared files:
  python core/tools/pr_overlap.py --files core/src/jarves/dashboard.py core/src/jarves/brain.py
Or, after edits (requires a locally available origin/main):
  python core/tools/pr_overlap.py

Exit: 0 = no known overlapping open PR; 1 = overlaps; 2 = unable to check.
No remote writes, no CI invocation, no need to merge main or re-run tests.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_REPO = "Jarvis-igerman-ai/jarves"
MAX_OPEN_PRS = 30
MAX_WORKERS = 4


class PreflightError(Exception):
    pass


def paths(items: list[str]) -> set[str]:
    """Normalize ordinary repo-relative Git paths, preserving exact names."""
    return {
        p.strip().replace("\\", "/").removeprefix("./")
        for p in items
        if p.strip()
    }


def command(argv: list[str], timeout: int = 20) -> str:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"{argv[0]} unavailable or timed out: {exc}") from exc
    if result.returncode:
        raise PreflightError(f"{argv[0]} exited {result.returncode}: {result.stderr.strip()[:250]}")
    return result.stdout


def changed_files(base: str) -> set[str]:
    """Committed changes plus unstaged, staged and untracked worktree files."""
    refs = [
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACDMRT", f"origin/{base}...HEAD"],
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACDMRT"],
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACDMRT"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    ]
    result: set[str] = set()
    for argv in refs:
        result |= paths(command(argv).split("\0"))
    return result


def list_open_prs(repo: str, base: str) -> list[dict]:
    raw = command([
        "gh", "pr", "list", "-R", repo, "--base", base, "--state", "open",
        "--limit", str(MAX_OPEN_PRS + 1), "--json", "number,title,url",
    ])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreflightError("GitHub returned invalid PR JSON") from exc
    if not isinstance(data, list) or len(data) > MAX_OPEN_PRS:
        raise PreflightError(f"More than {MAX_OPEN_PRS} open PRs: narrow the comparison manually")
    return data


def pr_files(repo: str, number: int) -> set[str]:
    # --paginate --slurp means a JSON array of all page arrays, not only the first 100 files.
    raw = command(["gh", "api", "--paginate", "--slurp",
                   f"repos/{repo}/pulls/{number}/files?per_page=100"], timeout=40)
    try:
        pages = json.loads(raw)
        if not isinstance(pages, list):
            raise ValueError("unexpected response")
        return paths([entry["filename"] for page in pages for entry in page])
    except (ValueError, TypeError, KeyError) as exc:
        raise PreflightError(f"Invalid file listing for PR #{number}") from exc


def find_overlaps(own: set[str], other_pr_files: dict[int, set[str]]) -> list[tuple[int, list[str]]]:
    return [
        (number, sorted(own & changed))
        for number, changed in sorted(other_pr_files.items())
        if own & changed
    ]


def check(repo: str, base: str, own: set[str], exclude_pr: int | None = None) -> list[tuple[int, list[str]]]:
    prs = [p for p in list_open_prs(repo, base) if p["number"] != exclude_pr]
    if not prs:
        return []
    found: dict[int, set[str]] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(prs))) as executor:
        jobs = {executor.submit(pr_files, repo, p["number"]): p["number"] for p in prs}
        for job in as_completed(jobs):
            number = jobs[job]
            try:
                found[number] = job.result()
            except PreflightError:
                # Never label an incomplete check "safe". Fail even if another PR did overlap.
                raise
    return find_overlaps(own, found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", nargs="+", help="Planned repo-relative files BEFORE editing")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--base", default="main")
    parser.add_argument("--exclude-pr", type=int, help="Ignore the current PR when rechecking")
    args = parser.parse_args(argv)
    try:
        own = paths(args.files) if args.files is not None else changed_files(args.base)
        if not own:
            raise PreflightError("No files to compare; use --files path1 path2 before editing")
        conflicts = check(args.repo, args.base, own, args.exclude_pr)
    except PreflightError as exc:
        print(f"PR overlap UNKNOWN: {exc}", file=sys.stderr)
        return 2
    if not conflicts:
        print(f"No file overlap among open {args.base} PRs for {len(own)} planned/changed file(s).")
        print("This is a snapshot, not a guarantee; coordinate shared-file writers.")
        return 0
    print(f"Potential merge conflicts: {len(conflicts)} open PR(s) touch the same file(s).")
    for number, files in conflicts:
        print(f"  PR #{number}: {', '.join(files)}")
    print("Assign one file owner, choose another non-overlapping task, or coordinate before changing these files.")
    print("Do NOT merge main into every behind-but-mergeable PR or rerun full CI just to remove the warning.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
