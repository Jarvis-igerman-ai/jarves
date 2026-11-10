"""JAR-135: hermetic dashboard payload meter - every referenced asset exists, no dead assets, budgets guard growth."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import dashboard_payload as dp  # noqa: E402


def test_every_referenced_asset_resolves_and_counts():
    res = dp.measure()
    assert set(res) == set(dp.PAGES)
    for r in res.values():
        assert r["requests"] == len(r["files"]) >= 2
        assert all(f["raw"] > 0 and f["gzip"] <= f["raw"] for f in r["files"])


def test_no_dead_assets():
    """Every JS/CSS is in an entry page, or named by a lazy loader (pages.d/*.toml, another script, the server)."""
    used = {f["file"] for r in dp.measure().values() for f in r["files"]}
    corpus = "".join(p.read_text("utf-8", errors="ignore") for p in dp.JARVES.rglob("*")
                     if p.is_file() and p.suffix in (".py", ".js", ".toml", ".html") and "locales" not in p.parts)
    dead = [p.name for p in dp.STATIC.iterdir() if p.is_file() and p.suffix in (".js", ".css")
            and p.name not in {Path(u).name for u in used} and f"/{p.name}" not in corpus and f'"{p.name}"' not in corpus]
    assert dead == [], f"unreferenced dashboard assets: {dead}"


def test_payload_budget():
    res = dp.measure()
    # ~6% headroom over the 2026-11-09 baseline (dashboard 300814 gz / 49 requests); raise deliberately, with a reason
    assert res["dashboard (/)"]["gzip"] < 320_000
    assert res["dashboard (/)"]["requests"] <= 55
    assert res["phone PWA (/m/)"]["gzip"] < 75_000
