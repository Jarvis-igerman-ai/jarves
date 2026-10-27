"""`jarvis install-latest`: the flow with fakes (no network, no installer, no process, frozen clock)."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from jarves import install_latest as il
from jarves.updates import UpdateError

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


class Fake:
    def __init__(self, tmp=None, available=True, error=None, core=True, cli=None, wait="done", prep_error=None, backup_error=False, launch_error=None, installer_exits=True, spawn=False):
        self.tmp, self.launch_error, self.installer_exits, self.spawn = tmp, launch_error, installer_exits, spawn
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

        def launch(p, w, restart):
            self.calls.append(f"launch restart={restart}")
            if self.launch_error:
                raise self.launch_error
            return 4242

        def wait_installer():
            self.calls.append("wait_installer")
            return self.installer_exits

        def spawn(state):
            self.calls.append("spawn")
            self.spawned = state

        def publish(pid):
            self.calls.append(f"pid={pid}")

        return il.Deps(
            home=self.tmp, start_core=rec("start_core"), wait_installer=wait_installer,
            spawn_finisher=spawn if self.spawn else None, publish_pid=publish,
            channel=self.channel, set_channel=rec("set_channel"), backup_now=backup,
            check=lambda ch: {"error": self.error, "available": self.available, "current": "4.5.0",
                              "latest": {"version": "4.5.1", "display": "4.5.1-beta.9"}},
            prepare=prepare, core_running=lambda: self.core, detect_running=lambda up: {"core": up},
            record_was_running=rec("record"), stop_core=rec("stop"),
            launch=launch,
            wait=lambda c: self.wait_end, run_cli=run_cli, rollback=rec("rollback", {}),
            now=lambda: NOW)


def test_happy_path_order_and_report(tmp_path):
    f = Fake(tmp=tmp_path)
    rep = il.run(f.deps(), channel="beta")
    assert rep.ok and rep.changed and not rep.rolled_back
    assert f.calls[:8] == ["set_channel", "backup", "prepare", "record", "stop", "launch restart=True", "wait_installer",
                           "cli:update status"]
    assert [c for c in f.calls if c.startswith("cli:")] == ["cli:update status", "cli:doctor", "cli:selftest",
                                                            "cli:status --brief"]
    text = rep.render()
    assert text.startswith("PASS  install-latest  2026-10-27 09:00 UTC")
    assert "4.5.0 -> 4.5.1" in text and "revert id: pre-update.zip" in text and "jarvis update --rollback --yes" in text
    assert text.count("[PASS]") >= 6 and "[FAIL]" not in text


def test_failed_check_rolls_back(tmp_path):
    f = Fake(tmp=tmp_path, cli={"selftest": (1, "selftest FAIL: database")})
    rep = il.run(f.deps())
    assert not rep.ok and rep.rolled_back and "rollback" in f.calls
    assert ("selftest", False, "exit 1: selftest FAIL: database") in rep.checks
    assert rep.render().startswith("FAIL") and "rolled back" in rep.render()


def test_failed_installer_rolls_back_without_running_checks(tmp_path):
    f = Fake(tmp=tmp_path, wait="failed")
    rep = il.run(f.deps())
    assert not rep.ok and rep.rolled_back and not any(c.startswith("cli:") for c in f.calls)


def test_version_mismatch_is_a_failure(tmp_path):
    f = Fake(tmp=tmp_path, cli={"update": (0, "JARVES 4.5.0 (commit abc)")})
    rep = il.run(f.deps())
    assert not rep.ok and rep.rolled_back


def test_rollback_that_cannot_run_is_reported_with_the_command(tmp_path):
    f = Fake(tmp=tmp_path, wait="failed")
    d = f.deps()
    d.rollback = lambda: (_ for _ in ()).throw(UpdateError("ROLLBACK_NONE", "no previous"))
    rep = il.run(d)
    assert not rep.ok and not rep.rolled_back
    assert any(n == "automatic rollback" and not ok and "jarvis update --rollback --yes" in d_ for n, ok, d_ in rep.checks)


def test_core_not_running_installs_without_restart_flag_and_tolerates_status_1(tmp_path):
    f = Fake(tmp=tmp_path, core=False, wait="timeout", cli={"status": (1, "Core is NOT running")})
    rep = il.run(f.deps())
    assert rep.ok and "launch restart=False" in f.calls and "stop" not in f.calls


def test_backup_failure_stops_before_anything_changes(tmp_path):
    f = Fake(tmp=tmp_path, backup_error=True)
    rep = il.run(f.deps())
    assert not rep.ok and not rep.changed and f.calls == ["backup"]
    assert "prepare" not in f.calls and "stop" not in f.calls


def test_verification_failure_stops_before_stopping_the_core(tmp_path):
    f = Fake(tmp=tmp_path, prep_error=UpdateError("UPDATE_CHECKSUM_MISMATCH", "bad sha"))
    rep = il.run(f.deps())
    assert not rep.ok and not rep.changed and "stop" not in f.calls and "launch restart=True" not in f.calls
    assert "UPDATE_CHECKSUM_MISMATCH" in rep.render()


def test_up_to_date_and_check_error(tmp_path):
    assert il.run(Fake(tmp=tmp_path, available=False).deps()).ok
    f = Fake(tmp=tmp_path, error="offline")
    rep = il.run(f.deps())
    assert not rep.ok and "offline" in rep.render() and "backup" not in f.calls


def test_dry_run_changes_nothing(tmp_path):
    f = Fake(tmp=tmp_path)
    rep = il.run(f.deps(), channel="beta", dry_run=True)
    assert rep.ok and rep.dry_run and not rep.changed
    assert f.calls == []                                   # not even the channel switch
    assert rep.render().startswith("DRY RUN") and "would: backup now" in rep.render()


def test_bad_channel_and_parser(tmp_path):
    assert not il.run(Fake(tmp=tmp_path).deps(), channel="nightly").ok
    sub = argparse.ArgumentParser().add_subparsers(dest="cmd")
    il.add_cli(sub)
    ns = sub.choices["install-latest"].parse_args(["--channel", "beta", "--dry-run"])
    assert ns.channel == "beta" and ns.dry_run is True


def test_launch_failure_restarts_core_and_reports_cleanly(tmp_path):
    for err in (UpdateError("UPDATE_CHECKSUM_MISMATCH", "changed after verification"), OSError("Popen failed")):
        f = Fake(tmp=tmp_path, launch_error=err)
        rep = il.run(f.deps())                           # must not raise
        assert not rep.ok and not rep.changed and "start_core" in f.calls
        assert f.calls.index("stop") < f.calls.index("start_core")
        text = rep.render()
        assert text.startswith("FAIL") and "nothing was installed" in text and "restart Core" in text
        assert not any(c.startswith("cli:") for c in f.calls) and "rollback" not in f.calls
    f = Fake(tmp=tmp_path, core=False, launch_error=OSError("x"))
    il.run(f.deps())
    assert "start_core" not in f.calls                    # the Core was not running: nothing to restart


def test_checks_wait_for_the_setup_process_and_never_roll_back_while_it_runs(tmp_path):
    f = Fake(tmp=tmp_path, core=False, wait="timeout")
    rep = il.run(f.deps())
    assert rep.ok and f.calls.index("wait_installer") < f.calls.index("cli:update status")
    g = Fake(tmp=tmp_path, core=False, installer_exits=False)
    rep = il.run(g.deps())
    assert not rep.ok and not rep.rolled_back and "rollback" not in g.calls
    assert not any(c.startswith("cli:") for c in g.calls) and "still running" in rep.render()


def test_core_not_running_does_not_wait_for_the_core_watch(tmp_path):
    f = Fake(tmp=tmp_path, core=False, wait="failed")      # watch() would say failed/timeout: it must not be consulted
    assert il.run(f.deps()).ok


def test_spawned_finisher_runs_outside_the_installed_program(tmp_path):
    f = Fake(tmp=tmp_path, spawn=True)
    rep = il.begin(f.deps())
    assert rep.pending and rep.changed and rep.render().startswith("PENDING")
    assert f.calls.index("spawn") < f.calls.index("stop") < f.calls.index("launch restart=True") < f.calls.index("pid=4242")
    assert not any(c.startswith("cli:") or c == "rollback" for c in f.calls)
    state = __import__("json").loads(f.spawned.read_text(encoding="utf-8"))
    d = f.deps()
    done = il.finish(d, state, installer_running=False)    # what the waiter's `--finish` does in the new program
    assert done.ok and not done.pending and done.version == "4.5.1" and done.revert_id == "pre-update.zip"
    assert (tmp_path / "updates" / il.REPORT_FILE).read_text(encoding="utf-8").startswith("PASS")
    again = il.finish(f.deps(), state, installer_running=True)   # waiter timed out: no judgement, no rollback
    assert not again.ok and "rollback" not in f.calls


def test_finisher_script_quoting_and_order(tmp_path):
    evil = tmp_path / "O\u2019Brien \u2018x\u201a y\u201b it's"
    s = il.finisher_script(evil / "st.json", r"C:\Apps\JARVES\python\python.exe", evil / "r.txt", evil / "p.pid",
                           r"C:\u\JARVES-Setup-4.5.1.exe")
    for q in "'\u2018\u2019\u201a\u201b":
        assert q * 2 in s                                   # every quote character in the path is doubled
    assert "O\u2019\u2019Brien" in s and "it''s" in s
    assert s.index("cancel") < s.index("Wait-Process") < s.index("--finish")
    assert "--installer-running" in s and "jarvis update --rollback --yes" in s and "rollback()" not in s
    assert il._ps("a\nb'c") == "'ab''c'"


def test_waiter_script_bom_and_non_ascii_paths(tmp_path):
    p = tmp_path / "\u05d1\u05d3\u05d9\u05e7\u05d4.ps1"
    p.write_text(il.finisher_script(tmp_path / "\u05e2.json", "py.exe", tmp_path / "r", tmp_path / "p", "i.exe"),
                 encoding="utf-8-sig")
    raw = p.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf" and "\u05e2.json" in raw.decode("utf-8-sig")


def test_real_spawn_writes_bom_script_and_starts_hidden_waiter_outside_app(tmp_path, monkeypatch):
    import argparse
    import subprocess
    from jarves import updates
    from jarves.config import Config
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(updates, "is_installed_build", lambda: True)
    home = tmp_path / "h\u05d1"
    (home / "updates").mkdir(parents=True)
    d = il.real_deps(argparse.Namespace(), Config(), home)
    st = home / "updates" / il.STATE_FILE
    st.write_text('{"installer": "C:/x/Setup.exe"}', encoding="utf-8")
    assert d.spawn_finisher is not None
    d.spawn_finisher(st)
    (args, kw), = calls
    assert (home / "updates" / "install-latest-finish.ps1").read_bytes()[:3] == b"\xef\xbb\xbf"
    assert not kw["creationflags"] & 0x8                    # no DETACHED_PROCESS: it would cancel CREATE_NO_WINDOW
    assert kw["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW", 0) == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert Path(kw["cwd"]) == home / "updates"
    d.publish_pid(None)
    assert (home / "updates" / il.PID_FILE).read_text() == "cancel"
    d.publish_pid(77)
    assert (home / "updates" / il.PID_FILE).read_text() == "77"


def test_old_report_state_and_pid_are_removed_before_launch(tmp_path):
    up = tmp_path / "updates"
    up.mkdir()
    for n in (il.REPORT_FILE, il.STATE_FILE, il.PID_FILE):
        (up / n).write_text("PASS old", encoding="utf-8")
        os.utime(up / n, (1, 1))                           # an old finished run
    f = Fake(tmp=tmp_path, spawn=True)
    il.begin(f.deps())
    assert not (up / il.REPORT_FILE).exists() and not (up / il.PID_FILE).exists()
    assert (up / il.STATE_FILE).read_text(encoding="utf-8") != "PASS old"


def test_launch_failure_cancels_the_waiter_and_waiter_starts_before_setup(tmp_path):
    f = Fake(tmp=tmp_path, spawn=True, launch_error=OSError("x"))
    rep = il.begin(f.deps())
    assert not rep.ok and "pid=None" in f.calls and "start_core" in f.calls
    assert f.calls.index("spawn") < f.calls.index("stop") < f.calls.index("launch restart=True")


def test_wait_report_returns_new_report_only(tmp_path):
    (tmp_path / "updates").mkdir()
    p = tmp_path / "updates" / il.REPORT_FILE
    t = [0.0]
    assert il.wait_report(tmp_path, 0, timeout_s=6, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0]) is None
    p.write_text("PASS x\n", encoding="utf-8")
    assert il.wait_report(tmp_path, 0, timeout_s=6, sleep=lambda s: None, clock=lambda: 0.0) == "PASS x"
    assert il.wait_report(tmp_path, p.stat().st_mtime + 100, timeout_s=3, sleep=lambda s: None,
                          clock=iter([0, 1, 5]).__next__) is None


def test_second_begin_is_refused_while_a_run_is_pending(tmp_path):
    f = Fake(tmp=tmp_path, spawn=True)
    assert il.begin(f.deps()).pending
    g = Fake(tmp=tmp_path, spawn=True)
    rep = il.begin(g.deps())
    assert not rep.ok and rep.stopped_at == "pending" and "backup" not in g.calls and "launch restart=True" not in g.calls
    (tmp_path / "updates" / il.REPORT_FILE).write_text("PASS", encoding="utf-8")      # the first run ended
    h = Fake(tmp=tmp_path, spawn=True)
    assert il.begin(h.deps()).pending
    up = tmp_path / "updates"
    os.utime(up / il.STATE_FILE, (1, 1))                  # an abandoned, very old state file does not block
    os.utime(up / il.REPORT_FILE, (0, 0)) if (up / il.REPORT_FILE).exists() else None
    assert not il.pending_run(h.deps())


def test_waiter_never_judges_when_setup_was_not_seen():
    s = il.finisher_script(Path("st.json"), "py.exe", Path("r"), Path("p"), "i.exe")
    assert "$left = ($p -eq 0) -or" in s and "--installer-running" in s
    assert f"AddSeconds({il.SETUP_SEEN_S})" in s and il.SETUP_SEEN_S > 180


def test_finish_with_installer_running_flag_judges_nothing(tmp_path):
    f = Fake(tmp=tmp_path, spawn=True)
    il.begin(f.deps())
    state = __import__("json").loads(f.spawned.read_text(encoding="utf-8"))
    g = Fake(tmp=tmp_path)
    rep = il.finish(g.deps(), state, installer_running=True)
    assert not rep.ok and not rep.rolled_back and "rollback" not in g.calls and not any(c.startswith("cli:") for c in g.calls)
