"""#166: the marker a standalone dashboard leaves for the installer (safe flags only, ours only, never an error)."""
import json
import os
from types import SimpleNamespace

from jarves import runmarks


def test_marker_holds_pid_and_only_the_safe_flags(tmp_path):
    args = SimpleNamespace(lan=True, mobile=True, tls_selfsigned=False, tailnet_only=True, page="#/setup", port=5)
    flags = runmarks.dashboard_flags(args)
    assert flags == ["--lan", "--mobile", "--tailnet-only"]
    runmarks.write_dashboard(tmp_path, flags + ["--page=#/secret", "--port=1"])
    d = json.loads((tmp_path / runmarks.DASHBOARD_FILE).read_text("utf-8"))
    assert d == {"pid": os.getpid(), "flags": ["--lan", "--mobile", "--tailnet-only"]}


def test_marker_is_removed_by_its_owner_only(tmp_path):
    runmarks.write_dashboard(tmp_path, [], pid=1234)
    runmarks.clear_dashboard(tmp_path, pid=999)                        # another dashboard's stop keeps it
    assert (tmp_path / runmarks.DASHBOARD_FILE).exists()
    runmarks.clear_dashboard(tmp_path, pid=1234)
    assert not (tmp_path / runmarks.DASHBOARD_FILE).exists()
    runmarks.clear_dashboard(tmp_path)                                 # nothing there: no error
    (tmp_path / runmarks.DASHBOARD_FILE).write_text("{broken", "utf-8")
    runmarks.clear_dashboard(tmp_path)


def test_unwritable_home_never_stops_the_dashboard(tmp_path):
    runmarks.write_dashboard(tmp_path / "missing" / "dir", [])
