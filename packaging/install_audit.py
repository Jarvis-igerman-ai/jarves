"""Clean-machine residue audit for the silent-install test (SW-V4 L). Python stdlib only.

packaging/smoke_windows.ps1 installs the CI installer silently into a temp program folder with a disposable data
folder, then calls this module before the install, after the install and after the uninstall:

    python -I packaging/install_audit.py collect --app APP --home HOME --out state.json
    python -I packaging/install_audit.py installed  --before before.json --after after.json
    python -I packaging/install_audit.py uninstalled --before before.json --after after.json (--keep-data | --purged)

`collect` only READS: the HKCU Run key (autostart), the user PATH, the scheduled-task names, the Start-menu entry and the two
folders. The checks are pure functions over those dicts, so the unit tests (core/tests/test_installer_clean.py) drive them with
fake states and never touch a real registry, PATH or install.

Rules (the INSTALL_TEST.md rows):
  after a default silent install   autostart is OFF (no JARVES value in Run), PATH unchanged (the "path" task was not
                                   asked for), no JARVES scheduled task, the program folder and the Start-menu entry exist
  after an uninstall               program folder, Start-menu entry, Run value, PATH entry and scheduled tasks are all
                                   gone and every other Run value / PATH entry is exactly what it was before; the data
                                   folder is byte-for-byte kept (--keep-data) or gone (--purged, only when asked)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ---------------------------------------------------------------------- collecting (read-only)
def _real_run() -> dict:
    import winreg
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[name] = str(val)
                i += 1
    except OSError:
        pass
    return out


def _real_path() -> str:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, "Path")[0])
    except OSError:
        return ""


def _real_tasks() -> list[str]:
    import csv
    import io
    import subprocess
    try:
        r = subprocess.run(["schtasks", "/query", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({row[0].lstrip("\\") for row in csv.reader(io.StringIO(r.stdout)) if row})


def tree_hashes(root: Path) -> dict:
    """{relative path: sha256} of every file under root (empty when root is missing)."""
    root = Path(root)
    out = {}
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def collect(app: Path, home: Path, *, read_run: Callable[[], dict] = _real_run, read_path: Callable[[], str] = _real_path,
            list_tasks: Callable[[], list[str]] = _real_tasks, start_menu: Path | None = None) -> dict:
    menu = start_menu if start_menu is not None else Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / \
        "Start Menu" / "Programs" / "JARVES"
    app, home = Path(app), Path(home)
    return {"run": read_run(), "path": read_path(), "tasks": list_tasks(), "menu": menu.is_dir(),
            "app": app.is_dir() and any(app.iterdir()),
            "app_children": sorted(p.name for p in app.iterdir()) if app.is_dir() else [], "uninstaller": (app / "unins000.exe").is_file(),
            "data": tree_hashes(home), "data_dir": home.is_dir()}


# ---------------------------------------------------------------------- the checks
def _ours(name: str) -> bool:
    return name.lower().startswith("jarves") or name.lower().startswith("jarvis")


def _path_parts(p: str) -> list[str]:
    return [x for x in (s.strip() for s in p.split(";")) if x]


def installed_findings(before: dict, after: dict, path_entry: str | None = None) -> list[str]:
    """After a silent install. `path_entry` = the installer's own defaults were used (the "add jarvis to PATH" task is
    ticked by default), so the user PATH must be the old one plus exactly that folder; otherwise it must be unchanged."""
    bad = []
    if not after["app"]:
        bad.append("the program folder is empty or missing after the install")
    if not after["uninstaller"]:
        bad.append("no uninstaller (unins000.exe) was installed")
    if not after["menu"]:
        bad.append("no Start-menu entry")
    ours = sorted(k for k in after["run"] if _ours(k))
    if ours:
        bad.append(f"autostart is ON after a default install: Run value(s) {ours}")
    if after["run"] != before["run"]:
        bad.append("the Run key changed during a default install")
    want = ([path_entry] + [p for p in _path_parts(before["path"]) if p.rstrip("\\").lower() != path_entry.rstrip("\\").lower()]) if path_entry else _path_parts(before["path"])
    if [x.lower() for x in _path_parts(after["path"])] != [x.lower() for x in want]:
        bad.append("the user PATH is not " + ("the program folder first followed by the old PATH" if path_entry else
                                              "unchanged although the PATH task was not asked for"))
    new_tasks = [t for t in after["tasks"] if t not in before["tasks"] or _ours(t)]
    if new_tasks:
        bad.append(f"scheduled task(s) were created: {new_tasks}")
    return bad


def uninstalled_findings(before: dict, after: dict, *, keep_data: bool, data_before: dict | None = None) -> list[str]:
    """After the uninstall. `before` = the state before the install, `data_before` = the data hashes just before the uninstall."""
    bad = []
    if after["app"]:
        bad.append("program files are still there after the uninstall: " + ", ".join(after.get("app_children", [])))
    if after["menu"]:
        bad.append("the Start-menu entry is still there")
    left_run = sorted(k for k in after["run"] if _ours(k))
    if left_run:
        bad.append(f"autostart residue: Run value(s) {left_run}")
    if after["run"] != before["run"]:
        bad.append("the Run key is not what it was before the install")
    if _path_parts(after["path"]) != _path_parts(before["path"]):
        bad.append("the user PATH is not what it was before the install")
    left_tasks = [t for t in after["tasks"] if _ours(t)]
    if left_tasks:
        bad.append(f"scheduled task residue: {left_tasks}")
    if keep_data:
        want = data_before if data_before is not None else before["data"]
        if after["data"] != want:
            changed = sorted(set(want) ^ set(after["data"]) | {k for k in want if k in after["data"] and want[k] != after["data"][k]})
            bad.append(f"user data was not kept byte-for-byte: {changed[:5]}")
    elif after["data_dir"]:
        bad.append("the data folder is still there although the purge was asked for: " + ", ".join(list(after.get("data", {}))[:8]))
    return bad


# ---------------------------------------------------------------------- CLI
def _load(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8-sig"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="install_audit.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--app", type=Path, required=True)
    c.add_argument("--home", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    for name in ("installed", "uninstalled"):
        s = sub.add_parser(name)
        s.add_argument("--before", required=True)
        s.add_argument("--after", required=True)
        if name == "installed":
            s.add_argument("--path-entry", help="the install used the default tasks: PATH must gain exactly this folder")
        if name == "uninstalled":
            g = s.add_mutually_exclusive_group(required=True)
            g.add_argument("--keep-data", action="store_true")
            g.add_argument("--purged", action="store_true")
            s.add_argument("--data-before", help="state file taken just before the uninstall (its data hashes)")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        a.out.write_text(json.dumps(collect(a.app, a.home), indent=1), encoding="utf-8")
        return 0
    before, after = _load(a.before), _load(a.after)
    if a.cmd == "installed":
        bad = installed_findings(before, after, a.path_entry)
    else:
        db = _load(a.data_before)["data"] if a.data_before else None
        bad = uninstalled_findings(before, after, keep_data=a.keep_data, data_before=db)
    for b in bad:
        print("INSTALL AUDIT:", b)
    print(f"INSTALL AUDIT ({a.cmd}): " + ("FAIL" if bad else "PASS") + f" ({len(bad)} finding(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
