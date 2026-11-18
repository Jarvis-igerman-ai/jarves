"""JAR-230 (slice 1: core + CLI): `jarvis feedback wrong` saves the last exchange, redacted, as a local case.
Hermetic: tmp home, a store.db written directly, an injected clock; nothing is sent."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from jarves import failure_log as fl, features as feat, logs, wrong_feedback as wf
from jarves.store import Store

NOW = datetime(2026, 11, 18, 12, 0)


def _history(home, turns, tools=(), session="s1"):
    st = Store(home / "store.db")
    st.x("INSERT OR IGNORE INTO sessions (id, kind, started) VALUES (?, 'live', 1)", (session,))
    for ts, role, text in turns:
        st.x("INSERT INTO turns (session_id, ts, role, text, chars) VALUES (?, ?, ?, ?, ?)",
             (session, ts, role, text, len(text)))
    for ts, name in tools:
        st.x("INSERT INTO tool_calls (session_id, ts, name) VALUES (?, ?, ?)", (session, ts, name))
    st.commit()
    st.close()


def test_feature_is_beta_and_off_by_default():
    f = feat.CATALOG[wf.FEATURE]
    assert f.default_enabled is False and f.stage == "beta"


def test_redaction_masks_secrets_pii_and_sensitive_turns():
    assert "dana@example.com" not in wf.redact("write to dana@example.com please")
    assert "sk-proj" not in wf.redact("my key is sk-proj-abcdefghijklmnopqrstuvwxyz0123456789")
    assert wf.redact("הסיסמה שלי היא hunter2") == "<sensitive, 22 chars>"
    assert len(wf.redact("x" * 5000)) == wf.MAX_CHARS


def test_last_exchange_skips_the_report_and_keeps_its_tools_and_time(home):
    _history(home, [(10, "user", "מה מזג האוויר?"), (11, "assistant", "שלג."), (12, "assistant", "מינוס עשר."),
                    (20, "user", "ג׳רוויס, זה היה לא נכון")], tools=[(10.5, "web_search"), (25, "later")])
    ex = wf.last_exchange(home)
    assert (ex["say"], ex["reply"], ex["tools"], ex["ts"]) == ("מה מזג האוויר?", "שלג. מינוס עשר.", ["web_search"], 12)


def test_repeated_reports_save_the_exchange_before_all_of_them(home):
    """Review R1: "q, a, that was wrong, sorry, that was wrong" saves q/a, not the first report and its reply."""
    _history(home, [(1, "user", "q"), (2, "assistant", "a"), (3, "user", "that was wrong"), (4, "assistant", "sorry"),
                    (5, "user", "Jarvis, that was wrong")])
    assert (wf.last_exchange(home)["say"], wf.last_exchange(home)["reply"]) == ("q", "a")


def test_a_question_containing_the_phrase_is_not_a_report(home):
    _history(home, [(1, "user", "לא נכון שהבירה היא סידני?"), (2, "assistant", "נכון, זו קנברה.")])
    assert wf.last_exchange(home)["say"] == "לא נכון שהבירה היא סידני?"


def test_no_history_or_unreadable_history_is_reported_not_raised(home):
    assert wf.report_wrong(home, via="cli") == {"ok": False, "code": "FEEDBACK_NO_EXCHANGE"}
    (home / "store.db").write_text("not a database", encoding="utf-8")
    assert wf.last_exchange(home) is None


def test_privacy_mode_keeps_lengths_only():
    c = wf.make_case({"say": "q text", "reply": "an answer"}, via="cli", note="secret note", now=NOW, privacy=True)
    assert (c["say"], c["reply"], c["note"]) == ("<6 chars>", "<9 chars>", "<11 chars>")


def test_cli_saves_shows_the_exchange_time_lists_and_deletes(home, capsys, monkeypatch):
    """Review R2: the output names when the saved exchange happened, so a stale press is visible."""
    monkeypatch.setattr(logs, "_privacy", False)
    monkeypatch.setattr(feat.Features, "active", lambda self, fid: fid in (wf.FEATURE, fl.FEATURE))
    ts = datetime(2026, 11, 15, 9, 30).timestamp()
    _history(home, [(ts - 5, "user", "q"), (ts, "assistant", "a"), (ts + 9, "user", "that was wrong")])
    cfg = SimpleNamespace(log=SimpleNamespace(privacy=False))
    args = SimpleNamespace(action="wrong", target="", note="bad", json=False)
    assert wf.cmd_feedback(args, cfg, home) == 0
    assert "2026-11-15 09:30" in capsys.readouterr().out
    (case,) = wf.cases(home)
    assert (case["say"], case["reply"], case["note"], case["via"]) == ("q", "a", "bad", "cli")
    assert any(r["kind"] == "user_correction" for r in map(json.loads, fl.path(home).read_text().splitlines()))
    assert wf.cmd_feedback(SimpleNamespace(action="list", target="", note="", json=False), cfg, home) == 0
    assert case["id"] in capsys.readouterr().out and "2026-11-15 09:30" in case["exchange_at"].replace("T", " ")
    assert wf.cmd_feedback(SimpleNamespace(action="delete", target=case["id"], note="", json=False), cfg, home) == 0
    assert wf.cases(home) == []


def test_cli_refuses_while_the_feature_is_off(home, capsys):
    args = SimpleNamespace(action="wrong", target="", note="", json=False)
    assert wf.cmd_feedback(args, SimpleNamespace(), home) == 2
    assert not wf.path(home).exists()


def test_a_corrupt_line_in_the_cases_file_is_skipped(home):
    wf.path(home).write_text('not json\n[1]\n{"id": "FB-0000abcd"}\n', encoding="utf-8")
    assert [c["id"] for c in wf.cases(home)] == ["FB-0000abcd"]
