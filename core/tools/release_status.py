"""One screen that answers "can we release, and what is in the way?".

    python core/tools/release_status.py [--repo igalgerman/jarves] [--releases-repo igalgerman/jarvis-releases] [--json]

Prints, for the head of `main`: the latest jarves-checks run (and whether it reused the PR proof), the installer artifact
for the head tree (content-addressed, core/tools/ci_reuse.py), the jarves-package run and its signed release candidate,
the newest beta and stable releases, the stable-gate inputs, and a BLOCKING list. Exit code 0 = nothing blocks a beta,
1 = something blocks. Read-only; needs the `gh` CLI logged in. `build_status()` is pure and unit-tested with a fake `gh`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_reuse  # noqa: E402


def gh_json(path: str) -> object:
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or f"gh api {path} failed")
    return json.loads(r.stdout)


def _try(api: Callable[[str], object], path: str, default: object) -> object:
    try:
        return api(path)
    except RuntimeError as e:
        return {"_error": str(e)} if isinstance(default, dict) else default


def _run_line(run: dict | None) -> str:
    if not run:
        return "none"
    return f"#{run.get('run_number')} {run.get('status')}/{run.get('conclusion') or '-'} {run.get('html_url', '')}"


def build_status(repo: str, releases_repo: str, api: Callable[[str], object] = gh_json) -> dict:
    blocking: list[str] = []
    head = api(f"repos/{repo}/commits/main")
    sha, tree = head["sha"], head["commit"]["tree"]["sha"]
    runs = api(f"repos/{repo}/actions/runs?branch=main&event=push&head_sha={sha}&per_page=20").get("workflow_runs", [])
    checks = next((r for r in runs if r.get("name") == "jarves-checks"), None)
    package = next((r for r in runs if r.get("name") == "jarves-package"), None)
    if package is None:        # workflow_run-triggered runs carry the head sha too, but list them separately
        pr = api(f"repos/{repo}/actions/workflows/package.yml/runs?head_sha={sha}&per_page=5").get("workflow_runs", [])
        package = pr[0] if pr else None
    if checks is None:
        blocking.append("main CI: no jarves-checks run for the head commit yet")
    elif checks.get("status") != "completed":
        blocking.append(f"main CI is still {checks.get('status')}: {checks.get('html_url')}")
    elif checks.get("conclusion") != "success":
        blocking.append(f"main CI {checks.get('conclusion')}: {checks.get('html_url')}")
    try:
        installer = ci_reuse.resolve(repo, tree, api)
    except RuntimeError:
        installer = None
    if installer is None:
        blocking.append(f"no proven installer artifact for the head tree {tree[:12]} (jarves-installer-<tree>): "
                        "no green run built it, or it expired (3-30 days); re-run jarves-checks")
    cand = None
    arts = api(f"repos/{repo}/actions/artifacts?name=jarves-release-candidate-{sha}&per_page=5").get("artifacts", [])
    cand = next((a for a in arts if not a.get("expired")), None)
    if cand is None:
        blocking.append("no signed release candidate for the head commit (jarves-package has not produced one): "
                        "gh workflow run package.yml -f ref=main")
    rels = _try(api, f"repos/{releases_repo}/releases?per_page=30", [])
    rels = rels if isinstance(rels, list) else []
    stable = next((r for r in rels if not r.get("prerelease") and not r.get("draft")), None)
    beta = next((r for r in rels if r.get("prerelease") and str(r.get("tag_name", "")).startswith("beta-v")), None)
    assets = {a["name"] for r in (stable, beta) if r for a in r.get("assets", [])}
    variables = _try(api, f"repos/{repo}/actions/variables/ENABLE_BETA_PUBLISH", {})
    beta_on = isinstance(variables, dict) and str(variables.get("value", "")).lower() == "true"
    notes = []
    if not beta_on:
        notes.append("ENABLE_BETA_PUBLISH is not 'true': green main makes a candidate but publishes no beta")
    if stable is not None and "latest.json" not in assets:
        notes.append("the newest stable release has no latest.json (signed manifest) asset")
    return {"repo": repo, "head": sha, "tree": tree,
            "main_ci": _run_line(checks), "package": _run_line(package),
            "installer_artifact": installer, "candidate_artifact": (cand or {}).get("name"),
            "stable": (stable or {}).get("tag_name"), "beta": (beta or {}).get("tag_name"),
            "beta_publishing": beta_on, "notes": notes, "blocking": blocking,
            "stable_gate": "manual: tag vX.Y.Z after docs/reviews/MANUAL_TEST_<X.Y.Z>.md is signed (core/tools/release_gate.py)"}


def render(s: dict) -> str:
    inst = s["installer_artifact"]
    lines = [f"JARVES release status  ({s['repo']}, main {s['head'][:12]}, tree {s['tree'][:12]})",
             f"  main CI            {s['main_ci']}",
             f"  installer artifact {('run ' + str(inst['run_id']) + ' #' + str(inst['run_number']) + ' (' + str(inst['event']) + ')') if inst else 'MISSING'}",
             f"  package/candidate  {s['package']}  artifact: {s['candidate_artifact'] or 'MISSING'}",
             f"  beta channel       {s['beta'] or 'none'}   (publishing {'ON' if s['beta_publishing'] else 'off'})",
             f"  stable channel     {s['stable'] or 'none'}",
             f"  stable gate        {s['stable_gate']}"]
    lines += [f"  note: {n}" for n in s["notes"]]
    lines.append("BLOCKING: " + ("nothing" if not s["blocking"] else ""))
    lines += [f"  - {b}" for b in s["blocking"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release_status.py")
    ap.add_argument("--repo", default="igalgerman/jarves")
    ap.add_argument("--releases-repo", default="igalgerman/jarvis-releases")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        s = build_status(a.repo, a.releases_repo)
    except RuntimeError as e:
        print(f"release_status: {e}", file=sys.stderr)
        return 2
    print(json.dumps(s, indent=1) if a.json else render(s))
    return 1 if s["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
