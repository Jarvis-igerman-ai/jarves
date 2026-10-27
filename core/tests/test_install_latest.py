"""`jarvis install-latest`: the flow with fakes (no network, no installer, no process, frozen clock)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from jarves import install_latest as il
from jarves.updates import UpdateError

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


class Fake:
    def __init__(self, available=True, error=None, core=True, cli=None, wait="done", prep_error=None, backup_error=False):
        self.calls: list = []
        self.available, self.error, self.core, self.wait_end = available, error, core, wait
        self.cli = cli or {}
        self.prep_error, self.backup_error = prep_error, backup_error
        self.channel = "stable"

    def deps(self) -> il.Deps:
        def rec(name, ret=None):
            def f(*a):
                self.calls.append(name)
                return ret(*a) if callable(ret) else ret
            return f

        def backup():
            self.calls.append("backup")
            if self.backup_error:
                raise OSError("disk full")
            return Path("jarves-backup-1.zip")

        def prepare(ch):
            self.calls.append("prepare")
            if self.prep_error:
                raise self.prep_error
            return {"installer": "C:/x/JARVES-Setup-4.5.1-beta.9.exe", "backup": "C:/b/pre-update.zip",
                    "version": "4.5.1", "signed": True}

        def run_cli(argv):
            self.calls.append("cli:" + " ".join(argv))
            key = argv[0]
            return self.cli.get(key, (0, "JARVES 4.5.1-beta.9 (commit abc)" if key == "update" else "ok"))

        return il.Deps(
            channel=self.channel, set_channel=rec("set_channel"), backup_now=backup,
            check=lambda ch: {"error": self.error, "available": self.available, "current": "4.5.0",
                              "latest": {"version": "4.5.1", "display": "4.5.1-beta.9"}},
            prepare=prepare, core_running=lambda: self.core, detect_running=lambda up: {"core": up},
            record_was_running=rec("record"), stop_core=rec("stop"),
            launch=lambda p, w, restart: self.calls.append(f"launch restart={restart}"),
            wait=lambda c: self.wait_end, run_cli=run_cli, rollback=rec("rollback", {}),
            now=lambda: NOW, say=lambda s: None)


def test_happy_path_order_and_report():
    f = Fake()
    rep = il.run(f.deps(), channel="beta")
    assert rep.ok and rep.changed and not rep.rolled_back
    assert f.calls[:7] == ["set_channel", "backup", "prepare", "record", "stop", "launch restart=True", "cli:update status"]
    assert [c for c in f.calls if c.startswith("cli:")] == ["cli:update status", "cli:doctor", "cli:selftest",
                                                            "cli:status --brief"]
    text = rep.render()
    assert text.startswith("PASS  install-latest  2026-10-27 09:00 UTC")
    assert "4.5.0 -> 4.5.1" in text and "revert id: pre-update.zip" in text and "jarvis update --rollback --yes" in text
    assert text.count("[PASS]") >= 6 and "[FAIL]" not in text


def test_failed_check_rolls_back():
    f = Fake(cli={"selftest": (1, "selftest FAIL: database")})
    rep = il.run(f.deps())
    assert not rep.ok and rep.rolled_back and "rollback" in f.calls
    assert ("selftest", False, "exit 1: selftest FAIL: database") in rep.checks
    assert rep.render().startswith("FAIL") and "rolled back" in rep.render()


def test_failed_installer_rolls_back_without_running_checks():
    f = Fake(wait="failed")
    rep = il.run(f.deps())
    assert not rep.ok and rep.rolled_back and not any(c.startswith("cli:") for c in f.calls)


def test_version_mismatch_is_a_failure():
    f = Fake(cli={"update": (0, "JARVES 4.5.0 (commit abc)")})
    rep = il.run(f.deps())
    assert not rep.ok and rep.rolled_back


def test_rollback_that_cannot_run_is_reported_with_the_command():
    f = Fake(wait="failed")
    d = f.deps()
    d.rollback = lambda: (_ for _ in ()).throw(UpdateError("ROLLBACK_NONE", "no previous"))
    rep = il.run(d)
    assert not rep.ok and not rep.rolled_back
    assert any(n == "automatic rollback" and not ok and "jarvis update --rollback --yes" in d_ for n, ok, d_ in rep.checks)


def test_core_not_running_installs_without_restart_flag_and_tolerates_status_1():
    f = Fake(core=False, wait="timeout", cli={"status": (1, "Core is NOT running")})
    rep = il.run(f.deps())
    assert rep.ok and "launch restart=False" in f.calls and "stop" not in f.calls


def test_backup_failure_stops_before_anything_changes():
    f = Fake(backup_error=True)
    rep = il.run(f.deps())
    assert not rep.ok and not rep.changed and f.calls == ["backup"]
    assert "prepare" not in f.calls and "stop" not in f.calls


def test_verification_failure_stops_before_stopping_the_core():
    f = Fake(prep_error=UpdateError("UPDATE_CHECKSUM_MISMATCH", "bad sha"))
    rep = il.run(f.deps())
    assert not rep.ok and not rep.changed and "stop" not in f.calls and "launch restart=True" not in f.calls
    assert "UPDATE_CHECKSUM_MISMATCH" in rep.render()


def test_up_to_date_and_check_error():
    assert il.run(Fake(available=False).deps()).ok
    f = Fake(error="offline")
    rep = il.run(f.deps())
    assert not rep.ok and "offline" in rep.render() and "backup" not in f.calls


def test_dry_run_changes_nothing():
    f = Fake()
    rep = il.run(f.deps(), channel="beta", dry_run=True)
    assert rep.ok and rep.dry_run and not rep.changed
    assert f.calls == []                                   # not even the channel switch
    assert rep.render().startswith("DRY RUN") and "would: backup now" in rep.render()


def test_bad_channel_and_parser():
    assert not il.run(Fake().deps(), channel="nightly").ok
    sub = argparse.ArgumentParser().add_subparsers(dest="cmd")
    il.add_cli(sub)
    ns = sub.choices["install-latest"].parse_args(["--channel", "beta", "--dry-run"])
    assert ns.channel == "beta" and ns.dry_run is True
