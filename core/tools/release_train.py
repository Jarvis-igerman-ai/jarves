"""The release train: promote, status, rollback (docs/RELEASE_TRAIN.md). One installer, three labels.

    python core/tools/release_train.py status [--json]                 # what is on each channel, what blocks promotion
    python core/tools/release_train.py promote beta [--from TAG] [--dry-run]
                                                                       # newest alpha that passed the extended gates
    python core/tools/release_train.py promote stable --version 4.1.0 [--dry-run] [--create-refs]
                                                                       # the signed-off beta, gated by release_gate.py
    python core/tools/release_train.py rollback alpha|beta|stable --to TAG [--execute]

ALPHA is published by package.yml after every green main (ENABLE_ALPHA_PUBLISH). BETA = promote: a new signed
latest-beta.json that points at the alpha's installer (no build, no upload). STABLE = promote: the signed-off beta's
installer (checked against the signed SHA256/size) becomes the stable release; needs docs/reviews/MANUAL_TEST_<v>.md and
passes core/tools/release_gate.py. Every promotion re-verifies the source manifest with the Ed25519 keys built into the
app and re-signs with JARVES_UPDATE_SIGNING_KEY. `--dry-run` (the default in the workflows) changes nothing.
Network: GitHub API with `gh`/RELEASES_TOKEN; the pure functions take plain data (unit-tested without network).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "packaging"))
sys.path.insert(0, str(ROOT / "core" / "src"))
import ci_reuse  # noqa: E402
import release as rel  # noqa: E402

CHECKS_REPO = "igalgerman/jarves"
# Extended gates a beta needs on top of the alpha's green jarves-checks: these jobs of the run that proved the tree must be
# green (a job skipped by the path filter did not apply to that change; the nightly below covers the whole tree), and
# the nightly (full suite x2 OS, flake detector, perf x3, canaries, redaction audit) must be green on the alpha or later.
EXTENDED_JOBS = ("Windows installer", "Performance gate", "Core-Godot E2E", "Python Core tests", "Real-browser tests")
NIGHTLY_WORKFLOW = "nightly.yml"
GateApi = Callable[[str], dict]


def gh_api(path: str) -> dict:
    import release_status
    return release_status.gh_json(path)  # type: ignore[return-value]


# ---------------------------------------------------------------------- the beta gate (pure: takes the API callable)
def beta_gate(repo: str, m: dict, api: GateApi = gh_api) -> list[str]:
    """Problems that stop the alpha with signed manifest `m` from becoming a beta. [] = may be promoted."""
    problems: list[str] = []
    sha, tree = str(m.get("commit") or ""), str(m.get("tree") or "")
    if not sha:
        return ["the alpha manifest names no commit"]
    proof = ci_reuse.resolve(repo, tree, api) if tree else None
    if proof is None:
        problems.append(f"no green jarves-checks run proves tree {tree[:12] or '?'} (artifact missing or expired)")
    else:
        jobs = api(f"repos/{repo}/actions/runs/{proof['run_id']}/jobs?per_page=100").get("jobs", [])
        for prefix in EXTENDED_JOBS:
            mine = [j for j in jobs if str(j.get("name", "")).startswith(prefix)]
            bad = [j for j in mine if j.get("conclusion") not in ("success", "skipped")]
            if bad:
                problems.append(f"gate job '{bad[0]['name']}' is {bad[0].get('conclusion')} in run {proof['run_id']}")
    runs = api(f"repos/{repo}/actions/workflows/{NIGHTLY_WORKFLOW}/runs?branch=main&status=completed&per_page=10").get("workflow_runs", [])
    if not runs:
        problems.append("no completed nightly run yet (full suite, perf x3, canaries)")
    else:
        newest = runs[0]
        if newest.get("conclusion") != "success":
            problems.append(f"the newest nightly is {newest.get('conclusion')}: {newest.get('html_url')}")
        else:
            cmp = api(f"repos/{repo}/compare/{sha}...{newest['head_sha']}")
            if cmp.get("status") not in ("identical", "ahead"):
                problems.append(f"the newest green nightly ({str(newest['head_sha'])[:12]}) does not contain the alpha commit "
                                f"{sha[:12]} yet; it qualifies after the next nightly")
    return problems


def pick_alpha(releases: list[dict], current_beta: dict | None, load: Callable[[dict], dict | None],
               gate: Callable[[dict], list[str]], limit: int = 10) -> tuple[dict | None, dict | None, list[str]]:
    """Newest alpha (newer than the current beta) whose verified manifest passes `gate`. -> (release, manifest, notes)."""
    c = rel.CHANNELS["alpha"]
    alphas = [r for r in releases if rel.channel_number(r, "alpha") and not r.get("draft")]
    alphas.sort(key=lambda r: (rel.version_key(rel.TAG_PREFIX + r["tag_name"][len(c["prefix"]):]),
                               rel.channel_number(r, "alpha")[1]), reverse=True)
    cur = rel.version_key(rel.TAG_PREFIX + current_beta["tag_name"][len(rel.BETA_PREFIX):]) if current_beta else ()
    notes: list[str] = []
    for r in alphas[:limit]:
        if cur and rel.version_key(rel.TAG_PREFIX + r["tag_name"][len(c["prefix"]):]) <= cur:
            notes.append(f"{r['tag_name']}: not newer than the current beta; nothing newer qualifies")
            break
        m = load(r)
        if m is None:
            notes.append(f"{r['tag_name']}: signed manifest does not verify")
            continue
        why = gate(m)
        if not why:
            return r, m, notes
        notes.append(f"{r['tag_name']}: " + "; ".join(why))
    return None, None, notes


def cmd_promote_beta(args, env=None, gh=None, fetch=None, api: GateApi = gh_api, keys=None, out=print) -> int:
    env = os.environ if env is None else env
    repo = args.releases_repo
    gh = gh or rel.GitHub(repo, str(env.get("RELEASES_TOKEN") or "").strip())
    fetch = fetch or rel._fetch_bytes
    releases = gh.releases()
    if args.source and args.source != "auto":
        got = rel.verified_alpha(releases, args.source, keys, fetch, out, repo)
        if got is None:
            return 1
        why = beta_gate(args.repo, got[1], api)
        if why:
            out("PROMOTE REFUSED (extended gates): " + "; ".join(why))
            return 1
        tag = args.source
    else:
        def load(r):
            got = rel.verified_alpha(releases, r["tag_name"], keys, fetch, lambda *_: None, repo)
            return got[1] if got else None
        r, m, notes = pick_alpha(releases, rel.newest_of(releases, "beta"), load, lambda mm: beta_gate(args.repo, mm, api))
        for n in notes:
            out("  skipped " + n)
        if r is None:
            out("Nothing to promote: no alpha newer than the current beta passes the extended gates.")
            return 0
        tag = r["tag_name"]
    return rel.promote_to_beta(tag, repo, env, gh, args.dry_run, fetch, keys, out)


# ---------------------------------------------------------------------- stable
def stable_blockers(root: Path, version: str) -> list[str]:
    """Local, offline part of the stable gate: the sign-off sheet and the checklist's open P0-P2 (what `status` shows)."""
    import release_gate
    from jarves import release_signoff as so
    probs: list[str] = []
    try:
        sheet = so.load(root, version)
        probs += [f"sign-off: {p}" for p in sheet.problems()]
    except so.SheetError as e:
        probs.append(str(e))
    cl = release_gate.find_checklist(root, version, None)
    if cl is None:
        probs.append("release checklist not found (docs/reviews/RELEASE_CHECKLIST_<version>.md)")
    else:
        probs += release_gate.open_p0_p2(cl.read_text(encoding="utf-8"))
    return probs


def signed_off_commit(root: Path, version: str) -> str | None:
    f = root / "docs" / "reviews" / f"MANUAL_TEST_{version}.md"
    if not f.is_file():
        return None
    m = re.search(r"(?m)^- Commit: ([0-9a-f]{40})", f.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def find_beta_for_commit(releases: list[dict], commit: str, keys, fetch, repo: str, out=print) -> tuple[dict, dict] | None:
    """The newest beta whose VERIFIED manifest carries the signed-off commit."""
    bs = [r for r in releases if rel.channel_number(r, "beta") and not r.get("draft")]
    bs.sort(key=lambda r: (rel.version_key(rel.TAG_PREFIX + r["tag_name"][len(rel.BETA_PREFIX):]),
                           rel.channel_number(r, "beta")[1]), reverse=True)
    for r in bs:
        got = rel.verified_beta(releases, r["tag_name"], keys, fetch, lambda *_: None, repo)
        if got and str(got[1].get("commit") or "") == commit:
            return got
    out(f"PROMOTE REFUSED: no beta carries the signed-off commit {commit[:12]}")
    return None


def cmd_promote_stable(args, env=None, gh=None, fetch=None, keys=None, gate=None, out=print, root: Path = ROOT) -> int:
    env = os.environ if env is None else env
    repo = args.releases_repo
    version = args.version
    if not re.fullmatch(r"\d+\.\d+\.\d+", version or ""):
        out("PROMOTE REFUSED: --version must be X.Y.Z")
        return 1
    commit = signed_off_commit(root, version)
    if not commit:
        out(f"PROMOTE REFUSED: docs/reviews/MANUAL_TEST_{version}.md has no signed-off commit (jarvis release signoff)")
        return 1
    gh = gh or rel.GitHub(repo, str(env.get("RELEASES_TOKEN") or "").strip())
    fetch = fetch or rel._fetch_bytes
    got = find_beta_for_commit(gh.releases(), commit, keys, fetch, repo, out)
    if got is None:
        return 1
    brel, m = got
    if not str(m["version"]).startswith(version + "."):
        out(f"PROMOTE REFUSED: the beta installer is {m['version']}, not {version}.<build>")
        return 1
    with tempfile.TemporaryDirectory() as td:
        dist = Path(td) / "dist"
        rel.stage_installer(m, dist, fetch)        # sha256 + size must equal the SIGNED values
        gate = gate or (lambda d: _run_gate(version, d, root))
        rc = gate(dist)
        if rc != 0:
            out(f"PROMOTE REFUSED: the stable gate failed for v{version}")
            return 1
        out(f"{'DRY RUN (nothing changed)' if args.dry_run else 'PROMOTE'} beta {m.get('display_version')} ({brel['tag_name']}) -> "
            f"stable {version} (win-v{m['version']}); same bytes, sha256 {m['sha256'][:12]}")
        if args.dry_run:
            return 0
        rc = rel.public_publish(dist, keep=3, env=env, gh=gh)
    if rc == 0 and args.create_refs:
        sha = _git("rev-parse", "HEAD")
        minor = ".".join(version.split(".")[:2])
        for ref in (f"refs/tags/v{version}", f"refs/heads/release/{minor}"):
            _create_ref(args.repo, ref, sha, out)
    return rc


def _git(*a: str) -> str:
    import subprocess
    return subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()


def _create_ref(repo: str, ref: str, sha: str, out=print) -> None:
    import subprocess
    r = subprocess.run(["gh", "api", "-X", "POST", f"repos/{repo}/git/refs", "-f", f"ref={ref}", "-f", f"sha={sha}"],
                       capture_output=True, text=True)
    out(f"{'created' if r.returncode == 0 else 'kept (exists or failed)'} {ref} at {sha[:12]}")


def _run_gate(version: str, dist: Path, root: Path) -> int:
    import release_gate
    sha = _git("rev-parse", "HEAD")
    return release_gate.main(["--tag", f"v{version}", "--sha", sha, "--verified-sha", sha, "--candidate-dir", str(dist),
                              "--repo-root", str(root), "--bug-tables", "docs/reviews/*BUG_TABLE*.md"])


# ---------------------------------------------------------------------- status
def _commit_of(r: dict) -> str:
    m = re.search(r"commit ([0-9a-f]{40})", str(r.get("body") or ""))
    return m.group(1) if m else ""


def train_status(releases: list[dict], gate_for_newest_alpha: list[str] | None = None, stable_version: str | None = None,
                 stable_problems: list[str] | None = None, flags: dict | None = None) -> dict:
    """What is on each channel and what blocks the next promotion. Pure."""
    def row(r):
        return None if r is None else {"tag": r["tag_name"], "name": r.get("name"), "commit": _commit_of(r)[:12],
                                       "published": r.get("created_at")}
    stable = next((r for r in sorted((r for r in releases if rel.version_key(r.get("tag_name", "")) and not r.get("draft")
                                      and not r.get("prerelease")), key=lambda r: rel.version_key(r["tag_name"]), reverse=True)), None)
    alpha, beta = rel.newest_of(releases, "alpha"), rel.newest_of(releases, "beta")
    blocking = {"alpha->beta": [], "beta->stable": []}
    if alpha is None:
        blocking["alpha->beta"].append("no alpha published (ENABLE_ALPHA_PUBLISH?)")
    elif beta is not None and rel.version_key(rel.TAG_PREFIX + alpha["tag_name"][len(rel.CHANNELS["alpha"]["prefix"]):]) <= \
            rel.version_key(rel.TAG_PREFIX + beta["tag_name"][len(rel.BETA_PREFIX):]):
        blocking["alpha->beta"].append("the newest alpha is already the beta (nothing to promote)")
    blocking["alpha->beta"] += gate_for_newest_alpha or []
    if beta is None:
        blocking["beta->stable"].append("no beta yet")
    blocking["beta->stable"] += stable_problems or []
    return {"alpha": row(alpha), "beta": row(beta), "stable": row(stable), "stable_next_version": stable_version,
            "flags": flags or {}, "blocking": blocking}


def render_status(s: dict) -> str:
    def line(name, r):
        return f"  {name:<7}{(r['name'] + '  (' + r['tag'] + ', commit ' + r['commit'] + ')') if r else 'none'}"
    out = ["Release train", line("alpha", s["alpha"]), line("beta", s["beta"]), line("stable", s["stable"])]
    out += [f"  {k:<28}{'ON' if v else 'off'}" for k, v in s["flags"].items()]
    for k, v in s["blocking"].items():
        out.append(f"BLOCKING {k}: " + ("nothing" if not v else ""))
        out += [f"  - {x}" for x in v]
    return "\n".join(out)


def cmd_status(args, api: GateApi = gh_api, gh=None, fetch=None, keys=None) -> int:
    gh = gh or rel.GitHub(args.releases_repo, "")
    releases = gh.releases()
    fetch = fetch or rel._fetch_bytes
    why: list[str] = []
    alpha = rel.newest_of(releases, "alpha")
    if alpha is not None:
        got = rel.verified_alpha(releases, alpha["tag_name"], keys, fetch, lambda *_: None, args.releases_repo)
        why = beta_gate(args.repo, got[1], api) if got else ["the newest alpha's signed manifest does not verify"]
    flags = {}
    for v in ("ENABLE_ALPHA_PUBLISH", "ENABLE_AUTO_PROMOTE"):
        try:
            flags[v] = str(api(f"repos/{args.repo}/actions/variables/{v}").get("value", "")).lower() == "true"
        except Exception:                                           # noqa: BLE001 - variable not set / no access
            flags[v] = False
    ver = args.version
    sp = stable_blockers(ROOT, ver) if ver else None
    s = train_status(releases, why, ver, sp, flags)
    print(json.dumps(s, indent=1) if args.json else render_status(s))
    return 1 if any(s["blocking"].values()) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release_train.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=CHECKS_REPO, help="the source repo whose CI proves the builds")
    ap.add_argument("--releases-repo", default=os.environ.get("RELEASES_REPO") or rel.DEFAULT_RELEASES_REPO)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("promote")
    p.add_argument("channel", choices=["beta", "stable"])
    p.add_argument("--from", dest="source", default="auto", help="beta: alpha release tag (default: newest qualifying)")
    p.add_argument("--version", help="stable: X.Y.Z whose sign-off sheet is complete")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--create-refs", action="store_true", help="stable: create tag vX.Y.Z and branch release/X.Y")
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.add_argument("--version", help="also check the stable blockers of X.Y.Z")
    r = sub.add_parser("rollback", help="re-point a channel to an older release (alias of release_rollback.py)")
    r.add_argument("channel", choices=["alpha", "beta", "stable"])
    r.add_argument("--to", required=True)
    r.add_argument("--execute", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "rollback":
        import release_rollback
        return release_rollback.rollback(a.to, a.releases_repo, channel=a.channel, execute=a.execute)
    return cmd_promote_beta(a) if a.channel == "beta" else cmd_promote_stable(a)


if __name__ == "__main__":
    sys.exit(main())
