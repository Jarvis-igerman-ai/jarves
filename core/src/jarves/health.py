"""Truthful component health: every entry carries a timestamp; nothing is reported OK without a fresh check."""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Status = Literal["ok", "degraded", "unavailable", "disabled", "unknown"]


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

    def snapshot(self, root: Path | None = None) -> dict:
        disk = None
        try:
            disk = round(shutil.disk_usage(root or Path.home()).free / 1e9, 1)
        except OSError:
            pass
        return {"ts": time.time(), "core": self.core, "uptime_s": round(time.time() - self.started),
                "storage_free_gb": disk, "last_error": self.last_error,
                "components": {k: asdict(v) for k, v in sorted(self.components.items())}}

    def save(self) -> None:
        if self._path:
            try:
                self._path.write_text(json.dumps(self.snapshot(self._path.parent), indent=1), encoding="utf-8")
            except OSError:
                pass


def read_snapshot(path: Path) -> dict | None:
    """Last snapshot written by a (possibly no longer running) Core, marked with its age."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    d["age_s"] = round(time.time() - d.get("ts", 0))
    return d
