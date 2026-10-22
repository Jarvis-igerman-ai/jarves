"""Installer-only adapter to the app's verified backup and migration tools. No network or secrets output."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import os
import sys
import time
from pathlib import Path


def safe_home(home: Path) -> Path:
    if home.is_symlink() or home.is_junction():
        raise ValueError("linked data directory")
    home = home.resolve()
    protected = [Path(home.anchor), Path.home()]
    protected += [Path(os.environ[k]) for k in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "WINDIR") if os.environ.get(k)]
    if any(home == p.absolute() for p in protected) or home.is_symlink() or home.is_junction():
        raise ValueError("unsafe data directory")
    return home


def profiles(home: Path) -> list[Path]:
    homes = [safe_home(home)]
    root = home / "profiles"
    if root.is_symlink() or root.is_junction():
        raise ValueError("linked profiles directory")
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_symlink() or child.is_junction():
                raise ValueError("linked profile directory")
            if child.is_dir():
                homes.append(child)
    return homes


def prepare(home: Path, state: Path) -> dict:
    from jarves import backup, migrations
    home = safe_home(home)
    restore_shims(Path(sys.executable).parent.parent / "install-state.json")
    records = []
    for profile in profiles(home):
        if not profile.is_dir() or not any(profile.iterdir()):
            continue
        archive = backup.checkpoint(profile, f"schema {migrations.current(profile)}->{migrations.LATEST}; installer upgrade")
        checked = backup.verify(archive)
        if not checked["ok"] or any("unreadable" in item["reason"] for item in checked["manifest"].get("excluded", [])):
            raise ValueError("backup verification failed; data was not migrated")
        records.append({"home": str(profile), "backup": str(archive), "schema_marker": (profile / migrations.MARKER).is_file()})
    record = {"format": 1, "home": str(home), "backups": records, "created": time.time()}
    state.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def complete(home: Path, state: Path, *, keep: bool = True, language: str = "en") -> dict:
    from jarves import backup, migrations
    home = safe_home(home)
    record = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else prepare(home, state)
    if record["home"] != str(home):
        raise ValueError("backup belongs to a different data directory")
    for item in record["backups"]:
        if not backup.verify(Path(item["backup"]))["ok"]:
            raise ValueError("pre-install backup no longer verifies")
    if not keep and home.is_dir() and any(home.iterdir()):
        # Same-volume rename retains credentials/models excluded by the app's portable backup policy.
        previous = home.with_name(home.name + ".previous-" + str(time.time_ns()))
        home.rename(previous)
        record["previous_data"] = str(previous)
        record["backups"] = [{**item, "home": str(previous / Path(item["home"]).relative_to(home)),
                               "backup": str(previous / Path(item["backup"]).relative_to(home))} for item in record["backups"]]
        state.write_text(json.dumps(record, indent=2), encoding="utf-8")
    home.mkdir(parents=True, exist_ok=True)
    if not (home / "config.toml").exists():
        (home / "config.toml").write_text('[voice]\nlanguage = "' + language + '"\n', encoding="utf-8")
    if keep:
        try:
            for item in record["backups"]:
                migrations.run(Path(item["home"]), checkpoint=lambda _home, _reason, p=item["backup"]: Path(p))
        except Exception:
            for item in record["backups"]:
                target = Path(item["home"])
                backup.restore(Path(item["backup"]), target, checkpoint_first=False)
                if not item["schema_marker"]:
                    (target / migrations.MARKER).unlink(missing_ok=True)
            raise
    record["complete"] = True
    state.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def restore_data(home: Path, state: Path) -> None:
    from jarves import backup, migrations
    home = safe_home(home)
    record = json.loads(state.read_text(encoding="utf-8"))
    if record["home"] != str(home):
        raise ValueError("rollback data directory mismatch")
    for item in record["backups"]:
        if not backup.verify(Path(item["backup"]))["ok"]:
            raise ValueError("rollback backup no longer verifies")
    if record.get("previous_data"):
        previous = safe_home(Path(record["previous_data"]))
        if previous.parent != home.parent or not previous.name.startswith(home.name + ".previous-"):
            raise ValueError("invalid previous data directory")
        after = home.with_name(home.name + ".after-upgrade-" + str(time.time_ns()))
        home.rename(after)
        try:
            previous.rename(home)
        except Exception:
            after.rename(home)
            raise
    else:
        for item in record["backups"]:
            target = safe_home(Path(item["home"]))
            if target != home and home not in target.parents:
                raise ValueError("profile outside data directory")
            backup.restore(Path(item["backup"]), target)
            if not item["schema_marker"]:
                (target / migrations.MARKER).unlink(missing_ok=True)


def retire_shims(app: Path, state: Path, *, user: Path | None = None, roaming: Path | None = None) -> list[dict]:
    user = user or Path.home()
    roaming = roaming or Path(os.environ.get("APPDATA", user / "AppData/Roaming"))
    roots = [user / ".local/bin", user / ".local/share/uv/tools/jarves/Scripts"]
    roots += list((roaming / "Python").glob("Python*/Scripts"))
    moved = []
    for root in roots:
        if root.is_symlink() or root.is_junction():
            continue
        for name in ("jarvis.exe", "jarvis.cmd", "jarvis.bat", "jarves.exe"):
            source = root / name
            if not source.is_file() or source.is_symlink() or source.stat().st_size > 2_000_000:
                continue
            data = source.read_bytes()
            if not any(marker in data.lower() for marker in (b"jarves.cli", b"jarves.__main__", b"-m jarves", b"core\\src\\jarves")):
                continue  # never move an unrelated executable or the developer's source launcher
            target = app / "rollback/shims" / (str(len(moved)) + "-" + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), target)
            moved.append({"original": str(source), "saved": str(target), "sha256": hashlib.sha256(data).hexdigest()})
    if state.is_file():
        record = json.loads(state.read_text(encoding="utf-8"))
        record["shims"] = moved
        state.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return moved


def restore_shims(state: Path) -> None:
    if not state.is_file():
        return
    record = json.loads(state.read_text(encoding="utf-8"))
    for shim in record.get("shims", []):
        saved, original = Path(shim["saved"]), Path(shim["original"])
        if saved.is_file() and not original.exists() and hashlib.sha256(saved.read_bytes()).hexdigest() == shim["sha256"]:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(saved), original)
    record["shims"] = []
    state.write_text(json.dumps(record, indent=2), encoding="utf-8")


def setup_url(url: str, static: Path) -> str:
    # v5/wizard exposes PAGES.setup in wizard.js; legacy setup.js alone is not the guided wizard.
    wizard = static / "wizard.js"
    registered = wizard.is_file() and "PAGES.setup" in wizard.read_text(encoding="utf-8")
    return url + ("#/setup" if registered else "")


def open_setup(home: Path) -> None:
    import webbrowser
    from jarves.config import load_config
    from jarves.dashboard import Dashboard, STATIC
    cfg, _ = load_config(home / "config.toml")
    dashboard = Dashboard(home, cfg)
    webbrowser.open(setup_url(dashboard.url, STATIC))
    try:
        dashboard.serve_forever()
    finally:
        dashboard.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("prepare", "complete", "open-setup", "restore-shims", "restore-data"))
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--state", type=Path)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--language", choices=("en", "he"), default="en")
    args = ap.parse_args(argv)
    try:
        if args.action == "open-setup":
            open_setup(args.home)
        elif args.action == "restore-data":
            restore_data(args.home, args.state)
        elif args.action == "restore-shims":
            restore_shims(args.state)
        elif args.action == "prepare":
            prepare(args.home, args.state)
        else:
            complete(args.home, args.state, keep=not args.fresh, language=args.language)
            if os.name == "nt":
                retire_shims(args.state.parent, args.state)
        return 0
    except Exception as error:
        # Never print exception text: a corrupt config could contain a credential.
        if args.state:
            args.state.with_suffix(".error").write_text(type(error).__name__ + ": backup/migration failed; keep the old data and retry.", encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main())
