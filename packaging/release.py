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

Channels (docs/RELEASING.md): STABLE = tag-driven (`public-publish`, run by .github/workflows/release.yml after
core/tools/release_gate.py), manifest latest.json on the newest non-pre-release. BETA = after every green main
(`beta-publish`, .github/workflows/beta.yml; DISABLED unless ENABLE_BETA_PUBLISH=true): release `beta-v<build>` marked
pre-release (never "latest") plus the manifest latest-beta.json on the one rolling pre-release `beta-channel`.
`candidate` builds the signed manifest next to the tested installer as a workflow artifact and publishes nothing.
  bridge-publish --dir bridge/whatsapp/dist   the prebuilt WhatsApp bridge -> rolling release `whatsapp-bridge` (versioned exe +
                                        .sha256 + signed whatsapp-bridge.json, own signing domain; same secrets)
`--dry-run` on public-publish / beta-publish / bridge-publish prints what would be published and touches nothing.

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
BRIDGE_SIGN_DOMAIN = b"jarves-whatsapp-bridge-manifest-v1\n"   # same constant in core/src/jarves/whatsapp_bridge_install.py
BRIDGE_MANIFEST = "whatsapp-bridge.json"
BRIDGE_TAG = "whatsapp-bridge"               # rolling release of the public repo that holds the prebuilt WhatsApp bridge
BRIDGE_EXE = "whatsapp_bridge.exe"
PUBLIC_SECRETS = ("RELEASES_TOKEN", "JARVES_UPDATE_SIGNING_KEY")
DEFAULT_RELEASES_REPO = "igalgerman/jarvis-releases"   # same default in core/src/jarves/updates.py
MANIFEST = "latest.json"
BETA_MANIFEST = "latest-beta.json"           # same names in core/src/jarves/updates.py
BETA_TAG = "beta-channel"                    # the rolling pre-release that only carries latest-beta.json
BETA_PREFIX = "beta-v"                       # one pre-release per published beta: beta-v<numeric build version>
BETA_KEEP = 5


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


def verify(dist: Path, commit: str | None = None, tree: str | None = None) -> dict:
    """Check the installer in `dist`. Either it was built from exactly `commit` on main, or (reuse of the PR's
    installer, docs/CI_PERFORMANCE.md) it was built from a checkout whose git TREE equals `tree`: the code is
    byte-identical, only the commit that carries it differs. The caller proves that the producing run was a green
    jarves-checks run of this repository (core/tools/ci_reuse.py)."""
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8-sig"))
    problems = []
    if info.get("build_profile", "release") != "release":
        problems.append("CI smoke builds are never published")
    if tree:
        if info.get("tree") != tree:
            problems.append(f"tree {info.get('tree')} != published tree {tree}")
        if info.get("channel") not in ("main", "pr"):
            problems.append(f"channel {info.get('channel')!r} is not 'main' or 'pr' (dev builds are never published)")
    else:
        if not commit or info.get("commit") != commit:
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


def stamp(dist: Path, commit: str, tree: str) -> dict:
    """Manifest-level stamping of a REUSED installer: the executable is never touched (no rebuild). The version is the
    one already in the installer (`X.Y.Z.<run number of the build>`; run numbers only grow, and a PR is only merged
    after being tested on top of the current main, so merged builds are strictly increasing). What changes is the
    metadata that describes it: `commit` becomes the commit on main that carries the tree, the commit the installer was
    really built from is kept as `built_from_commit`, and the channel becomes `main`. Writes dist/build-info.json."""
    path = dist / "build-info.json"
    info = verify(dist, None, tree)
    if info.get("commit") != commit:
        info["built_from_commit"] = info.get("commit")
        info["commit"] = commit
    info["channel"] = "main"
    info["tree"] = tree
    path.write_text(json.dumps(info, indent=1), encoding="utf-8")
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
def canonical(manifest: dict, domain: bytes = SIGN_DOMAIN) -> bytes:
    """The exact bytes that are signed (same function in core/src/jarves/updates.py)."""
    return domain + json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def key_id(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def openssl_exe() -> str | None:
    """The openssl CLI: $JARVES_OPENSSL, PATH, else the copy bundled with Git for Windows (always on the runners)."""
    import shutil
    found = os.environ.get("JARVES_OPENSSL") or shutil.which("openssl")
    if found:
        return found
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")):
        for sub in (r"Git\usr\bin\openssl.exe", r"Git\mingw64\bin\openssl.exe"):
            if root and Path(root, sub).is_file():
                return str(Path(root, sub))
    return None


def _openssl(*args: str) -> bytes:
    return subprocess.run([openssl_exe() or "openssl", *args], check=True, capture_output=True).stdout


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


def sign_manifest(manifest: dict, private_pem: str, domain: bytes = SIGN_DOMAIN) -> dict:
    sig, pub = ed25519_sign(canonical(manifest, domain), private_pem)
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


def build_manifest(info: dict, exe: Path, url: str, now: _dt.datetime | None = None,
                   previous: dict | None = None) -> dict:
    m = {"product": "jarves", "platform": "windows-x64", "channel": info.get("channel"),
         "version": info["version"], "commit": info.get("commit"), "built": info.get("built"),
         "installer": exe.name, "sha256": sha256(exe), "size": exe.stat().st_size, "url": url,
         "python": info.get("python"), "godot": info.get("godot"),
         "published": (now or _dt.datetime.now(_dt.timezone.utc)).isoformat(timespec="seconds")}
    if previous:                       # SW-V4 L: lets `jarvis update --rollback` fetch the version before this one
        m["previous"] = previous
    return m


def previous_entry(releases: list[dict], tag: str, repo: str) -> dict | None:
    """The newest stable release older than `tag` with an installer asset whose SHA256 GitHub reports (asset digest):
    {version, installer, sha256, size, url}. None when there is no such release (the manifest then has no block)."""
    cur = version_key(tag)
    best = None
    for r in releases:
        t = str(r.get("tag_name") or "")
        v = version_key(t)
        if r.get("draft") or r.get("prerelease") or not t.startswith(TAG_PREFIX) or not v or v >= cur:
            continue
        for a in r.get("assets") or []:
            m = re.fullmatch(r"JARVES-Setup-(\d+(?:\.\d+){1,3})\.exe", str(a.get("name") or ""))
            digest = str(a.get("digest") or "")
            if not m or not digest.startswith("sha256:") or not isinstance(a.get("size"), int):
                continue
            if best is None or v > best[0]:
                best = (v, {"version": m.group(1), "installer": a["name"], "sha256": digest.split(":", 1)[1].lower(),
                            "size": a["size"], "url": f"https://github.com/{repo}/releases/download/{t}/{a['name']}"})
    return best[1] if best else None


# ---------------------------------------------------------------------- publish to the public releases repo
def public_publish(dist: Path, keep: int = 3, env: dict | None = None, gh: GitHub | None = None,
                   dry_run: bool = False) -> int:
    """Release the verified installer in the public releases repo: draft -> installer + .sha256 + signed
    latest.json -> published as "latest" -> prune to the newest `keep` (pinned exempt). Exit 0 + a clear log
    without the secrets (the private-repo release stays the fallback)."""
    env = os.environ if env is None else env
    if dry_run:
        return _dry_run_plan(dist, env, "stable")
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
    prev = previous_entry(gh.releases(), tag, repo)
    envelope = sign_manifest(build_manifest(info, exe, url, previous=prev), env["JARVES_UPDATE_SIGNING_KEY"])
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


# ---------------------------------------------------------------------- prebuilt WhatsApp bridge (issue #17)
def bridge_manifest(info: dict, exe: Path, url: str, now: _dt.datetime | None = None) -> dict:
    """The manifest the app verifies before it installs the bridge (core/src/jarves/whatsapp_bridge_install.py)."""
    return {"product": "jarves-whatsapp-bridge", "platform": "windows-x64", "asset": BRIDGE_EXE,
            "version": str(info["version"]), "commit": info.get("commit"), "go": info.get("go"),
            "whatsmeow": info.get("whatsmeow"), "sha256": sha256(exe), "size": exe.stat().st_size, "url": url,
            "published": (now or _dt.datetime.now(_dt.timezone.utc)).isoformat(timespec="seconds")}


def _read_bridge(dist: Path) -> tuple[dict, Path]:
    """bridge/whatsapp/dist as left by scripts/build_whatsapp_bridge.ps1: the exe must equal its .sha256 and
    build-info.json (the same reproducible-build evidence payload_check.py demands)."""
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8-sig"))
    exe = dist / BRIDGE_EXE
    want = (dist / (BRIDGE_EXE + ".sha256")).read_text(encoding="utf-8-sig").split()[0].lower()
    got = sha256(exe)
    if got != want or got != str(info.get("sha256") or "").lower():
        raise SystemExit("bridge exe SHA256 does not match its .sha256 / build-info.json; not publishing")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?", str(info.get("version") or "")):
        raise SystemExit("bridge build-info.json has no valid version; not publishing")
    return info, exe


def bridge_publish(dist: Path, env: dict | None = None, gh: GitHub | None = None, dry_run: bool = False) -> int:
    """Publish the prebuilt bridge to the rolling release `whatsapp-bridge` of the public releases repo: the versioned
    exe + .sha256 first, the Ed25519-signed whatsapp-bridge.json LAST (the switch; the app never sees a manifest whose
    file is missing). Exit 0 with a clear log without the secrets. Never touches installer releases."""
    env = os.environ if env is None else env
    info, exe = _read_bridge(dist)
    repo = str(env.get("RELEASES_REPO") or "").strip() or DEFAULT_RELEASES_REPO
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise SystemExit(f"Bridge publish: bad RELEASES_REPO {repo!r}")
    asset = f"whatsapp_bridge-{info['version']}.exe"
    url = f"https://github.com/{repo}/releases/download/{BRIDGE_TAG}/{asset}"
    if dry_run:
        print(f"DRY RUN (bridge): nothing is published, no network call is made.\n  repo     {repo}\n"
              f"  release  {BRIDGE_TAG} (rolling pre-release)\n  files    {asset} ({exe.stat().st_size} bytes), {asset}.sha256, "
              f"{BRIDGE_MANIFEST} (Ed25519-signed, domain {BRIDGE_SIGN_DOMAIN.decode().strip()})\n  version  {info['version']}")
        return 0
    missing = [k for k in PUBLIC_SECRETS if not str(env.get(k) or "").strip()]
    if missing:
        print(f"::warning::Bridge publish: SKIPPED - missing secret {', '.join(missing)} (docs/RELEASE_HOSTING.md)")
        return 0
    gh = gh or GitHub(repo, env["RELEASES_TOKEN"].strip())
    envelope = sign_manifest(bridge_manifest(info, exe, url), env["JARVES_UPDATE_SIGNING_KEY"], BRIDGE_SIGN_DOMAIN)
    rel = gh.call("GET", f"/releases/tags/{BRIDGE_TAG}", ok404=True)
    if rel is None:
        rel = gh.call("POST", "/releases", {"tag_name": BRIDGE_TAG, "name": "JARVES WhatsApp bridge (prebuilt, signed)",
                                            "body": f"The prebuilt WhatsApp bridge. `{BRIDGE_MANIFEST}` is the Ed25519-signed "
                                                    "manifest JARVES verifies (SHA256 + size) before installing it.",
                                            "draft": False, "prerelease": True, "make_latest": "false"})
    gh.upload(rel, asset, exe.read_bytes(), "application/vnd.microsoft.portable-executable")
    gh.upload(rel, asset + ".sha256", f"{sha256(exe)}  {asset}\n".encode(), "text/plain")
    gh.upload(rel, BRIDGE_MANIFEST, json.dumps(envelope, indent=1).encode(), "application/json")      # last: the switch
    print(f"Bridge publish: published {info['version']} in {repo} (key {envelope['signature']['key_id']}) -> "
          f"https://github.com/{repo}/releases/download/{BRIDGE_TAG}/{BRIDGE_MANIFEST}")
    return 0


# ---------------------------------------------------------------------- beta channel + candidate + dry run
def beta_base(version: str) -> str:
    """'4.1.0.57' -> '4.1.0' (the X.Y.Z a beta is a pre-release of)."""
    return ".".join(version.split(".")[:3])


def beta_number(rel: dict) -> tuple[str, int] | None:
    """(X.Y.Z, N) from a beta release named 'JARVES X.Y.Z-beta.N'; None for anything else."""
    m = re.fullmatch(r"JARVES (\d+\.\d+\.\d+)-beta\.(\d+)", str(rel.get("name") or "").strip())
    if not m or not str(rel.get("tag_name") or "").startswith(BETA_PREFIX):
        return None
    return m.group(1), int(m.group(2))


def next_beta_number(releases: list[dict], base: str) -> int:
    """Beta counter per X.Y.Z base: 1 for the first beta of a base, else the highest published + 1."""
    ns = [b[1] for r in releases if (b := beta_number(r)) and b[0] == base]
    return max(ns, default=0) + 1


def beta_for_commit(releases: list[dict], commit: str) -> dict | None:
    """A beta release already built from this commit (re-run of the same green main is a no-op)."""
    for r in releases:
        if beta_number(r) and f"commit {commit}" in str(r.get("body") or ""):
            return r
    return None


def beta_manifest(info: dict, exe: Path, url: str, n: int, previous: dict | None, now: _dt.datetime | None = None) -> dict:
    m = build_manifest(info, exe, url, now=now, previous=previous)
    m["channel"] = "beta"
    m["beta"] = n
    m["display_version"] = f"{beta_base(info['version'])}-beta.{n}"
    return m


def previous_any(releases: list[dict], version: str, repo: str) -> dict | None:
    """The newest published release (stable `win-v` or beta `beta-v`) with a lower numeric version and a hashed
    installer asset, as the manifest's `previous` block (so a bad beta can always be rolled back)."""
    cur = version_key(f"{TAG_PREFIX}{version}")
    best = None
    for r in releases:
        t = str(r.get("tag_name") or "")
        pre = TAG_PREFIX if t.startswith(TAG_PREFIX) else BETA_PREFIX if t.startswith(BETA_PREFIX) else None
        v = version_key(TAG_PREFIX + t[len(pre):]) if pre else ()
        if r.get("draft") or not v or v >= cur:
            continue
        for a in r.get("assets") or []:
            m = re.fullmatch(r"JARVES-Setup-(\d+(?:\.\d+){1,3})\.exe", str(a.get("name") or ""))
            digest = str(a.get("digest") or "")
            if not m or not digest.startswith("sha256:") or not isinstance(a.get("size"), int):
                continue
            if best is None or v > best[0]:
                best = (v, {"version": m.group(1), "installer": a["name"], "sha256": digest.split(":", 1)[1].lower(),
                            "size": a["size"], "url": f"https://github.com/{repo}/releases/download/{t}/{a['name']}"})
    return best[1] if best else None


def beta_notes(info: dict, display: str) -> str:
    return (f"JARVES {display} for Windows (64-bit), BETA channel (early build, not manually tested). "
            f"Built {info.get('built')} (UTC) from commit {info.get('commit')}.\n\n"
            f"- Installer: `{info['installer']}` ({round(info['size'] / 1048576)} MB)\n"
            f"- SHA256: `{info['sha256']}`\n"
            f"- `{BETA_MANIFEST}` (on the `{BETA_TAG}` pre-release) is the Ed25519-signed manifest JARVES checks.\n\n"
            "Only installed by JARVES copies whose owner switched to the beta channel "
            "(`jarvis update --channel beta`). Stable users are never offered this.\n")


def _read_info(dist: Path) -> tuple[dict, Path]:
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8-sig"))
    exe = dist / str(info["installer"])
    if not exe.is_file() or sha256(exe) != info.get("sha256"):
        raise SystemExit("installer is missing or its SHA256 does not match build-info.json; not publishing")
    return info, exe


def _dry_run_plan(dist: Path, env: dict, channel: str) -> int:
    """Print exactly what a real publish would do. No secrets, no network, nothing written."""
    info, exe = _read_info(dist)
    repo = str(env.get("RELEASES_REPO") or "").strip() or DEFAULT_RELEASES_REPO
    have_key = bool(str(env.get("JARVES_UPDATE_SIGNING_KEY") or "").strip())
    if channel == "beta":
        tag, name, mf = f"{BETA_PREFIX}{info['version']}", f"JARVES {beta_base(info['version'])}-beta.<next N>", BETA_MANIFEST
        extra = f"  + pre-release {BETA_TAG}: replace asset {BETA_MANIFEST}\n  keeps the newest {BETA_KEEP} beta releases\n"
        on = str(env.get("ENABLE_BETA_PUBLISH")).strip().lower() == "true"
        flag = "ENABLE_BETA_PUBLISH is " + ("true" if on else "NOT true (a real run would publish nothing)")
    else:
        tag, name, mf, extra = f"{TAG_PREFIX}{info['version']}", f"JARVES {info['version']}", MANIFEST, ""
        flag = 'stable: made "latest"; keeps the newest 3'
    print(f"DRY RUN ({channel}): nothing is published, no network call is made.\n"
          f"  repo      {repo}\n  release   {tag}  ({name}){' pre-release' if channel == 'beta' else ''}\n"
          f"  files     {exe.name} ({info['size']} bytes), {exe.name}.sha256, {mf} (Ed25519-signed)\n{extra}"
          f"  commit    {info.get('commit')}\n  signing   {'key present' if have_key else 'JARVES_UPDATE_SIGNING_KEY not set here'}\n"
          f"  {flag}")
    return 0


def beta_publish(dist: Path, env: dict | None = None, gh: GitHub | None = None, dry_run: bool = False) -> int:
    """Publish the verified installer as the next beta: a pre-release `beta-v<build>` (installer + .sha256) and the
    signed latest-beta.json on the rolling pre-release `beta-channel`. Exit 0 with a clear log when disabled
    (ENABLE_BETA_PUBLISH is not "true") or when the secrets are missing; never touches stable releases."""
    env = os.environ if env is None else env
    if dry_run:
        return _dry_run_plan(dist, env, "beta")
    if str(env.get("ENABLE_BETA_PUBLISH") or "").strip().lower() != "true":
        print("Beta publish: DISABLED - ENABLE_BETA_PUBLISH is not 'true'; nothing is published "
              "(the owner enables it: docs/RELEASING.md)")
        return 0
    missing = [k for k in PUBLIC_SECRETS if not str(env.get(k) or "").strip()]
    if missing:
        print(f"::warning::Beta publish: SKIPPED - {len(missing)} required repository secret(s) not set (docs/RELEASE_HOSTING.md)")
        return 0
    repo = str(env.get("RELEASES_REPO") or "").strip() or DEFAULT_RELEASES_REPO
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise SystemExit(f"Beta publish: bad RELEASES_REPO {repo!r}")
    info, exe = _read_info(dist)
    gh = gh or GitHub(repo, env["RELEASES_TOKEN"].strip())
    releases = gh.releases()
    if beta_for_commit(releases, str(info.get("commit"))):
        print(f"Beta publish: commit {str(info.get('commit'))[:12]} already has a beta; nothing to do")
        return 0
    base = beta_base(info["version"])
    n = next_beta_number(releases, base)
    display = f"{base}-beta.{n}"
    tag = f"{BETA_PREFIX}{info['version']}"
    url = f"https://github.com/{repo}/releases/download/{tag}/{exe.name}"
    manifest = beta_manifest(info, exe, url, n, previous_any(releases, info["version"], repo))
    envelope = sign_manifest(manifest, env["JARVES_UPDATE_SIGNING_KEY"])
    rel = gh.call("GET", f"/releases/tags/{tag}", ok404=True)
    if rel is None:
        rel = gh.call("POST", "/releases", {"tag_name": tag, "name": f"JARVES {display}", "body": beta_notes(info, display),
                                            "draft": True, "prerelease": True})
    gh.upload(rel, exe.name, exe.read_bytes(), "application/vnd.microsoft.portable-executable")
    gh.upload(rel, exe.name + ".sha256", f"{info['sha256']}  {exe.name}\n".encode(), "text/plain")
    body = json.dumps(envelope, indent=1).encode()
    gh.upload(rel, BETA_MANIFEST, body, "application/json")        # kept per beta so a rollback can re-publish it
    gh.call("PATCH", f"/releases/{rel['id']}", {"draft": False, "prerelease": True, "make_latest": "false"})
    chan = gh.call("GET", f"/releases/tags/{BETA_TAG}", ok404=True)
    if chan is None:
        chan = gh.call("POST", "/releases", {"tag_name": BETA_TAG, "name": "JARVES beta channel (manifest only)",
                                             "body": "Holds only latest-beta.json, replaced after every published beta.",
                                             "draft": False, "prerelease": True, "make_latest": "false"})
    gh.upload(chan, BETA_MANIFEST, body, "application/json")   # last: the switch
    print(f"Beta publish: published {display} ({tag}) in {repo}, signed {BETA_MANIFEST} key "
          f"{envelope['signature']['key_id']} -> https://github.com/{repo}/releases/download/{BETA_TAG}/{BETA_MANIFEST}")
    betas = sorted((r for r in gh.releases() if beta_number(r) and not is_pinned(r)),
                   key=lambda r: (version_key(TAG_PREFIX + r["tag_name"][len(BETA_PREFIX):]), r.get("created_at") or ""),
                   reverse=True)
    for r in betas[BETA_KEEP:]:
        if r.get("tag_name") == tag:
            continue
        print(f"Beta publish: delete old {r['tag_name']}")
        gh.call("DELETE", f"/releases/{r['id']}")
        gh.call("DELETE", f"/git/refs/tags/{r['tag_name']}", ok404=True)
    return 0


def candidate(dist: Path, commit: str, out: Path, env: dict | None = None) -> dict:
    """Release CANDIDATE of a green main: the verified installer files plus a signed manifest (when the signing key
    is available) in `out`, to be uploaded as a workflow artifact. Publishes nothing, ever. `dist` must already be
    stamped (`stamp`) when the installer was reused from the PR run."""
    env = os.environ if env is None else env
    info = verify(dist, commit)
    out.mkdir(parents=True, exist_ok=True)
    exe = dist / info["installer"]
    for f in (exe, dist / (exe.name + ".sha256"), dist / "build-info.json"):
        (out / f.name).write_bytes(f.read_bytes())
    repo = str(env.get("RELEASES_REPO") or "").strip() or DEFAULT_RELEASES_REPO
    url = f"https://github.com/{repo}/releases/download/{TAG_PREFIX}{info['version']}/{exe.name}"
    manifest = build_manifest(info, exe, url)
    key = str(env.get("JARVES_UPDATE_SIGNING_KEY") or "").strip()
    signed = bool(key)
    (out / "candidate-latest.json").write_text(
        json.dumps(sign_manifest(manifest, key) if signed else {"format": 1, "manifest": manifest, "signature": None},
                   indent=1), encoding="utf-8")
    (out / "CANDIDATE.txt").write_text(
        f"Release candidate {info['version']} of commit {info['commit']}\n"
        f"manifest: {'signed' if signed else 'UNSIGNED (JARVES_UPDATE_SIGNING_KEY not available in this run)'}\n"
        "Never published automatically. Beta: .github/workflows/beta.yml. Stable: tag vX.Y.Z (docs/RELEASING.md).\n",
        encoding="utf-8")
    print(f"Candidate {info['version']} ({'signed' if signed else 'unsigned'} manifest) written to {out}")
    return {"version": info["version"], "signed": signed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen")
    kg.add_argument("--out", type=Path, required=True)
    rp = sub.add_parser("public-publish")
    rp.add_argument("--dir", type=Path, required=True)
    rp.add_argument("--keep", type=int, default=3)
    rp.add_argument("--dry-run", action="store_true", help="print what would be published; touch nothing")
    bp = sub.add_parser("beta-publish")
    bp.add_argument("--dir", type=Path, required=True)
    bp.add_argument("--dry-run", action="store_true", help="print what would be published; touch nothing")
    brp = sub.add_parser("bridge-publish", help="publish the prebuilt WhatsApp bridge (signed whatsapp-bridge.json)")
    brp.add_argument("--dir", type=Path, required=True, help="bridge/whatsapp/dist")
    brp.add_argument("--dry-run", action="store_true")
    cp = sub.add_parser("candidate")
    cp.add_argument("--dir", type=Path, required=True)
    cp.add_argument("--commit", required=True)
    cp.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--dir", type=Path, required=True)
    v.add_argument("--commit", required=True)
    v.add_argument("--tree", help="accept an installer built from this git tree (reuse of the PR build)")
    st = sub.add_parser("stamp", help="rewrite build-info.json of a reused installer (commit, channel); never rebuilds")
    st.add_argument("--dir", type=Path, required=True)
    st.add_argument("--commit", required=True)
    st.add_argument("--tree", required=True)
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
        return public_publish(a.dir, a.keep, dry_run=a.dry_run)
    if a.cmd == "beta-publish":
        return beta_publish(a.dir, dry_run=a.dry_run)
    if a.cmd == "bridge-publish":
        return bridge_publish(a.dir, dry_run=a.dry_run)
    if a.cmd == "candidate":
        candidate(a.dir, a.commit, a.out)
        return 0
    if a.cmd == "verify":
        info = verify(a.dir, a.commit, a.tree)
        print(f"VERIFY OK: {info['installer']} {info['version']} {info['sha256']}")
        print(f"version={info['version']}", file=open(os.environ["GITHUB_OUTPUT"], "a")
              if os.environ.get("GITHUB_OUTPUT") else sys.stdout)
        return 0
    if a.cmd == "stamp":
        info = stamp(a.dir, a.commit, a.tree)
        print(f"STAMPED: {info['installer']} {info['version']} commit {a.commit[:12]} tree {a.tree[:12]} "
              f"(built from {str(info.get('built_from_commit', a.commit))[:12]}); installer bytes untouched")
        return 0
    if a.cmd == "notes":
        print(notes(json.loads((a.dir / "build-info.json").read_text(encoding="utf-8-sig")), a.run_url))
        return 0
    if a.keep < 1:
        raise SystemExit("--keep must be at least 1")
    return prune(a.keep, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
