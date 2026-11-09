"""Action receipts for ALL tool calls (V7-X1 #420, owner Q2: receipts first).

Extends the browser pilot's receipts (browser_pilot.py: kind + result + sequence + time, never content) to every tool call
of the SkillRegistry. A receipt holds the tool NAME, ok, result code, origin, duration and a sequence: never arguments, never
the result text, never a URL or a value. It is observation only: it cannot approve, refuse or change a call, and any
receipt failure is swallowed. Off by default (`jarvis features on action_receipts`); file: <home>/receipts/tool_receipts.jsonl.
`jarvis receipts status|list|clear`."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Callable

from . import clilang, features as feat, logs
from .approvals import append_audit
from .channels_common import targets

FEATURE = "action_receipts"
RECEIPT_FILE = "receipts/tool_receipts.jsonl"
KEEP = 100                                   # receipts kept in memory
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,60}$")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")        # result codes are UPPER_SNAKE; anything else is dropped
ORIGINS = ("model", "user")


class ReceiptLog:
    """Appends one receipt per finished tool call while `enabled()` is true. `wall` is the clock for the timestamp."""

    def __init__(self, home: Path, enabled: Callable[[], bool], wall: Callable[[], float] = time.time) -> None:
        self.home, self.enabled, self.wall = Path(home), enabled, wall
        self.seq = 0
        self.receipts: list[dict] = []
        self._lock = threading.Lock()

    def record(self, tool: str, ok: bool, code: str = "", origin: str = "model", ms: int = 0) -> dict | None:
        try:
            if not self.enabled():
                return None
            with self._lock:
                self.seq += 1
                rec = {"seq": self.seq, "ts": self.wall(),
                       "tool": tool if _SAFE_NAME.match(str(tool)) else "other",   # a model-chosen name never reaches the file
                       "ok": bool(ok), "code": code if _SAFE_CODE.match(str(code or "")) else "",
                       "origin": origin if origin in ORIGINS else "other", "ms": max(0, int(ms))}
                self.receipts.append(rec)
                del self.receipts[:-KEEP]
                append_audit(self.home / RECEIPT_FILE, dict(rec))
            return rec
        except Exception:                                  # noqa: BLE001  a receipt problem never changes a tool result
            logs.warn("receipts", "RECEIPT_FAILED", "could not write a tool receipt")
            return None


def attach(registry, features, home) -> ReceiptLog:
    """Wire receipts into a SkillRegistry (Core and text session). Off = nothing is written."""
    log = ReceiptLog(home, lambda: features.active(FEATURE))
    registry.receipts = log
    return log


def read(home: Path, limit: int = 20) -> list[dict]:
    try:
        lines = (Path(home) / RECEIPT_FILE).read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------- CLI: jarvis receipts status|list|clear
def add_cli(sub) -> None:
    p = sub.add_parser("receipts", help="action receipts: jarvis receipts status | list | clear. Tool name, result and time only, "
                                        "never arguments or results. Off by default (jarvis features on action_receipts)")
    p.add_argument("action", nargs="?", default="status", choices=["status", "list", "clear"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")


def cmd_receipts(a, cfg, home) -> int:
    from .cli import _cli_features
    path = Path(home) / RECEIPT_FILE
    if a.action == "clear":
        try:
            path.unlink()
        except OSError:
            pass
        print(clilang.fmt("הקבלות נמחקו", "receipts cleared"))
        return 0
    rows = read(home, a.limit)
    if a.action == "list":
        if a.json:
            print(json.dumps(rows, ensure_ascii=False))
        else:
            for r in rows:
                print(f"{r.get('seq')}. {r.get('tool')} {'ok' if r.get('ok') else 'failed'} {r.get('code', '')} ({r.get('ms', 0)} ms)")
        return 0
    state = {"action_receipts": "on" if _cli_features(cfg, home).active(FEATURE) else "off", "stored": len(read(home, 100000))}
    print(json.dumps(state) if a.json else f"action receipts: {state['action_receipts']}; stored: {state['stored']}")
    return 0


COMMANDS = {"receipts": cmd_receipts}


# ---------------------------------------------------------------------- feature
feat.register(FEATURE, "קבלות פעולה", "Action receipts", False, risk="low",
              about="writes a short receipt for every tool call: tool name, ok, result code and time; never arguments or results. "
                    "Off by default; it never changes approvals and never blocks anything",
              targets=targets("UNIT test_action_receipts.py (fake registry and clock, no real tools)",
                              "not run with a real assistant session yet"),
              permissions=[], settings="jarvis features on action_receipts")
