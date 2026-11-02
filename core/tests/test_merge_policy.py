"""Merge steward policy (#777): decide() thresholds, history adjustment, ledger round trip, simulation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import merge_policy as mp  # noqa: E402


def cyc(mode, eligible=3, skipped=0, minutes=20, reverts=0):
    return {"mode": mode, "eligible": eligible, "skipped": skipped, "conflicts": 0,
            "minutes_to_merge": minutes, "reverts": reverts}


def test_default_threshold_three():
    assert mp.decide(2, [])["mode"] == "individual"
    assert mp.decide(3, [])["mode"] == "integration"


def test_overlap_and_lone_tier_r_force_individual():
    assert mp.decide(5, [], overlap=True)["mode"] == "individual"
    assert mp.decide(5, [], lone_tier_r=True)["mode"] == "individual"


def test_high_skip_rate_raises_threshold():
    hist = [cyc("integration", 3, skipped=2)] * 2
    d = mp.decide(3, hist)
    assert d["threshold"] == 4 and d["mode"] == "individual"
    assert mp.decide(4, hist)["mode"] == "integration"


def test_integration_slower_than_serial_raises():
    hist = [cyc("integration", minutes=60)] * 2 + [cyc("individual", 1, minutes=20)] * 2
    assert mp.decide(3, hist)["threshold"] == 4


def test_revert_raises_threshold():
    assert mp.decide(3, [cyc("integration", reverts=1)] * 2)["threshold"] == 4


def test_long_serial_wait_lowers_threshold():
    hist = [cyc("individual", 2, minutes=55)] * 2
    d = mp.decide(2, hist)
    assert d["threshold"] == 2 and d["mode"] == "integration"


def test_long_serial_wait_with_conflicts_does_not_lower():
    hist = [cyc("individual", 2, skipped=1, minutes=55)] * 2
    assert mp.decide(2, hist)["threshold"] == 3


def test_too_few_samples_keep_default():
    assert mp.decide(3, [cyc("integration", 3, skipped=3)])["threshold"] == 3


def test_threshold_is_bounded():
    hist = [cyc("integration", 3, skipped=3)] * 5
    assert mp.MIN_THRESHOLD <= mp.decide(3, hist)["threshold"] <= mp.MAX_THRESHOLD


def test_ledger_round_trip(tmp_path):
    from jarves.workflow.store import Store
    store = Store(tmp_path / "w.db")
    try:
        mp.record_cycle(store, cyc("integration", 4, skipped=1))
        rows = mp.history_from_events(store.snapshot()["events"])
        assert rows and rows[-1]["mode"] == "integration" and rows[-1]["skipped"] == 1
    finally:
        store.close()


def test_record_rejects_bad_mode():
    with pytest.raises(ValueError):
        mp.record_cycle(None, {"mode": "x"})


def test_simulation_adapts_to_bad_integration_runs():
    bad = {"skipped": 2, "minutes_to_merge": 50}
    log = mp.simulate([(3, bad), (3, bad), (3, bad), (3, bad)])
    assert log[:2] == ["integration"] * 2
    assert log[3] == "individual"  # threshold rose to 4 after two bad runs
