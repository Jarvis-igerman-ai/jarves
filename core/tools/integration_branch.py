"""Plan, build and close out ONE integration PR for several green PRs (merge steward helper).

Standard library + git + gh via subprocess. Network writes only through `gh pr create` (--open-pr), the normal
`git push` of the new branch (--open-pr), `gh pr comment/close` and the remote branch delete in close-superseded.
Never forces, never resolves conflicts: a conflicting PR is skipped with the reason.

    python core/tools/integration_branch.py plan 743 742 745      (or --auto)   -> JSON with a recommendation
    python core/tools/integration_branch.py build 743 742 745 [--open-pr] [--no-prepush]
    python core/tools/integration_branch.py close-superseded INTEGRATION_PR 743 742 745
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import re
import subprocess
import sys
from itertools import combinations
from pathlib import Path

REMOTE = "jarves"
BASE = f"{REMOTE}/main"
FRAGMENT = re.compile(r"^- [A-Z][A-Za-z]*: \S.*$")
FOOTER = "\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)"
MIN_INTEGRATION = 3
GOOD_MERGE_STATES = ("CLEAN", "BLOCKED", "HAS_HOOKS")


def _run(cmd, cwd=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(p.stderr or p.stdout).strip()[:400]}")
    return p


def gh_json(args, cwd=None):
    return json.loads(_run(["gh", *args], cwd).stdout or "null")


def _risk_module():
    spec = importlib.util.spec_from_file_location("jarves_risk_class_ib", Path(__file__).resolve().parent / "risk_class.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tier_of(files, labels) -> str:
    """R = blocking label or high-risk path; L = docs/tests/guides only; N = other code."""
    risk = _risk_module()
    if {x.lower() for x in labels} & set(risk.BLOCKING_LABELS) or any(risk.is_high_risk(f) and not risk.is_docs(f) for f in files):
        return "R"
    cls, _ = risk.classify(files, 0, set(), "", "true")
    return "L" if cls else "N"


def pr_info(number, cwd=None):
    d = gh_json(["pr", "view", str(number), "--json",
                 "number,title,headRefName,isDraft,labels,files,statusCheckRollup,mergeStateStatus,state"], cwd)
    files = [f["path"] for f in d.get("files") or []]
    labels = [x["name"] for x in d.get("labels") or []]
    return {"number": d["number"], "title": d["title"], "head": d["headRefName"], "draft": bool(d.get("isDraft")),
            "state": d.get("state"), "labels": labels, "files": files, "tier": tier_of(files, labels),
            "merge_state": d.get("mergeStateStatus"), "checks": d.get("statusCheckRollup") or []}


def checks_passed(checks) -> bool:
    if not checks:
        return False
    for c in checks:
        if (c.get("conclusion") or c.get("state") or "").upper() not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            return False
    return True


def eligible(info) -> bool:
    return (info["state"] == "OPEN" and not info["draft"] and checks_passed(info["checks"])
            and info["merge_state"] in GOOD_MERGE_STATES)


def overlap_pairs(infos):
    out = []
    for a, b in combinations(infos, 2):
        shared = sorted(set(a["files"]) & set(b["files"]))
        if shared:
            out.append({"prs": [a["number"], b["number"]], "files": shared})
    return out


def merge_tree_conflicts(a, b, cwd=None):
    """True/False from `git merge-tree --write-tree`, None when unavailable."""
    p = _run(["git", "merge-tree", "--write-tree", f"{REMOTE}/{a['head']}", f"{REMOTE}/{b['head']}"], cwd, check=False)
    return {0: False, 1: True}.get(p.returncode)


def recommend(infos, overlaps, conflicts):
    n = len(infos)
    if n < MIN_INTEGRATION:
        return "individual", f"only {n} eligible PR(s); need at least {MIN_INTEGRATION}"
    if overlaps or conflicts:
        return "individual", "PRs overlap on files or conflict, so they are not independent"
    extra = " (Tier R included: no-automerge + one Opus review of the combined diff)" if any(i["tier"] == "R" for i in infos) else ""
    return "integration", f"{n} independent eligible PRs{extra}"


def cmd_plan(a):
    cwd = a.repo
    numbers = a.prs
    if a.auto:
        numbers = [p["number"] for p in gh_json(["pr", "list", "--state", "open", "--json", "number", "--limit", "100"], cwd)]
    infos, rejected = [], []
    for n in numbers:
        info = pr_info(n, cwd)
        (infos if (a.prs and not a.auto) or eligible(info) else rejected).append(info)
    overlaps = overlap_pairs(infos)
    conflicts = [[x["number"], y["number"]] for x, y in combinations(infos, 2) if merge_tree_conflicts(x, y, cwd)]
    rec, why = recommend(infos, overlaps, conflicts)
    print(json.dumps({
        "prs": [{k: i[k] for k in ("number", "title", "head", "tier", "files")} for i in infos],
        "not_eligible": [{"number": i["number"], "reason": f"state={i['state']} draft={i['draft']} merge={i['merge_state']}"} for i in rejected],
        "conflict_forecast": {"file_overlap": overlaps, "merge_tree_conflicts": conflicts},
        "recommendation": rec, "reason": why, "needs_no_automerge": any(i["tier"] == "R" for i in infos)}, indent=2))
    return 0


def check_fragments(cwd):
    """Changelog fragments added on this branch; returns bad ones as (path, reason)."""
    names = _run(["git", "diff", "--name-only", "--diff-filter=A", BASE, "HEAD", "--", "changelog.d"], cwd).stdout.split()
    bad = []
    for name in names:
        lines = [x for x in Path(cwd or ".", name).read_text(encoding="utf-8").splitlines() if x.strip()]
        if len(lines) != 1 or not FRAGMENT.match(lines[0]):
            bad.append((name, "must be one line '- Kind: text'"))
    return bad


def branch_name(cwd):
    base = "integration-green-" + datetime.date.today().strftime("%m%d")
    name, n = base, 1
    while (_run(["git", "rev-parse", "--verify", "-q", name], cwd, check=False).returncode == 0
           or _run(["git", "ls-remote", "--exit-code", "--heads", REMOTE, name], cwd, check=False).returncode == 0):
        n += 1
        name = f"{base}-{n}"
    return name


def pr_body(included, skipped):
    rows = ["| PR | Title | Tier | Files |", "|---|---|---|---|"]
    for i in included:
        rows.append(f"| #{i['number']} | {i['title'].replace('|', '/')} | {i['tier']} | {len(i['files'])} |")
    body = ["Integration of green PRs (one review, one CI run).", "", *rows, ""]
    if skipped:
        body += ["Skipped:", *[f"- #{n}: {why}" for n, why in skipped], ""]
    body += ["Supersedes: " + " ".join(f"#{i['number']}" for i in included), ""]
    if any(i["tier"] == "R" for i in included):
        body += ["Contains Tier R changes: label `no-automerge`; needs ONE independent Opus review of the combined diff.", ""]
    body.append(FOOTER)
    return "\n".join(body)


def cmd_build(a):
    cwd = a.repo
    _run(["git", "fetch", REMOTE, "main"], cwd)
    infos = [pr_info(n, cwd) for n in a.prs]
    for i in infos:
        _run(["git", "fetch", REMOTE, i["head"]], cwd)
    name = branch_name(cwd)
    _run(["git", "checkout", "-b", name, BASE], cwd)
    included, skipped = [], []
    for i in infos:
        p = _run(["git", "merge", "--no-ff", "--no-edit", f"{REMOTE}/{i['head']}"], cwd, check=False)
        if p.returncode != 0:
            _run(["git", "merge", "--abort"], cwd, check=False)
            skipped.append((i["number"], "merge conflict: " + (p.stdout.strip().splitlines() or ["?"])[-1][:200]))
        else:
            included.append(i)
    bad = check_fragments(cwd)
    if bad:
        print(json.dumps({"branch": name, "error": "bad changelog fragment", "fragments": bad}), file=sys.stderr)
        return 2
    if not included:
        print(json.dumps({"branch": name, "error": "nothing could be merged", "skipped": skipped}), file=sys.stderr)
        return 3
    if not a.no_prepush:
        if subprocess.run([sys.executable, "core/tools/prepush.py"], cwd=cwd).returncode != 0:
            print(json.dumps({"branch": name, "error": "prepush failed"}), file=sys.stderr)
            return 4
    body = pr_body(included, skipped)
    result = {"branch": name, "included": [i["number"] for i in included], "skipped": skipped,
              "no_automerge": any(i["tier"] == "R" for i in included)}
    if a.open_pr:
        _run(["git", "push", "-u", REMOTE, name], cwd)
        cmd = ["gh", "pr", "create", "--base", "main", "--head", name, "--title",
               "Integrate green PRs " + " ".join(f"#{i['number']}" for i in included), "--body", body]
        if result["no_automerge"]:
            cmd += ["--label", "no-automerge"]
        result["pr"] = _run(cmd, cwd).stdout.strip()
    else:
        result["body"] = body
    print(json.dumps(result, indent=2))
    return 0


def cmd_close(a):
    cwd = a.repo
    state = gh_json(["pr", "view", str(a.integration_pr), "--json", "state"], cwd).get("state")
    if state != "MERGED":
        print(f"refusing: integration PR #{a.integration_pr} is {state}, not MERGED", file=sys.stderr)
        return 2
    _run(["git", "fetch", REMOTE, "main"], cwd)
    for n in a.prs:
        info = gh_json(["pr", "view", str(n), "--json", "headRefName,state"], cwd)
        if info.get("state") == "OPEN":
            _run(["gh", "pr", "comment", str(n), "--body", f"Merged via #{a.integration_pr}"], cwd)
            _run(["gh", "pr", "close", str(n)], cwd)
        head = info["headRefName"]
        _run(["git", "fetch", REMOTE, head], cwd, check=False)
        if _run(["git", "merge-base", "--is-ancestor", f"{REMOTE}/{head}", BASE], cwd, check=False).returncode == 0:
            _run(["git", "push", REMOTE, "--delete", head], cwd, check=False)
            print(f"#{n}: closed, branch {head} deleted")
        else:
            print(f"#{n}: closed, branch {head} kept (tip is not an ancestor of main)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=None, help="working directory of the git repo (default: current)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("prs", nargs="*", type=int)
    p.add_argument("--auto", action="store_true")
    p.set_defaults(fn=cmd_plan)
    b = sub.add_parser("build")
    b.add_argument("prs", nargs="+", type=int)
    b.add_argument("--open-pr", action="store_true")
    b.add_argument("--no-prepush", action="store_true")
    b.set_defaults(fn=cmd_build)
    c = sub.add_parser("close-superseded")
    c.add_argument("integration_pr", type=int)
    c.add_argument("prs", nargs="+", type=int)
    c.set_defaults(fn=cmd_close)
    a = ap.parse_args(argv)
    if a.cmd == "plan" and not (a.prs or a.auto):
        ap.error("plan needs PR numbers or --auto")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
