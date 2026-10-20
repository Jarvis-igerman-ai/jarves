"""Build identity and the update check (SW-V2 M6, D16: notify + ask approval; never a silent install).

- build_info()   what this copy is: version, commit, build time, channel. Installed builds carry
                 jarves/_build_info.json (written by packaging/build_windows.ps1); a source checkout says "dev".
- UpdateChecker  read-only HTTPS GET of the GitHub releases of the JARVES repo, at most once a day (or on request).
                 It only looks at stable Windows releases (tag `win-v<version>`, asset `JARVES-Setup-<version>.exe`)
                 published by .github/workflows/package.yml after a green `main`; PR builds never appear there.
                 Result and "already told the user" state live in <state_dir>/updates.json.
- install        only after an explicit approval (CLI `jarvis update install` asks "yes"; the dashboard button is
                 the user's click). Steps: download -> verify SHA256 (refuse without a checksum) -> verified local backup
                 (jarves.backup.checkpoint, WP-M5) -> stop the Core -> start the normal installer window (the user sees it).
                 The installer keeps user data (it lives in <state_dir>, not in the program folder).

Private repository: anonymous requests cannot see the releases (GitHub answers 404). A user may put a read-only
fine-grained token in secrets.env as JARVES_UPDATE_TOKEN (never shipped in the installer); otherwise the check
reports "unavailable" truthfully instead of "up to date".

Public releases (WP-O, docs/RELEASE_HOSTING.md): package.yml also publishes every green `main` build to a separate
PUBLIC repo that holds only release files (DEFAULT_RELEASES_REPO). Once this build trusts a signing key, the check
reads https://github.com/<repo>/releases/latest/download/latest.json anonymously (a plain download, not the REST
API, so no rate limit applies; at most once a day anyway): {"manifest": {...}, "signature": {"alg": "ed25519",
"key_id", "sig"}}. The signature (made in CI with a key that exists only as a GitHub secret) must verify against a
public key built into this app (UPDATE_PUBLIC_KEYS, plus an optional config `update.public_key`); without a valid
signature the check is "unavailable", never "update available". The signed manifest pins the installer's SHA256
and size. Config `[update] base_url` (or env JARVES_UPDATE_BASE_URL) points the same check at any other HTTPS folder
holding latest.json (custom hosting). No GitHub token is ever sent for this source.

Interface for other packages (tray, voice greeting, scheduler):
    from jarves.updates import UpdateChecker
    u = UpdateChecker(home); u.check()          # cheap when fresh (cached), safe to call at Core start
    text = u.notice("he")                       # "" or one sentence to say/show once per new version
    u.mark_notified()                           # after it was said/shown
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__, logs

REPO = "igalgerman/JARVIS"
TAG_PREFIX = "win-v"
ASSET_RE = re.compile(r"^JARVES-Setup-(\d+(?:\.\d+){1,3})\.exe$")
CHECK_INTERVAL_S = 24 * 3600
STATE_FILE = "updates.json"
BUILD_INFO = Path(__file__).with_name("_build_info.json")
MANIFEST = "latest.json"
SIGN_DOMAIN = b"jarves-update-manifest-v1\n"      # same constant in packaging/release.py
# Ed25519 public keys (base64 of the raw 32 bytes) whose signatures this app trusts for latest.json. The private
# half exists only as the GitHub secret JARVES_UPDATE_SIGNING_KEY (`python packaging/release.py keygen`). Empty
# until the owner creates the key: a hosted source then reports "unavailable" (no key = no trust).
UPDATE_PUBLIC_KEYS: tuple[str, ...] = ("k95ZucnexPDgELN5ylkcxR0fy38QH5EsQs90yIpY/Go=",)   # key id a2bfe35a27dcd5fd
DEFAULT_RELEASES_REPO = "igalgerman/jarvis-releases"   # public, release files only (same default in release.py)


def public_releases_url(repo: str = DEFAULT_RELEASES_REPO) -> str:
    return f"https://github.com/{repo}/releases/latest/download"

# Windows / Pi status of the M6 features (spec section 5). jarves.features has no targets field yet, so this is
# the record the capability matrix (WP-M4) reads; the issue is real (created with `gh issue create`).
PI_ISSUE = "https://github.com/igalgerman/JARVIS/issues/19"
PLATFORMS = {
    "windows_installer": {
        "windows": {"status": "implemented_unverified",
                    "evidence": "CI silent install/run/uninstall on windows-2025: "
                                "https://github.com/igalgerman/JARVIS/actions/runs/39290421823; "
                                "a clean second PC is the owner's test (docs/INSTALL_TEST.md)"},
        "linux_arm64_pi": {"status": "planned", "blocker": "no Linux ARM64 package/systemd service yet (M7)",
                           "roadmap_issue": PI_ISSUE}},
    "update_check": {
        "windows": {"status": "implemented_unverified",
                    "evidence": "core/tests/test_updates.py (fake GitHub); needs a published release to verify live"},
        "linux_arm64_pi": {"status": "planned", "blocker": "the check works, but there is no Pi package to install",
                           "roadmap_issue": PI_ISSUE}},
}

Http = Callable[[str, dict], tuple[int, object]]      # (url, headers) -> (status, parsed JSON or bytes)


# ---------------------------------------------------------------------- versions
def version_tuple(v: str | None) -> tuple[int, ...] | None:
    """'0.1.0.57' / 'v0.1.0.57' / 'win-v0.1.0.57' -> (0, 1, 0, 57); anything else -> None."""
    if not v:
        return None
    m = re.fullmatch(r"(?:win-)?v?(\d+(?:\.\d+){0,3})", v.strip())
    if not m:
        return None
    parts = tuple(int(x) for x in m.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def is_newer(candidate: str, current: str) -> bool:
    c, cur = version_tuple(candidate), version_tuple(current.split("+")[0])
    return c is not None and cur is not None and c > cur


def build_info() -> dict:
    """This copy's identity. Never raises: a broken file reads as a dev build."""
    info = {"version": __version__, "commit": None, "built": None, "channel": "dev", "platform": sys.platform}
    try:
        raw = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            info.update({k: raw[k] for k in ("version", "commit", "built", "channel", "platform", "update_base_url")
                         if k in raw})
    except (OSError, ValueError):
        pass
    return info


def is_installed_build() -> bool:
    """True in a packaged/installed copy (packaging/build_windows.ps1 writes _build_info.json; a frozen exe counts
    too). Dev checkouts have neither. Installed builds ignore update-source env overrides and the config key, and
    launch only installers verified against the signed manifest."""
    return bool(getattr(sys, "frozen", False)) or BUILD_INFO.is_file()


def version_line() -> str:
    b = build_info()
    extra = [f"commit {b['commit'][:12]}" if b.get("commit") else None, f"built {b['built']}" if b.get("built") else None,
             f"channel {b['channel']}"]
    return f"JARVES {b['version']} (" + ", ".join(x for x in extra if x) + ")"


# ---------------------------------------------------------------------- GitHub releases
def pick_release(releases: list, current: str) -> dict | None:
    """The newest stable Windows release with an installer asset, if it is newer than `current`."""
    best, best_v = None, None
    for r in releases or []:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        tag = str(r.get("tag_name") or "")
        if not tag.startswith(TAG_PREFIX):
            continue
        assets = {a.get("name"): a for a in r.get("assets") or [] if isinstance(a, dict)}
        exe = next((a for n, a in assets.items() if n and ASSET_RE.match(n)), None)
        if exe is None:
            continue
        v = ASSET_RE.match(exe["name"]).group(1)
        vt = version_tuple(v)
        if vt is None or (best_v is not None and vt <= best_v):
            continue
        best_v = vt
        sha = assets.get(exe["name"] + ".sha256")
        best = {"version": v, "tag": tag, "page": r.get("html_url"), "published": r.get("published_at"),
                "notes": str(r.get("body") or "")[:1500],
                "asset": {"name": exe["name"], "api_url": exe.get("url"), "download_url": exe.get("browser_download_url"),
                          "size": exe.get("size"), "digest": exe.get("digest")},
                "sha256_asset": {"api_url": sha.get("url"), "download_url": sha.get("browser_download_url")} if sha else None}
    if best is None or not is_newer(best["version"], current):
        return None
    return best


def _httpx_get(url: str, headers: dict) -> tuple[int, object]:
    import httpx
    r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
    if r.status_code != 200:
        return r.status_code, None
    if "json" in r.headers.get("content-type", ""):
        return 200, r.json()
    return 200, r.content


class UpdateError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---------------------------------------------------------------------- hosted manifest (public releases repo), Ed25519-signed
def canonical(manifest: dict) -> bytes:
    """The exact signed bytes (same function in packaging/release.py)."""
    return SIGN_DOMAIN + json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def key_id(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def verify_manifest(envelope: object, public_keys: list[str] | tuple[str, ...]) -> dict:
    """The manifest inside a latest.json envelope, only if an Ed25519 signature by one of `public_keys` covers it."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not public_keys:
        raise UpdateError("UPDATE_NO_KEY", "this build has no update signing key, so a hosted update cannot be "
                                           "trusted (docs/RELEASE_HOSTING.md)")
    if not isinstance(envelope, dict) or not isinstance(envelope.get("manifest"), dict) \
            or not isinstance(envelope.get("signature"), dict):
        raise UpdateError("UPDATE_BAD_MANIFEST", "latest.json is not a signed JARVES manifest")
    sig = envelope["signature"]
    if sig.get("alg") != "ed25519":
        raise UpdateError("UPDATE_BAD_SIGNATURE", f"unsupported signature algorithm {sig.get('alg')!r}")
    try:
        raw_sig = base64.b64decode(str(sig.get("sig") or ""), validate=True)
    except ValueError:
        raise UpdateError("UPDATE_BAD_SIGNATURE", "latest.json signature is not base64") from None
    msg = canonical(envelope["manifest"])
    for k in public_keys:
        try:
            pub = base64.b64decode(k.strip(), validate=True)
            Ed25519PublicKey.from_public_bytes(pub).verify(raw_sig, msg)
            return envelope["manifest"]
        except (ValueError, InvalidSignature):
            continue
    raise UpdateError("UPDATE_BAD_SIGNATURE", "latest.json is not signed by a trusted JARVES key; not using it")


def release_from_manifest(m: dict, base_url: str, current: str) -> dict | None:
    """The same release shape pick_release() returns, from a verified manifest; None when not newer."""
    v, name, url = str(m.get("version") or ""), str(m.get("installer") or ""), str(m.get("url") or "")
    sha, size = str(m.get("sha256") or "").lower(), m.get("size")
    if m.get("product") != "jarves" or m.get("platform") != "windows-x64" or m.get("channel") != "main":
        raise UpdateError("UPDATE_BAD_MANIFEST", "latest.json is not a stable JARVES Windows release")
    if not ASSET_RE.match(name) or ASSET_RE.match(name).group(1) != v or not re.fullmatch(r"[0-9a-f]{64}", sha) \
            or not isinstance(size, int) or size <= 0:
        raise UpdateError("UPDATE_BAD_MANIFEST", "latest.json is missing the version, installer, SHA256 or size")
    if not url:
        url = f"{base_url}/{name}"
    if not url.startswith("https://"):
        raise UpdateError("UPDATE_BAD_MANIFEST", "the installer link is not HTTPS")
    if not is_newer(v, current):
        return None
    return {"version": v, "tag": f"{TAG_PREFIX}{v}", "page": None, "published": m.get("published"),
            "commit": m.get("commit"), "built": m.get("built"), "notes": "", "source": "hosted",
            "asset": {"name": name, "api_url": None, "download_url": url, "size": size, "digest": f"sha256:{sha}"},
            "sha256_asset": None}


class UpdateChecker:
    def __init__(self, home: Path, *, repo: str | None = None, http: Http | None = None,
                 now: Callable[[], float] = time.time, current: str | None = None, cfg=None,
                 base_url: str | None = None, public_keys: list[str] | tuple[str, ...] | None = None) -> None:
        self.home = Path(home)
        self.installed = is_installed_build()
        dev_env = (lambda k: "") if self.installed else (lambda k: os.environ.get(k) or "")   # dev checkouts only
        self.repo = repo or dev_env("JARVES_UPDATE_REPO") or REPO
        self.http = http or _httpx_get
        self.now = now
        self.current = current or build_info()["version"]
        self.path = self.home / STATE_FILE
        ucfg = getattr(cfg, "update", None)
        # An extra trusted key from config.toml is for dev/testing only: an installed build trusts only the keys
        # it was built with (a local file must not be able to add trust).
        extra = "" if self.installed else str(getattr(ucfg, "public_key", "") or "").strip()
        self.public_keys = tuple(public_keys if public_keys is not None else UPDATE_PUBLIC_KEYS) + \
            ((extra,) if extra else ())
        # Order: explicit argument > env JARVES_UPDATE_BASE_URL > config [update] base_url > the build's default
        # (CI variable baked into _build_info.json) > the public releases repo once a signing key is trusted >
        # "" (releases of the private repo, needs JARVES_UPDATE_TOKEN).
        base = base_url if base_url is not None else (
            os.environ.get("JARVES_UPDATE_BASE_URL") or getattr(ucfg, "base_url", "")
            or str(build_info().get("update_base_url") or "")
            or (public_releases_url(dev_env("JARVES_RELEASES_REPO") or DEFAULT_RELEASES_REPO)
                if self.public_keys else ""))
        if base and not base.strip().startswith("https://"):
            base = ""                                      # never fetch an update manifest over plain HTTP
        self.base_url = base.strip().rstrip("/")
        # A different source invalidates the cached answer (e.g. base_url was just set).
        self.source = f"hosted:{self.base_url}" if self.base_url else f"github:{self.repo}"

    # --- state
    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _headers(self, accept: str = "application/vnd.github+json") -> dict:
        if self.base_url:                                  # hosted source: never send a GitHub token elsewhere
            return {"Accept": "*/*" if accept.startswith("application/vnd.github") else accept,
                    "User-Agent": f"jarves/{self.current}"}
        h = {"Accept": accept, "User-Agent": f"jarves/{self.current}", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("JARVES_UPDATE_TOKEN", "").strip()
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def status(self) -> dict:
        d = self._load()
        rel = d.get("release") if d.get("source", self.source) == self.source else None
        # A cached release that is no longer newer (this copy was updated meanwhile) is not an update.
        available = bool(rel and is_newer(rel.get("version", ""), self.current))
        return {"current": self.current, "build": build_info(), "available": available,
                "latest": rel if available else None, "checked_at": d.get("checked_at"), "error": d.get("error"),
                "notified": d.get("notified"), "repo": self.repo, "source": self.source, "policy": "notify_and_ask"}

    # --- check
    def _check_github(self) -> tuple[str | None, dict | None]:
        url = f"https://api.github.com/repos/{self.repo}/releases?per_page=30"
        try:
            code, body = self.http(url, self._headers())
        except Exception as e:          # network down, DNS, TLS: an update check never breaks anything
            return f"no connection to GitHub ({type(e).__name__})", None
        if code == 200 and isinstance(body, list):
            return None, pick_release(body, self.current)
        if code in (401, 403, 404):
            return ("releases not reachable (private repository or rate limit). Ask the owner for access or set "
                    "JARVES_UPDATE_TOKEN (read-only) in secrets.env, or [update] base_url"), None
        return f"GitHub answered HTTP {code}", None

    def _fetch_manifest(self) -> dict:
        """GET + verify <base_url>/latest.json; returns the verified manifest or raises UpdateError."""
        url = f"{self.base_url}/{MANIFEST}"
        if not url.startswith("https://"):
            raise UpdateError("UPDATE_BAD_SOURCE", "update.base_url must be an https:// address")
        try:
            code, body = self.http(url, self._headers("application/json"))
        except Exception as e:
            raise UpdateError("UPDATE_OFFLINE", f"no connection to the update server ({type(e).__name__})") from None
        if code != 200:
            raise UpdateError("UPDATE_HTTP", f"the update server answered HTTP {code} for {MANIFEST}")
        if isinstance(body, (bytes, bytearray)):
            try:
                body = json.loads(bytes(body).decode("utf-8"))
            except ValueError:
                raise UpdateError("UPDATE_BAD_MANIFEST", f"{MANIFEST} is not JSON") from None
        return verify_manifest(body, self.public_keys)

    def _check_hosted(self) -> tuple[str | None, dict | None]:
        try:
            return None, release_from_manifest(self._fetch_manifest(), self.base_url, self.current)
        except UpdateError as e:
            return e.message, None

    def check(self, force: bool = False) -> dict:
        d = self._load()
        if not force and d.get("checked_at") and d.get("source", self.source) == self.source \
                and self.now() - float(d.get("checked_ts") or 0) < CHECK_INTERVAL_S:
            return self.status()
        err, rel = self._check_hosted() if self.base_url else self._check_github()
        if d.get("source", self.source) != self.source:
            d.pop("release", None)                         # an answer from the old source is not this source's
        d.update({"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "checked_ts": self.now(),
                  "error": err, "source": self.source})
        if err is None:
            d["release"] = rel
        self._save(d)
        logs.info("updates", "UPDATE_CHECKED", "update check finished", detail=err,
                  current=self.current, latest=(rel or {}).get("version"), ok=err is None)
        return self.status()

    # --- notify (once per version)
    def notice(self, lang: str = "he") -> str:
        s = self.status()
        if not s["available"] or s["notified"] == s["latest"]["version"]:
            return ""
        v = s["latest"]["version"]
        if lang == "he":
            return f"יש גרסה חדשה של ג'רביס ({v}). להתקין? אני מתקין רק אחרי שתאשר, בלוח הבקרה או בפקודה jarvis update."
        return f"A new JARVES version ({v}) is available. Install it? I only install after you approve " \
               f"(dashboard or `jarvis update install`)."

    def mark_notified(self) -> None:
        s = self.status()
        if s["available"]:
            d = self._load()
            d["notified"] = s["latest"]["version"]
            self._save(d)

    # --- install (approval required)
    def _fetch(self, asset: dict) -> bytes:
        url = asset.get("api_url") or asset.get("download_url")
        if not url:
            raise UpdateError("UPDATE_NO_ASSET", "the release has no download link")
        accept = "application/octet-stream" if asset.get("api_url") else "*/*"
        code, body = self.http(url, self._headers(accept))
        if code != 200 or not isinstance(body, (bytes, bytearray)):
            raise UpdateError("UPDATE_DOWNLOAD_FAILED", f"download failed (HTTP {code})")
        return bytes(body)

    def expected_sha256(self, rel: dict) -> str:
        """From the release's .sha256 file, cross-checked with GitHub's own asset digest when both exist."""
        want = None
        if rel.get("sha256_asset"):
            text = self._fetch(rel["sha256_asset"]).decode("utf-8", "replace")
            m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
            want = m.group(1).lower() if m else None
        digest = str(rel["asset"].get("digest") or "")
        gh = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None
        if want and gh and want != gh:
            raise UpdateError("UPDATE_CHECKSUM_MISMATCH", "the release checksum file and GitHub's digest disagree")
        if not (want or gh):
            raise UpdateError("UPDATE_NO_CHECKSUM", "the release has no SHA256; refusing to install it")
        return want or gh

    def download(self, rel: dict) -> Path:
        want = self.expected_sha256(rel)
        data = self._fetch(rel["asset"])
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            raise UpdateError("UPDATE_CHECKSUM_MISMATCH", "the downloaded installer does not match its SHA256")
        if rel.get("source") == "hosted" and len(data) != rel["asset"].get("size"):
            raise UpdateError("UPDATE_CHECKSUM_MISMATCH", "the downloaded installer has the wrong size")
        out = self.home / "updates" / rel["asset"]["name"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        logs.info("updates", "UPDATE_DOWNLOADED", "installer verified", version=rel["version"], sha256=got,
                  size=len(data))
        return out

    def prepare_install(self, approved: bool) -> dict:
        """Download + verify + back up. Returns {installer, backup, version}; launching is a separate step."""
        if approved is not True:
            raise UpdateError("UPDATE_NEEDS_APPROVAL", "an update is installed only after the user approves it")
        if sys.platform != "win32":
            raise UpdateError("UPDATE_NOT_APPLICABLE", "there is no installer for this platform yet (Pi: planned)")
        s = self.status()
        if not s["available"]:
            raise UpdateError("UPDATE_NONE", "no newer version is known (run a check first)")
        rel = s["latest"]
        signed = rel.get("source") == "hosted"
        if not signed and self.installed:
            # A GitHub release's SHA256 / digest come from the same release as the installer: they catch
            # corruption, not a malicious release. Installed builds launch only what the signed manifest names.
            raise UpdateError("UPDATE_UNSIGNED", "this update has no signed manifest, so it is not installed "
                                                 "automatically; download it by hand and check its SHA256 "
                                                 "(docs/RELEASE_HOSTING.md)")
        if signed:
            # Re-read and re-verify the signed manifest now: the SHA256 we check against is never taken from the
            # local cache file alone.
            rel = release_from_manifest(self._fetch_manifest(), self.base_url, self.current)
            if rel is None:
                raise UpdateError("UPDATE_NONE", "the update server no longer offers a newer version")
        exe = self.download(rel)
        bak = backup_before_update(self.home, f"pre-update-{self.current}-to-{rel['version']}")
        return {"installer": str(exe), "backup": str(bak), "version": rel["version"], "signed": signed,
                "sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}   # = the verified value (download checked)


def launch_installer(path: str | Path, verified: dict | None = None) -> None:
    """Start the normal (visible) setup window, detached, so the user sees and confirms every step. `verified` is
    prepare_install()'s result; an installed build refuses to start anything without a signed, verified result
    whose SHA256 still matches the file on disk (checked again right before launch)."""
    if is_installed_build():
        if not verified or verified.get("signed") is not True or Path(verified.get("installer") or "") != Path(path):
            raise UpdateError("UPDATE_UNSIGNED", "not starting an installer that was not verified against the "
                                                 "signed update manifest")
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != verified.get("sha256"):
            raise UpdateError("UPDATE_CHECKSUM_MISMATCH", "the installer changed after it was verified; not "
                                                          "starting it")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(path)], creationflags=flags, close_fds=True)
    logs.info("updates", "UPDATE_INSTALLER_STARTED", "installer started after user approval", installer=Path(path).name)


# ---------------------------------------------------------------------- backup before update / migration
def backup_before_update(home: Path, label: str) -> Path:
    """WP-M5's verified pre-migration backup (jarves.backup.checkpoint: SQLite-safe copies, integrity check, no
    credentials). Any failure stops the update: no install without a verified restore point."""
    from . import backup
    try:
        return backup.checkpoint(Path(home), label)
    except backup.BackupError as e:
        raise UpdateError("UPDATE_BACKUP_FAILED", f"backup before the update failed ({e.code}); not installing") from e

# ---------------------------------------------------------------------- feature switch + dashboard
def _register_feature() -> None:
    from . import features
    if "update_check" in features.CATALOG:
        return
    import inspect
    kw = {"targets": PLATFORMS["update_check"]} if "targets" in inspect.signature(features.register).parameters else {}
    features.register("update_check", "בדיקת עדכונים", "Update check", True, risk="low",
                      about="looks for a newer JARVES once a day (GitHub, or the signed release folder in "
                            "update.base_url; read-only); installs only after you approve", **kw)


_register_feature()


def _checker(ctx) -> UpdateChecker:
    return UpdateChecker(ctx.home, cfg=getattr(ctx, "cfg", None))


def _enabled(ctx) -> bool:
    from . import features
    try:
        return features.Features(ctx.cfg, ctx.home).enabled("update_check")
    except Exception:
        return True


def api_updates(ctx, m, body):
    u = _checker(ctx)
    s = u.check() if _enabled(ctx) else u.status()       # cached for a day; opening the page is not a poll loop
    return 200, {**s, "enabled": _enabled(ctx), "platforms": PLATFORMS, "notice": u.notice("he")}


def api_updates_check(ctx, m, body):
    if not _enabled(ctx):
        return 409, {"error": "update check is switched off (Features: update_check)"}
    return 200, _checker(ctx).check(force=True)


def api_updates_install(ctx, m, body):
    """POST {"approve": true}: the dashboard click is the approval. Downloads, verifies, backs up, starts setup."""
    try:
        r = _checker(ctx).prepare_install((body or {}).get("approve") is True)
        launch_installer(r["installer"], verified=r)
    except UpdateError as e:
        return 409, {"error": e.message, "code": e.code}
    return 200, {**r, "note": "the setup window is open; it closes JARVES, keeps your data and can start it again"}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/updates", api_updates)
    register_route("POST", r"/api/updates/check", api_updates_check)
    register_route("POST", r"/api/updates/install", api_updates_install)
