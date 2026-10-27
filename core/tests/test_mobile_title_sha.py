"""Approvals carry a fingerprint of the full action text; a decision that quotes it is only taken for the text the phone saw."""
from __future__ import annotations

from types import SimpleNamespace as NS

from jarves import mobile


def fake_mob(tmp_path, monkeypatch, title, on=True):
    monkeypatch.setattr("jarves.approvals.read_pending_file", lambda home, reveal=False: [
        {"id": "0123456789abcdef", "title": title, "kind": "command", "risk": "low", "expires": 9e12, "typed": False, "pc_only": False}])
    sent = []
    monkeypatch.setattr("jarves.approvals.submit_answer", lambda home, aid, ok, channel="": (sent.append((aid, ok)) or (True, "")))
    mob = NS(home=tmp_path, settings=lambda: {"phone_approvals": on}, audit=lambda *a, **k: None)
    return mob, sent


def test_the_list_shows_more_text_and_a_fingerprint_of_all_of_it(tmp_path, monkeypatch):
    long = "run " + "x" * 600
    mob, _ = fake_mob(tmp_path, monkeypatch, long)
    row = mobile.approvals_view(mob)["pending"][0]
    assert len(row["title"]) == mobile.APPROVAL_TITLE_CHARS and row["title"].startswith("run xxx")
    assert row["title_sha"] == mobile.title_sha(long) and len(row["title_sha"]) == 16


def test_a_decision_with_the_matching_fingerprint_is_taken(tmp_path, monkeypatch):
    mob, sent = fake_mob(tmp_path, monkeypatch, "run tests")
    assert mobile.answer_approval(mob, "0123456789abcdef", True, "d1", title_sha_seen=mobile.title_sha("run tests")) == (200, {"ok": True})
    assert sent == [("0123456789abcdef", True)]


def test_a_decision_for_text_that_changed_is_refused_and_nothing_is_answered(tmp_path, monkeypatch):
    mob, sent = fake_mob(tmp_path, monkeypatch, "rm -rf the repo")
    status, body = mobile.answer_approval(mob, "0123456789abcdef", True, "d1", title_sha_seen=mobile.title_sha("run tests"))
    assert status == 409 and body["code"] == "STALE_TITLE" and sent == []


def test_without_a_fingerprint_the_old_behaviour_is_unchanged(tmp_path, monkeypatch):
    mob, sent = fake_mob(tmp_path, monkeypatch, "run tests")
    assert mobile.answer_approval(mob, "0123456789abcdef", False, "d1")[0] == 200 and sent
