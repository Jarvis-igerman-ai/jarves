"""Hotfix lane (docs/RELEASE_TRAIN.md): backport a fix that is already merged into the next line to a released minor.

    python core/tools/hotfix.py --pr 123 --to release/4.0 [--dry-run] [--remote origin]

The fix always lands in `next`/main FIRST (so the next version never loses it). This tool then:
  1. checks the PR is merged and the target is `release/<x.y>` (created when x.y.0 went stable);
  2. creates `hotfix/<pr>-to-<x.y>` from the release branch, cherry-picks the merge commit (`-x -m 1`: the origin
     is recorded in the message), pushes it and opens a PR into the release branch with the label `backport`
     (full CI runs there: base is not `next`);
  3. prints the version rule: the release branch carries x.y.<patch+1>; after the PR is merged, complete the short
     sign-off sheet, tag vX.Y.Z on the merge commit and release.yml publishes the stable hotfix.
It pushes only to a remote whose URL is the public igalgerman/jarves repo (never the private one). --dry-run prints the
exact commands and runs none. The pure helpers are unit-tested without git or network.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Callable

PUBLIC_REPO = "igalgerman/jarves"
BRANCH_RE = re.compile(r"^release/(\d+)\.(\d+)$")


def release_minor(branch: str) -> str:
    m = BRANCH_RE.match(branch or "")
    if not m:
        raise SystemExit(f"--to must be a release branch like release/4.0, got {branch!r}")
    return f"{m.group(1)}.{m.group(2)}"


def next_patch(tags: list[str], minor: str) -> str:
    """The version the next hotfix of `minor` carries: highest tag vX.Y.N + 1 (the .0 is the minor's first stable)."""
    ns = [int(m.group(1)) for t in tags if (m := re.fullmatch(rf"v{re.escape(minor)}\.(\d+)", t))]
    return f"{minor}.{max(ns, default=0) + 1}"


def check_pr(info: dict) -> list[str]:
    problems = []
    if info.get("state") != "MERGED":
        problems.append(f"PR #{info.get('number')} is not merged (merge it into next first; the fix must not exist only on a release branch)")
    if info.get("baseRefName") not in ("next", "main"):
        problems.append(f"PR #{info.get('number')} was merged into {info.get('baseRefName')!r}, expected next or main")
    if not (info.get("mergeCommit") or {}).get("oid"):
        problems.append("the PR has no merge commit")
    return problems


def check_remote(url: str) -> list[str]:
    ok = re.search(r"github\.com[:/]igalgerman/jarves(\.git)?/?$", url.strip(), re.I)
    return [] if ok else [f"remote URL {url!r} is not the public {PUBLIC_REPO} repo; refusing to push"]


def commands(pr: int, minor: str, oid: str, remote: str, title: str, patch: str) -> list[list[str]]:
    br, work = f"release/{minor}", f"hotfix/{pr}-to-{minor}"
    body = (f"Backport of #{pr} (merge commit {oid[:12]}) to `{br}`.\n\nThis becomes **v{patch}**: after merge, complete the short "
            f"sign-off sheet (`python core/tools/manual_test_sheet.py --version {patch}`), tag `v{patch}`; release.yml gates and "
            "publishes it (docs/RELEASE_TRAIN.md, hotfix lane).")
    return [["git", "fetch", remote, br],
            ["git", "switch", "-c", work, f"{remote}/{br}"],
            ["git", "cherry-pick", "-x", "-m", "1", oid],
            ["git", "push", "-u", remote, work],
            ["gh", "pr", "create", "--repo", PUBLIC_REPO, "--base", br, "--head", work, "--label", "backport",
             "--title", f"[backport {minor}] {title}", "--body", body]]


def run_cmd(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(cmd[:4])}...: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def backport(pr: int, to: str, remote: str, dry_run: bool, run: Callable[[list[str]], str] = run_cmd, out=print) -> int:
    minor = release_minor(to)
    info = json.loads(run(["gh", "pr", "view", str(pr), "--repo", PUBLIC_REPO, "--json", "number,state,baseRefName,mergeCommit,title"]))
    problems = check_pr(info) + check_remote(run(["git", "remote", "get-url", remote]))
    if problems:
        out("HOTFIX REFUSED:\n  - " + "\n  - ".join(problems))
        return 1
    tags = run(["git", "tag", "--list", f"v{minor}.*"]).split()
    patch = next_patch(tags, minor)
    cmds = commands(pr, minor, info["mergeCommit"]["oid"], remote, info["title"], patch)
    out(f"{'DRY RUN - ' if dry_run else ''}backport #{pr} to release/{minor}; the release becomes v{patch}")
    for c in cmds:
        out("  $ " + " ".join(c[:9]) + (" ..." if len(c) > 9 else ""))
        if not dry_run:
            run(c)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hotfix.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--to", required=True, help="release/<x.y>")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    return backport(a.pr, a.to, a.remote, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
