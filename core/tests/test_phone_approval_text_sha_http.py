"""Abuse cases for POST /api/m/approvals/<id> with the text fingerprint `text_sha` (#698), over real HTTP with a real approvals
service: a malformed field is a 400, a row that is gone is a 409 APPROVAL_ALREADY_ANSWERED, changed text is a 409 STALE_TEXT
and answers nothing, the matching fingerprint answers once, and an absent field behaves exactly as before."""
from __future__ import annotations

import asyncio
import time

import pytest

from agents_helpers import Owner
from jarves import approvals
from test_mobile_access import admin, fake, pair, req, srv  # noqa: F401  (fixtures and helpers)


@pytest.fixture()
def phone(srv):
    owner = Owner(srv.ctx.home, policy=None)                       # a request that is shown and never answered by itself
    approvals.attach(owner.svc)
    tok, dev, _ = pair(srv)
    srv.mobile.set_phone_approvals(True)
    yield srv, owner, tok
    approvals.attach(None)
    owner.stop()


def ask(owner, prompt="run the build", kind="shell"):
    fut = asyncio.run_coroutine_threadsafe(
        owner.svc.ask(prompt, title="build", risk="low", kind=kind, details={"cmd": "make"}, source="user"), owner.loop)
    end = time.time() + 5
    while time.time() < end and not any(p["prompt"] == prompt for p in owner.svc.pending()):
        time.sleep(0.02)
    return fut, next(p for p in owner.svc.pending() if p["prompt"] == prompt)


def decide(srv, tok, aid, body):
    return req(srv, "POST", f"/api/m/approvals/{aid}", token=tok, body=body)


@pytest.mark.parametrize("bad", ["", "abc", "A" * 16, "g" * 16, "a" * 15, "a" * 17, 12345678, True, ["a" * 16], {"x": 1}])
def test_a_malformed_text_sha_is_a_400_and_answers_nothing(phone, bad):
    srv, owner, tok = phone
    fut, r = ask(owner)
    st, out, _ = decide(srv, tok, r["id"], {"approved": True, "text_sha": bad})
    assert st == 400 and "text_sha" in out["error"]
    assert owner.svc.pending() and not fut.done()


def test_a_gone_row_with_a_fingerprint_is_409_already_answered(phone):
    srv, owner, tok = phone
    fut, r = ask(owner)
    assert owner.svc.answer(r["id"], False, channel="dashboard")[0] is True      # answered on the computer first
    st, out, _ = decide(srv, tok, r["id"], {"approved": True, "text_sha": r["text_sha"]})
    assert st == 409 and out["code"] == "APPROVAL_ALREADY_ANSWERED"
    st, out, _ = decide(srv, tok, "f" * 16, {"approved": True, "text_sha": "0" * 16})      # an id that never existed
    assert st == 409 and out["code"] == "APPROVAL_ALREADY_ANSWERED"


def test_changed_text_is_stale_text_and_the_matching_one_answers_exactly_once(phone):
    srv, owner, tok = phone
    fut, r = ask(owner)
    st, out, _ = decide(srv, tok, r["id"], {"approved": True, "text_sha": "0" * 16 if r["text_sha"] != "0" * 16 else "1" * 16})
    assert st == 409 and out["code"] == "STALE_TEXT" and not fut.done() and owner.svc.pending()
    st, out, _ = decide(srv, tok, r["id"], {"approved": True, "text_sha": r["text_sha"]})
    assert st == 200 and out["ok"] is True and fut.result(5) is True
    st, out, _ = decide(srv, tok, r["id"], {"approved": True, "text_sha": r["text_sha"]})      # a second answer finds nothing
    assert st == 409 and out["code"] == "APPROVAL_ALREADY_ANSWERED"


def test_an_absent_text_sha_behaves_as_before(phone):
    srv, owner, tok = phone
    fut, r = ask(owner)
    st, out, _ = decide(srv, tok, r["id"], {"approved": False})
    assert st == 200 and out["ok"] is True and fut.result(5) is False
    st, out, _ = decide(srv, tok, r["id"], {"approved": False})                  # without the field a gone row is not the new code
    assert st == 409                                                            # same refusal as before the fingerprint existed


def test_a_typed_code_request_cannot_be_approved_from_the_phone_even_with_the_right_fingerprint(phone):
    srv, owner, tok = phone
    fut = asyncio.run_coroutine_threadsafe(
        owner.svc.ask("delete everything", title="danger", risk="high", kind="shell", details={"cmd": "rm"}, source="user",
                      typed=True), owner.loop)
    end = time.time() + 5
    while time.time() < end and not any(p["prompt"] == "delete everything" for p in owner.svc.pending()):
        time.sleep(0.02)
    r = next(p for p in owner.svc.pending() if p["prompt"] == "delete everything")
    st, out, _ = decide(srv, tok, r["id"], {"approved": True, "text_sha": r["text_sha"]})
    assert st == 403 and out["code"] == "TYPED_PC_ONLY" and not fut.done()
