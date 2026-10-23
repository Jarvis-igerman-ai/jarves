"""Download the prebuilt WhatsApp bridge (Issue #17): ONE owner-initiated step, so nobody builds Go.

Trust is the same as for JARVES updates and nothing new: the bridge is fetched only through an Ed25519-SIGNED manifest
(`whatsapp-bridge.json`, signed in CI with the same key as `latest.json`, but under its own signing domain so the two
kinds of signature are not interchangeable). The keys are the ones built into the app (updates.UPDATE_PUBLIC_KEYS).
The signed manifest carries the SHA256, the exact size and the https link of `whatsapp_bridge.exe`.

Rules (docs/RELEASE_HOSTING.md applies unchanged):
- owner-initiated only: install() needs approved=True; the dashboard route is local-only and the CLI is `jarvis
  whatsapp install`. Nothing downloads in the background, at start-up, or from a voice tool.
- installed builds ignore every override (env, config, parameters): only the built-in source and keys count.
  A source checkout may point JARVES_WHATSAPP_BRIDGE_BASE_URL (https) at its own signed folder for testing.
- SHA256 and size are checked on the bytes in memory, then again on the temporary file, then the file is moved into
  place atomically (<home>/bridge/whatsapp_bridge.exe) together with the signed manifest it was verified against.
- before EVERY start the exe is hashed again and the stored signed manifest is re-verified (verify_installed), so a
  file swapped or damaged later is refused with a clear message.
- never an older version than the one already installed (a replayed old signed manifest), never while the bridge runs.
- no secrets are involved and none are logged: the link carries no token and the log has version, size and SHA256 only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from . import logs, updates

SIGN_DOMAIN = b"jarves-whatsapp-bridge-manifest-v1\n"      # same constant in packaging/release.py
MANIFEST = "whatsapp-bridge.json"
RELEASE_TAG = "whatsapp-bridge"                             # the rolling release of DEFAULT_RELEASES_REPO that holds it
PRODUCT = "jarves-whatsapp-bridge"
PLATFORM = "windows-x64"
ASSET = "whatsapp_bridge.exe"
BASE_ENV = "JARVES_WHATSAPP_BRIDGE_BASE_URL"                # source checkouts only
MAX_BYTES = 150 * 1024 * 1024
SIDECAR = "whatsapp_bridge.manifest.json"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?$")

MESSAGES: dict[str, tuple[str, str]] = {            # code -> (English, Hebrew)
    "BRIDGE_NOT_PUBLISHED": (
        "The WhatsApp bridge download is not published yet, so nothing was downloaded. Nothing is wrong on this "
        "computer. Next step: ask the maintainer to publish the signed bridge (docs/HANDOFF.md), or reinstall JARVES "
        "from an installer that includes the bridge.",
        "הורדת גשר הוואטסאפ עוד לא פורסמה, ולכן לא הורד כלום. אין תקלה במחשב הזה. הצעד הבא: לבקש מהמפתח לפרסם את "
        "הגשר החתום, או להתקין מחדש את JARVES מתוכנית התקנה שכוללת את הגשר."),
    "BRIDGE_OFFLINE": (
        "Could not reach the download server. Check the internet connection and press the button again.",
        "אי אפשר להגיע לשרת ההורדה. בודקים את החיבור לאינטרנט ולוחצים שוב על הכפתור."),
    "BRIDGE_NO_KEY": (
        "This build has no update signing key, so the download cannot be trusted and was not made.",
        "בגרסה הזו אין מפתח חתימה לעדכונים, ולכן אי אפשר לסמוך על ההורדה והיא לא בוצעה."),
    "BRIDGE_BAD_SIGNATURE": (
        "The bridge manifest is not signed by a trusted JARVES key. Nothing was installed.",
        "רשימת הגשר לא חתומה במפתח מהימן של JARVES. לא הותקן כלום."),
    "BRIDGE_BAD_MANIFEST": (
        "The bridge manifest is incomplete or not for this computer. Nothing was installed.",
        "רשימת הגשר חסרה או לא מתאימה למחשב הזה. לא הותקן כלום."),
    "BRIDGE_CHECKSUM": (
        "The downloaded bridge does not match its signed SHA256 or size. It was discarded; nothing was installed.",
        "הגשר שהורד לא תואם ל-SHA256 או לגודל החתומים. הוא נמחק ולא הותקן כלום."),
    "BRIDGE_DOWNLOAD_FAILED": (
        "The bridge download failed. Nothing was installed. Press the button again in a minute.",
        "ההורדה של הגשר נכשלה. לא הותקן כלום. לוחצים שוב על הכפתור בעוד דקה."),
    "BRIDGE_UNSUPPORTED": (
        "The prebuilt bridge is for Windows only. On other systems build it from the source checkout.",
        "הגשר המוכן הוא ל-Windows בלבד. במערכות אחרות בונים אותו מקוד המקור."),
    "BRIDGE_RUNNING": (
        "The WhatsApp bridge is running. Stop it first (jarvis whatsapp stop), then download again.",
        "גשר הוואטסאפ פועל כרגע. עוצרים אותו (jarvis whatsapp stop) ואז מורידים שוב."),
    "BRIDGE_OLDER": (
        "The published bridge is older than the one installed. Nothing was changed.",
        "הגשר שפורסם ישן יותר מזה שמותקן. לא שונה כלום."),
    "BRIDGE_WRITE_FAILED": (
        "The bridge could not be saved on this computer. Nothing was changed.",
        "אי אפשר לשמור את הגשר במחשב הזה. לא שונה כלום."),
    "BRIDGE_TAMPERED": (
        "The installed WhatsApp bridge no longer matches its signed manifest (changed or damaged), so it was not "
        "started. Next step: press \"Download the WhatsApp bridge\" (jarvis whatsapp install) to replace it.",
        "גשר הוואטסאפ המותקן כבר לא תואם לרשימה החתומה שלו (שונה או ניזוק), ולכן הוא לא הופעל. הצעד הבא: ללחוץ על "
        "\"הורדת גשר הוואטסאפ\" (jarvis whatsapp install) כדי להחליף אותו."),
}


class BridgeInstallError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        en, he = MESSAGES[code]
        super().__init__(en)
        self.code, self.message, self.he, self.detail = code, en, he, detail


# ---------------------------------------------------------------------- where
def install_dir(home: Path) -> Path:
    return Path(home) / "bridge"


def installed_exe(home: Path) -> Path:
    return install_dir(home) / (ASSET if sys.platform == "win32" else ASSET.removesuffix(".exe"))


def sidecar(home: Path) -> Path:
    return install_dir(home) / SIDECAR


def base_url(override: str | None = None) -> str:
    """The folder that holds whatsapp-bridge.json. Installed builds: always the built-in public releases repo."""
    default = f"https://github.com/{updates.DEFAULT_RELEASES_REPO}/releases/download/{RELEASE_TAG}"
    if updates.is_installed_build():
        return default
    return (override or os.environ.get(BASE_ENV) or default).strip().rstrip("/")


def _keys(public_keys) -> tuple[str, ...]:
    if updates.is_installed_build() or public_keys is None:
        return tuple(updates.UPDATE_PUBLIC_KEYS)
    return tuple(public_keys)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------- the signed manifest
def verify_signed(envelope: object, public_keys) -> dict:
    """The manifest inside `envelope`, only if a trusted key signed it (bridge domain) and it describes THIS bridge."""
    keys = _keys(public_keys)
    if not keys:
        raise BridgeInstallError("BRIDGE_NO_KEY")
    try:
        m = updates.verify_manifest(envelope, keys, SIGN_DOMAIN)
    except updates.UpdateError as e:
        raise BridgeInstallError("BRIDGE_NO_KEY" if e.code == "UPDATE_NO_KEY" else
                                 "BRIDGE_BAD_SIGNATURE" if e.code == "UPDATE_BAD_SIGNATURE" else "BRIDGE_BAD_MANIFEST",
                                 e.message) from None
    sha, size, url = str(m.get("sha256") or "").lower(), m.get("size"), str(m.get("url") or "")
    if (m.get("product") != PRODUCT or m.get("platform") != PLATFORM or m.get("asset") != ASSET
            or not VERSION_RE.match(str(m.get("version") or "")) or not re.fullmatch(r"[0-9a-f]{64}", sha)
            or not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_BYTES
            or not url.startswith("https://")):
        raise BridgeInstallError("BRIDGE_BAD_MANIFEST", "fields")
    return m


def _decode(body: object) -> object:
    if isinstance(body, (bytes, bytearray)):
        try:
            return json.loads(bytes(body).decode("utf-8"))
        except ValueError:
            raise BridgeInstallError("BRIDGE_BAD_MANIFEST", "not JSON") from None
    return body


def installed_version(home: Path) -> str | None:
    try:
        env = json.loads(sidecar(home).read_text(encoding="utf-8"))
        return str(env["manifest"]["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def verify_installed(home: Path, public_keys=None) -> dict:
    """Run before EVERY start of the downloaded bridge: the stored signed manifest must still verify and the exe on
    disk must still have its exact SHA256 and size. Raises BRIDGE_TAMPERED otherwise; returns the manifest."""
    exe = installed_exe(home)
    try:
        m = verify_signed(json.loads(sidecar(home).read_text(encoding="utf-8")), public_keys)
        if exe.stat().st_size != m["size"] or _sha256_file(exe) != m["sha256"]:
            raise BridgeInstallError("BRIDGE_TAMPERED", "hash")
    except (OSError, ValueError):
        raise BridgeInstallError("BRIDGE_TAMPERED", "unreadable") from None
    except BridgeInstallError as e:
        if e.code == "BRIDGE_TAMPERED":
            raise
        raise BridgeInstallError("BRIDGE_TAMPERED", e.code) from None
    return m


def status(home: Path) -> dict:
    """Read-only, no network: is the downloaded bridge in place, and what does the download button do."""
    ver = installed_version(home)
    return {"downloaded": installed_exe(home).is_file() and ver is not None, "version": ver,
            "path": str(installed_exe(home)), "supported": sys.platform == "win32"}


# ---------------------------------------------------------------------- install
def install(home: Path, *, approved: bool, http: updates.Http | None = None, public_keys=None,
            base: str | None = None, platform: str | None = None, running: Callable[[], bool] | None = None) -> dict:
    """Download, verify and install the bridge. `approved` is the owner's press of the button / CLI command."""
    if approved is not True:
        raise PermissionError("the bridge is downloaded only when the owner asks")
    home = Path(home)
    if (platform or sys.platform) != "win32":
        raise BridgeInstallError("BRIDGE_UNSUPPORTED")
    if running is None:
        from . import whatsapp_bridge as wab
        running = lambda: wab.status_or_none(home) is not None        # noqa: E731
    if running():
        raise BridgeInstallError("BRIDGE_RUNNING")
    get = http or updates._httpx_get
    headers = {"Accept": "*/*", "User-Agent": f"jarves/{updates.build_info()['version']}"}   # no token, ever
    src = base_url(base)
    if not src.startswith("https://"):
        raise BridgeInstallError("BRIDGE_BAD_MANIFEST", "source is not https")
    try:
        code, body = get(f"{src}/{MANIFEST}", headers)
    except Exception as e:                                       # DNS, TLS, offline: never a crash
        raise BridgeInstallError("BRIDGE_OFFLINE", type(e).__name__) from None
    if code in (404, 410):
        raise BridgeInstallError("BRIDGE_NOT_PUBLISHED")
    if code != 200:
        raise BridgeInstallError("BRIDGE_OFFLINE", f"HTTP {code}")
    m = verify_signed(_decode(body), public_keys)
    have = installed_version(home)
    if have and (updates.version_tuple(m["version"].split("-")[0]) or ()) < (updates.version_tuple(have.split("-")[0]) or ()):
        raise BridgeInstallError("BRIDGE_OLDER")
    try:
        code, data = get(m["url"], headers)
    except Exception as e:
        raise BridgeInstallError("BRIDGE_DOWNLOAD_FAILED", type(e).__name__) from None
    if code != 200 or not isinstance(data, (bytes, bytearray)):
        raise BridgeInstallError("BRIDGE_DOWNLOAD_FAILED", f"HTTP {code}")
    data = bytes(data)
    if len(data) != m["size"] or hashlib.sha256(data).hexdigest() != m["sha256"]:
        raise BridgeInstallError("BRIDGE_CHECKSUM")
    exe, side = installed_exe(home), sidecar(home)
    tmp, stmp = exe.with_name(exe.name + ".partial"), side.with_name(side.name + ".partial")
    try:
        exe.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if _sha256_file(tmp) != m["sha256"]:                     # what reached the disk is what was signed
            raise BridgeInstallError("BRIDGE_CHECKSUM")
        stmp.write_text(json.dumps({"format": 1, "manifest": m, "signature": _decode(body)["signature"]},
                                   indent=1), encoding="utf-8")
        os.replace(tmp, exe)
        os.replace(stmp, side)
    except OSError as e:
        raise BridgeInstallError("BRIDGE_WRITE_FAILED", type(e).__name__) from None
    finally:
        tmp.unlink(missing_ok=True)
        stmp.unlink(missing_ok=True)
    logs.info("whatsapp", "WA_BRIDGE_DOWNLOADED", "WhatsApp bridge downloaded and verified", version=m["version"],
              size=m["size"], sha256=m["sha256"])
    return {"ok": True, "version": m["version"], "sha256": m["sha256"], "size": m["size"], "path": str(exe),
            "message": f"WhatsApp bridge {m['version']} downloaded and verified (SHA256 {m['sha256'][:12]}...).",
            "message_he": f"גשר הוואטסאפ {m['version']} הורד ואומת (SHA256 {m['sha256'][:12]}...)."}


def failure(e: BridgeInstallError) -> dict:
    """The JSON the dashboard and CLI show for a failed download."""
    return {"ok": False, "code": e.code, "message": e.message, "message_he": e.he}
