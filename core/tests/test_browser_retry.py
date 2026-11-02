"""Hermetic test of the shared goto retry helper (no browser)."""
import pytest

import browser_retry as br

NOBUF = Exception("Page.goto: net::ERR_NO_BUFFER_SPACE at http://127.0.0.1:1/")


def test_retries_only_transient_errors_then_succeeds():
    seq = [NOBUF, NOBUF, "ok"]
    pauses = []

    def goto():
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v
    assert br.retrying(goto, sleep=pauses.append, pause=1)() == "ok"
    assert pauses == [1, 2]


def test_real_errors_are_not_retried_and_exhaustion_raises():
    calls = []

    def bad():
        calls.append(1)
        raise Exception("net::ERR_NAME_NOT_RESOLVED")
    with pytest.raises(Exception, match="NAME_NOT_RESOLVED"):
        br.retrying(bad, sleep=lambda s: None)()
    assert len(calls) == 1
    calls.clear()

    def always():
        calls.append(1)
        raise NOBUF
    with pytest.raises(Exception, match="NO_BUFFER_SPACE"):
        br.retrying(always, attempts=3, sleep=lambda s: None)()
    assert len(calls) == 3


def test_install_is_idempotent():
    if not br.install():
        pytest.skip("playwright not installed")
    from playwright.sync_api import Page
    first = Page.goto
    br.install()
    assert Page.goto is first and first._retrying
