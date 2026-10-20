"""SW-V4 O (#17): regression detection between versions on the replay harness (real store.db, fixed clock)."""
from __future__ import annotations

import pytest

import diag_replay as rp
from jarves.diag import regression
from jarves.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "store.db")
    yield s
    s.close()


def test_a_fake_update_with_a_regression_gives_a_version_report(store):
    rp.build(store, plant=("version_regression", "rate_step_3x"))
    rep = regression.version_report(store, now=rp.NOW)
    assert rep["version"] == rp.V1 and rep["previous"] == rp.V0 and rep["commit"] == rp.C1 and rep["regressed"]
    kinds = {(i["kind"], i.get("code") or i.get("metric")) for i in rep["items"]}
    assert ("metric", "tool_ms") in kinds and ("rate_step", "LIVE_RECONNECT") in kinds
    step = next(i for i in rep["items"] if i.get("code") == "LIVE_RECONNECT")
    assert step["ratio"] >= 2.0
    [f] = regression.version_findings(store, now=rp.NOW)
    assert f.fingerprint == "version:4.0.1" and f.title.startswith("Since 4.0.1:") and "מאז גרסה 4.0.1" in f.title_he
    assert "LIVE_RECONNECT" in f.codes and f.action == {"type": "remedy", "remedy": "rollback_update"}


def test_no_report_without_an_update_or_without_a_regression(store, tmp_path):
    rp.build(store, plant=())
    assert regression.version_report(store, now=rp.NOW) is None and regression.version_findings(store, now=rp.NOW) == []
    s2 = Store(tmp_path / "b.db")
    try:
        rp.build(s2, plant=("version_regression",))
        from jarves.diag import db
        db.write(s2, "UPDATE diag_metrics SET value = value / 1.4 WHERE name='tool_ms' AND app_version=?", (rp.V1,))
        rep = regression.version_report(s2, now=rp.NOW)
        assert rep is not None and not rep["regressed"] and regression.version_findings(s2, now=rp.NOW) == []
    finally:
        s2.close()


def test_the_report_expires_after_a_week(store):
    rp.build(store, plant=("version_regression",))
    assert regression.version_report(store, now=rp.NOW + 5 * 86400)
    assert regression.version_report(store, now=rp.NOW + 8 * 86400) is None
