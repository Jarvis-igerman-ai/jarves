"""JAR-229: the local 'what went wrong' log. Only privacy-safe metadata is stored (kind, day, tool, code); never user
text. `jarvis report weekly` turns the last 7 days into a short list. Hermetic: tmp home, injected clock."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from jarves import cli, failure_log as fl
from jarves.features import CATALOG

NOW = datetime(2026, 11, 11, 12, 0)


def test_feature_is_beta_and_off_by_default():
    f = CATALOG[fl.FEATURE]
    assert f.default_enabled is False and f.stage == "beta"


def test_nothing_is_recorded_while_off(home):
    assert fl.record(home, "tool_error", tool="calendar", on=False, now=NOW) is False
    assert not fl.path(home).exists()


def test_only_safe_metadata_is_kept(home):
    with pytest.raises(ValueError):
        fl.record(home, "whatever", on=True, now=NOW)
    fl.record(home, "tool_error", tool="call Dana 050-1234567", code="my secret text", on=True, now=NOW)
    row = json.loads(fl.path(home).read_text(encoding="utf-8"))
    assert row == {"day": "2026-11-11", "kind": "tool_error", "tool": "other", "code": "other"}


def test_weekly_groups_last_seven_days_most_frequent_first(home):
    for _ in range(3):
        fl.record(home, "tool_error", tool="calendar", code="TIMEOUT", on=True, now=NOW)
    fl.record(home, "not_understood", on=True, now=NOW - timedelta(days=2))
    fl.record(home, "user_correction", on=True, now=NOW - timedelta(days=9))   # too old
    assert fl.weekly(home, now=NOW) == ["3x tool_error: calendar (TIMEOUT)", "1x not_understood"]


def test_the_log_is_bounded(home, monkeypatch):
    monkeypatch.setattr(fl, "MAX_ROWS", 5)
    for _ in range(8):
        fl.record(home, "repeated_request", on=True, now=NOW)
    assert len(fl.path(home).read_text(encoding="utf-8").splitlines()) == 5


def test_cli_report_weekly(home, capsys):
    assert cli.main(["report", "weekly"]) == 0
    assert "No failures" in capsys.readouterr().out
    fl.record(home, "tool_error", tool="music", code="OFFLINE", on=True, now=datetime.now())
    assert cli.main(["report", "weekly"]) == 0
    assert "1x tool_error: music (OFFLINE)" in capsys.readouterr().out
