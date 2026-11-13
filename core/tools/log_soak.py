"""Hermetic log-size soak (JAR-90, follow-up to the perf baseline): no Core, no microphone, no network, no real clock.

1. idle_hour(): one simulated hour of the idle writers (60 s health sampler + 5-minute store flush + health heartbeat)
   on a fake clock; reports bytes written per hour per file.
2. flood(): pushes far more records than any real day through every append-only log writer and reports the largest
   on-disk size, to prove each one has a rotation cap.
Usage: python core/tools/log_soak.py [--json]   (PYTHONPATH=core/src)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import tempfile
from pathlib import Path

T0 = 1_792_500_000.0 // 3600 * 3600


def _du(root: Path, pattern: str = "*") -> int:
    return sum(p.stat().st_size for p in root.rglob(pattern) if p.is_file())


def idle_hour(home: Path) -> dict:
    from jarves.diag.monitor import Monitor
    from jarves.health import Health
    from jarves.store import Store
    home.mkdir(parents=True, exist_ok=True)
    now = [T0]
    store = Store(home / "store.db")
    mon = Monitor(store, clock=lambda: now[0], version=("soak", "soak"))
    health = Health(home / "health.json")
    health.set("ai", "ok", "x")
    saves = 0
    mon.metrics.rollup(now[0])
    store._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = _du(home)
    for _ in range(60):                       # 60 one-minute ticks
        mon.sample(0.0)
        mon.flush_due()
        now[0] += 60
        health._saved_at -= 60                 # health.heartbeat() looks at the wall clock: age it by the tick
        saves += bool(health.heartbeat())
    mon.metrics.flush(all_=True)
    store._db.commit()
    store._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")       # compare like with like: WAL folded into store.db
    store.close()
    return {"idle_hour_bytes_total_growth": _du(home) - before, "health_json_rewrites": saves,
            "store_db_bytes": sum(p.stat().st_size for p in home.glob("store.db*"))}


def flood(home: Path, n: int = 4000, records: int = 60_000) -> dict:
    from jarves import approvals, instagram_channel, logs
    out: dict = {}
    d = home / "flood"
    d.mkdir(parents=True, exist_ok=True)
    # jarves.log: ~300 B x 60000 records, caps = max_bytes x (backups + 1)
    logdir = d / "logs"
    logs.setup(logdir, level="INFO", max_bytes=200_000, backups=2, console=False)
    for i in range(records):
        logs.info("soak", "SOAK_TICK", "soak tick", n=i)
    for h in list(logs._logger.handlers):      # release the file (Windows cannot delete an open log)
        h.flush()
        if isinstance(h, logging.handlers.RotatingFileHandler):
            logs._logger.removeHandler(h)
            h.close()
    out["jarves.log"] = {"cap": 200_000 * 3 + 2000, "size": _du(logdir, "jarves.log*")}
    out["audit"] = {}
    ap = d / "approvals" / "audit.jsonl"
    for i in range(n * 2):
        approvals.append_audit(ap, {"event": "x", "i": i, "pad": "p" * 200})
    out["audit"]["approvals"] = {"cap": 2 * approvals.AUDIT_MAX_BYTES + 1000, "size": _du(ap.parent, "audit*")}
    for i in range(n * 4):
        instagram_channel.audit(d, "webhook_received", count=i, pad="p" * 200)
    out["audit"]["instagram"] = {"cap": 2 * getattr(instagram_channel, "AUDIT_MAX_BYTES", 10**12) + 1000,
                                 "size": _du(d / "instagram", "audit*")}
    return out


def main() -> int:
    with tempfile.TemporaryDirectory() as t:
        r = {"idle": idle_hour(Path(t) / "idle"), "flood": flood(Path(t))}
    print(json.dumps(r, indent=1) if "--json" in sys.argv else r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
