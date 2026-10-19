"""Capability matrix (SW-V2 spec section 5): per feature, its status on each deployment target, with evidence, the
real blocker and a REAL roadmap issue. The dashboard Features page shows it next to the switch (jarves.features).

Statuses (spec section 1):
- verified_on_device       ran on the real target device; `evidence` says where (HANDOFF section / QA report).
- implemented_unverified   code path exists for this target, never run on the real device (CI is not a device).
- planned                  not built for this target yet.
- blocked                  cannot proceed without something external (a decision, a trained model, hardware).
- not_applicable           only for device-specific UI mechanics that have an equivalent control.

Rules (validated by `problems()`, tested in test_capabilities.py):
- every status other than verified_on_device / not_applicable has a `blocker`;
- every non-verified Pi status links a roadmap issue that EXISTS (created with `gh issue create`, never invented);
- a feature with no declared matrix shows "planned" with "platform status not declared" (never silently hidden).

Interface for other packages:
    features.register(..., targets={"windows": {"status": "implemented_unverified", "evidence": "...",
                                                "blocker": "..."},
                                    "linux_arm64_pi": {"status": "planned", "blocker": "...",
                                                       "roadmap_issue": PI_ROADMAP}},
                      permissions=["action"], settings="settings page / config key")
    capabilities.declare(fid, windows=..., linux_arm64_pi=..., permissions=[...])   # same, without a switch
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

TARGETS = ("windows", "linux_arm64_pi")
STATUSES = ("verified_on_device", "implemented_unverified", "planned", "blocked", "not_applicable")
READY = ("verified_on_device", "not_applicable")
# Real issues (created 2026-10-19 by WP-M4). Add new ones here only after `gh issue create` returned the URL.
TRACKING = "https://github.com/igalgerman/JARVIS/issues/17"
PI_ROADMAP = "https://github.com/igalgerman/JARVIS/issues/18"
KNOWN_ISSUES = {TRACKING, PI_ROADMAP}
_ISSUE_URL = re.compile(r"^https://github\.com/igalgerman/JARVIS/issues/\d+$")
LAST_CHECKED = "2026-10-19"


@dataclass
class Target:
    status: str
    evidence: str = ""
    blocker: str = ""
    roadmap_issue: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, not {self.status!r}")
        if self.roadmap_issue and not _ISSUE_URL.match(self.roadmap_issue):
            raise ValueError(f"roadmap_issue must be a github.com/igalgerman/JARVIS issue URL: {self.roadmap_issue}")


@dataclass
class Capability:
    targets: dict[str, Target]
    permissions: list[str] = field(default_factory=list)
    settings: str = ""                 # where it is configured (dashboard page / config key), "" = switch only
    last_checked: str = LAST_CHECKED
    declared: bool = True


MATRIX: dict[str, Capability] = {}


def _t(v) -> Target:
    return v if isinstance(v, Target) else Target(**v)


def declare(fid: str, *, windows, linux_arm64_pi, permissions: list[str] | None = None, settings: str = "",
            last_checked: str = LAST_CHECKED) -> Capability:
    c = Capability({"windows": _t(windows), "linux_arm64_pi": _t(linux_arm64_pi)}, list(permissions or []),
                   settings, last_checked)
    MATRIX[fid] = c
    return c


def get(fid: str) -> Capability:
    c = MATRIX.get(fid)
    if c is not None:
        return c
    missing = Target("planned", blocker="platform status not declared yet (spec section 5)",
                     roadmap_issue=PI_ROADMAP)
    return Capability({t: missing for t in TARGETS}, declared=False)


def view(fid: str) -> dict:
    """What the dashboard and `jarvis features` show."""
    c = get(fid)
    return {"targets": {k: asdict(v) for k, v in c.targets.items()}, "permissions": c.permissions,
            "settings": c.settings, "last_checked": c.last_checked, "declared": c.declared,
            "pi_ready": c.targets["linux_arm64_pi"].status in READY, "problems": problems(fid)}


def problems(fid: str) -> list[str]:
    """Matrix rule violations for one feature ([] = truthful and complete)."""
    c = get(fid)
    out = [] if c.declared else ["platform status not declared"]
    for name, t in c.targets.items():
        if t.status == "verified_on_device" and not t.evidence:
            out.append(f"{name}: verified without evidence")
        if t.status not in READY and not t.blocker:
            out.append(f"{name}: {t.status} without a blocker")
        if name == "linux_arm64_pi" and t.status not in READY and t.roadmap_issue not in KNOWN_ISSUES:
            out.append(f"{name}: no real roadmap issue linked")
    return out


# ---------------------------------------------------------------------- the built-in catalog (checked 2026-10-19)
# Evidence = where a real run on that device is recorded. "UNIT" = offline tests only (not device evidence).
_PI_NOT_RUN = "never run on a Raspberry Pi 5 (no device yet)"
_PI_OAUTH = ("Google sign-in redirects to a browser on the same device; the Pi needs sign-in from a paired PC or "
             "phone (spec section 8), not built")
_NO_GOOGLE = "owner has not connected Google yet; offline tests with API-shaped fakes only (test_google.py)"


def _pi(status: str, blocker: str = _PI_NOT_RUN) -> dict:
    return {"status": status, "blocker": blocker, "roadmap_issue": PI_ROADMAP}


def _win(status: str, evidence: str = "", blocker: str = "") -> dict:
    return {"status": status, "evidence": evidence, "blocker": blocker}


for _fid, _w, _p, _perm, _set in [
    ("music", _win("verified_on_device", "HANDOFF 'Real YouTube Music playback verified - 18 Oct 2026 (night)'"),
     _pi("implemented_unverified", "Pear Desktop ARM64 build + API Server plugin playback not yet verified on a Pi"),
     ["read", "action"], "config [music]"),
    ("web_search", _win("implemented_unverified", "user's live session 19 Oct used it (docs/reviews/"
                        "CONVERSATION_LOG_REVIEW_19OCT.md)", "a 19 Oct search stalled ~90 s; not re-verified live"),
     _pi("implemented_unverified"), ["read"], "config ai.web_search"),
    ("background_tasks", _win("implemented_unverified", "UNIT test_tasks.py", "no real background run checked"),
     _pi("implemented_unverified"), ["action"], "config ai.task_*"),
    ("memory_curator", _win("implemented_unverified", "UNIT test_workspace_memory.py", "not checked on real sessions"),
     _pi("implemented_unverified"), ["action"], ""),
    ("memory_llm_pass", _win("implemented_unverified", "UNIT", "no real paid extraction checked"),
     _pi("implemented_unverified"), ["action"], ""),
    ("diagnosis_llm_pass", _win("planned", blocker="switch only: no LLM diagnosis pass is wired yet"),
     _pi("planned", "not built on any platform yet"), ["read"], ""),
    ("google_calendar", _win("implemented_unverified", "UNIT test_google.py", _NO_GOOGLE), _pi("planned", _PI_OAUTH),
     ["read"], "dashboard Calendar page"),
    ("calendar_write", _win("implemented_unverified", "UNIT test_calendar_write.py", _NO_GOOGLE),
     _pi("planned", _PI_OAUTH), ["destructive (always confirmed)"], "dashboard Calendar page"),
    ("gmail_read", _win("implemented_unverified", "UNIT test_google.py", _NO_GOOGLE), _pi("planned", _PI_OAUTH),
     ["read"], ""),
    ("gmail_send", _win("implemented_unverified", "UNIT test_google.py", _NO_GOOGLE), _pi("planned", _PI_OAUTH),
     ["action", "destructive"], ""),
    ("camera", _win("implemented_unverified", "UNIT test_camera.py", "no hands-on camera check recorded"),
     _pi("implemented_unverified", "picamera2 backend never run on Pi Camera hardware"), ["read"], "config [camera]"),
    ("face_recognition", _win("planned", blocker="no faces Skill yet"), _pi("planned", "no faces Skill yet"),
     ["read"], ""),
    ("camera_when_away", _win("planned", blocker="no away-watch Skill yet"), _pi("planned", "no away-watch Skill yet"),
     ["read"], ""),
    ("wake_word", _win("verified_on_device", "HANDOFF 'Wake word through the air': speakers -> webcam mic, 4/4, "
                       "CPU 14-25 ms/frame (owner's own voice peaked ~0.17: threshold calibrated)"),
     _pi("implemented_unverified", "pyopen-wakeword linux-aarch64 wheel; Pi mic array/acoustics/CPU not measured"),
     ["read"], "dashboard Identity & voice page (wake phrase)"),
    ("greeting", _win("verified_on_device", "HANDOFF: owner's real runs 19 Oct heard the greeting"),
     _pi("implemented_unverified", "Pi audio output not tested"), ["read"], "config voice.greeting_delay_s"),
    ("chimes", _win("verified_on_device", "HANDOFF acoustic path: reconnect chime 0.5 s after 'Hey Jarvis'"),
     _pi("implemented_unverified", "Pi audio output not tested"), ["read"], ""),
    ("dashboard", _win("implemented_unverified", "headless screenshots docs/reviews/swv2_dashboard/ (not hands-on)",
                       "owner has not reviewed it hands-on yet"),
     _pi("implemented_unverified", "stdlib server; never run as a Pi service"), ["read", "action"], ""),
    ("dashboard_lan", _win("implemented_unverified", "UNIT test_dashboard_lan.py (spoofed host/origin, CSRF, "
                           "rate limit)", "never paired with a real phone"),
     _pi("implemented_unverified", "never paired with a real phone on the Pi"), ["action"],
     "dashboard Devices page / `jarvis dashboard --lan`"),
    ("workspace_agent_edit", _win("implemented_unverified", "UNIT test_workspace_memory.py",
                                  "not checked in a real conversation"),
     _pi("implemented_unverified"), ["action", "destructive"], "dashboard Workspace page"),
    ("voice_change", _win("implemented_unverified", "UNIT test_identity.py", "owner has not compared voices by ear"),
     _pi("implemented_unverified", "Pi audio output not tested"), ["action"], "dashboard Identity & voice page"),
    ("assistant_identity", _win("implemented_unverified", "UNIT test_identity.py",
                                "rename not yet tried in a real spoken conversation"),
     _pi("implemented_unverified", "Pi audio output not tested"), ["destructive (always confirmed)"],
     "dashboard Identity & voice page"),
]:
    declare(_fid, windows=_w, linux_arm64_pi=_p, permissions=_perm, settings=_set)

# Not a switch: the wake PHRASE itself (spec 6.3). Shown on the Identity page; a renamed assistant still wakes on
# "Hey Jarvis" until a custom wake model exists.
WAKE_PHRASE = {
    "current": "hey_jarvis", "spoken": "Hey Jarvis",
    "targets": {"windows": asdict(Target("verified_on_device", MATRIX["wake_word"].targets["windows"].evidence)),
                "linux_arm64_pi": asdict(MATRIX["wake_word"].targets["linux_arm64_pi"])},
    # `jarvis wake train "<phrase>"` (voice/wake_train.py): local synthetic samples -> small head on openWakeWord's
    # frozen front end. Changing the NAME alone never changes the phrase; the user trains and picks a phrase.
    "custom": {
        "windows": asdict(Target("implemented_unverified", evidence="trained on this PC 2026-10-19 from 4 local "
                                 "Windows voices (synthetic held-out voice: recall 96 %, 0 false wakes): "
                                 "docs/reviews/SWV2_WP-M4.md", blocker="never tried with a real voice and mic: run "
                                 "`jarvis wake test --model ...` and `--false-wakes 30`")),
        "linux_arm64_pi": asdict(Target("implemented_unverified", blocker="numpy head + espeak-ng voices; never "
                                        "trained or run on a Pi", roadmap_issue=PI_ROADMAP)),
    },
}
