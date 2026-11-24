"""Refuse a real stable publish unless `stable-release` is a protected environment (#544).

GitHub creates an environment named by a job on first use WITHOUT protection rules, so `environment: stable-release`
alone does not keep RELEASES_TOKEN / JARVES_UPDATE_SIGNING_KEY behind the owner. This reads the environment over the
GitHub API (read-only) and exits 1 unless it has a required reviewer AND a custom deployment branch/tag policy
(the owner settings in docs/RELEASING.md, steps 3-4). Any API or parse failure also refuses (fail closed).

    python core/tools/release_env_guard.py --repo OWNER/REPO          # uses `gh api` (GH_TOKEN)
    python core/tools/release_env_guard.py --from-file env.json       # offline check of a saved response
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

ENV_NAME = "stable-release"


def problems(env: object) -> list[str]:
    """Reasons the environment does not protect the release secrets; empty means protected."""
    if not isinstance(env, dict):
        return ["the environment response is not a JSON object"]
    out = []
    if env.get("name") != ENV_NAME:
        out.append(f"the environment name is not {ENV_NAME}")
    rules = env.get("protection_rules")
    rules = rules if isinstance(rules, list) else []
    if not any(isinstance(r, dict) and r.get("type") == "required_reviewers" and r.get("reviewers") for r in rules):
        out.append("no required reviewer is set (Settings, Environments, Required reviewers)")
    policy = env.get("deployment_branch_policy")
    if not (isinstance(policy, dict) and policy.get("custom_branch_policies") is True):
        out.append("deployments are not limited to selected tags (add the tag rule v*.*.*)")
    return out


def _fetch(repo: str) -> object:
    res = subprocess.run(["gh", "api", f"repos/{repo}/environments/{ENV_NAME}"],
                         capture_output=True, text=True, timeout=60, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"gh api failed ({res.returncode}): {res.stderr.strip()[:300]}")
    return json.loads(res.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--repo")
    src.add_argument("--from-file")
    args = ap.parse_args(argv)
    try:
        if args.from_file:
            with open(args.from_file, encoding="utf-8") as fh:
                env = json.load(fh)
        else:
            env = _fetch(args.repo)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"::error::cannot read the {ENV_NAME} environment, refusing the publish (#544): {exc}")
        return 1
    found = problems(env)
    for p in found:
        print(f"::error::{ENV_NAME} is not protected, refusing the publish (#544): {p}")
    if not found:
        print(f"{ENV_NAME}: required reviewer and tag-only deployment policy present")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
