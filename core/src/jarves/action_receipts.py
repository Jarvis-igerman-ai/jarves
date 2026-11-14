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
                self.seq = max(self.seq, _last_seq(self.home)) + 1   # continue the file: Core and text session share it (#1107)
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


def _rows(raw: bytes) -> list[dict]:
    """Decode line by line: an undecodable or broken line drops only itself (#1107)."""
    out = []
    for line in raw.splitlines():
        try:
            row = json.loads(line.decode("utf-8"))
        except ValueError:                                 # UnicodeDecodeError and JSONDecodeError are ValueErrors
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _last_seq(home: Path) -> int:
    """Highest seq at the end of the live file, or of the rotated one right after a rotation; 0 if none."""
    path = Path(home) / RECEIPT_FILE
    for f in (path, path.with_suffix(".1.jsonl")):
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, fh.seek(0, 2) - 4096))
                tail = fh.read()
        except OSError:
            continue
        seqs = [r["seq"] for r in _rows(tail) if isinstance(r.get("seq"), int)]
        if seqs:
            return max(seqs)
    return 0


def read(home: Path, limit: int = 20) -> list[dict]:
    try:
        raw = (Path(home) / RECEIPT_FILE).read_bytes()
    except OSError:
        return []
    return _rows(raw)[-max(1, int(limit)):]


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
        files = (path, path.with_suffix(".1.jsonl"))            # the live file and the rotated one
        for f in files:
            try:
                f.unlink()
            except OSError:
                pass
        left = [f.name for f in files if f.exists()]
        if left:
            print(clilang.fmt("לא ניתן למחוק את כל הקבלות: " + ", ".join(left),
                              "could not delete all receipts: " + ", ".join(left)))
            return 1
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
