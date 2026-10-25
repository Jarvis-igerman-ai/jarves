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


def schema_of(profile: Path) -> int | None:
    """The data schema of one profile folder, read with the standard library only (the same rule as
    jarves.migrations.current: schema.json {"schema": n}; no marker = 1 with databases, 0 without). None = unreadable."""
    try:
        return int(json.loads((profile / "schema.json").read_text(encoding="utf-8"))["schema"])
    except FileNotFoundError:
        return 1 if any((profile / n).is_file() for n in ("store.db", "memory.db")) else 0
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prepare(home: Path, state: Path) -> dict:
    from jarves import backup, migrations
    home = safe_home(home)
    restore_shims(Path(sys.executable).parent.parent / "install-state.json")
    started = time.monotonic()
    records = []
    for profile in profiles(home):
        if not profile.is_dir() or not any(profile.iterdir()):
            continue
        archive = backup.checkpoint(profile, f"schema {migrations.current(profile)}->{migrations.LATEST}; installer upgrade")
        checked = backup.verify(archive)
        if not checked["ok"] or any("unreadable" in item["reason"] for item in checked["manifest"].get("excluded", [])):
            raise ValueError("backup verification failed; data was not migrated")
        records.append({"home": str(profile), "backup": str(archive), "schema_marker": (profile / migrations.MARKER).is_file(),
                        "schema_before": schema_of(profile)})
    # old_latest: the newest data schema the OLD program (this python's jarves) can open; a program-only rollback is
    # safe exactly while every profile's schema stays <= it (the old Core refuses only newer data)
    record = {"format": 1, "home": str(home), "backups": records, "created": time.time(), "old_latest": migrations.LATEST,
              "stage_timings_s": {"backup_and_verify": round(time.monotonic() - started, 3)}}
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
    started = time.monotonic()
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
    record.setdefault("stage_timings_s", {})["migration"] = round(time.monotonic() - started, 3)
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


HEALTH_FILE = "health.json"


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _same_version(a, b) -> bool:
    """4.0.1 == 4.0.1.0: trailing zero parts do not matter."""
    def norm(v) -> str:
        parts = str(v or "").strip().split(".")
        while len(parts) > 1 and parts[-1] == "0":
            parts.pop()
        return ".".join(parts)
    return bool(a) and bool(b) and norm(a) == norm(b)


def health(home: Path, timeout_s: float = 180.0, poll_s: float = 1.0, *, expect: str = "", sleep=time.sleep,
           clock=time.monotonic) -> str:
    """After an in-app update (setup started with /RESTARTJARVES=1): wait for the new Core to reach CORE_READY, which
    updates.mark_healthy records as pending.json state "ok". Returns "ok", "failed" (an update was installing and
    never became ready in time) or "unknown" (no update record, or a record for another version than the one this
    setup installs (`expect`): a manual install or a stale record, never rolled back by this check).
    Standard library only: it runs under the old or the new python."""
    home = safe_home(home)
    path = home / "updates" / "pending.json"
    first = _read_json(path)
    if first.get("state") not in ("installing", "ok"):
        return "unknown"
    if expect and not _same_version(first.get("to"), expect):
        return "unknown"                    # a stale or mismatched record: the healthy new version never marks it ok
    deadline = clock() + max(1.0, min(float(timeout_s), 600.0))
    while True:
        state = _read_json(path).get("state")
        if state == "ok":
            return "ok"
        if state != "installing":
            return "unknown"
        if clock() >= deadline:
            return "failed"
        sleep(poll_s)


def can_program_rollback(state: Path) -> bool:
    """True only when putting back the PREVIOUS PROGRAM alone is safe: every profile's data schema NOW is one the old
    program can open (<= old_latest, recorded by `prepare` under the old python). The old Core refuses only newer data
    (MIGRATION_DATA_NEWER); a legacy 1 -> 3 or an empty 0 -> 3 stamp is fine when the old program knows 3. When it is not
    safe the installer must not swap the program; the owner gets the approval-gated `jarvis update --rollback`, which
    restores the data too. A missing or unreadable record or marker, or a fresh-data install, counts as "not safe"."""
    try:
        record = json.loads(Path(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict) or record.get("previous_data"):
        return False
    old_latest = record.get("old_latest")
    if not isinstance(old_latest, int) or isinstance(old_latest, bool) or not record.get("home"):
        return False
    try:
        homes = profiles(Path(record["home"]))
    except (OSError, ValueError):
        return False
    for profile in homes:
        now = schema_of(profile)
        if now is None or now > old_latest:
            return False
    return True


HEALTH_STATUSES = ("rolled_back", "rollback_failed", "health_failed")


def note_rollback(home: Path, status: str) -> dict:
    """Record, once, what the health check did (the Core tells the owner at its next start, updates.take_health_notice):
    rolled_back = previous program back; rollback_failed = it could not be put back; health_failed = the data schema
    changed, so the program was NOT swapped and the owner is offered the approval-gated data+program rollback.
    Data is never touched here."""
    if status not in HEALTH_STATUSES:
        raise ValueError("unknown health status")
    home = safe_home(home)
    pend = _read_json(home / "updates" / "pending.json")
    rec = {"status": status, "from": pend.get("from"), "to": pend.get("to"), "at": time.time(), "notified": False}
    target = home / "updates" / HEALTH_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    os.replace(tmp, target)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("prepare", "complete", "open-setup", "restore-shims", "restore-data", "health",
                                       "note-rollback", "can-program-rollback"))
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--state", type=Path)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--expect", default="")
    ap.add_argument("--status", choices=HEALTH_STATUSES, default="rolled_back")
    ap.add_argument("--language", choices=("en", "he"), default="en")
    args = ap.parse_args(argv)
    try:
        if args.action == "health":
            # exit code: 0 = ready, 3 = never became ready (the installer puts the old program back), 1 = no update record
            # (the installer treats every other exit code, e.g. a python that crashes, as "failed" too)
            return {"ok": 0, "failed": 3}.get(health(args.home, args.timeout, expect=args.expect), 1)
        if args.action == "can-program-rollback":
            # exit code: 0 = the program alone may be put back, 5 = the data schema changed (or unknown): do not swap
            return 0 if can_program_rollback(args.state) else 5
        if args.action == "note-rollback":
            note_rollback(args.home, args.status)
            return 0
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
