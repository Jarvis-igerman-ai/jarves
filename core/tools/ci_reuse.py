"""Reuse of the PR's already-built, already-smoke-tested installer for the merge commit (content-addressed by tree hash).

    python core/tools/ci_reuse.py tree [REV]                       # git tree hash of REV (default HEAD)
    python core/tools/ci_reuse.py resolve --repo O/R --tree T      # key=value lines for $GITHUB_OUTPUT; exit 4 = none

Every installer build uploads the artifact `jarves-installer-<tree>` where <tree> is the content address of the tested
checkout: the git tree minus documentation (on a pull_request run that is the merge of the PR head with its base). When the PR is merged and the
merge commit on main has the SAME tree, nothing about the code differs, so main neither re-runs the tests nor rebuilds
the installer: publishing downloads that artifact. `resolve` only trusts an artifact that is not expired, belongs to a
run of the `jarves-checks` workflow in THIS repository (not a fork) that finished with conclusion `success` (all jobs,
including the tests and the installer smoke, were green). Anything else returns "none" and the caller builds normally.
Needs the `gh` CLI with a token that can read Actions (`actions: read`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from typing import Callable

WORKFLOW_NAME = "jarves-checks"
PREFIX = "jarves-installer-"
PROOF_PREFIX = "jarves-no-installer-proof-"  # test-only PRs, no binary; main must still build installer


def artifact_name(tree: str) -> str:
    return PREFIX + tree


def gh_json(path: str) -> dict:
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def resolve(repo: str, tree: str, api: Callable[[str], dict] = gh_json) -> dict | None:
    """The newest trustworthy artifact for `tree`, or None."""
    if not tree or len(tree) < 20:
        return None
    arts = api(f"repos/{repo}/actions/artifacts?name={artifact_name(tree)}&per_page=30").get("artifacts", [])
    for art in sorted(arts, key=lambda a: a.get("created_at", ""), reverse=True):
        if art.get("expired"):
            continue
        run_id = (art.get("workflow_run") or {}).get("id")
        if not run_id:
            continue
        run = api(f"repos/{repo}/actions/runs/{run_id}")
        head_repo = (run.get("head_repository") or {}).get("full_name")
        if (run.get("name") == WORKFLOW_NAME and run.get("status") == "completed" and run.get("conclusion") == "success"
                and head_repo == repo):
            return {"run_id": run_id, "artifact_id": art["id"], "name": art["name"], "event": run.get("event"),
                    "head_sha": run.get("head_sha"), "run_number": run.get("run_number"), "size": art.get("size_in_bytes")}
    return None



def resolve_tests(repo: str, tree: str, api: Callable[[str], dict] = gh_json) -> dict | None:
    """Green, same-repo PR test proof for *no-installer* changes; main still builds an installer.

    This artifact is uploaded only by a non-draft PR classified code=true, installer=false.
    The exact docs-free content tree must match, and the ENTIRE jarves-checks run must be
    green. A failed/cancelled PR, fork, expired artifact or missing proof never skips tests.
    It is NOT an installer and must never be passed to packaging/release.py.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", tree):
        return None
    name = PROOF_PREFIX + tree
    arts = api(f"repos/{repo}/actions/artifacts?name={name}&per_page=30").get("artifacts", [])
    for art in sorted(arts, key=lambda a: a.get("created_at", ""), reverse=True):
        if art.get("expired") or art.get("name") != name:
            continue
        run_id = (art.get("workflow_run") or {}).get("id")
        if not run_id:
            continue
        run = api(f"repos/{repo}/actions/runs/{run_id}")
        if (run.get("name") == WORKFLOW_NAME and run.get("event") in ("pull_request", "merge_group")
                and run.get("status") == "completed" and run.get("conclusion") == "success"
                and (run.get("head_repository") or {}).get("full_name") == repo):
            return {"run_id": run_id, "artifact_id": art["id"], "name": name,
                    "head_sha": run.get("head_sha")}
    return None


# Documentation does not change what is tested or built, so it is not part of the content address: a docs-only merge
# has the same "tree" as the previous code state and reuses its proof (same patterns as core/tools/ci_plan.py DOCS).
DOCS = re.compile(r"^(docs/|[^/]+\.md$|LICENSE$|\.gitignore$)")


def content_tree(entries: list[str]) -> str:
    """sha1 over the sorted `mode type blob<TAB>path` lines of `git ls-tree -r`, minus documentation paths."""
    keep = sorted(e for e in entries if e and not DOCS.match(e.split("\t", 1)[1]))
    return hashlib.sha1("\n".join(keep).encode("utf-8")).hexdigest()


def git_tree(rev: str = "HEAD") -> str:
    out = subprocess.run(["git", "ls-tree", "-r", "-z", rev], capture_output=True, text=True, check=True).stdout
    return content_tree(out.split("\0"))


def api_tree(repo: str, git_tree_sha: str, api: Callable[[str], dict] = gh_json) -> str | None:
    """The same content address from the GitHub git-trees API (no checkout needed), or None when GitHub truncates the
    listing. NOT the commit's git tree sha: artifacts are named by this docs-free tree."""
    doc = api(f"repos/{repo}/git/trees/{git_tree_sha}?recursive=1")
    if doc.get("truncated"):
        return None
    return content_tree([f"{e['mode']} {e['type']} {e['sha']}\t{e['path']}"
                         for e in doc.get("tree", []) if e.get("type") != "tree"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ci_reuse.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tree")
    t.add_argument("rev", nargs="?", default="HEAD")
    r = sub.add_parser("resolve")
    r.add_argument("--repo", required=True)
    r.add_argument("--tree", required=True)
    v = sub.add_parser("resolve-tests", help="Reuse green PR tests only; main still needs installer build")
    v.add_argument("--repo", required=True)
    v.add_argument("--tree", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "tree":
        print(git_tree(a.rev))
        return 0
    try:
        hit = resolve_tests(a.repo, a.tree) if a.cmd == "resolve-tests" else resolve(a.repo, a.tree)
    except RuntimeError as e:
        print(f"::warning::{e}", file=sys.stderr)
        hit = None
    if not hit:
        print("reused-tests=false" if a.cmd == "resolve-tests" else "reused=false")
        kind = "green PR test proof" if a.cmd == "resolve-tests" else "installer"
        print(f"No proven {kind} for tree {a.tree[:12]}: normal checks/build needed.", file=sys.stderr)
        return 4
    print("reused-tests=true" if a.cmd == "resolve-tests" else "reused=true")
    for k, v in hit.items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
