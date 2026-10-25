"""pytest plugin for test_v6_drive_wire.py (issue #216): every SkillRegistry built during the run gets a drive-mode
gate whose switch is OFF, so an existing registry test file proves drive mode off changes nothing. The gate counts
its consultations; the count is printed at the end so the caller can tell it really ran."""
from __future__ import annotations

from jarves import drive_mode
from jarves import skills as skills_mod

CALLS = {"n": 0}
_orig_init = skills_mod.SkillRegistry.__init__


class _CountingOffGate(drive_mode.Gate):
    def check(self, tool, args, *, origin, route, tainted):
        CALLS["n"] += 1
        return super().check(tool, args, origin=origin, route=route, tainted=tainted)


def _init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    self.drive = _CountingOffGate(active=lambda: False, queue=drive_mode.DriveQueue(drive_mode.Path("unused.json")))


def pytest_configure(config):
    skills_mod.SkillRegistry.__init__ = _init


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"DRIVE_OFF_GATE_CALLS={CALLS['n']}")
