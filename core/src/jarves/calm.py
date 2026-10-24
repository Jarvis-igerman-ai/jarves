"""Calm gates shared by everything JARVES may say without being asked (V5 follow-ups): the goal-done line and the
offers inside an open conversation. Pure functions, clock-injected, no sensor and no network.

- quiet hours: the alerts window (default 22:00-07:00, local zone). Unlike alerts there is NO urgent exception and
  turning the alerts' quiet window off does not make these lines speak at night.
- at the PC: a conversation is open, or the camera presence tracker sees the owner. Unknown = not at the PC.
"""
from __future__ import annotations

import re
from datetime import datetime

from .alerts import in_quiet

# the capability row of the V5 follow-up features (Windows / Pi), one shared declaration
TARGETS = {"windows": {"status": "implemented_unverified",
                       "evidence": "core/tests/test_v5_followups.py (unit tests: real SQLite, fake clock, no network)"},
           "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3",
                              "blocker": "pure Python + SQLite, no platform code; not run on a Raspberry Pi yet"}}
_HEBREW = re.compile(r"[א-ת]")


def quiet_hours_now(db, ts: float) -> bool:
    from .scheduler import zone
    start = str(db.setting("alerts.quiet_start") or "22:00")
    end = str(db.setting("alerts.quiet_end") or "07:00")
    return in_quiet(datetime.fromtimestamp(ts, zone(db.setting("scheduler.timezone"))), start, end)


def owner_at_pc(core) -> bool:
    if getattr(core, "live", None) is not None:
        return True                                  # an open conversation: the owner is talking to it
    cam = getattr(core, "camera", None)
    tracker = getattr(cam, "tracker", None)
    return bool(getattr(tracker, "present", False))


def uses_hebrew(*texts: str) -> bool:
    return any(_HEBREW.search(t or "") for t in texts)
