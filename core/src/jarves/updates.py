"""Build identity and the update check (SW-V2 M6, D16: notify + ask approval; never a silent install).

- build_info()   what this copy is: version, commit, build time, channel. Installed builds carry
                 jarves/_build_info.json (written by packaging/build_windows.ps1); a source checkout says "dev".
- UpdateChecker  read-only HTTPS GET of the GitHub releases of the JARVES repo, at most once a day (or on request).
                 It only looks at stable Windows releases (tag `win-v<version>`, asset `JARVES-Setup-<version>.exe`)
                 published by .github/workflows/package.yml after a green `main`; PR builds never appear there.
                 Result and "already told the user" state live in <state_dir>/updates.json.
- install        only after an explicit approval (CLI `jarvis update install` asks "yes"; the dashboard button is
                 the user's click). Steps: download -> verify SHA256 (refuse without a checksum) -> local backup of the
                 databases and settings -> stop the Core -> start the normal installer window (the user sees it).
                 The installer keeps user data (it lives in <state_dir>, not in the program folder).

Private repository: anonymous requests cannot see the releases (GitHub answers 404). A user may put a read-only
fine-grained token in secrets.env as JARVES_UPDATE_TOKEN (never shipped in the installer); otherwise the check
reports "unavailable" truthfully instead of "up to date".

Interface for other packages (tray, voice greeting, scheduler):
    from jarves.updates import UpdateChecker
    u = UpdateChecker(home); u.check()          # cheap when fresh (cached), safe to call at Core start
    text = u.notice("he")                       # "" or one sentence to say/show once per new version
    u.mark_notified()                           # after it was said/shown
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import shutil
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
# What a pre-update backup keeps: user data and settings. Credentials stay where they are (secrets.env, OAuth
# tokens) - an update does not touch them, and a backup folder must never become a copy of the keys.
BACKUP_FILES = ("config.toml", "features.json", "setup_done")
NO_BACKUP = ("secrets.env", "link_token", "google_token.json", "google_oauth.json", "pear_client_id")

# Windows / Pi status of the M6 features (spec section 5). jarves.features has no targets field yet, so this is
# the record the capability matrix (WP-M4) reads; the issue is real (created with `gh issue create`).
PI_ISSUE = "https://github.com/igalgerman/JARVIS/issues/19"
PLATFORMS = {
    "windows_installer": {
        "windows": {"status": "implemented_unverified",
                    "evidence": "CI: jarves-checks `installer` job (silent install/run/uninstall on windows-2025); "
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
            info.update({k: raw[k] for k in ("version", "commit", "built", "channel", "platform") if k in raw})
    except (OSError, ValueError):
        pass
    return info


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


class UpdateChecker:
    def __init__(self, home: Path, *, repo: str | None = None, http: Http | None = None,
                 now: Callable[[], float] = time.time, current: str | None = None) -> None:
        self.home = Path(home)
        self.repo = repo or os.environ.get("JARVES_UPDATE_REPO") or REPO
        self.http = http or _httpx_get
        self.now = now
        self.current = current or build_info()["version"]
        self.path = self.home / STATE_FILE

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
        h = {"Accept": accept, "User-Agent": f"jarves/{self.current}", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("JARVES_UPDATE_TOKEN", "").strip()
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def status(self) -> dict:
        d = self._load()
        rel = d.get("release")
        # A cached release that is no longer newer (this copy was updated meanwhile) is not an update.
        available = bool(rel and is_newer(rel.get("version", ""), self.current))
        return {"current": self.current, "build": build_info(), "available": available,
                "latest": rel if available else None, "checked_at": d.get("checked_at"), "error": d.get("error"),
                "notified": d.get("notified"), "repo": self.repo, "policy": "notify_and_ask"}

    # --- check
    def check(self, force: bool = False) -> dict:
        d = self._load()
        if not force and d.get("checked_at") and self.now() - float(d.get("checked_ts") or 0) < CHECK_INTERVAL_S:
            return self.status()
        url = f"https://api.github.com/repos/{self.repo}/releases?per_page=30"
        err, rel = None, None
        try:
            code, body = self.http(url, self._headers())
            if code == 200 and isinstance(body, list):
                rel = pick_release(body, self.current)
            elif code in (401, 403, 404):
                err = ("releases not reachable (private repository or rate limit). Ask the owner for access or set "
                       "JARVES_UPDATE_TOKEN (read-only) in secrets.env")
            else:
                err = f"GitHub answered HTTP {code}"
        except Exception as e:          # network down, DNS, TLS: an update check never breaks anything
            err = f"no connection to GitHub ({type(e).__name__})"
        d.update({"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "checked_ts": self.now(),
                  "error": err})
        if err is None:
            d["release"] = rel
        self._save(d)
        logs.info("updates", "UPDATE_CHECKED", "update check" + (f": {err}" if err else ""),
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
        out = self.home / "updates" / rel["asset"]["name"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        logs.info("updates", "UPDATE_DOWNLOADED", f"installer {rel['version']} verified", sha256=got, size=len(data))
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
        exe = self.download(rel)
        bak = backup_before_update(self.home, f"pre-update-{self.current}-to-{rel['version']}")
        return {"installer": str(exe), "backup": str(bak), "version": rel["version"]}


def launch_installer(path: str | Path) -> None:
    """Start the normal (visible) setup window, detached, so the user sees and confirms every step."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(path)], creationflags=flags, close_fds=True)
    logs.info("updates", "UPDATE_INSTALLER_STARTED", "installer started after user approval", installer=Path(path).name)


# ---------------------------------------------------------------------- backup before update / migration
def backup_before_update(home: Path, label: str) -> Path:
    """Consistent copy of every SQLite db (sqlite3 backup API, so a running Core's WAL is included) plus the plain
    settings files into <home>/backups/<label>-<time>/; each copy passes `PRAGMA integrity_check`. Credentials are
    never copied (NO_BACKUP). A full scheduled backup/restore is WP-M5's; this is the minimum before an install."""
    home = Path(home)
    dest = home / "backups" / f"{re.sub(r'[^A-Za-z0-9._-]', '_', label)}-{datetime.now():%Y%m%d-%H%M%S}"
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for db in sorted(home.glob("*.db")):
        target = dest / db.name
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise UpdateError("UPDATE_BACKUP_BAD", f"backup of {db.name} failed its integrity check")
            finally:
                dst.close()
        finally:
            src.close()
        copied.append(db.name)
    for name in BACKUP_FILES:
        if name not in NO_BACKUP and (home / name).is_file():
            shutil.copy2(home / name, dest / name)
            copied.append(name)
    if (home / "workspace").is_dir():
        shutil.copytree(home / "workspace", dest / "workspace")
        copied.append("workspace/")
    (dest / "BACKUP.json").write_text(json.dumps({"label": label, "created": datetime.now(timezone.utc).isoformat(),
                                                  "from_version": build_info()["version"], "files": copied},
                                                 indent=1), encoding="utf-8")
    logs.info("updates", "UPDATE_BACKUP_DONE", f"backup before update: {len(copied)} item(s)", path=dest.name)
    return dest


# ---------------------------------------------------------------------- feature switch + dashboard
def _register_feature() -> None:
    from . import features
    if "update_check" in features.CATALOG:
        return
    import inspect
    kw = {"targets": PLATFORMS["update_check"]} if "targets" in inspect.signature(features.register).parameters else {}
    features.register("update_check", "בדיקת עדכונים", "Update check", True, risk="low",
                      about="looks for a newer JARVES on GitHub once a day (read-only); installs only after you "
                            "approve", **kw)


_register_feature()


def _checker(ctx) -> UpdateChecker:
    return UpdateChecker(ctx.home)


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
    except UpdateError as e:
        return 409, {"error": e.message, "code": e.code}
    launch_installer(r["installer"])
    return 200, {**r, "note": "the setup window is open; it closes JARVES, keeps your data and can start it again"}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/updates", api_updates)
    register_route("POST", r"/api/updates/check", api_updates_check)
    register_route("POST", r"/api/updates/install", api_updates_install)
