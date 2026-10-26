"""#546/#547/#549/#555: a test that leaves a browser context open (an assertion failed before its close, or new_page()
never closes it) must not leak it into the next tests of the module. Hermetic: a fake browser, no real browser."""
from __future__ import annotations

import pytest

from conftest import close_leftover_browser_contexts


class FakeCtx:
    def __init__(self, owner, fail=False):
        self.owner, self.fail, self.closed = owner, fail, False

    def close(self):
        if self.fail:
            raise RuntimeError("already closed")
        self.closed = True
        self.owner.contexts.remove(self)


class FakeBrowser:
    def __init__(self):
        self.contexts = []

    def new_context(self, **kw):
        c = FakeCtx(self)
        self.contexts.append(c)
        return c


@pytest.fixture(scope="module")
def browser():
    return FakeBrowser()


def test_helper_closes_all_and_survives_a_failing_close():
    b = FakeBrowser()
    b.new_context()
    b.new_context()
    b.contexts.append(FakeCtx(b, fail=True))
    assert close_leftover_browser_contexts(b) == 2 and len(b.contexts) == 1
    assert close_leftover_browser_contexts(object()) == 0


def test_leaks_a_context(browser):
    browser.new_context()
    browser.new_context()
    assert len(browser.contexts) == 2                     # left open on purpose, like new_page() does


def test_next_test_starts_clean(browser):
    assert browser.contexts == []                         # the autouse fixture closed what the last test left
