"""Publish + retention for the Windows installer (.github/workflows/package.yml). Python stdlib only.

  verify --dir DIST --commit SHA        build-info.json matches the tested commit, channel "main", and the SHA256
                                        of the installer matches both build-info.json and the .sha256 file
  notes  --dir DIST --run-url URL       release notes (version, commit, build time, SHA256, unsigned warning)
  prune  --keep 3 [--dry-run]           keep the newest 3 stable `win-v*` releases (+ their tags) and the newest 3
                                        `jarves-windows-installer-main` artifacts; delete older ones

Retention never touches: drafts, releases with other tag prefixes (e.g. future `pi-v*`), any release whose title
or notes contain "[pinned]" (rollback pins; they also do not count towards the 3), and any other artifact name.
Needs GITHUB_TOKEN (contents: write, actions: write) and GITHUB_REPOSITORY.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

TAG_PREFIX = "win-v"
ARTIFACT = "jarves-windows-installer-main"
PIN = "[pinned]"


def version_key(tag: str) -> tuple[int, ...]:
    m = re.fullmatch(rf"{re.escape(TAG_PREFIX)}(\d+(?:\.\d+){{0,3}})", tag or "")
    if not m:
        return ()
    parts = tuple(int(x) for x in m.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def is_pinned(r: dict) -> bool:
    return PIN in (r.get("name") or "") or PIN in (r.get("body") or "")


def releases_to_prune(releases: list[dict], keep: int = 3) -> list[dict]:
    """Stable Windows releases beyond the newest `keep` (by version), excluding drafts and pinned ones."""
    ours = [r for r in releases if not r.get("draft") and version_key(r.get("tag_name", "")) and not is_pinned(r)]
    ours.sort(key=lambda r: (version_key(r["tag_name"]), r.get("created_at") or ""), reverse=True)
    return ours[keep:]


def artifacts_to_prune(artifacts: list[dict], keep: int = 3, name: str = ARTIFACT) -> list[dict]:
    ours = [a for a in artifacts if a.get("name") == name and not a.get("expired")]
    ours.sort(key=lambda a: a.get("created_at") or "", reverse=True)
    return ours[keep:]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(dist: Path, commit: str) -> dict:
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8-sig"))
    problems = []
    if info.get("commit") != commit:
        problems.append(f"commit {info.get('commit')} != tested {commit}")
    if info.get("channel") != "main":
        problems.append(f"channel {info.get('channel')!r} is not 'main' (PR/dev builds are never published)")
    exe = dist / str(info.get("installer"))
    if not exe.is_file():
        problems.append(f"installer {exe.name} missing")
    else:
        got = sha256(exe)
        side = (dist / (exe.name + ".sha256")).read_text(encoding="ascii").split()[0] \
            if (dist / (exe.name + ".sha256")).is_file() else None
        if got != info.get("sha256") or got != side:
            problems.append("SHA256 of the installer does not match build-info.json / .sha256")
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", str(info.get("version"))):
        problems.append(f"bad version {info.get('version')!r}")
    if problems:
        raise SystemExit("VERIFY FAILED: " + "; ".join(problems))
    return info


def notes(info: dict, run_url: str) -> str:
    return (f"JARVES {info['version']} for Windows (64-bit), stable channel.\n\n"
            f"- Commit: {info['commit']}\n- Built: {info['built']} (UTC)\n"
            f"- Installer: `{info['installer']}` ({round(info['size'] / 1048576)} MB)\n"
            f"- SHA256: `{info['sha256']}`\n- Python {info.get('python')}, Godot {info.get('godot')}\n"
            f"- CI run (all checks green, silent install/uninstall smoke passed): {run_url}\n\n"
            "Per-user install, no admin. Your data stays in %LOCALAPPDATA%\\jarves and is kept on upgrade/uninstall.\n"
            "**Not code-signed yet**: Windows SmartScreen may say \"Windows protected your PC\" -> More info -> Run "
            "anyway. Check the SHA256 above first (PowerShell: `Get-FileHash JARVES-Setup-*.exe`).\n"
            "Music needs Pear Desktop (separate, not bundled).\n\n"
            f"Only the newest 3 releases are kept; add `{PIN}` to a release's notes to keep it as a rollback point.\n")


# ---------------------------------------------------------------------- GitHub REST (urllib)
def api(method: str, path: str) -> object:
    repo = os.environ["GITHUB_REPOSITORY"]
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}{path}", method=method, headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "jarves-release"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return json.loads(body) if body else None


def paged(path: str, key: str | None = None) -> list[dict]:
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        data = api("GET", f"{path}{sep}per_page=100&page={page}")
        items = data[key] if key else data
        out += items
        if len(items) < 100:
            return out
        page += 1


def prune(keep: int, dry: bool) -> int:
    for r in releases_to_prune(paged("/releases"), keep):
        print(f"{'would delete' if dry else 'delete'} release {r['tag_name']} ({r.get('created_at')})")
        if not dry:
            api("DELETE", f"/releases/{r['id']}")
            try:
                api("DELETE", f"/git/refs/tags/{r['tag_name']}")
            except urllib.error.HTTPError as e:
                print(f"  tag {r['tag_name']} not deleted: HTTP {e.code}")
    for a in artifacts_to_prune(paged(f"/actions/artifacts?name={ARTIFACT}", "artifacts"), keep):
        print(f"{'would delete' if dry else 'delete'} artifact {a['name']} #{a['id']} ({a.get('created_at')})")
        if not dry:
            api("DELETE", f"/actions/artifacts/{a['id']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--dir", type=Path, required=True)
    v.add_argument("--commit", required=True)
    n = sub.add_parser("notes")
    n.add_argument("--dir", type=Path, required=True)
    n.add_argument("--run-url", default="")
    p = sub.add_parser("prune")
    p.add_argument("--keep", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "verify":
        info = verify(a.dir, a.commit)
        print(f"VERIFY OK: {info['installer']} {info['version']} {info['sha256']}")
        print(f"version={info['version']}", file=open(os.environ["GITHUB_OUTPUT"], "a")
              if os.environ.get("GITHUB_OUTPUT") else sys.stdout)
        return 0
    if a.cmd == "notes":
        print(notes(json.loads((a.dir / "build-info.json").read_text(encoding="utf-8-sig")), a.run_url))
        return 0
    if a.keep < 1:
        raise SystemExit("--keep must be at least 1")
    return prune(a.keep, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
