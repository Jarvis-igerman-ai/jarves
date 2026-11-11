"""JAR-229: the local 'what went wrong' log (beta, OFF by default).

When it is on, JARVES appends ONE privacy-safe row per failure to `<home>/failure_log.jsonl`: the kind, the day and
an optional tool name and error code. Never what was said, never free text: a tool or code that is not a short plain
identifier is stored as "other". The file stays on this PC and is capped at MAX_ROWS rows. `jarvis report weekly`
turns the last 7 days into a short most-frequent-first list the factory can turn into bug tickets.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from . import features as feat, logs

FEATURE = "failure_log"
KINDS = ("not_understood", "tool_error", "repeated_request", "user_correction")
MAX_ROWS = 2000
_SAFE = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,39}")
ABOUT = ("keeps a local list of what went wrong (tool errors now; not understood, repeated request and wrong/stop are "
         "planned): only the kind, day, tool name and error code, never what was said; jarvis report weekly lists them")

feat.register(FEATURE, "יומן מה השתבש", "What went wrong log", False, stage="beta", about=ABOUT,
              targets={"windows": {"status": "implemented_unverified",
                                   "blocker": "unit tests only (test_failure_log.py); not run on the owner's PC"},
                       "linux_arm64_pi": {"status": "implemented_unverified",
                                          "blocker": "unit tests only; not run on a Pi",
                                          "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3"}},
              permissions=["local file only; nothing leaves the PC"], settings="jarvis report weekly")


def path(home) -> Path:
    return Path(home) / "failure_log.jsonl"


def _now() -> datetime:                     # the one clock; tests replace it
    return datetime.now()


def _safe(v: str) -> str:
    return v if not v or _SAFE.fullmatch(v) else "other"


def record(home, kind: str, *, tool: str = "", code: str = "", on: bool, now: datetime | None = None) -> bool:
    """Append one metadata row when the feature is `on`. Returns True when a row was written."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not on:
        return False
    row = {"day": (now or _now()).date().isoformat(), "kind": kind, "tool": _safe(tool), "code": _safe(code)}
    p = path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    lines = (lines + [json.dumps(row, ensure_ascii=False)])[-MAX_ROWS:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, p)                      # atomic: a crash never leaves a truncated log
    return True


def attach(core):
    """Record every failed tool call (tool_error) in front of the existing registry failure hook (self-diagnosis).
    Only registered tool names are kept; anything else becomes "other"."""
    reg, prev = core.registry, core.registry.on_failure

    def hook(name, res, route):
        try:
            record(core.home, "tool_error", tool=name if name in reg._tools else "other",
                   code=str(getattr(res, "code", "")), on=core.features.active(FEATURE))
        except Exception:
            logs.warn("failure_log", "FAILURE_LOG_WRITE_FAILED", "could not write the failure log")
        if prev is not None:
            prev(name, res, route)
    reg.on_failure = hook
    return hook


def weekly(home, *, now: datetime | None = None, top: int = 10) -> list[str]:
    """The last 7 days grouped by kind/tool/code, most frequent first, as short lines."""
    p = path(home)
    since = ((now or _now()) - timedelta(days=7)).date().isoformat()
    counts: Counter = Counter()
    for line in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("day", "") > since:
            counts[(r.get("kind", ""), r.get("tool", ""), r.get("code", ""))] += 1
    out = []
    for (kind, tool, code), n in counts.most_common(top):
        out.append(f"{n}x {kind}" + (f": {tool}" if tool else "") + (f" ({code})" if code else ""))
    return out


def add_cli(sub) -> None:
    r = sub.add_parser("report", help="local 'what went wrong' log: `weekly` lists the last 7 days (metadata only)")
    r.add_argument("action", choices=["weekly"])


def cmd_report(a, cfg, home) -> int:
    lines = weekly(home)
    print("\n".join(lines) if lines else "No failures recorded in the last 7 days. / לא נרשמו כשלים ב-7 הימים האחרונים.")
    return 0


COMMANDS = {"report": cmd_report}
