"""One-command rollback of a published release (docs/RELEASING.md, "Rollback").

    python tools/release_rollback.py --to win-v4.0.0.12                     # DRY RUN (default): prints the plan
    python tools/release_rollback.py --to win-v4.0.0.12 --execute           # does it (needs RELEASES_TOKEN)
    python tools/release_rollback.py --channel beta --to beta-v4.1.0.55 [--execute]

Stable: the target release's own signed latest.json (every stable release carries its manifest) becomes the manifest
JARVES downloads from `releases/latest/download/latest.json`: the target is marked "latest" and every newer stable
release is demoted to a pre-release (kept, not deleted, so it can be inspected or re-promoted by hand). Beta: the target
beta's signed latest-beta.json is copied onto the `beta-channel` pre-release. Before anything is changed the manifest is
downloaded and its Ed25519 signature is verified with the keys built into the app, so a manifest the app would refuse is
never published. JARVES copies already on the bad version are not downgraded silently: they run
`jarvis update --rollback` (the app keeps the previous installer), and no copy is offered the bad version again.
Fix forward by publishing a higher version. Network: GitHub API with RELEASES_TOKEN only when --execute.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))
sys.path.insert(0, str(ROOT / "core" / "src"))
import release as rel  # noqa: E402


def _asset(r: dict, name: str) -> dict | None:
    return next((a for a in r.get("assets") or [] if a.get("name") == name), None)


def plan(releases: list[dict], target_tag: str, channel: str = "stable") -> dict:
    """What a rollback to `target_tag` would do, or {"problems": [...]}. Pure: takes the releases list."""
    prefix = rel.BETA_PREFIX if channel == "beta" else rel.TAG_PREFIX
    manifest = rel.BETA_MANIFEST if channel == "beta" else rel.MANIFEST
    problems: list[str] = []
    if not target_tag.startswith(prefix):
        problems.append(f"{target_tag!r} is not a {channel} release tag ({prefix}...)")
    target = next((r for r in releases if r.get("tag_name") == target_tag), None)
    if target is None:
        problems.append(f"release {target_tag} does not exist")
    elif target.get("draft"):
        problems.append(f"release {target_tag} is a draft")
    elif _asset(target, manifest) is None:
        problems.append(f"release {target_tag} has no {manifest} (it was published before manifests were attached)")
    if problems:
        return {"problems": problems}
    demote = []
    if channel == "stable":
        tv = rel.version_key(target_tag)
        demote = [r["tag_name"] for r in releases if not r.get("draft") and not r.get("prerelease")
                  and rel.version_key(r.get("tag_name", "")) and rel.version_key(r["tag_name"]) > tv]
    return {"problems": [], "channel": channel, "target": target_tag, "manifest": manifest,
            "manifest_url": _asset(target, manifest)["browser_download_url"], "demote": demote}


def describe(p: dict, repo: str) -> str:
    if p["channel"] == "stable":
        lines = [f"  make {p['target']} the latest stable release in {repo} (its {p['manifest']} is then served from "
                 f"releases/latest/download/{p['manifest']})"]
        lines += [f"  demote {t} to a pre-release (kept, not deleted)" for t in p["demote"]] or ["  (no newer stable release to demote)"]
    else:
        lines = [f"  copy the signed {p['manifest']} of {p['target']} onto the {rel.BETA_TAG} pre-release in {repo}"]
    return "\n".join(lines)


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "jarves-rollback"}), timeout=60) as r:
        return r.read()


def check_manifest(raw: bytes, channel: str, target_tag: str, public_keys) -> dict:
    """The verified manifest inside `raw`, or SystemExit. Uses the app's own verifier and release_from_manifest rules."""
    from jarves import updates
    env = json.loads(raw.decode("utf-8"))
    m = updates.verify_manifest(env, public_keys)
    updates.release_from_manifest(m, "https://example.invalid", "0.0.0.0", channel)         # shape + channel rules
    return m


def rollback(target_tag: str, repo: str, *, channel: str = "stable", execute: bool = False, gh=None, fetch_fn=fetch,
             public_keys=None, out=print) -> int:
    from jarves import updates
    keys = public_keys if public_keys is not None else updates.UPDATE_PUBLIC_KEYS
    gh = gh or (rel.GitHub(repo, os.environ.get("RELEASES_TOKEN", "").strip()) if execute or os.environ.get("RELEASES_TOKEN")
                else rel.GitHub(repo, ""))
    p = plan(gh.releases(), target_tag, channel)
    if p["problems"]:
        out("ROLLBACK REFUSED: " + "; ".join(p["problems"]))
        return 1
    raw = fetch_fn(p["manifest_url"])
    try:
        m = check_manifest(raw, channel, target_tag, keys)
    except Exception as e:                                           # noqa: BLE001 - any failure refuses the rollback
        out(f"ROLLBACK REFUSED: the {p['manifest']} of {target_tag} does not verify ({type(e).__name__}: {e})")
        return 1
    out(f"{'ROLLBACK' if execute else 'DRY RUN (nothing changed; add --execute)'} {channel} -> {target_tag} "
        f"(version {m.get('display_version') or m['version']}, sha256 {str(m['sha256'])[:12]}):")
    out(describe(p, repo))
    if not execute:
        return 0
    if not getattr(gh, "token", "x"):
        out("ROLLBACK REFUSED: RELEASES_TOKEN is not set")
        return 1
    if channel == "stable":
        target = next(r for r in gh.releases() if r["tag_name"] == target_tag)
        for t in p["demote"]:                                        # demote first so "latest" never points at a newer one
            r = next(x for x in gh.releases() if x["tag_name"] == t)
            gh.call("PATCH", f"/releases/{r['id']}", {"prerelease": True})
        gh.call("PATCH", f"/releases/{target['id']}", {"draft": False, "prerelease": False, "make_latest": "true"})
    else:
        chan = gh.call("GET", f"/releases/tags/{rel.BETA_TAG}", ok404=True)
        if chan is None:
            out(f"ROLLBACK REFUSED: there is no {rel.BETA_TAG} release")
            return 1
        gh.upload(chan, rel.BETA_MANIFEST, raw, "application/json")
    out(f"Rolled back {channel} to {target_tag}. Copies on the bad version: `jarvis update --rollback`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release_rollback.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--to", required=True, help="release tag to go back to, e.g. win-v4.0.0.12 or beta-v4.1.0.55")
    ap.add_argument("--channel", choices=["stable", "beta"], default="stable")
    ap.add_argument("--repo", default=os.environ.get("RELEASES_REPO") or rel.DEFAULT_RELEASES_REPO)
    ap.add_argument("--execute", action="store_true", help="really change the releases (default is a dry run)")
    a = ap.parse_args(argv)
    return rollback(a.to, a.repo, channel=a.channel, execute=a.execute)


if __name__ == "__main__":
    sys.exit(main())
