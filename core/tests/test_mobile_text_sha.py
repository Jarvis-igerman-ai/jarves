"""Approvals carry a fingerprint of what will really run (redacted prompt + kind + details); a decision that quotes it only counts for the text
the phone saw. The check runs inside the service on the same row it resolves."""
from __future__ import annotations

import asyncio
import time

import pytest

from agents_helpers import Owner
from jarves import approvals, mobile

LONG = "run the build and then " + "step " * 120 + " api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture()
def owner(tmp_path):
    o = Owner(tmp_path, policy=None)                                 # a channel that shows the request and never answers
    approvals.attach(o.svc)
    yield o
    approvals.attach(None)
    o.stop()


def ask(owner, prompt=LONG, details=None, kind="shell"):
    fut = asyncio.run_coroutine_threadsafe(owner.svc.ask(prompt, title="build", risk="low", kind=kind, details=details or {"cmd": "make"}, source="user"), owner.loop)
    end = time.time() + 5
    while time.time() < end and not any(p for p in owner.svc.pending() if p["prompt"] == prompt and p["details"] == (details or {"cmd": "make"}) and p["kind"] == kind):
        time.sleep(0.02)
    return fut, next(p for p in owner.svc.pending() if p["prompt"] == prompt and p["details"] == (details or {"cmd": "make"}) and p["kind"] == kind)


def fake_mob(tmp_path):
    return type("M", (), {"home": tmp_path, "settings": lambda self: {"phone_approvals": True}, "audit": lambda *a, **k: None})()


def test_a_real_ask_shows_400_redacted_characters_and_fingerprints_all_of_it(owner):
    fut, r = ask(owner)
    assert len(r["title"]) <= 120 and len(r["shown_text"]) <= 400 and len(r["shown_text"]) > 120
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in r["shown_text"]
    assert len(r["text_sha"]) == 16 and r["text_sha"] == r["text_sha"].lower()
    assert owner.svc.answer(r["id"], False, channel="dashboard")[0] is True
    assert fut.result(5) is False


def test_the_same_text_is_answered_a_changed_text_is_refused_and_nothing_is_answered(owner):
    fut, r = ask(owner)
    ok, why = owner.svc.answer(r["id"], True, channel="phone", text_sha="0" * 16)
    assert ok is False and approvals.refusal_code(why) == "STALE_TEXT"
    assert owner.svc.pending()
    assert owner.svc.answer(r["id"], True, channel="phone", text_sha=r["text_sha"]) == (True, "")
    assert fut.result(5) is True


def test_the_fingerprint_covers_details_and_kind_not_only_the_label(owner):
    f1, r1 = ask(owner, "same", {"cmd": "make"})
    f2, r2 = ask(owner, "same", {"cmd": "rm -rf /"})
    f3, r3 = ask(owner, "same", {"cmd": "make"}, kind="install")
    assert len({r1["text_sha"], r2["text_sha"], r3["text_sha"]}) == 3
    for r in (r1, r2, r3):
        owner.svc.answer(r["id"], False, channel="dashboard")


def test_the_phone_route_helpers_use_the_same_check_and_refuse_a_gone_row(owner, tmp_path):
    fut, r = ask(owner)
    mob = fake_mob(owner.svc.home)
    status, body = mobile.answer_approval(mob, r["id"], True, "d1", text_sha="f" * 16)
    assert status == 409 and body["code"] == "STALE_TEXT" and owner.svc.pending()
    status, body = mobile.answer_approval(mob, "0123456789abcdef", True, "d1", text_sha="a" * 16)
    assert status == 409 and body["code"] == "APPROVAL_ALREADY_ANSWERED"
    assert mobile.answer_approval(mob, r["id"], False, "d1", text_sha=r["text_sha"])[0] == 200
    assert fut.result(5) is False


def test_the_phone_list_has_label_text_and_fingerprint(owner):
    fut, r = ask(owner)
    owner.svc._write_pending()
    rows = mobile.approvals_view(fake_mob(owner.svc.home))["pending"]
    assert rows and set(rows[0]) >= {"id", "title", "text", "text_sha", "kind", "risk"} and 120 < len(rows[0]["text"]) <= 400
    assert rows[0]["text_sha"] == r["text_sha"]
    owner.svc.answer(r["id"], False, channel="dashboard")
    fut.result(5)
