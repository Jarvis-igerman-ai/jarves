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

Repositories: REPO (Jarvis-igerman-ai/jarves, public) is the source repo; signed installers and latest.json live in
DEFAULT_RELEASES_REPO (igalgerman/jarvis-releases). If a repo answers 404/403 the check reports "unavailable"
truthfully instead of "up to date" (an optional read-only JARVES_UPDATE_TOKEN in secrets.env is only used for the
GitHub REST fallback, never for hosted sources).

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

REPO = "Jarvis-igerman-ai/jarves"   # the PUBLIC source repo (V4-RP-1); signed installers live in DEFAULT_RELEASES_REPO
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
# Channels (docs/RELEASING.md): "stable" = latest.json of the newest stable release (tag-driven, manually signed off);
# "beta" = latest-beta.json attached to the one rolling pre-release tagged BETA_TAG, written after every green main.
CHANNELS = ("stable", "beta")
BETA_MANIFEST = "latest-beta.json"
BETA_TAG = "beta-channel"
BETA_DISPLAY_RE = re.compile(r"^\d+\.\d+\.\d+-beta\.\d+$")


def public_releases_url(repo: str = DEFAULT_RELEASES_REPO) -> str:
    return f"https://github.com/{repo}/releases/latest/download"


def public_beta_url(repo: str = DEFAULT_RELEASES_REPO) -> str:
    return f"https://github.com/{repo}/releases/download/{BETA_TAG}"


def manifest_name(channel: str) -> str:
    return BETA_MANIFEST if channel == "beta" else MANIFEST

# Windows / Pi status of the M6 features (spec section 5). jarves.features has no targets field yet, so this is
# the record the capability matrix (WP-M4) reads; the issue is real (created with `gh issue create`).
PI_ISSUE = "https://github.com/Jarvis-igerman-ai/jarves/issues/3"
PLATFORMS = {
    "windows_installer": {
        "windows": {"status": "implemented_unverified",
                    "evidence": "CI silent install/run/uninstall on windows-2025: "
                                "CI run 39290421823 of the legacy private repo; "
                                "SW-V4 L adds the clean-install audit, the upgrade/rollback scenario "
                                "(packaging/smoke_windows.ps1 -UpgradeFrom -Rollback; core/tests/test_installer_clean.py) "
                                "and the unsigned decision (docs/reviews/SWV4_L_INSTALLER.md); a clean second PC is "
                                "the owner's test (docs/INSTALL_TEST.md)"},
        "linux_arm64_pi": {"status": "planned", "blocker": "no Linux ARM64 package/systemd service yet (M7)",
                           "roadmap_issue": PI_ISSUE}},
    "beta_channel": {
        "windows": {"status": "implemented_unverified",
                    "evidence": "core/tests/test_release_channels.py (channel selection, signed beta manifest, "
                                "beta label); the first live beta needs the owner to enable ENABLE_BETA_PUBLISH"},
        "linux_arm64_pi": {"status": "planned", "blocker": "there is no Pi package to install",
                           "roadmap_issue": PI_ISSUE}},
    "update_check": {
        "windows": {"status": "implemented_unverified",
                    "evidence": "core/tests/test_updates.py (fake GitHub), core/tests/test_update_flow.py (local signed "
                                "release server: ask first, SHA256 + size, failure keeps the old version, downgrade "
                                "refused, rollback); needs a published release to verify live"},
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


def version_line(home: Path | None = None) -> str:
    b = build_info()
    extra = [f"commit {b['commit'][:12]}" if b.get("commit") else None, f"built {b['built']}" if b.get("built") else None,
             f"channel {b['channel']}"]
    shown = beta_label(home, b["version"]) if home is not None else None
    if shown:
        extra.append("BETA")
    return f"JARVES {shown or b['version']} (" + ", ".join(x for x in extra if x) + ")"


KNOWN_BUILDS_KEEP = 10                   # versions whose display/channel stay remembered (rollback targets included)


def installed_record(home: Path | None, version: str | None = None) -> dict:
    """What the updater recorded when it installed a copy: {version, display, channel}. {} when unknown.

    `installed` is the LAST install the updater started; it is written before setup runs, so after a failed or
    cancelled setup (#141) or a rollback it names a version that is not the one running. `builds` keeps the record per
    version, so the copy that really runs is still found."""
    if home is None:
        return {}
    try:
        d = json.loads((Path(home) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    last = d.get("installed")
    last = last if isinstance(last, dict) else {}
    if version is None or last.get("version") == version:
        return last
    builds = d.get("builds")
    rec = builds.get(version) if isinstance(builds, dict) else None
    return {**rec, "version": version} if isinstance(rec, dict) else {}


def remember_build(d: dict, rec: dict) -> None:
    """Record {version, display, channel, commit} as the last install and in the per-version list (bounded)."""
    builds = d.get("builds") if isinstance(d.get("builds"), dict) else {}
    old = d.get("installed")
    if isinstance(old, dict) and old.get("version") and old["version"] not in builds:
        builds[old["version"]] = {k: v for k, v in old.items() if k != "version"}     # the record from before builds
    builds.pop(rec["version"], None)
    builds[rec["version"]] = {k: v for k, v in rec.items() if k != "version"}
    while len(builds) > KNOWN_BUILDS_KEEP:
        builds.pop(next(iter(builds)))
    d["builds"], d["installed"] = builds, rec


def beta_label(home: Path | None, version: str | None = None) -> str | None:
    """'4.1.0-beta.7' when this copy was installed from the beta channel, else None (status/dashboard show it)."""
    v = version or build_info()["version"]
    rec = installed_record(home, v)
    if rec.get("channel") == "beta" and rec.get("version") == v and BETA_DISPLAY_RE.match(str(rec.get("display") or "")):
        return rec["display"]
    return None


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


def _offline_get(url: str, headers: dict) -> tuple[int, object]:
    """JARVES_TEST_OFFLINE=1: deterministic stand-in for GitHub (no releases published), never the network."""
    return (200, []) if "/releases" in url and "latest.json" not in url else (404, None)


def _httpx_get(url: str, headers: dict) -> tuple[int, object]:
    if os.environ.get("JARVES_TEST_OFFLINE"):
        return _offline_get(url, headers)
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
def canonical(manifest: dict, domain: bytes = SIGN_DOMAIN) -> bytes:
    """The exact signed bytes (same function in packaging/release.py). `domain` keeps the signatures of different
    manifest kinds (installer vs WhatsApp bridge) from being valid for each other."""
    return domain + json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def key_id(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def verify_manifest(envelope: object, public_keys: list[str] | tuple[str, ...], domain: bytes = SIGN_DOMAIN) -> dict:
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
    msg = canonical(envelope["manifest"], domain)
    for k in public_keys:
        try:
            pub = base64.b64decode(k.strip(), validate=True)
            Ed25519PublicKey.from_public_bytes(pub).verify(raw_sig, msg)
            return envelope["manifest"]
        except (ValueError, InvalidSignature):
            continue
    raise UpdateError("UPDATE_BAD_SIGNATURE", "latest.json is not signed by a trusted JARVES key; not using it")


def release_from_manifest(m: dict, base_url: str, current: str, channel: str = "stable") -> dict | None:
    """The same release shape pick_release() returns, from a verified manifest; None when not newer."""
    v, name, url = str(m.get("version") or ""), str(m.get("installer") or ""), str(m.get("url") or "")
    sha, size = str(m.get("sha256") or "").lower(), m.get("size")
    want = "beta" if channel == "beta" else "main"
    if m.get("product") != "jarves" or m.get("platform") != "windows-x64" or m.get("channel") != want:
        raise UpdateError("UPDATE_BAD_MANIFEST", f"the manifest is not a {'beta' if want == 'beta' else 'stable'} "
                                                 "JARVES Windows release")
    display = str(m.get("display_version") or "") if want == "beta" else ""
    if want == "beta" and not BETA_DISPLAY_RE.match(display):
        raise UpdateError("UPDATE_BAD_MANIFEST", "the beta manifest has no valid display_version (X.Y.Z-beta.N)")
    if not ASSET_RE.match(name) or ASSET_RE.match(name).group(1) != v or not re.fullmatch(r"[0-9a-f]{64}", sha) \
            or not isinstance(size, int) or size <= 0:
        raise UpdateError("UPDATE_BAD_MANIFEST", "latest.json is missing the version, installer, SHA256 or size")
    if not url:
        url = f"{base_url}/{name}"
    if not url.startswith("https://"):
        raise UpdateError("UPDATE_BAD_MANIFEST", "the installer link is not HTTPS")
    if not is_newer(v, current):
        return None
    return {"version": v, "display": display or v, "channel": want, "tag": f"{TAG_PREFIX}{v}", "page": None, "published": m.get("published"),
            "commit": m.get("commit"), "built": m.get("built"), "notes": "", "source": "hosted",
            "asset": {"name": name, "api_url": None, "download_url": url, "size": size, "digest": f"sha256:{sha}"},
            "sha256_asset": None, "previous": _previous_entry(m.get("previous"), base_url)}


def _previous_entry(p: object, base_url: str) -> dict | None:
    """The optional `previous` block of a signed manifest: the installer of the release before this one, so that a
    rollback has something to reinstall even when that version was not installed by the updater. A malformed block is
    ignored (rollback then needs a kept installer), never an error for the update itself."""
    if not isinstance(p, dict):
        return None
    v, name, sha, size = str(p.get("version") or ""), str(p.get("installer") or ""), str(p.get("sha256") or "").lower(), p.get("size")
    url = str(p.get("url") or "") or f"{base_url}/{name}"
    if version_tuple(v) is None or not ASSET_RE.match(name) or ASSET_RE.match(name).group(1) != v             or not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(size, int) or size <= 0             or not url.startswith("https://"):
        return None
    return {"version": v, "name": name, "url": url, "sha256": sha, "size": size}


def _beta_switch(home: Path, cfg) -> bool:
    """The one place the channel lives: the `beta_channel` feature switch (`jarvis update --channel beta`, the
    dashboard Updates page and `jarvis features on beta_channel` all flip it). Off = stable."""
    try:
        from . import features
        from .config import Config
        return features.Features(cfg if cfg is not None else Config(), home).enabled("beta_channel")
    except Exception:
        return False


def set_channel(home: Path, cfg, channel: str) -> str:
    """Switch the update channel (stable | beta). Returns the channel now in force."""
    if channel not in CHANNELS:
        raise UpdateError("UPDATE_BAD_CHANNEL", f"channel must be one of {', '.join(CHANNELS)}")
    from . import features
    from .config import Config
    features.Features(cfg if cfg is not None else Config(), home).set("beta_channel", channel == "beta")
    logs.info("updates", "UPDATE_CHANNEL_SET", "update channel changed", channel=channel)
    try:
        from . import auto_update
        auto_update.mark_channel_chosen(home)            # the first-run channel prompt is answered; installs nothing
    except Exception:
        pass
    return channel


class UpdateChecker:
    def __init__(self, home: Path, *, repo: str | None = None, http: Http | None = None,
                 now: Callable[[], float] = time.time, current: str | None = None, cfg=None,
                 base_url: str | None = None, public_keys: list[str] | tuple[str, ...] | None = None,
                 channel: str | None = None) -> None:
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
        # Channel: explicit argument > env (dev checkouts only) > the `beta_channel` feature switch > "stable".
        ch = channel or dev_env("JARVES_UPDATE_CHANNEL") or ("beta" if _beta_switch(self.home, cfg) else "stable")
        self.channel = ch if ch in CHANNELS else "stable"
        # Order: explicit argument > env JARVES_UPDATE_BASE_URL > config [update] base_url > the build's default
        # (CI variable baked into _build_info.json) > the public releases repo once a signing key is trusted >
        # "" (releases of the private repo, needs JARVES_UPDATE_TOKEN).
        base = base_url if base_url is not None else (
            os.environ.get("JARVES_UPDATE_BASE_URL") or getattr(ucfg, "base_url", "")
            or str(build_info().get("update_base_url") or "")
            or ((public_beta_url if self.channel == "beta" else public_releases_url)(
                dev_env("JARVES_RELEASES_REPO") or DEFAULT_RELEASES_REPO) if self.public_keys else ""))
        self.bad_base = bool(base and not base.strip().startswith("https://"))
        if self.bad_base:
            base = ""                                      # never fetch an update manifest over plain HTTP
        self.base_url = base.strip().rstrip("/")
        # A different source invalidates the cached answer (e.g. base_url was just set).
        self.source = f"hosted:{self.base_url}" if self.base_url else f"github:{self.repo}"
        if self.channel == "beta":
            self.source += "|beta"

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
                "notified": d.get("notified"), "repo": self.repo, "source": self.source, "policy": "notify_and_ask",
                "channel": self.channel, "beta_build": beta_label(self.home, self.current)}

    # --- check
    def _check_github(self) -> tuple[str | None, dict | None]:
        if self.channel == "beta":
            return ("the beta channel needs the signed release folder (a build that trusts a signing key, or "
                    "[update] base_url); it is not read from GitHub releases"), None
        url = f"https://api.github.com/repos/{self.repo}/releases?per_page=30"
        try:
            code, body = self.http(url, self._headers())
        except Exception as e:          # network down, DNS, TLS: an update check never breaks anything
            return f"no connection to GitHub ({type(e).__name__})", None
        if code == 200 and isinstance(body, list):
            return None, pick_release(body, self.current)
        if code in (401, 403, 404):
            return (f"releases not reachable on GitHub ({self.repo}: none published there, or rate limit). Signed "
                    f"installers are published at https://github.com/{DEFAULT_RELEASES_REPO}/releases; "
                    "or set [update] base_url"), None
        return f"GitHub answered HTTP {code}", None

    def _fetch_manifest(self) -> dict:
        """GET + verify <base_url>/latest.json; returns the verified manifest or raises UpdateError."""
        url = f"{self.base_url}/{manifest_name(self.channel)}"
        if not url.startswith("https://"):
            raise UpdateError("UPDATE_BAD_SOURCE", "update.base_url must be an https:// address")
        try:
            code, body = self.http(url, self._headers("application/json"))
        except Exception as e:
            raise UpdateError("UPDATE_OFFLINE", f"no connection to the update server ({type(e).__name__})") from None
        if code != 200:
            raise UpdateError("UPDATE_HTTP", f"the update server answered HTTP {code} for {manifest_name(self.channel)}")
        if isinstance(body, (bytes, bytearray)):
            try:
                body = json.loads(bytes(body).decode("utf-8"))
            except ValueError:
                raise UpdateError("UPDATE_BAD_MANIFEST", f"{manifest_name(self.channel)} is not JSON") from None
        return verify_manifest(body, self.public_keys)

    def _check_hosted(self) -> tuple[str | None, dict | None]:
        try:
            return None, release_from_manifest(self._fetch_manifest(), self.base_url, self.current, self.channel)
        except UpdateError as e:
            return e.message, None

    def check(self, force: bool = False) -> dict:
        d = self._load()
        if not force and d.get("checked_at") and d.get("source", self.source) == self.source \
                and self.now() - float(d.get("checked_ts") or 0) < CHECK_INTERVAL_S:
            return self.status()
        if self.bad_base:                                  # V4-UP-1: say why, never fall back to GitHub silently
            err, rel = "update.base_url must be an https:// address (plain http is never used)", None
        else:
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
        v = s["latest"].get("display") or s["latest"]["version"]
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
        tmp = out.with_name(out.name + ".partial")          # a failed write never leaves a half installer behind
        try:
            tmp.write_bytes(data)
            os.replace(tmp, out)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise UpdateError("UPDATE_DOWNLOAD_FAILED", f"the installer could not be saved ({type(e).__name__})") from e
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
            rel = release_from_manifest(self._fetch_manifest(), self.base_url, self.current, self.channel)
            if rel is None:
                raise UpdateError("UPDATE_NONE", "the update server no longer offers a newer version")
        refuse_downgrade(rel["version"], self.current)
        exe = self.download(rel)
        bak = backup_before_update(self.home, f"pre-update-{self.current}-to-{rel['version']}")
        keep_previous(self.home, self.current, rel["version"], bak, rel.get("previous"), signed, keep=exe.name)
        d = self._load()                     # remembered so status/dashboard can say "beta" after the restart
        remember_build(d, {"version": rel["version"], "display": rel.get("display") or rel["version"],
                           "channel": rel.get("channel") or "main", "commit": rel.get("commit")})
        self._save(d)
        return {"installer": str(exe), "backup": str(bak), "version": rel["version"], "signed": signed,
                "sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}   # = the verified value (download checked)


SILENT_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS")


def refuse_downgrade(candidate: str, current: str) -> None:
    """An installer that is not newer than this copy is never started by the update path; only an explicit rollback
    (rollback() below, which passes /ROLLBACK=1 to setup) may install an older version."""
    if not is_newer(candidate, current):
        raise UpdateError("UPDATE_DOWNGRADE", "this installer is not newer than the installed version; going back "
                                              "is only possible with `jarvis update --rollback`")


def installer_args(path: str | Path, *, silent: bool = False, rollback: bool = False, restart: bool = False) -> list[str]:
    """The setup command line. `silent` is the in-app updater path (no wizard, no message boxes; setup closes the
    running app itself); `restart` asks setup to start the Core again at the end."""
    args = [str(path)]
    if silent:
        args += list(SILENT_ARGS)
    if rollback:
        args.append("/ROLLBACK=1")
    if restart:
        args.append("/RESTARTJARVES=1")
    return args


def launch_installer(path: str | Path, verified: dict | None = None, *, silent: bool = False, rollback: bool = False,
                     restart: bool = False, popen: Callable | None = None) -> None:
    """Start setup, detached. By default the normal (visible) window, so the user sees and confirms every step;
    `silent` runs /VERYSILENT /SUPPRESSMSGBOXES (the in-app updater after the user said yes). `verified` is
    prepare_install()'s result; an installed build refuses to start anything without a signed, verified result
    whose SHA256 still matches the file on disk (checked again right before launch)."""
    if is_installed_build():
        if not verified or verified.get("signed") is not True or Path(verified.get("installer") or "") != Path(path):
            raise UpdateError("UPDATE_UNSIGNED", "not starting an installer that was not verified against the "
                                                 "signed update manifest")
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != verified.get("sha256"):
            raise UpdateError("UPDATE_CHECKSUM_MISMATCH", "the installer changed after it was verified; not "
                                                          "starting it")
    if not rollback and verified and verified.get("version"):
        refuse_downgrade(str(verified["version"]), build_info()["version"])
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    (popen or subprocess.Popen)(installer_args(path, silent=silent, rollback=rollback, restart=restart),
                                creationflags=flags, close_fds=True)
    logs.info("updates", "UPDATE_INSTALLER_STARTED", "installer started after user approval", installer=Path(path).name,
              mode="silent" if silent else "window")


# ---------------------------------------------------------------------- previous version, pending check, rollback
PREVIOUS_DIR = "previous"
PREVIOUS_FILE = "previous.json"
PENDING_FILE = "pending.json"


def _updates_dir(home: Path) -> Path:
    return Path(home) / "updates"


def _write_json(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_previous(home: Path) -> dict:
    """What a rollback would go back to ({} = nothing kept): {version, to, backup, installer, sha256, size, source,
    signed, schema}. The installer file lives in <updates>/previous/."""
    return _read_json(_updates_dir(home) / PREVIOUS_DIR / PREVIOUS_FILE)


def keep_previous(home: Path, current: str, to: str, backup_path: Path | str, entry: dict | None, signed: bool,
                  keep: str | None = None) -> dict:
    """Called by prepare_install after the new installer is verified and the backup exists: remember how to get
    back to `current`. The old installer is kept when this copy was itself installed by the updater (it is still in
    <updates>/); otherwise the signed manifest's `previous` block is recorded and downloaded on demand."""
    import shutil
    from . import migrations
    home = Path(home)
    udir, pdir = _updates_dir(home), _updates_dir(home) / PREVIOUS_DIR
    pdir.mkdir(parents=True, exist_ok=True)
    for old in pdir.iterdir():                              # one previous version only
        if old.is_file():
            old.unlink(missing_ok=True)
    rec: dict = {"version": current, "to": to, "backup": str(backup_path), "installer": None, "sha256": None,
                 "size": None, "source": None, "signed": bool(signed), "created_ts": time.time()}
    try:
        rec["schema"] = migrations.current(home)
    except migrations.MigrationError:
        rec["schema"] = None
    local = udir / f"JARVES-Setup-{current}.exe"
    if local.is_file():
        shutil.copy2(local, pdir / local.name)
        rec.update(installer=local.name, sha256=_sha256_file(local), size=local.stat().st_size)
    elif entry and entry.get("version") == current:
        rec["source"] = {k: entry[k] for k in ("name", "url", "sha256", "size")}
    _write_json(pdir / PREVIOUS_FILE, rec)
    for f in udir.glob("JARVES-Setup-*.exe"):               # only the new installer and the previous one stay
        if f.name != keep:
            f.unlink(missing_ok=True)
    _write_json(udir / PENDING_FILE, {"from": current, "to": to, "state": "installing", "attempts": 0,
                                      "ts": time.time()})
    logs.info("updates", "UPDATE_PREVIOUS_KEPT", "previous version recorded for rollback", version=current,
              ok=bool(rec["installer"] or rec["source"]))
    return rec


def rollback_info(home: Path) -> dict:
    """For the CLI and the dashboard: is a rollback possible, and to what."""
    rec = read_previous(home)
    have = bool(rec.get("installer") and (_updates_dir(home) / PREVIOUS_DIR / rec["installer"]).is_file())
    possible = bool(rec) and (have or bool(rec.get("source")))
    return {"possible": possible, "version": rec.get("version"), "from_version": rec.get("to"),
            "installer_kept": have, "downloads": bool(rec.get("source")) and not have,
            "backup": Path(rec["backup"]).name if rec.get("backup") else None,
            "pending": _read_json(_updates_dir(home) / PENDING_FILE).get("state")}


def _previous_installer(home: Path, rec: dict, http: Http | None) -> Path:
    """The previous installer, verified (SHA256 + size) against what was recorded when it was still trusted."""
    pdir = _updates_dir(home) / PREVIOUS_DIR
    if rec.get("installer"):
        f = pdir / rec["installer"]
        if f.is_file():
            if _sha256_file(f) != rec.get("sha256") or f.stat().st_size != rec.get("size"):
                raise UpdateError("ROLLBACK_BAD_INSTALLER", "the kept previous installer does not match its SHA256; "
                                                            "not using it")
            return f
    src = rec.get("source")
    if not src:
        raise UpdateError("ROLLBACK_NO_INSTALLER", "there is no previous installer to go back to; download that "
                                                   "version's installer and run it, your data backup is in "
                                                   "`jarvis backup list`")
    url = str(src.get("url") or "")
    if not url.startswith("https://"):
        raise UpdateError("ROLLBACK_BAD_INSTALLER", "the previous installer link is not HTTPS")
    try:
        code, body = (http or _httpx_get)(url, {"Accept": "*/*", "User-Agent": "jarves-rollback"})
    except Exception as e:
        raise UpdateError("UPDATE_OFFLINE", f"no connection to download the previous version ({type(e).__name__})") from None
    if code != 200 or not isinstance(body, (bytes, bytearray)):
        raise UpdateError("UPDATE_DOWNLOAD_FAILED", f"download of the previous version failed (HTTP {code})")
    data = bytes(body)
    if hashlib.sha256(data).hexdigest() != src.get("sha256") or len(data) != src.get("size"):
        raise UpdateError("UPDATE_CHECKSUM_MISMATCH", "the previous installer does not match its SHA256 or size")
    out = pdir / str(src["name"])
    tmp = out.with_name(out.name + ".partial")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    return out


def rollback(home: Path, *, approved: bool, current: str | None = None, http: Http | None = None,
             restore_data: bool = True, launch: bool = True, silent: bool = True, port: int | None = None,
             popen: Callable | None = None) -> dict:
    """`jarvis update --rollback` / the dashboard button: reinstall the previous version and put the data back the way
    it was before the update (the pre-update backup). Needs approval; the Core must be stopped (the CLI does that).

    Order: everything that can fail without changing anything comes first (approval, record, installer download and
    SHA256/size check); then the data restore (it takes its own safety checkpoint first); then setup is started."""
    from . import backup, migrations
    if approved is not True:
        raise UpdateError("UPDATE_NEEDS_APPROVAL", "a rollback is done only after the user approves it")
    if sys.platform != "win32":
        raise UpdateError("UPDATE_NOT_APPLICABLE", "there is no installer for this platform yet (Pi: planned)")
    home = Path(home)
    cur = current or build_info()["version"]
    rec = read_previous(home)
    if not rec or version_tuple(rec.get("version")) is None:
        raise UpdateError("ROLLBACK_NONE", "there is no previous version kept; nothing to roll back to")
    if not is_newer(cur, rec["version"]):
        raise UpdateError("ROLLBACK_NONE", "the installed version is not newer than the kept previous one")
    exe = _previous_installer(home, rec, http)
    restored = None
    bak = Path(rec["backup"]) if rec.get("backup") else None
    if restore_data and bak is not None:
        if not bak.is_file():
            raise UpdateError("ROLLBACK_NO_BACKUP", "the backup from before the update is missing; use "
                                                    "`--keep-data` to roll back the program only")
        try:
            res = backup.restore(bak, home, port=port)
        except backup.BackupError as e:
            raise UpdateError(e.code, e.message) from e
        restored = res["from"]
        if rec.get("schema") == migrations.LEGACY:           # a legacy backup predates schema.json: drop ours
            (home / migrations.MARKER).unlink(missing_ok=True)
    verified = {"installer": str(exe), "signed": bool(rec.get("signed")), "version": rec["version"],
                "sha256": _sha256_file(exe)}
    _write_json(_updates_dir(home) / PENDING_FILE, {"from": cur, "to": rec["version"], "state": "rolled_back",
                                                    "attempts": 0, "ts": time.time()})
    if launch:
        launch_installer(exe, verified, silent=silent, rollback=True, restart=True, popen=popen)
    logs.info("updates", "UPDATE_ROLLBACK", "rollback prepared", version=rec["version"], backup=restored or "none",
              mode="silent" if silent else "window")
    return {**verified, "restored": restored, "kept_data": not restore_data}


def note_start(home: Path, version: str | None = None) -> dict:
    """Core start, right after the pre-migration checkpoint. Counts starts of a freshly updated version that never
    reached "ready"; the second one asks for a rollback (offer_rollback). Also marks a pending record when setup
    failed and the old version is the one starting ({"state": "not_applied"}: the old version keeps working)."""
    home, v = Path(home), version or build_info()["version"]
    path = _updates_dir(home) / PENDING_FILE
    d = _read_json(path)
    if d.get("state") != "installing":
        return {"offer": False, "state": d.get("state")}
    if v == d.get("from"):
        d["state"] = "not_applied"
        _write_json(path, d)
        logs.warn("updates", "UPDATE_NOT_APPLIED", "update did not install; the previous version is running", version=v)
        return {"offer": False, "state": "not_applied"}
    if v != d.get("to"):
        return {"offer": False, "state": "installing"}
    d["attempts"] = int(d.get("attempts") or 0) + 1
    _write_json(path, d)
    offer = d["attempts"] >= 2 and rollback_info(home)["possible"]
    if offer:
        logs.warn("updates", "UPDATE_FIRST_START_FAILED", "the updated version did not become ready last time",
                  version=v, attempts=d["attempts"])
    return {"offer": offer, "state": "installing", "attempts": d["attempts"]}


def mark_healthy(home: Path, version: str | None = None) -> None:
    """Core reached CORE_READY: the update is confirmed; the rollback record stays (rollback remains possible)."""
    home, v = Path(home), version or build_info()["version"]
    path = _updates_dir(home) / PENDING_FILE
    d = _read_json(path)
    if d.get("state") == "installing" and v == d.get("to"):
        d.update(state="ok", attempts=0)
        _write_json(path, d)


HEALTH_FILE = "health.json"


def take_health_notice(home: Path) -> dict | None:
    """What the installer's health check did after an update that never became ready, returned ONCE so the owner is
    told a single time: {"status": "rolled_back" (previous program back, data untouched) | "rollback_failed" |
    "health_failed" (the data schema had changed, so the program was NOT swapped: the owner is offered the
    approval-gated `jarvis update --rollback`, which restores data and program), "from", "to"}."""
    path = _updates_dir(home) / HEALTH_FILE
    d = _read_json(path)
    if d.get("status") not in ("rolled_back", "rollback_failed", "health_failed") or d.get("notified"):
        return None
    d["notified"] = True
    _write_json(path, d)
    return d


def start_rollback_process(home: Path, popen: Callable | None = None) -> None:
    """Run `jarvis update --rollback --yes` as a separate process (it stops the Core, restores the data and starts
    setup), after the owner's approval was recorded. The Core cannot roll itself back while it is running."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    (popen or subprocess.Popen)([sys.executable, "-m", "jarves", "update", "--rollback", "--yes"],
                                creationflags=flags, close_fds=True, env={**os.environ, "JARVES_HOME": str(home)})
    logs.info("updates", "UPDATE_ROLLBACK_STARTED", "rollback process started after approval")


def offer_rollback(home: Path, *, ask: Callable | None = None, popen: Callable | None = None) -> bool:
    """After a failed first start: ask through approvals (medium risk; never automatic) and, only on a yes, start the
    rollback. True when it was started."""
    info = rollback_info(home)
    if not info["possible"]:
        return False
    if ask is None:
        from . import approvals
        ask = approvals.ask_owner
    ok, _ = ask(f"JARVES {info['from_version']} did not start properly after the update. Go back to {info['version']}?",
                title="Roll back the update?", kind="update", risk="medium",
                why="the new version failed to start; your data from before the update will be restored",
                details={"version": info["version"]})
    if ok:
        start_rollback_process(home, popen)
    return bool(ok)


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
                            "update.base_url; read-only); installs only after you approve, and one command or "
                            "button goes back to the previous version", **kw)


def _register_beta_feature() -> None:
    from . import features
    if "beta_channel" in features.CATALOG:
        return
    import inspect
    kw = {"targets": PLATFORMS["beta_channel"]} if "targets" in inspect.signature(features.register).parameters else {}
    features.register("beta_channel", "ערוץ בטא (גרסאות מוקדמות)", "Beta channel (early builds)", False, risk="medium",
                      about="gets every green main build automatically as a signed beta (latest-beta.json) instead "
                            "of waiting for a tested stable release; one command or button goes back to stable, "
                            "and the previous version can always be restored", **kw)


_register_feature()
_register_beta_feature()


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
    return 200, {**s, "enabled": _enabled(ctx), "platforms": PLATFORMS, "notice": u.notice("he"),
                 "rollback": rollback_info(ctx.home)}


def api_updates_channel(ctx, m, body):
    """POST {"channel": "stable"|"beta"}: the dashboard switch (same switch as `jarvis update --channel`)."""
    try:
        ch = set_channel(ctx.home, getattr(ctx, "cfg", None), str((body or {}).get("channel") or ""))
    except UpdateError as e:
        return 409, {"error": e.message, "code": e.code}
    return 200, {"channel": ch, "note": "the next check reads the " + ch + " channel; nothing is installed without your approval"}


def api_updates_check(ctx, m, body):
    if not _enabled(ctx):
        return 409, {"error": "update check is switched off (Features: update_check)"}
    return 200, _checker(ctx).check(force=True)


def api_updates_install(ctx, m, body):
    """POST {"approve": true, "silent": bool}: the dashboard click is the approval. Downloads, verifies, backs up,
    starts setup (silent = the in-app updater path: no wizard, setup restarts JARVES at the end)."""
    silent = (body or {}).get("silent") is True
    try:
        r = _checker(ctx).prepare_install((body or {}).get("approve") is True)
        launch_installer(r["installer"], verified=r, silent=silent, restart=silent)
    except UpdateError as e:
        return 409, {"error": e.message, "code": e.code}
    return 200, {**r, "note": "setup is running; it closes JARVES, keeps your data and starts it again" if silent else
                 "the setup window is open; it closes JARVES, keeps your data and can start it again"}


def api_updates_rollback(ctx, m, body):
    """POST {"approve": true}: go back to the previous version. The click asks the same approval as every medium-risk
    action (popup / tray / banner); only after a yes does a separate process stop the Core, restore the data from
    before the update and start setup."""
    if (body or {}).get("approve") is not True:
        return 409, {"error": "a rollback needs your approval", "code": "UPDATE_NEEDS_APPROVAL"}
    info = rollback_info(ctx.home)
    if not info["possible"]:
        return 409, {"error": "there is no previous version kept; nothing to roll back to", "code": "ROLLBACK_NONE"}
    from . import approvals
    ok, why = approvals.ask_owner(ctx.home, f"Go back from JARVES {info['from_version']} to {info['version']}?",
                                  title="Roll back the update?", kind="update", risk="medium",
                                  why="your data from before the update is restored; newer data is kept in a backup",
                                  details={"version": info["version"]})
    if not ok:
        return 409, {"error": f"not rolled back: {why}", "code": "ROLLBACK_NOT_APPROVED"}
    start_rollback_process(ctx.home)
    return 200, {"started": True, "version": info["version"],
                 "note": "JARVES stops, the previous version is installed and started again"}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/updates", api_updates)
    register_route("POST", r"/api/updates/channel", api_updates_channel)
    register_route("POST", r"/api/updates/check", api_updates_check)
    register_route("POST", r"/api/updates/install", api_updates_install)
    register_route("POST", r"/api/updates/rollback", api_updates_rollback)
    from . import auto_update
    auto_update.dashboard_routes(register_route)


from . import auto_update  # noqa: E402,F401  (registers the auto_update switch; imports this module)
