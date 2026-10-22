"""core/tools/prepush.py: output parsing, budget judging, table formatting, check selection, hook text. Offline."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "tools"))

import prepush  # noqa: E402


def test_first_error_prefers_pytest_e_line():
    out = "collecting\n___ t ___\n    assert 1 == 2\nE   AssertionError: backend prompt is 61000 chars\nFAILED tests/a.py::t - x\n"
    assert prepush.first_error(out) == "AssertionError: backend prompt is 61000 chars"
    assert prepush.pytest_failed_test(out) == "tests/a.py::t"


def test_first_error_falls_back_and_truncates():
    assert prepush.first_error("") == ""
    assert prepush.first_error("one\ntwo\n") == "two"
    assert len(prepush.first_error("E   " + "x" * 500)) == 200


def test_budget_read_from_toml(tmp_path):
    f = tmp_path / "b.toml"
    f.write_text('[metrics.dashboard_api_max_ms]\nci_max = 1500\nowner_max = 500\n', encoding="utf-8")
    assert prepush.dash_budget(f) == 1500.0
    assert prepush.dash_budget(f, "owner") == 500.0
    assert prepush.dash_budget() > 0       # the real file


def test_judge_timings():
    ok, msg = prepush.judge_timings({"/api/a": 100.0, "/api/b": 900.0}, 1500)
    assert ok and "/api/b" in msg
    ok, msg = prepush.judge_timings({"/api/a": 3800.0, "/api/b": 20.0}, 1500)
    assert not ok and "/api/a 3800 ms" in msg and "/api/b" not in msg
    assert prepush.judge_timings({}, 1500)[0] is False


def test_format_table_and_exit_semantics():
    c = prepush.CHECKS[0]
    good = prepush.Result(c, True, 1.5)
    bad = prepush.Result(prepush.CHECKS[4], False, 2.0, "AssertionError: too big", "cd core && python -m pytest tests/test_p_perf.py")
    t = prepush.format_table([good, bad])
    assert "PASS" in t and "FAIL" in t and "AssertionError: too big" in t
    assert "cd core && python -m pytest tests/test_p_perf.py" in t
    assert t.rstrip().endswith("PREPUSH FAILED: do not push on a red local gate")
    assert prepush.format_table([good]).rstrip().endswith("PREPUSH PASSED")


def test_select_fast_only_and_changed():
    names = [c.name for c in prepush.select(None, fast=False, only=None)]
    assert names == [c.name for c in prepush.CHECKS]
    fast = [c.name for c in prepush.select(None, fast=True, only=None)]
    assert "dash" in fast and "perf" in fast and "net" not in fast and "ci" not in fast
    assert [c.name for c in prepush.select(None, False, ["net"])] == ["net"]
    assert prepush.select([], False, None) == []
    docs = [c.name for c in prepush.select(["docs/PREPUSH.md"], False, None)]
    assert docs == ["docs"]
    ci = [c.name for c in prepush.select([".github/workflows/jarves-checks.yml"], False, None)]
    assert ci == [c.name for c in prepush.CHECKS]          # workflow change selects everything
    guides = [c.name for c in prepush.select(["core/src/jarves/guides/data/x.toml"], False, None)]
    assert "guides" in guides and "net" not in guides


def test_hook_text_is_ours_and_bypassable():
    h = prepush.hook_text(r"C:\x\python.exe")
    assert prepush.MARK in h and "C:/x/python.exe" in h and "JARVES_PREPUSH_SKIP" in h
    assert h.startswith("#!/bin/sh\n") and "--changed-only" in h
