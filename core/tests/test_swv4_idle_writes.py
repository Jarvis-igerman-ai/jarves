"""SW-V4 idle writes: an idle Core does not keep writing files. Offline, silent: the real background Core in a tmp home
measured with the same code as tools/perf_gate.py (bench_startup.one_run), plus unit checks of the two churn sources
found in the V4 trace (health heartbeat every 20 s, a stuck alert's attempts counter bumped every scheduler tick)."""
import sys
from pathlib import Path

import pytest

from jarves import alerts as A
from jarves.health import Health
from jarves.userdata import UserDB

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bench_startup as bs  # noqa: E402


def test_health_heartbeat_writes_at_most_once_per_window(tmp_path):
    h = Health(tmp_path / "health.json")
    h.set("ai", "ok", "x")                       # a change always saves
    first = (tmp_path / "health.json").stat().st_mtime_ns
    assert h.heartbeat() is False                # inside the window: no write
    assert (tmp_path / "health.json").stat().st_mtime_ns == first
    h._saved_at -= 60
    assert h.heartbeat() is True                 # older than the window: refreshed (readers need a recent ts)


@pytest.mark.asyncio
async def test_stuck_alert_is_not_rewritten_every_flush(tmp_path):
    db = UserDB(tmp_path / "memory.db")
    e = A.AlertEngine(db, outputs={})            # no output can deliver
    e.raise_("t", "stuck", priority="normal")
    writes = []
    real = db.x
    db.x = lambda sql, args=(): (writes.append(sql), real(sql, args))[1]
    for _ in range(5):
        assert await e.flush() == 0
    assert len([w for w in writes if w.startswith("UPDATE alerts SET attempts")]) == 1
    assert db.row("SELECT attempts, status FROM alerts") == {"attempts": 1, "status": "pending"}


def test_idle_core_has_no_steady_state_file_churn(monkeypatch):
    """Real Core, tmp home, 20 s after a 8 s warm-up: bytes written per minute stay small and only the two heartbeat
    files may change (store.db / memory.db / logs must stay untouched while nothing happens)."""
    monkeypatch.setattr(bs, "WARMUP_S", 8.0)
    r = bs.one_run("core", 20, 18771)
    changed = {x.rsplit(" x", 1)[0] for x in r["idle_files"].split(", ") if x}
    assert changed <= {"health.json", "runtime.json"}, r["idle_files"]
    assert r["idle_disk_write_kb_per_min"] < 30, r
