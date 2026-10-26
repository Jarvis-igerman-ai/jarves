"""Hotfix recovery entry (#467): restore the backed-up originals of a hotfix when Core / `jarvis` cannot start.

Standard library only: it imports nothing else from jarves (not the config, not the CLI), so it still runs when an
interrupted dependency hotfix damaged a package the normal `jarvis hotfix revert` needs. It reads the same manifest
(`<data folder>/hotfix/<id>/manifest.json`) and restores the same things: replaced files, added files, the bridge exe,
dependency files, the build info. Every saved original is verified by sha256 BEFORE anything is changed.

    python -m jarves.hotfix_recover [--list] [--latest | --id ID] [--dry-run] [--home DIR] [--install DIR]

Run it with the installed Python; `jarvis recover-hotfix` (installed jarvis.cmd) does exactly that, isolated (-I).
Stop JARVES first (tray Quit); this tool never starts or stops anything. Exit: 0 ok, 1 error, 2 refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

BUILD_INFO_NAME = "_build_info.json"
HOTFIX_DIR = "hotfix"
RESTORABLE = ("applied", "applying", "revert-failed")
COMMAND = "jarvis recover-hotfix   (or: python -I -m jarves.hotfix_recover --latest)"


class Recover(Exception):
    def __init__(self, code: str, message: str, refused: bool = False):
        super().__init__(message)
        self.code, self.message, self.refused = code, message, refused


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def default_home() -> Path:
    if os.environ.get("JARVES_HOME"):
        return Path(os.environ["JARVES_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "jarves"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "jarves"


def default_install() -> Path:
    return Path(__file__).resolve().parent


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _inside(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if root.resolve() not in p.parents:
        raise Recover("RECOVER_UNSAFE_PATH", f"manifest path escapes the install folder: {rel}", True)
    return p


def list_manifests(home: Path) -> list[tuple[Path, dict]]:
    root = Path(home) / HOTFIX_DIR
    out = []
    if root.is_dir():
        for d in sorted((x for x in root.iterdir() if (x / "manifest.json").is_file()), reverse=True):
            try:
                out.append((d, json.loads((d / "manifest.json").read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
    return out


def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".hotfix-tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _verify(d: Path, man: dict, install: Path) -> None:
    """Every saved original must exist and match its hash, every target path must stay inside the install; nothing
    is touched before all pass."""
    for item in man.get("replaced", []):
        _inside(install, item["path"])
        saved = d / "originals" / item["path"]
        if not saved.is_file() or _sha(saved) != item["sha256_original"]:
            raise Recover("RECOVER_ORIGINAL_BAD", f"saved original of {item['path']} is missing or changed")
    for item in man.get("added", []):
        _inside(install, item["path"])
    b = man.get("bridge")
    if b:
        root = install.parents[3] if len(install.parents) > 3 else install.parent
        _inside(root, os.path.relpath(b["dest"], root))
    if b and b.get("sha256_original"):
        saved = d / "bridge_original" / "exe"
        if not saved.is_file() or _sha(saved) != b["sha256_original"]:
            raise Recover("RECOVER_ORIGINAL_BAD", "the saved original bridge exe is missing or changed")
    for e in man.get("deps") or []:
        for item in e["backed_up"]:
            _inside(install.parent, item["path"])
            saved = d / "deps_originals" / _norm(e["name"]) / item["path"]
            if not saved.is_file() or _sha(saved) != item["sha256"]:
                raise Recover("RECOVER_ORIGINAL_BAD", f"saved original of {item['path']} ({e['name']}) is missing or changed")
        for rel in e["new_paths"]:
            _inside(install.parent, rel)
        if e.get("new_dist"):
            _inside(install.parent, e["new_dist"])


def _prune(sp: Path, rel: str) -> None:
    p = (sp / rel).parent
    while p != sp and sp in p.parents:
        try:
            p.rmdir()
        except OSError:
            break
        p = p.parent


def _restore_bridge(d: Path, b: dict) -> None:
    dest = Path(b["dest"])
    if b.get("sha256_original"):
        _atomic_copy(d / "bridge_original" / "exe", dest)
    else:
        dest.unlink(missing_ok=True)
    side = dest.with_name(dest.name + ".sha256")
    if b.get("had_sidecar") and (d / "bridge_original" / "sidecar").is_file():
        _atomic_copy(d / "bridge_original" / "sidecar", side)
    else:
        side.unlink(missing_ok=True)


def _restore_deps(d: Path, entries: list[dict], sp: Path) -> list[str]:
    done = []
    for e in reversed(entries):
        for rel in e["new_paths"]:
            f = sp / rel
            if f.is_file() or f.is_symlink():
                f.unlink()
                _prune(sp, rel)
        if e.get("new_dist") and (sp / e["new_dist"]).is_dir():
            shutil.rmtree(_inside(sp, e["new_dist"]), ignore_errors=True)
        for item in e["backed_up"]:
            _atomic_copy(d / "deps_originals" / _norm(e["name"]) / item["path"], sp / item["path"])
        done.append(f"{e['name']}=={e.get('old_version') or '(removed)'}")
    return done


def _check_unlocked(man: dict, install: Path) -> None:
    """#501: a running Core locks its exe/.pyd/.dll files on Windows; probe every file we are about to replace and
    refuse with one clear message before anything is changed."""
    paths = [install / i["path"] for i in man.get("replaced", [])] + [install / i["path"] for i in man.get("added", [])]
    if man.get("bridge"):
        paths.append(Path(man["bridge"]["dest"]))
    sp = install.parent
    for e in man.get("deps") or []:
        paths += [sp / i["path"] for i in e["backed_up"]] + [sp / r for r in e["new_paths"]]
    for p in paths:
        if p.is_file():
            try:
                with open(p, "r+b"):
                    pass
            except PermissionError:
                raise Recover("RECOVER_CORE_RUNNING", f"{p.name} is locked: JARVES (Core or the bridge) is still running. "
                              "Quit it from the tray and run this again. Nothing was changed.", True) from None


def recover(install: Path, home: Path, which: str | None = None, *, dry_run: bool = False) -> dict:
    install, home = Path(install), Path(home)
    found = list_manifests(home)
    if not found:
        raise Recover("RECOVER_NONE", f"no hotfix backup found in {home / HOTFIX_DIR}", True)
    if which:
        pick = [x for x in found if x[1].get("id") == which or x[0].name == which]
        if not pick:
            raise Recover("RECOVER_UNKNOWN_ID", f"no hotfix backup with id {which} (use --list)", True)
        d, man = pick[0]
    else:                                              # #501: the newest backup that is still applied, not a reverted one
        d, man = next((x for x in found if x[1].get("status") in RESTORABLE), found[0])
    if man.get("status") not in RESTORABLE:
        raise Recover("RECOVER_NOT_NEEDED", f"hotfix {man.get('id')} is already {man.get('status')}: nothing to restore", True)
    _verify(d, man, install)
    if not dry_run:
        _check_unlocked(man, install)
    files = [i["path"] for i in man.get("replaced", [])] + [i["path"] for i in man.get("added", [])]
    result = {"status": "dry-run" if dry_run else "restored", "id": man.get("id"), "files": files,
              "deps": [e["name"] for e in man.get("deps") or []], "bridge": bool(man.get("bridge"))}
    if dry_run:
        return result
    done: list[str] = []
    try:
        if man.get("bridge"):
            _restore_bridge(d, man["bridge"])
            done.append("bridge")
        if man.get("deps"):
            done += _restore_deps(d, man["deps"], install.parent)
        for item in man.get("replaced", []):
            _atomic_copy(d / "originals" / item["path"], install / item["path"])
            done.append(item["path"])
        for item in man.get("added", []):
            (install / item["path"]).unlink(missing_ok=True)
            done.append(item["path"])
        saved_bi = d / "originals" / BUILD_INFO_NAME
        if saved_bi.is_file():
            _atomic_copy(saved_bi, install / BUILD_INFO_NAME)
        else:
            (install / BUILD_INFO_NAME).unlink(missing_ok=True)
    except OSError as e:                               # #501: report the exact partial state; a rerun finishes it
        man["status"] = "revert-failed"
        man["partial"] = list(done)
        (d / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
        raise Recover("RECOVER_PARTIAL", f"stopped part way ({e}). Already restored: {', '.join(done) or 'nothing'}. "
                      f"Run {COMMAND} again to finish; nothing is lost.") from e
    man["status"] = "reverted"
    (d / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    result["restored"] = done
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis recover-hotfix",
                                 description="Restore the originals of a hotfix without starting JARVES (stdlib only).")
    ap.add_argument("--list", action="store_true", help="list the hotfix backups and their status")
    ap.add_argument("--latest", action="store_true", help="restore the newest hotfix backup (the default action)")
    ap.add_argument("--id", help="restore this backup id (see --list)")
    ap.add_argument("--dry-run", action="store_true", help="verify the backup and show what would be restored; change nothing")
    ap.add_argument("--home", type=Path, help="data folder (default: JARVES_HOME or the per-user folder)")
    ap.add_argument("--install", type=Path, help="the installed jarves package folder (default: this one)")
    a = ap.parse_args(argv)
    home, install = a.home or default_home(), a.install or default_install()
    try:
        if a.list:
            rows = list_manifests(home)
            for d, m in rows:
                print(f"{m.get('id', d.name)}  {m.get('status')}")
            if not rows:
                print(f"no hotfix backups in {home / HOTFIX_DIR}")
            return 0
        r = recover(install, home, a.id, dry_run=a.dry_run)
    except Recover as e:
        print(f"{'REFUSED' if e.refused else 'ERROR'} ({e.code}): {e.message}", file=sys.stderr)
        return 2 if e.refused else 1
    except OSError as e:
        print(f"ERROR (RECOVER_IO): {e}. Is JARVES still running? Quit it from the tray and try again.", file=sys.stderr)
        return 1
    if r["status"] == "dry-run":
        print(f"would restore hotfix {r['id']}: {len(r['files'])} file(s), {len(r['deps'])} dependency package(s)"
              + (", the bridge exe" if r["bridge"] else "") + ". Nothing was changed.")
    else:
        print(f"restored the original files of hotfix {r['id']}: {len(r['restored'])} item(s). Start JARVES again (jarvis start).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
