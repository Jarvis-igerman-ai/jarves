"""Locate (or fetch, SHA-pinned, into a cache) the real external tools some tests drive, so nothing is ever skipped.

Cache dir: $JARVES_BUILD_CACHE, else <tmp>/jarves-build-cache (the same folder packaging/build_windows.ps1 and the CI
cache use). Godot 4.7.2 and Inno Setup 7.1.0 are pinned by SHA256 (the values in packaging/build_windows.ps1; the Linux
Godot zip is verified against the release's own SHA512-SUMS.txt)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GODOT_VERSION = "4.7.2-stable"
GH = "https://github.com"
GODOT_BASE = f"{GH}/godotengine/godot/releases/download/{GODOT_VERSION}"
GODOT_WIN = ("Godot_v4.7.2-stable_win64.exe.zip", "731980f9608d61333e5baf54a2ef17210acc7a538446c0cb9969f002aca1e953")
GODOT_LINUX = "Godot_v4.7.2-stable_linux.x86_64.zip"
INNO = ("innosetup-7.1.0-x64.exe", "0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f",
        f"{GH}/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe")


def cache_dir() -> Path:
    d = Path(os.environ.get("JARVES_BUILD_CACHE") or Path(tempfile.gettempdir()) / "jarves-build-cache")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(url: str, dest: Path) -> Path:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:      # noqa: S310 (pinned https URL)
        shutil.copyfileobj(r, f)
    tmp.replace(dest)
    return dest


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def godot_exe() -> str:
    """A Godot 4.7 binary that can run headless: $GODOT_EXE, the build cache, else downloaded + verified."""
    env = os.environ.get("GODOT_EXE")
    if env and Path(env).is_file():
        return env
    win = sys.platform == "win32"
    gdir = cache_dir() / "godot-4.7.2"
    exe = gdir / ("Godot_v4.7.2-stable_win64_console.exe" if win else "Godot_v4.7.2-stable_linux.x86_64")
    if not exe.is_file():
        dl = cache_dir() / "dl"
        dl.mkdir(exist_ok=True)
        name = GODOT_WIN[0] if win else GODOT_LINUX
        zf = _download(f"{GODOT_BASE}/{name}", dl / name)
        if win:
            assert _digest(zf, "sha256") == GODOT_WIN[1], "Godot SHA256 mismatch"
        else:
            sums = urllib.request.urlopen(f"{GODOT_BASE}/SHA512-SUMS.txt", timeout=60).read().decode()   # noqa: S310
            want = next(line.split()[0] for line in sums.splitlines() if line.strip().endswith(name))
            assert _digest(zf, "sha512") == want, "Godot SHA512 mismatch"
        with zipfile.ZipFile(zf) as z:
            z.extractall(gdir)
        if not win:
            exe.chmod(0o755)
    assert exe.is_file(), f"Godot not found in {gdir}"
    return str(exe)


def godot_project_imported(exe: str) -> None:
    """The face needs its assets imported once (the CI jobs do the same: godot --headless --import)."""
    proj = REPO / "godot"
    if not (proj / ".godot" / "imported").is_dir():
        subprocess.run([exe, "--headless", "--path", str(proj), "--import"], check=True, capture_output=True,
                       timeout=600)


def iscc_exe() -> Path:
    """Inno Setup compiler (Windows only): $JARVES_ISCC, the build cache, PATH, Program Files, else the pinned
    installer is run portable into the build cache (exactly like packaging/build_windows.ps1)."""
    assert sys.platform == "win32", "Inno Setup only runs on Windows"
    cands = [os.environ.get("JARVES_ISCC"), cache_dir() / "inno-7.1.0" / "ISCC.exe", shutil.which("ISCC")]
    for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if pf:
            cands += [Path(pf) / "Inno Setup 7" / "ISCC.exe", Path(pf) / "Inno Setup 6" / "ISCC.exe"]
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    name, sha, url = INNO
    dl = cache_dir() / "dl"
    dl.mkdir(exist_ok=True)
    setup = _download(url, dl / name)
    assert _digest(setup, "sha256") == sha, "Inno Setup SHA256 mismatch"
    target = cache_dir() / "inno-7.1.0"
    subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CURRENTUSER", "/PORTABLE=1",
                    f"/DIR={target}"], check=True, timeout=600)
    assert (target / "ISCC.exe").is_file(), "Inno Setup install failed"
    return target / "ISCC.exe"
