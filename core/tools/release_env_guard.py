"""Refuse a real stable publish unless `stable-release` is a protected environment (#544).

GitHub creates an environment named by a job on first use WITHOUT protection rules, so `environment: stable-release`
alone does not keep RELEASES_TOKEN / JARVES_UPDATE_SIGNING_KEY behind the owner. This reads the environment and its
deployment policies over the GitHub API (read-only) and exits 1 unless it has a required reviewer AND a custom policy
made only of tag rules (at least one, no branch rule). That is docs/RELEASING.md "Owner settings", step 3; the tag
ruleset (step 2) and where the secrets live (step 4) cannot be read here. `can_admins_bypass: true` is a warning only.
Any API or parse failure also refuses (fail closed).

    python core/tools/release_env_guard.py --repo OWNER/REPO          # uses `gh api` (GH_TOKEN)
    python core/tools/release_env_guard.py --from-file env.json --policies-file policies.json   # offline check
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

ENV_NAME = "stable-release"


def problems(env: object, policies: object) -> list[str]:
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
    if not _custom(env):
        out.append("deployments are not limited to selected tags (add the tag rule v*.*.*)")
        return out  # the rule list only exists for a custom policy; it is not fetched then
    entries = policies.get("branch_policies") if isinstance(policies, dict) else None
    total = policies.get("total_count") if isinstance(policies, dict) else None
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        out.append("the deployment policy list is not readable")
    elif not isinstance(total, int) or isinstance(total, bool) or total != len(entries):
        out.append(f"the deployment policy list is incomplete ({len(entries)} of {total} rules read)")
    else:
        if not any(e.get("type") == "tag" for e in entries):
            out.append("no tag deployment rule is set (add the tag rule v*.*.*)")
        branches = [str(e.get("name")) for e in entries if e.get("type") != "tag"]
        if branches:
            out.append(f"branch deployment rules are allowed ({', '.join(branches)[:200]}); keep only the tag rule")
    return out


def _custom(env: dict) -> bool:
    policy = env.get("deployment_branch_policy")
    return isinstance(policy, dict) and policy.get("custom_branch_policies") is True


def warnings(env: object) -> list[str]:
    if isinstance(env, dict) and env.get("can_admins_bypass") is not False:
        return ["administrators may bypass the required reviewer (untick 'Allow administrators to bypass')"]
    return []


def _gh(path: str) -> object:
    res = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"gh api {path} failed ({res.returncode}): {res.stderr.strip()[:300]}")
    return json.loads(res.stdout)


def _load(path: str) -> object:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--repo")
    src.add_argument("--from-file")
    ap.add_argument("--policies-file", help="with --from-file: saved deployment-branch-policies response")
    args = ap.parse_args(argv)
    if args.from_file and not args.policies_file:
        ap.error("--from-file needs --policies-file")
    try:
        if args.from_file:
            env, policies = _load(args.from_file), _load(args.policies_file)
        else:
            base = f"repos/{args.repo}/environments/{ENV_NAME}"
            env = _gh(base)
            # one page of up to 100 rules; problems() refuses when total_count says more exist
            policies = _gh(f"{base}/deployment-branch-policies?per_page=100") if isinstance(env, dict) and _custom(env) else None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"::error::cannot read the {ENV_NAME} environment, refusing the publish (#544): {exc}")
        return 1
    found = problems(env, policies)
    for p in found:
        print(f"::error::{ENV_NAME} is not protected, refusing the publish (#544): {p}")
    for w in warnings(env):
        print(f"::warning::{ENV_NAME}: {w}")
    if not found:
        print(f"{ENV_NAME}: required reviewer and tag-only deployment policy present")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
