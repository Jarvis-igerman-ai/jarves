"""Truthful component health: every entry carries a timestamp; nothing is reported OK without a fresh check."""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from . import logs

HEARTBEAT_MIN_S = 70.0    # health.json is rewritten on a change, and otherwise at most this often (loop runs every 20 s)
Status = Literal["ok", "degraded", "unavailable", "disabled", "idle", "unknown"]   # idle: optional, not running, starts on demand


@dataclass
class Component:
    status: Status = "unknown"
    detail: str = ""
    action: str = ""          # what the user can try
    since: float = field(default_factory=time.time)


class Health:
    def __init__(self, snapshot_path: Path | None = None) -> None:
        self.core = "starting"   # starting / ready / degraded / stopping / stopped
        self.components: dict[str, Component] = {}
        self.last_error: dict | None = None
        self.started = time.time()
        self._path = snapshot_path
        self._listeners: list = []
        self._saved_at = float("-inf")

    def on_change(self, fn) -> None:
        self._listeners.append(fn)

    def set(self, name: str, status: Status, detail: str = "", action: str = "") -> None:
        cur = self.components.get(name)
        if cur and (cur.status, cur.detail, cur.action) == (status, detail, action):
            return
        self.components[name] = Component(status, detail, action)
        if status in ("unavailable", "degraded") and detail:
            self.last_error = {"component": name, "detail": detail, "action": action, "ts": time.time()}
        self._recompute()
        self.save()
        for fn in self._listeners:
            fn()

    def _recompute(self) -> None:
        if self.core in ("starting", "stopping", "stopped"):
            return
        bad = [c for c in self.components.values() if c.status in ("unavailable", "degraded")]
        self.core = "degraded" if bad else "ready"

    def mark(self, core: str) -> None:
        self.core = core
        self._recompute()
        self.save()

    def components_view(self) -> dict:
        """{name: {status, detail}} for features / self-check: the same cleaning as the snapshot."""
        return _clean({k: {"status": v.status, "detail": v.detail} for k, v in self.components.items()}) or {}

    def snapshot(self, root: Path | None = None) -> dict:
        disk = None
        try:
            disk = round(shutil.disk_usage(root or Path.home()).free / 1e9, 1)
        except OSError:
            pass
        return {"ts": time.time(), "core": self.core, "uptime_s": round(time.time() - self.started),
                "storage_free_gb": disk, "last_error": _clean(self.last_error),
                "components": _clean({k: asdict(v) for k, v in sorted(self.components.items())}) or {}}

    def heartbeat(self, min_age_s: float = HEARTBEAT_MIN_S) -> bool:
        """Re-save only when the file is older than min_age_s (readers call a snapshot older than 120 s "off")."""
        if time.monotonic() - self._saved_at < min_age_s:
            return False
        self.save()
        return True

    def save(self) -> None:
        if self._path:
            self._saved_at = time.monotonic()
            try:
                self._path.write_text(json.dumps(self.snapshot(self._path.parent), indent=1), encoding="utf-8")
            except OSError:
                pass


def _clean(d: dict | None) -> dict | None:
    """SW-V4 B: health text (health.json, `jarvis status`, dashboard) never carries a secret, and in privacy mode no
    e-mail address or phone number (e.g. the signed-in ChatGPT account in the ai detail).

    CodeQL #15: EVERY string (any key, nested dicts/lists, and the keys themselves) is cleaned, not only
    `detail`/`action`, so a future field cannot carry a secret into health.json. Numbers and structure are kept."""
    if not d:
        return d
    fix = (lambda s: logs.redact_pii(logs.redact(s))) if logs.privacy() else logs.redact
    return _clean_value(d, fix, 0)


def _clean_value(v: object, fix, depth: int) -> object:
    if isinstance(v, str):
        return fix(v)
    if depth > 8:                        # health data is shallow; anything deeper is dropped, never written raw
        return None
    if isinstance(v, dict):
        return {fix(k) if isinstance(k, str) else k: _clean_value(x, fix, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean_value(x, fix, depth + 1) for x in v]
    return v


def read_snapshot(path: Path) -> dict | None:
    """Last snapshot written by a (possibly no longer running) Core, marked with its age."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    d["age_s"] = round(time.time() - d.get("ts", 0))
    return d
