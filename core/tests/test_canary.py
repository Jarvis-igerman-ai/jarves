"""SW-V4 O (#17): nightly synthetic user-journey canaries on the throw-away TEST profile. Each journey runs against
fakes (profile "ci"): a real Core with a virtual mic and a measured speaker, the music Skill on the fake player, a
read-only calendar list on a mock Google API, the scheduler with a moved clock, the installer self-check. A journey
that cannot run here is BLOCKED with its reason (never a pass). The real state folder is never written."""
from __future__ import annotations

import json
import os

import httpx
import pytest

from jarves.diag import canary
from jarves.diag.anomaly import Detector
from jarves.store import Store


def tree(p):
    return {str(f.relative_to(p)): (f.stat().st_size, f.stat().st_mtime_ns) for f in p.rglob("*") if f.is_file()}


async def test_ci_journeys_pass_or_block_with_reason(home, monkeypatch):
    monkeypatch.setattr("jarves.setup_wizard.check_addons",
                        lambda: {"ok": True, "rows": [{"id": "music", "ok": True}], "load_failed": []})
    rep = await canary.run_all("ci", only=["voice_stack", "music", "calendar_read", "scheduler", "installer_check",
                                           "bridges"])
    st = {r["name"]: r for r in rep["results"]}
    for name in ("voice_stack", "music", "calendar_read", "scheduler", "installer_check"):
        assert st[name]["status"] == canary.PASS, st[name]
    assert st["bridges"]["status"] == canary.BLOCKED and "E / F" in st["bridges"]["detail"]
    assert "STT -> reply -> speaker ok" in st["voice_stack"]["detail"]
    assert "read-only" in st["calendar_read"]["detail"] and "job ran" in st["scheduler"]["detail"]
    assert rep["failed"] == 0 and os.environ["JARVES_HOME"] == str(home)          # the test home is restored


async def test_dashboard_journey_is_real_browser_or_blocked(home):
    rep = await canary.run_all("ci", only=["dashboard_load"], browser_channel="msedge" if os.name == "nt" else None)
    r = rep["results"][0]
    if r["status"] == canary.BLOCKED:
        assert "browser" in r["detail"] or "playwright" in r["detail"]
    else:
        assert r["status"] == canary.PASS, r
        for page in canary.DASHBOARD_PAGES:
            assert page in r["detail"]


async def test_installer_check_fails_on_a_missing_addon_and_hash_mismatch(home, monkeypatch, tmp_path):
    monkeypatch.setattr("jarves.setup_wizard.check_addons",
                        lambda: {"ok": False, "rows": [{"id": "camera", "ok": False}], "load_failed": []})
    r = (await canary.run_all("ci", only=["installer_check"]))["results"][0]
    assert r["status"] == canary.FAIL and "camera" in r["detail"]
    monkeypatch.setattr("jarves.setup_wizard.check_addons", lambda: {"ok": True, "rows": [], "load_failed": []})
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"real")
    info = tmp_path / "_build_info.json"
    info.write_text(json.dumps({"version": "4.0.0", "payload_sha256": {"payload.bin": "0" * 64}}))
    monkeypatch.setattr("jarves.updates.BUILD_INFO", info)
    r = (await canary.run_all("ci", only=["installer_check"]))["results"][0]
    assert r["status"] == canary.FAIL and "hash mismatch" in r["detail"]
    import hashlib
    info.write_text(json.dumps({"payload_sha256": {"payload.bin": hashlib.sha256(b"real").hexdigest()}}))
    r = (await canary.run_all("ci", only=["installer_check"]))["results"][0]
    assert r["status"] == canary.PASS and "1 payload hash(es) verified" in r["detail"]


async def test_owner_profile_reads_a_copy_and_never_writes_the_real_home(home, tmp_path, monkeypatch):
    real = tmp_path / "real-home"
    (real / "google").mkdir(parents=True)
    tok = {"refresh_token": "r-real", "access_token": "a-real", "expires_at": 4e9,
           "scopes": ["https://www.googleapis.com/auth/calendar.calendarlist.readonly"]}
    (real / "google" / "token.json").write_text(json.dumps(tok), encoding="utf-8")
    (real / "memory.db").write_bytes(b"real memory")
    before = tree(real)
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.method)
        return httpx.Response(200, json={"items": [{"id": "primary"}]}) if req.method == "GET" else httpx.Response(405)
    monkeypatch.setattr("jarves.features._tcp_open", lambda url, timeout=0.3: False)       # Pear closed
    rep = await canary.run_all("owner", real_home=real, only=["calendar_read", "music", "bridges"],
                               transport=httpx.MockTransport(handler))
    st = {r["name"]: r for r in rep["results"]}
    assert st["calendar_read"]["status"] == canary.PASS and seen == ["GET"]               # read-only
    assert st["music"]["status"] == canary.BLOCKED and st["music"]["detail"] == "Pear closed"
    assert tree(real) == before                                                            # never written
    rep = await canary.run_all("owner", real_home=tmp_path / "nobody", only=["calendar_read"])
    assert rep["results"][0]["status"] == canary.BLOCKED and "not connected" in rep["results"][0]["detail"]


def test_two_failures_in_a_row_create_a_finding(tmp_path):
    st = Store(tmp_path / "s.db")
    try:
        ok = {"ts": 1.0, "profile": "owner", "ms": 1.0, "passed": 1, "failed": 0, "blocked": 0,
              "results": [{"name": "dashboard_load", "status": "PASS", "detail": "", "ms": 1.0}]}
        bad = {**ok, "passed": 0, "failed": 1,
               "results": [{"name": "dashboard_load", "status": "FAIL", "detail": "console error", "ms": 1.0}]}
        canary.save(st, ok)
        canary.save(st, bad)
        assert not [f for f in Detector(st).run(86400, now=10.0) if f.fingerprint.startswith("canary:")]   # once: no
        canary.save(st, bad)
        [f] = [f for f in Detector(st).run(86400, now=10.0) if f.fingerprint.startswith("canary:")]
        assert f.fingerprint == "canary:dashboard_load" and f.severity == "high"
        assert canary.latest(st, 1)[0]["results"][0]["status"] == "FAIL"
    finally:
        st.close()


def test_a_broken_dashboard_page_fails_the_dashboard_journey(home, monkeypatch, tmp_path):
    """The journey fails on any console error: a page script that throws must be caught."""
    import asyncio
    import shutil

    from jarves import dashboard
    broken = tmp_path / "static"
    shutil.copytree(dashboard.STATIC, broken)
    (broken / "health.js").write_text("throw new Error('deliberately broken health page');", encoding="utf-8")
    monkeypatch.setattr(dashboard, "STATIC", broken)
    r = asyncio.run(canary.run_all("ci", only=["dashboard_load"],
                                   browser_channel="msedge" if os.name == "nt" else None))["results"][0]
    if r["status"] == canary.BLOCKED:
        assert "browser" in r["detail"] or "playwright" in r["detail"]
    else:
        assert r["status"] == canary.FAIL and "pageerror" in r["detail"], r


def test_synthetic_canaries_runner_wraps_the_journeys(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import synthetic_canaries as sc
    sc.CANARIES.clear()
    sc.register_package_o()
    rep = sc.run({"bridges": sc.CANARIES["bridges"], "scheduler": sc.CANARIES["scheduler"]})
    assert rep["blocked"] == 1 and rep["passed"] == 1 and rep["failed"] == 0
