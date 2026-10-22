import datetime as dt

from flake_report import quarantine_candidates, render


def test_two_flakes_in_seven_days_quarantine():
    today = dt.date(2026, 10, 22)
    h = [{"date": "2026-10-20", "test": "a", "passed_on_retry": True},
         {"date": "2026-10-21", "test": "a", "passed_on_retry": True},
         {"date": "2026-10-01", "test": "b", "passed_on_retry": True},
         {"date": "2026-10-21", "test": "b", "passed_on_retry": True}]
    assert quarantine_candidates(h, today) == ["a"]
    assert "`a` | 2" in render(h, today)
