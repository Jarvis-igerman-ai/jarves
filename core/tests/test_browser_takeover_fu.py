"""#659 narrow re-check findings: start() holds no lock across the driver, view_frame reads the address inside the driver lock,
a frame in flight when the owner gives back is dropped, resuming needs the takeover switch, no public open() after the owner."""
from __future__ import annotations

import threading
import time

import pytest
from test_browser_takeover import Drv, LOCAL, code, mk  # noqa: F401

from jarves import browser_liveview as lv
from jarves import browser_pilot as bp


def test_start_never_holds_the_view_lock_across_the_driver(tmp_path):
    v, s, d, _ = mk(tmp_path)
    started, release = threading.Event(), threading.Event()

    def hung():
        started.set()
        release.wait(5)
        return b"PNG-fake"
    d.screenshot = hung
    th = threading.Thread(target=lambda: _swallow(v.start))
    th.start()
    assert started.wait(5)
    got = v._lock.acquire(timeout=1)                    # the view lock is free while the driver hangs
    assert got
    v._lock.release()
    assert v.stop() == {"stopped": True}
    release.set()
    th.join(5)


def _swallow(fn, *a):
    try:
        fn(*a)
    except (lv.LiveError, bp.PilotError):
        pass


def test_view_frame_reads_the_address_only_inside_the_driver_lock(tmp_path, monkeypatch):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    monkeypatch.setattr(bp.PilotSession, "DRIVER_WAIT_S", 0.2)
    reads = []
    orig = bp.PilotSession._final_url

    def counting(self):
        reads.append(1)
        return orig(self)
    monkeypatch.setattr(bp.PilotSession, "_final_url", counting)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "x"
    d.text = slow
    th = threading.Thread(target=lambda: _swallow(s.read))
    th.start()
    assert started.wait(5)
    n = len(reads)
    assert code(v.frame, g) == "BROWSER_PILOT_BUSY"
    assert len(reads) == n                              # a busy page: the driver address was not even read
    release.set()
    th.join(5)


def test_a_frame_in_flight_when_the_owner_gives_back_is_not_cached(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    started, release = threading.Event(), threading.Event()

    def shot():
        started.set()
        release.wait(5)
        return b"PNG-with-typed-text"
    d.screenshot = shot
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("e", code(v.frame, g)))
    th.start()
    assert started.wait(5)
    v.give_back(g)                                       # hands the page back while the picture is being taken
    release.set()
    th.join(5)
    assert out["e"] == "LIVEVIEW_TOKEN" and v._last == b""


def test_resuming_the_agent_needs_the_takeover_switch(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    v.give_back(g)
    assert s.state == bp.PAUSED
    v.takeover_enabled = lambda: False
    assert code(v.give_back, g, True) == "LIVEVIEW_TAKEOVER_OFF" and s.state == bp.PAUSED


def test_no_public_open_after_the_owner_used_the_page(tmp_path):
    v, s, d, _ = mk(tmp_path)
    g = v.start()["grant"]
    v.takeover(g)
    v.give_back(g)
    v.give_back(g, True)
    assert s.state == bp.AGENT
    assert code(s.open, "https://example.com/") == "BROWSER_PILOT_URL"
    assert s.open(LOCAL)["scope"] == "local"
