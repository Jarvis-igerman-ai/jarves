"""Publish + retention for the Windows installer (.github/workflows/package.yml). Python stdlib only.

  verify --dir DIST --commit SHA        build-info.json matches the tested commit, channel "main", and the SHA256
                                        of the installer matches both build-info.json and the .sha256 file
  notes  --dir DIST --run-url URL       release notes (version, commit, build time, SHA256, unsigned warning)
  prune  --keep 3 [--dry-run]           keep the newest 3 stable `win-v*` releases (+ their tags) and the newest 3
                                        `jarves-windows-installer-main` artifacts; delete older ones

Retention never touches: drafts, releases with other tag prefixes (e.g. future `pi-v*`), any release whose title
or notes contain "[pinned]" (rollback pins; they also do not count towards the 3), and any other artifact name.
Needs GITHUB_TOKEN (contents: write, actions: write) and GITHUB_REPOSITORY.

Release hosting outside the private repo (WP-O, docs/RELEASE_HOSTING.md): a separate PUBLIC GitHub repo that holds
only release files (default igalgerman/jarvis-releases, no source code). Free; no card.
  keygen --out FILE                     new Ed25519 signing key (PEM, for the CI secret) + the public key to embed
  public-publish --dir DIST [--keep 3]  release win-v<v> there (draft -> installer + .sha256 + signed latest.json ->
                                        published as latest), then keep the newest 3 (pinned exempt). Needs secrets
                                        RELEASES_TOKEN (fine-grained, Contents: write on that repo only) and
                                        JARVES_UPDATE_SIGNING_KEY; skips cleanly (exit 0, clear log) without them.

latest.json = {"format": 1, "manifest": {version, commit, built, sha256, size, installer, url, ...},
               "signature": {"alg": "ed25519", "key_id", "sig"}}; the signature covers SIGN_DOMAIN + the canonical
JSON of "manifest" (sorted keys, no spaces, ASCII). The app (core/src/jarves/updates.py) downloads
https://github.com/<repo>/releases/latest/download/latest.json anonymously, refuses an unsigned/mis-signed
manifest, then checks the installer's SHA256 + size from the signed manifest. Ed25519 uses `cryptography` when
importable, else the `openssl` CLI (OpenSSL 3, present on the Ubuntu runners).
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TAG_PREFIX = "win-v"
ARTIFACT = "jarves-windows-installer-main"
PIN = "[pinned]"
SIGN_DOMAIN = b"jarves-update-manifest-v1\n"      # same constant in core/src/jarves/updates.py
PUBLIC_SECRETS = ("RELEASES_TOKEN", "JARVES_UPDATE_SIGNING_KEY")
DEFAULT_RELEASES_REPO = "igalgerman/jarvis-releases"   # same default in core/src/jarves/updates.py
MANIFEST = "latest.json"


def version_key(tag: str) -> tuple[int, ...]:
    m = re.fullmatch(rf"{re.escape(TAG_PREFIX)}(\d+(?:\.\d+){{0,3}})", tag or "")
    if not m:
        return ()
    parts = tuple(int(x) for x in m.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def is_pinned(r: dict) -> bool:
    # The generated notes explain pinning as `[pinned]` (in backticks); that mention is not a pin, or every
    # release would be kept forever.
    text = f"{r.get('name') or ''}\n{r.get('body') or ''}".replace(f"`{PIN}`", "")
    return PIN in text


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


# ---------------------------------------------------------------------- public releases repo (GitHub REST, urllib)
class GitHubError(Exception):
    pass


class GitHub:
    """Minimal GitHub REST client for the PUBLIC releases repo, authenticated with RELEASES_TOKEN (fine-grained,
    Contents: write on that one repo). `api_base` is overridable for the offline tests' local stub."""

    def __init__(self, repo: str, token: str, api_base: str = "https://api.github.com"):
        self.repo, self.token, self.api_base = repo, token, api_base.rstrip("/")

    def call(self, method: str, url: str, data: bytes | dict | None = None, content_type: str = "application/json",
             ok404: bool = False) -> object:
        if not url.startswith("http"):
            url = f"{self.api_base}/repos/{self.repo}{url}"
        body = json.dumps(data).encode() if isinstance(data, dict) else data
        h = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "jarves-release"}
        if body is not None:
            h["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if ok404 and e.code == 404:
                return None
            msg = e.read()[:300].decode("utf-8", "replace")
            raise GitHubError(f"{method} {url.split('?')[0]}: HTTP {e.code} {msg}") from None
        return json.loads(raw) if raw else {}

    def releases(self) -> list[dict]:
        out, page = [], 1
        while True:
            items = self.call("GET", f"/releases?per_page=100&page={page}") or []
            out += items
            if len(items) < 100:
                return out
            page += 1

    def upload(self, rel: dict, name: str, data: bytes, content_type: str) -> None:
        for a in rel.get("assets") or []:
            if a.get("name") == name:                       # re-run: replace a half-uploaded asset
                self.call("DELETE", f"/releases/assets/{a['id']}")
        base = re.sub(r"\{.*\}$", "", rel["upload_url"])
        self.call("POST", f"{base}?name={urllib.parse.quote(name)}", data, content_type)


def public_notes(info: dict) -> str:
    return (f"JARVES {info['version']} for Windows (64-bit), stable channel. Built {info.get('built')} (UTC) from "
            f"commit {str(info.get('commit'))[:12]}.\n\n"
            f"- Installer: `{info['installer']}` ({round(info['size'] / 1048576)} MB)\n"
            f"- SHA256: `{info['sha256']}` (PowerShell: `Get-FileHash {info['installer']}`)\n"
            f"- `{MANIFEST}` is the Ed25519-signed update manifest JARVES checks before offering an update.\n\n"
            "Per-user install, no admin. **Not code-signed yet**: SmartScreen -> More info -> Run anyway.\n"
            f"Only the newest 3 releases are kept; `{PIN}` in a release's title or notes keeps it.\n")


# ---------------------------------------------------------------------- signed manifest (Ed25519)
def canonical(manifest: dict) -> bytes:
    """The exact bytes that are signed (same function in core/src/jarves/updates.py)."""
    return SIGN_DOMAIN + json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def key_id(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def _openssl(*args: str) -> bytes:
    return subprocess.run(["openssl", *args], check=True, capture_output=True).stdout


def ed25519_sign(message: bytes, private_pem: str) -> tuple[bytes, bytes]:
    """(signature, raw public key). The private key never leaves memory / a 0600 temp file that is deleted."""
    try:
        from cryptography.hazmat.primitives import serialization
        k = serialization.load_pem_private_key(private_pem.encode(), password=None)
        pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return k.sign(message), pub
    except ImportError:
        return openssl_sign(message, private_pem)


def openssl_sign(message: bytes, private_pem: str) -> tuple[bytes, bytes]:
    with tempfile.TemporaryDirectory() as d:
        kf, mf, sf = Path(d, "k.pem"), Path(d, "m.bin"), Path(d, "s.bin")
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(private_pem)
        mf.write_bytes(message)
        _openssl("pkeyutl", "-sign", "-inkey", str(kf), "-rawin", "-in", str(mf), "-out", str(sf))
        pub = _openssl("pkey", "-in", str(kf), "-pubout", "-outform", "DER")[-32:]
        return sf.read_bytes(), pub


def sign_manifest(manifest: dict, private_pem: str) -> dict:
    sig, pub = ed25519_sign(canonical(manifest), private_pem)
    return {"format": 1, "manifest": manifest,
            "signature": {"alg": "ed25519", "key_id": key_id(pub), "sig": base64.b64encode(sig).decode()}}


def keygen(out: Path) -> str:
    """Write a new private key (PEM) to `out` (refuses to overwrite) and return the public key (base64, 32 bytes)."""
    if out.exists():
        raise SystemExit(f"{out} exists; not overwriting a signing key")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        k = Ed25519PrivateKey.generate()
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except ImportError:
        _openssl("genpkey", "-algorithm", "ed25519", "-out", str(out))
        pub = _openssl("pkey", "-in", str(out), "-pubout", "-outform", "DER")[-32:]
    return base64.b64encode(pub).decode()


def build_manifest(info: dict, exe: Path, url: str, now: _dt.datetime | None = None) -> dict:
    return {"product": "jarves", "platform": "windows-x64", "channel": info.get("channel"),
            "version": info["version"], "commit": info.get("commit"), "built": info.get("built"),
            "installer": exe.name, "sha256": sha256(exe), "size": exe.stat().st_size, "url": url,
            "python": info.get("python"), "godot": info.get("godot"),
            "published": (now or _dt.datetime.now(_dt.timezone.utc)).isoformat(timespec="seconds")}


# ---------------------------------------------------------------------- publish to the public releases repo
def public_publish(dist: Path, keep: int = 3, env: dict | None = None, gh: GitHub | None = None) -> int:
    """Release the verified installer in the public releases repo: draft -> installer + .sha256 + signed
    latest.json -> published as "latest" -> prune to the newest `keep` (pinned exempt). Exit 0 + a clear log
    without the secrets (the private-repo release stays the fallback)."""
    env = os.environ if env is None else env
    missing = [k for k in PUBLIC_SECRETS if not str(env.get(k) or "").strip()]
    if len(missing) == len(PUBLIC_SECRETS):
        print("Public releases: SKIPPED - secrets RELEASES_TOKEN and JARVES_UPDATE_SIGNING_KEY are not set, so the "
              "private-repo release is the only download (set them up: docs/RELEASE_HOSTING.md)")
        return 0
    if missing:
        print(f"::warning::Public releases: SKIPPED - missing secret {', '.join(missing)} "
              "(docs/RELEASE_HOSTING.md); the private-repo release was still published")
        return 0
    repo = str(env.get("RELEASES_REPO") or "").strip() or DEFAULT_RELEASES_REPO
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise SystemExit(f"Public releases: bad RELEASES_REPO {repo!r}")
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8-sig"))
    exe = dist / str(info["installer"])
    if sha256(exe) != info.get("sha256"):
        raise SystemExit("Public releases: installer SHA256 does not match build-info.json; not uploading")
    gh = gh or GitHub(repo, env["RELEASES_TOKEN"].strip())
    tag = f"{TAG_PREFIX}{info['version']}"
    latest = gh.call("GET", "/releases/latest", ok404=True)
    newest = version_key((latest or {}).get("tag_name", ""))
    if newest and newest > version_key(tag):
        print(f"Public releases: {repo} already has the newer {latest['tag_name']}; not publishing {tag}")
        return 0
    url = f"https://github.com/{repo}/releases/download/{tag}/{exe.name}"
    envelope = sign_manifest(build_manifest(info, exe, url), env["JARVES_UPDATE_SIGNING_KEY"])
    rel = gh.call("GET", f"/releases/tags/{tag}", ok404=True)
    if rel is None:     # a draft first: nobody sees a release whose files are not all uploaded yet
        rel = gh.call("POST", "/releases", {"tag_name": tag, "name": f"JARVES {info['version']}",
                                            "body": public_notes(info), "draft": True, "prerelease": False})
    gh.upload(rel, exe.name, exe.read_bytes(), "application/vnd.microsoft.portable-executable")
    gh.upload(rel, exe.name + ".sha256", f"{info['sha256']}  {exe.name}\n".encode(), "text/plain")
    gh.upload(rel, MANIFEST, json.dumps(envelope, indent=1).encode(), "application/json")
    gh.call("PATCH", f"/releases/{rel['id']}", {"draft": False, "make_latest": "true"})
    print(f"Public releases: published {tag} in {repo} ({info['size']} bytes + signed {MANIFEST}, key "
          f"{envelope['signature']['key_id']}) -> https://github.com/{repo}/releases/latest/download/{MANIFEST}")
    for r in releases_to_prune(gh.releases(), keep):
        if r.get("tag_name") == tag:
            continue
        print(f"Public releases: delete old {r['tag_name']}")
        gh.call("DELETE", f"/releases/{r['id']}")
        if gh.call("DELETE", f"/git/refs/tags/{r['tag_name']}", ok404=True) is None:
            print(f"  tag {r['tag_name']} was already gone")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen")
    kg.add_argument("--out", type=Path, required=True)
    rp = sub.add_parser("public-publish")
    rp.add_argument("--dir", type=Path, required=True)
    rp.add_argument("--keep", type=int, default=3)
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
    if a.cmd == "keygen":
        pub = keygen(a.out)
        print(f"Private signing key written to {a.out} (keep it secret; it goes only into the GitHub secret "
              f"JARVES_UPDATE_SIGNING_KEY and your password manager).")
        print(f"Public key (not secret, embed in core/src/jarves/updates.py UPDATE_PUBLIC_KEYS): {pub}")
        print(f"Key id: {key_id(base64.b64decode(pub))}")
        return 0
    if a.cmd == "public-publish":
        if a.keep < 1:
            raise SystemExit("--keep must be at least 1")
        return public_publish(a.dir, a.keep)
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
