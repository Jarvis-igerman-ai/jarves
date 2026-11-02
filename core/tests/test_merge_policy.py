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


def test_long_serial_wait_never_drops_below_three():
    hist = [cyc("individual", 2, minutes=55)] * 2
    d = mp.decide(2, hist)
    assert d["threshold"] == 3 and d["mode"] == "individual"  # OPERATING_MODEL: fewer than 3 -> individual


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


def test_history_read_from_real_ledger_is_oldest_first_and_survives_noise(tmp_path):
    from jarves.workflow.store import Store
    store = Store(tmp_path / "w.db")
    try:
        for i in range(11):  # old good cycles, then 4 recent bad ones: window must keep the NEWEST
            mp.record_cycle(store, cyc("integration", 3, skipped=0))
        for _ in range(4):
            mp.record_cycle(store, cyc("integration", 3, skipped=3))
        for i in range(150):  # more than the 100-event snapshot limit
            store.record_event("noise", f"n{i}")
        hist = mp.history_from_store(store)
        assert len(hist) == 15 and hist[-1]["skipped"] == 3
        assert mp.decide(3, hist)["threshold"] == 4
        assert mp.decide(3, hist)["mode"] == "individual"
    finally:
        store.close()


def test_history_from_events_order_and_robustness():
    events = [{"id": 2, "kind": "merge_cycle", "summary": '{"mode":"integration","eligible":2}'},
              {"id": 1, "kind": "merge_cycle", "summary": '{"mode":"individual","eligible":1}'},
              {"id": 3, "kind": "merge_cycle", "summary": "7"},
              {"id": 4, "kind": "merge_cycle", "summary": "[1]"},
              {"id": 5, "kind": "other", "summary": "{}"}, "junk"]
    assert [r["eligible"] for r in mp.history_from_events(events)] == [1, 2]
    bad = [{"mode": "integration", "eligible": None, "skipped": None, "reverts": "x"}, 5, "s"]
    assert mp.decide(3, bad)["mode"] == "integration"


def test_plan_auto_uses_ledger_threshold(tmp_path):
    import integration_branch as ib
    from jarves.workflow.store import Store
    store = Store(tmp_path / "workflow.db")
    try:
        for _ in range(2):
            mp.record_cycle(store, cyc("integration", 3, skipped=3))
    finally:
        store.close()
    assert ib.ledger_threshold(tmp_path) == 4
    assert ib.ledger_threshold(tmp_path / "missing") == 3
    infos = [{"tier": "N"}] * 3
    assert ib.recommend(infos, [], [], 4)[0] == "individual"
    assert ib.recommend(infos, [], [])[0] == "integration"


def test_simulation_adapts_to_bad_integration_runs():
    bad = {"skipped": 2, "minutes_to_merge": 50}
    log = mp.simulate([(3, bad), (3, bad), (3, bad), (3, bad)])
    assert log[:2] == ["integration"] * 2
    assert log[3] == "individual"  # threshold rose to 4 after two bad runs
