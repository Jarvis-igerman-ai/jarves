"""Publish + retention for the Windows installer (.github/workflows/package.yml). Python stdlib only.

  verify --dir DIST --commit SHA        build-info.json matches the tested commit, channel "main", and the SHA256
                                        of the installer matches both build-info.json and the .sha256 file
  notes  --dir DIST --run-url URL       release notes (version, commit, build time, SHA256, unsigned warning)
  prune  --keep 3 [--dry-run]           keep the newest 3 stable `win-v*` releases (+ their tags) and the newest 3
                                        `jarves-windows-installer-main` artifacts; delete older ones

Retention never touches: drafts, releases with other tag prefixes (e.g. future `pi-v*`), any release whose title
or notes contain "[pinned]" (rollback pins; they also do not count towards the 3), and any other artifact name.
Needs GITHUB_TOKEN (contents: write, actions: write) and GITHUB_REPOSITORY.

Release hosting outside the private repo (WP-O, docs/RELEASE_HOSTING.md): Cloudflare R2 through its S3 API.
  keygen --out FILE                     new Ed25519 signing key (PEM, for the CI secret) + the public key to embed
  r2-publish --dir DIST [--keep 3]      upload installer + .sha256, then the signed latest.json, then prune old
                                        installers in R2. Skips cleanly (exit 0, clear log) without the secrets.

R2 layout (public bucket, read-only for the world; only CI can write):
  <prefix>/JARVES-Setup-<v>.exe  <prefix>/JARVES-Setup-<v>.exe.sha256  <prefix>/latest.json  [<prefix>/pinned.txt]
latest.json = {"format": 1, "manifest": {version, commit, built, sha256, size, installer, url, ...},
               "signature": {"alg": "ed25519", "key_id", "sig"}}; the signature covers SIGN_DOMAIN + the canonical
JSON of "manifest" (sorted keys, no spaces, ASCII). The app (core/src/jarves/updates.py) holds the public key and
refuses an unsigned/mis-signed manifest, then checks the installer's SHA256 + size from the signed manifest.
Retention in R2: newest `keep` versions + every version listed in <prefix>/pinned.txt (one per line) + the one in
latest.json; nothing outside `<prefix>/JARVES-Setup-*` is ever deleted. S3 signing (SigV4) is stdlib; Ed25519 uses
`cryptography` when importable, else the `openssl` CLI (OpenSSL 3, present on the Ubuntu runners).
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

TAG_PREFIX = "win-v"
ARTIFACT = "jarves-windows-installer-main"
PIN = "[pinned]"
SIGN_DOMAIN = b"jarves-update-manifest-v1\n"      # same constant in core/src/jarves/updates.py
R2_SECRETS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_BASE",
              "JARVES_UPDATE_SIGNING_KEY")
R2_DEFAULT_PREFIX = "jarves/windows"
EXE_RE = re.compile(r"^JARVES-Setup-(\d+(?:\.\d+){1,3})\.exe(\.sha256)?$")


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


# ---------------------------------------------------------------------- R2 (S3 API, SigV4, stdlib)
def _q(s: str, safe: str = "") -> str:
    return urllib.parse.quote(s, safe=safe)        # RFC 3986 unreserved stay as-is (Python 3.7+)


def sigv4(method: str, url: str, headers: dict, payload_sha256: str, access_key: str, secret_key: str,
          region: str = "auto", service: str = "s3", now: _dt.datetime | None = None) -> dict:
    """AWS Signature Version 4 headers for one request (R2 accepts region "auto"). Returns the headers to send."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    u = urllib.parse.urlsplit(url)
    h = {k.lower().strip(): " ".join(str(v).split()) for k, v in headers.items()}
    h.update({"host": u.netloc, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha256})
    names = sorted(h)
    query = sorted((_q(k), _q(v)) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    canonical = "\n".join([method, _q(urllib.parse.unquote(u.path) or "/", "/"), "&".join(f"{k}={v}" for k, v in query),
                           "".join(f"{k}:{h[k]}\n" for k in names), ";".join(names), payload_sha256])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + secret_key).encode()
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {k: v for k, v in headers.items() if k.lower() != "host"}
    out.update({"x-amz-date": amz_date, "x-amz-content-sha256": payload_sha256,
                "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, "
                                 f"Signature={sig}"})
    return out


class S3Error(Exception):
    pass


class S3:
    """The few S3 calls the release needs, path-style (`<endpoint>/<bucket>/<key>`), as R2 documents them."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str = "auto"):
        self.endpoint, self.bucket = endpoint.rstrip("/"), bucket
        self.access_key, self.secret_key, self.region = access_key, secret_key, region

    def _call(self, method: str, key: str = "", query: dict | None = None, body: bytes | Path | None = None,
              headers: dict | None = None) -> tuple[int, bytes]:
        url = f"{self.endpoint}/{_q(self.bucket)}" + (f"/{_q(key, '/')}" if key else "")
        if query:
            url += "?" + "&".join(f"{_q(k)}={_q(str(v))}" for k, v in query.items())
        headers = dict(headers or {})
        if isinstance(body, Path):
            digest, size = sha256(body), body.stat().st_size
        else:
            digest, size = hashlib.sha256(body or b"").hexdigest(), len(body or b"")
        signed = sigv4(method, url, headers, digest, self.access_key, self.secret_key, self.region)
        if body is not None:
            signed["Content-Length"] = str(size)
        fh = body.open("rb") if isinstance(body, Path) else None
        try:
            req = urllib.request.Request(url, data=fh if fh else body, method=method, headers=signed)
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        finally:
            if fh:
                fh.close()

    def _ok(self, what: str, code: int, data: bytes) -> bytes:
        if not 200 <= code < 300:
            msg = re.search(rb"<Code>([^<]+)</Code>", data or b"")
            raise S3Error(f"{what}: HTTP {code} {msg.group(1).decode() if msg else ''}".strip())
        return data

    def put(self, key: str, body: bytes | Path, content_type: str, cache_control: str = "") -> None:
        h = {"Content-Type": content_type, **({"Cache-Control": cache_control} if cache_control else {})}
        self._ok(f"PUT {key}", *self._call("PUT", key, body=body, headers=h))

    def get(self, key: str) -> bytes | None:
        code, data = self._call("GET", key)
        return None if code == 404 else self._ok(f"GET {key}", code, data)

    def delete(self, key: str) -> None:
        self._ok(f"DELETE {key}", *self._call("DELETE", key))

    def list(self, prefix: str) -> list[str]:
        keys, token = [], None
        while True:
            q = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
            root = ET.fromstring(self._ok(f"LIST {prefix}", *self._call("GET", query=q)))
            for e in root.iter():
                e.tag = e.tag.rsplit("}", 1)[-1]           # drop the S3 XML namespace
            keys += [c.findtext("Key") for c in root.findall("Contents")]
            token = root.findtext("NextContinuationToken")
            if (root.findtext("IsTruncated") or "").lower() != "true" or not token:
                return keys


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


# ---------------------------------------------------------------------- R2 retention + publish
def _vt(v: str) -> tuple[int, ...]:
    parts = tuple(int(x) for x in v.split("."))
    return parts + (0,) * (4 - len(parts))


def parse_pins(text: str) -> set[str]:
    """pinned.txt: one version per line ("0.1.0.5", "win-v0.1.0.5" or the installer name); '#' starts a comment."""
    out = set()
    for line in (text or "").splitlines():
        m = re.search(r"\d+(?:\.\d+){1,3}", line.split("#", 1)[0])
        if m:
            out.add(m.group(0))
    return out


def r2_to_prune(keys: list[str], prefix: str, keep: int, pinned: set[str], current: str | None) -> list[str]:
    """Installer objects (+ their .sha256) beyond the newest `keep` versions. Pinned versions and the version in
    latest.json are always kept (pins do not count towards `keep`); any other key (other folders, latest.json, pinned.txt) is never listed."""
    versions: dict[str, list[str]] = {}
    for k in keys:
        head, _, name = k.rpartition("/")
        m = EXE_RE.match(name)
        if head == prefix and m:
            versions.setdefault(m.group(1), []).append(k)
    rest = sorted((v for v in versions if v not in pinned), key=_vt, reverse=True)
    return sorted(k for v in rest[keep:] if v != current for k in versions[v])


def r2_publish(dist: Path, keep: int = 3, env: dict | None = None, s3: S3 | None = None) -> int:
    env = os.environ if env is None else env
    missing = [k for k in R2_SECRETS if not str(env.get(k) or "").strip()]
    if len(missing) == len(R2_SECRETS):
        print("R2 hosting: SKIPPED - no R2 secrets are set, so the GitHub Release is the only download "
              "(set them up: docs/RELEASE_HOSTING.md)")
        return 0
    if missing:
        print(f"::warning::R2 hosting: SKIPPED - missing secrets {', '.join(missing)} (docs/RELEASE_HOSTING.md); "
              "the GitHub Release was still published")
        return 0
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8-sig"))
    exe = dist / str(info["installer"])
    if sha256(exe) != info.get("sha256"):
        raise SystemExit("R2 hosting: installer SHA256 does not match build-info.json; not uploading")
    prefix = (str(env.get("R2_PREFIX") or "").strip() or R2_DEFAULT_PREFIX).strip("/")
    base = f"{env['R2_PUBLIC_BASE'].strip().rstrip('/')}/{prefix}"
    if not base.startswith("https://"):
        raise SystemExit("R2 hosting: R2_PUBLIC_BASE must start with https://")
    s3 = s3 or S3(str(env.get("R2_ENDPOINT") or f"https://{env['R2_ACCOUNT_ID'].strip()}.r2.cloudflarestorage.com"),
                  env["R2_BUCKET"].strip(), env["R2_ACCESS_KEY_ID"].strip(), env["R2_SECRET_ACCESS_KEY"].strip())
    old = s3.get(f"{prefix}/latest.json")
    try:
        old_v = json.loads(old)["manifest"]["version"] if old else None
    except (ValueError, KeyError, TypeError):
        old_v = None
    if old_v and _vt(old_v) > _vt(info["version"]):
        print(f"R2 hosting: latest.json already has the newer {old_v}; not replacing it with {info['version']}")
        return 0
    envelope = sign_manifest(build_manifest(info, exe, f"{base}/{exe.name}"), env["JARVES_UPDATE_SIGNING_KEY"])
    s3.put(f"{prefix}/{exe.name}", exe, "application/vnd.microsoft.portable-executable",
           "public, max-age=31536000, immutable")
    s3.put(f"{prefix}/{exe.name}.sha256", f"{info['sha256']}  {exe.name}\n".encode(), "text/plain; charset=utf-8",
           "public, max-age=31536000, immutable")
    # latest.json last: a PC never sees a manifest whose installer is not uploaded yet.
    s3.put(f"{prefix}/latest.json", json.dumps(envelope, indent=1).encode(), "application/json",
           "public, max-age=300")
    print(f"R2 hosting: uploaded {exe.name} ({info['size']} bytes) + signed latest.json "
          f"(key {envelope['signature']['key_id']}) -> {base}/latest.json")
    pins = parse_pins((s3.get(f"{prefix}/pinned.txt") or b"").decode("utf-8", "replace"))
    for k in r2_to_prune(s3.list(f"{prefix}/"), prefix, keep, pins, info["version"]):
        print(f"R2 hosting: delete old {k}")
        s3.delete(k)
    print(f"R2 hosting: done. On each PC: config.toml [update] base_url = \"{base}\"")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen")
    kg.add_argument("--out", type=Path, required=True)
    rp = sub.add_parser("r2-publish")
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
    if a.cmd == "r2-publish":
        if a.keep < 1:
            raise SystemExit("--keep must be at least 1")
        return r2_publish(a.dir, a.keep)
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
