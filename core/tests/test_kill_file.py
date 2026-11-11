"""JAR-211 / #967: the WhatsApp kill file stops the bridge "within two seconds", deterministically on every OS.

The Windows CI flake (#967) came from reading the kill state over HTTP while sockets were being torn down
(ConnectionAbortedError, WinError 10053). This test checks the kill-file watcher itself, in-process: no socket,
no wall-clock assertion. The "within 2 s" promise is the watcher's poll period (asserted by value); the waits
are generous event waits. 20 kill/release cycles per run so one run is the repeat check."""
from __future__ import annotations

import os

from whatsapp_fixtures import KILL_POLL_S, bridge, clock, home, wait_until  # noqa: F401

CYCLES = 20


def test_kill_file_stops_within_two_seconds_and_release(bridge, home):
    assert KILL_POLL_S <= 0.25 < 2.0          # the promise is the poll period, never a measured wall time
    kill = home / "whatsapp_bridge" / "KILL"
    for i in range(CYCLES):
        assert not bridge.killed and bridge.connection == "connected", i
        tmp = kill.with_name("KILL.tmp")              # atomic: the watcher never reads a half-written file
        tmp.write_text(f"owner_test_{i}\n", encoding="utf-8")
        os.replace(tmp, kill)
        wait_until(lambda: bridge.killed, timeout=30.0)
        assert bridge.connection == "disconnected" and bridge.kill_reason == f"owner_test_{i}", i
        kill.unlink()
        wait_until(lambda: not bridge.killed, timeout=30.0)
        assert bridge.connection == "connected" and bridge.kill_reason == "", i
