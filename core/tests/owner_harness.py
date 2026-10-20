"""Test harness for the OWNER answering approvals from the dashboard.

While active, an open dashboard page polls the Approvals route and the owner presses Approve / Deny on whatever
waits. Everything else is the real ApprovalService (nothing about the decision is mocked): the answer travels through
the real dashboard routes (api_approvals / api_approval_answer) and the real answers file / service."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from jarves import approvals as appr


class OwnerAnswers:
    def __init__(self, home, approve=True):
        self.home, self.approve, self.asked = home, approve, []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        seen = set()
        ctx = SimpleNamespace(home=self.home)
        while not self._stop.is_set():
            try:
                body = appr.api_approvals(ctx, None, None)[1]          # also marks the dashboard as watching
                for p in body["pending"]:
                    if p["id"] not in seen:
                        seen.add(p["id"])
                        self.asked.append(p)
                        appr.api_approval_answer(ctx, SimpleNamespace(group=lambda i, _p=p: _p["id"]),
                                                 {"approved": bool(self.approve)})
            except Exception:
                pass
            time.sleep(0.05)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(2)


@pytest.fixture
def owner_approves(home):
    with OwnerAnswers(home, True) as o:
        yield o


@pytest.fixture
def owner_denies(home):
    with OwnerAnswers(home, False) as o:
        yield o
