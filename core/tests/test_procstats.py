"""Hermetic tests for jarves.procstats (own-process sampling, no clocks, no network)."""
from __future__ import annotations

import os

from jarves import procstats

_KEYS = {"rss_mb", "peak_mb", "private_mb", "cpu_s", "read_mb", "write_mb", "threads"}


def test_sample_own_process_has_all_fields():
    s = procstats.sample(os.getpid())
    assert s is not None
    assert _KEYS <= set(s)
    assert s["rss_mb"] > 0
    assert s["threads"] >= 1
    assert s["cpu_s"] >= 0 and s["read_mb"] >= 0 and s["write_mb"] >= 0


def test_sample_missing_pid_returns_none():
    assert procstats.sample(2**31 - 7) is None


def test_children_is_non_negative_int_and_zero_for_missing_pid():
    assert isinstance(procstats.children(os.getpid()), int)
    assert procstats.children(os.getpid()) >= 0
    assert procstats.children(2**31 - 7) == 0


def test_target_returns_int_and_keeps_missing_pid():
    assert isinstance(procstats.target(os.getpid()), int)
    assert procstats.target(2**31 - 7) == 2**31 - 7
