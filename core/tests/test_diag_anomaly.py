"""SW-V4 O (#17): baselines and anomaly detection on the replay harness (real store.db in a tmp dir, fixed clock,
fixed seed). Each of the 12 planted anomalies produces the right finding within its window; 30 boring replayed
days produce 0 high / 0 medium and at most 2 low findings."""
from __future__ import annotations

import pytest

import diag_replay as rp
from jarves.diag import anomaly
from jarves.diag.anomaly import Detector, cusum, robust, slope
from jarves.store import Store

WINDOW = 2 * 86400


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "store.db")
    yield s
    s.close()


def detect(store, now=rp.NOW, window=WINDOW):
    return Detector(store, clock=lambda: now).run(window_s=window, now=now)


def test_statistics_helpers():
    m, s = robust([1, 2, 3, 4, 100])
    assert m == 3 and 1.4 < s < 1.6                       # the outlier does not move the robust spread
    b, r2 = slope([(i * 3600.0, 10 + 2 * i) for i in range(50)])
    assert abs(b * 3600 - 2) < 1e-9 and r2 > 0.99
    assert cusum([0.2] * 24, 0.2)[0] == 0.0               # steady rate: no alarm
    assert cusum([1.0] * 24, 0.2)[0] > 8                  # a 5x step


@pytest.mark.parametrize("name", rp.ALL)
def test_each_planted_anomaly_alone_gives_its_finding(store, name):
    rp.build(store, plant=(name,))
    found = {f.fingerprint: f for f in detect(store)}
    want = rp.expected_findings((name,))
    assert want <= set(found), (name, sorted(found))
    extra = {fp for fp in found if fp not in want}
    assert not extra, (name, sorted(extra))              # nothing else fires on the same baseline


def test_all_twelve_together(store):
    rp.build(store, plant=rp.ALL)
    found = {f.fingerprint for f in detect(store)}
    assert rp.expected_findings(rp.ALL) <= found, sorted(rp.expected_findings(rp.ALL) - found)


def test_thirty_boring_days_produce_nothing(store):
    rp.build(store, days=46, plant=())
    seen: dict[str, str] = {}
    for d in range(30):
        now = rp.NOW - d * 86400
        for f in detect(store, now=now, window=86400):
            seen[f.fingerprint] = f.severity
    assert not [fp for fp, s in seen.items() if s == "high"], seen
    assert not [fp for fp, s in seen.items() if s == "medium"], seen
    assert sum(s == "low" for s in seen.values()) <= 2, seen


def test_findings_are_stored_with_state_and_resolve(store):
    rp.build(store, plant=("false_action_claim", "ram_creep"))
    found = detect(store)
    anomaly.save(store, found, rp.NOW)
    rows = anomaly.findings(store, now=rp.NOW)
    assert {r["fingerprint"] for r in rows} == {"anomaly:claim:LIVE_FALSE_ACTION_CLAIM", "trend:ram_mb"}
    assert rows[0]["rank"] == 1 and all(r["state"] == "open" for r in rows)
    anomaly.decide(store, "trend:ram_mb", "snooze", by="test", days=2, now=rp.NOW)
    assert next(r for r in anomaly.findings(store, now=rp.NOW) if r["fingerprint"] == "trend:ram_mb")["state"] == "snoozed"
    assert next(r for r in anomaly.findings(store, now=rp.NOW + 3 * 86400)
                if r["fingerprint"] == "trend:ram_mb")["state"] == "open"            # the snooze ran out
    anomaly.save(store, [f for f in found if f.fingerprint == "trend:ram_mb"], rp.NOW + 60)
    assert {r["fingerprint"] for r in anomaly.findings(store)} == {"trend:ram_mb"}  # the other one resolved
    with pytest.raises(KeyError):
        anomaly.decide(store, "nope", "accept", by="test")
