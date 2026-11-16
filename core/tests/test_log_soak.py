"""JAR-90: hermetic log-size soak. One simulated idle hour stays tiny and every append-only log has a rotation cap."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import log_soak  # noqa: E402


def test_idle_hour_is_small(tmp_path):
    r = log_soak.idle_hour(tmp_path / "idle")
    assert r["idle_hour_bytes_total_growth"] < 200_000, r
    assert r["health_json_rewrites"] <= 60, r


def test_every_log_is_capped(tmp_path):
    r = log_soak.flood(tmp_path, n=3000, records=8000)
    for name, v in {"jarves.log": r["jarves.log"], **r["audit"]}.items():
        assert v["size"] <= v["cap"], (name, v)
