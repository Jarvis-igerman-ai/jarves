"""JAR-230: 'Jarvis, that was wrong' saves the last exchange, redacted, as a local feedback case (voice, dashboard,
CLI). Hermetic: tmp home, a store.db written directly, injected clock; nothing is sent."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from jarves import cli, failure_log as fl, features as feat, wrong_feedback as wf
from jarves.features import CATALOG
from jarves.skills import SkillRegistry
from jarves.store import Store

NOW = datetime(2026, 11, 16, 12, 0)


def _history(home, turns, tools=()):
    st = Store(home / "store.db")
    st.x("INSERT OR IGNORE INTO sessions (id, kind, started) VALUES ('s1', 'live', 1)")
    for ts, role, text in turns:
        st.x("INSERT INTO turns (session_id, ts, role, text, chars) VALUES ('s1', ?, ?, ?, ?)", (ts, role, text, len(text)))
    for ts, name in tools:
        st.x("INSERT INTO tool_calls (session_id, ts, name) VALUES ('s1', ?, ?)", (ts, name))
    st.commit()
    st.close()


@pytest.fixture
def privacy_off(monkeypatch):
    from jarves import logs
    monkeypatch.setattr(logs, "_privacy", False)                       # full-text mode: the note text is kept


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(feat.Features, "active", lambda self, fid: fid == wf.FEATURE)


def test_feature_is_beta_off_by_default_and_gates_the_tool():
    f = CATALOG[wf.FEATURE]
    assert f.default_enabled is False and f.stage == "beta" and f.skills == ["wrong_feedback"]


def test_redaction_masks_secrets_pii_and_sensitive_turns():
    assert "<email>" in wf.redact("write to dana@example.com please")
    assert "sk-" not in wf.redact("my key is sk-proj-abcdefghijklmnopqrstuvwxyz0123456789")
    assert wf.redact("הסיסמה שלי היא hunter2") == "<sensitive, 22 chars>"
    assert wf.redact("<12 chars>") == "<12 chars>"                     # privacy mode: length only, kept as is
    assert len(wf.redact("x" * 5000)) == wf.MAX_CHARS


def test_last_exchange_skips_the_feedback_turn_and_what_followed(home):
    _history(home, [(10, "user", "מה מזג האוויר בחיפה?"), (11, "assistant", "בחיפה יורד שלג."),
                    (12, "assistant", "מינוס עשר."), (20, "user", "ג׳רוויס, זה היה לא נכון"), (21, "assistant", "רגע")],
             tools=[(10.5, "web_search"), (25, "later_tool")])
    ex = wf.last_exchange(home)
    assert ex["say"] == "מה מזג האוויר בחיפה?" and ex["reply"] == "בחיפה יורד שלג. מינוס עשר."
    assert ex["tools"] == ["web_search"]


def test_an_older_feedback_turn_or_everyday_phrase_never_moves_the_cut(home):
    """Review R1: a second report saves the NEWEST exchange, and an earlier question containing 'לא נכון' is kept."""
    _history(home, [(1, "user", "q1"), (2, "assistant", "a1"), (3, "user", "that was wrong"), (4, "assistant", "sorry"),
                    (5, "user", "q2"), (6, "assistant", "a2")])
    assert (wf.last_exchange(home)["say"], wf.last_exchange(home)["reply"]) == ("q2", "a2")
    _history(home, [(7, "user", "האם זה לא נכון שהשמש קרה?"), (8, "assistant", "זה לא נכון.")])
    assert wf.last_exchange(home)["say"] == "האם זה לא נכון שהשמש קרה?"
    _history(home, [(9, "user", "that was wrong"), (10, "assistant", "sorry")])        # the current feedback turn
    assert wf.last_exchange(home)["say"] == "האם זה לא נכון שהשמש קרה?"


def test_two_reports_in_a_row_save_the_exchange_before_both(home):
    """Review round 4 R1: "q, a, that was wrong, sorry, that was wrong" saves q/a, not the first report and its reply."""
    _history(home, [(1, "user", "q"), (2, "assistant", "a"), (3, "user", "that was wrong"), (4, "assistant", "sorry"),
                    (5, "user", "Jarvis, that was wrong")])
    ex = wf.last_exchange(home)
    assert (ex["say"], ex["reply"]) == ("q", "a")


def test_the_case_and_every_output_show_when_the_exchange_happened(home, capsys, on):
    """Review round 4 R2: a press long after the conversation shows the exchange's own time, not only the save time."""
    ts = datetime(2026, 11, 10, 9, 30).timestamp()
    _history(home, [(ts, "user", "hi"), (ts + 1, "assistant", "bye")])
    r = wf.report_wrong(home, via="cli", now=NOW, privacy=False)
    assert r["case"]["exchange_at"] == "2026-11-10T09:30" and r["case"]["saved"].startswith("2026-11-16")
    assert "2026-11-10 09:30" in wf.say(r, "en") and "2026-11-10 09:30" in wf.say(r, "he")
    assert cli.main(["feedback", "list"]) == 0 and "from 2026-11-10 09:30" in capsys.readouterr().out
    assert wf._at(None) == "" and wf._at("x") == ""


def test_a_long_correction_that_starts_with_the_phrase_is_skipped(home):
    """Review R3: 'Jarvis that was wrong, ...' of any length is the feedback turn, not the exchange to save."""
    long = "Jarvis that was wrong, the capital of Australia is Canberra not Sydney, please remember that next time"
    _history(home, [(1, "user", "what is the capital of Australia"), (2, "assistant", "Sydney"),
                    (3, "user", long), (4, "assistant", "Sorry, Canberra.")])
    assert len(long) > 60 and wf.last_exchange(home)["reply"] == "Sydney"
    assert wf.is_feedback_turn("ג׳רוויס, זה היה לא נכון, " + "ה" * 80)
    assert not wf.is_feedback_turn("האם זה לא נכון שהשמש קרה? " + "ה" * 80)


def test_text_kept_is_false_when_both_turns_are_masked_as_sensitive():
    """Review R5: the '<sensitive, N chars>' form counts as no text kept."""
    case = wf.make_case({"say": "הסיסמה שלי היא hunter2", "reply": "הסיסמה שלך היא hunter2"}, via="cli",
                         privacy=False)
    assert case["say"].startswith("<sensitive") and case["text_kept"] is False


def test_no_history_means_no_case_and_no_store_is_created(home):
    r = wf.report_wrong(home, via="cli", now=NOW)
    assert r == {"ok": False, "code": "FEEDBACK_NO_EXCHANGE"}
    assert not (home / "store.db").exists() and not wf.path(home).exists()


def test_report_saves_a_redacted_eval_shaped_case_and_counts_a_correction(home):
    _history(home, [(1, "user", "Call Dana at 050-123-4567"), (2, "assistant", "Calling Dana at 050-123-4567")],
             tools=[(1.5, "phone_call"), (1.6, "call Dana now")])
    r = wf.report_wrong(home, via="voice", note="wrong person, mail dana@x.io", failure_log_on=True, now=NOW,
                       privacy=False)
    case = r["case"]
    assert r["ok"] and wf.cases(home) == [case]
    assert case["lang"] == "en" and case["via"] == "voice" and case["saved"] == "2026-11-16T12:00:00"
    assert case["tools"] == ["phone_call", "other"] and case["say"].startswith("<sensitive")
    assert case["text_kept"] is False                                   # both turns masked (review R5)
    raw = wf.path(home).read_text(encoding="utf-8")
    assert "050" not in raw and "dana@x.io" not in raw
    assert fl.weekly(home, now=NOW) == ["1x user_correction"]


def test_cases_are_bounded_and_deletable(home, monkeypatch):
    monkeypatch.setattr(wf, "MAX_CASES", 3)
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    ids = [wf.report_wrong(home, via="cli", now=NOW)["case"]["id"] for _ in range(5)]
    assert [c["id"] for c in wf.cases(home)] == ids[-3:]
    assert wf.delete(home, ids[-1]) and not wf.delete(home, ids[-1])
    assert [c["id"] for c in wf.cases(home)] == ids[2:4]
    wf.path(home).write_text(wf.path(home).read_text(encoding="utf-8") + "not json\n[1]\n", encoding="utf-8")
    assert len(wf.cases(home)) == 2                                     # corrupt rows skipped, never a crash


async def test_voice_tool_is_lazy_and_saves_from_the_core_store(home, privacy_off):
    _history(home, [(1, "user", "what is 2+2"), (2, "assistant", "five")])
    st = Store(home / "store.db")
    reg = SkillRegistry()
    core = SimpleNamespace(home=home, registry=reg, history=SimpleNamespace(store=st),
                           cfg=SimpleNamespace(voice=SimpleNamespace(language="en")),
                           features=SimpleNamespace(active=lambda fid: False))
    wf.attach(core)
    assert "report_wrong_answer" in reg.allowed_actions
    assert "report_wrong_answer" not in [t.name for t in reg.tools()]
    reg.open_lazy("Jarvis, that was wrong")
    assert "report_wrong_answer" in [t.name for t in reg.tools()]
    res = await reg.call("report_wrong_answer", {"note": "it is four"})
    st.close()
    assert res.ok and "saved the last exchange" in res.message
    assert wf.cases(home)[0]["reply"] == "five" and wf.cases(home)[0]["note"] == "it is four"


def test_cli_wrong_list_show_delete(home, capsys, on):
    (home / "config.toml").write_text("[log]\nprivacy = false\n", encoding="utf-8")   # full text: the note is kept
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    assert cli.main(["feedback", "wrong", "--note", "rude"]) == 0
    cid = wf.cases(home)[0]["id"]
    assert cid in capsys.readouterr().out
    assert cli.main(["feedback", "list"]) == 0 and cid in capsys.readouterr().out
    assert cli.main(["feedback", "show", cid, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["note"] == "rude"
    assert cli.main(["feedback", "show", "nope"]) == 2
    assert cli.main(["feedback", "delete", cid]) == 0 and wf.cases(home) == []


def test_cli_note_follows_the_config_privacy_switch(home, capsys, on, monkeypatch):
    from jarves import logs
    monkeypatch.setattr(logs, "_privacy", False)                       # the process flag is not what the CLI reads
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    r = wf.cmd_feedback(SimpleNamespace(action="wrong", target="", note="met Dana", json=True),
                        SimpleNamespace(log=SimpleNamespace(privacy=True)), home)
    assert r == 0 and wf.cases(home)[0]["note"] == "<8 chars>"


def test_cli_wrong_refuses_while_off(home, capsys):
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    assert cli.main(["feedback", "wrong"]) == 2
    assert not wf.path(home).exists()
    wf.save(home, wf.make_case({"say": "hi", "reply": "bye"}, via="cli"))
    assert cli.main(["feedback", "list"]) == 2                              # review R6: same switch as the dashboard
    assert cli.main(["feedback", "show", wf.cases(home)[0]["id"]]) == 2


def test_core_registry_hides_and_refuses_the_tool_while_the_feature_is_off(home):
    """Review R4: the real Features gate on the registry, feature OFF (the default): not offered, a call refused."""
    from jarves.config import Config
    _history(home, [(1, "user", "q"), (2, "assistant", "a")])
    reg = SkillRegistry()
    reg.gate = feat.Features(Config(), home).gate
    core = SimpleNamespace(home=home, registry=reg, history=None, cfg=SimpleNamespace(voice=SimpleNamespace(language="en")),
                           features=SimpleNamespace(active=lambda fid: False))
    wf.attach(core)
    reg.open_lazy("Jarvis, that was wrong")
    assert "report_wrong_answer" not in [t.name for t in reg.tools()]
    res = asyncio.run(reg.call("report_wrong_answer", {}))
    assert not res.ok and not wf.path(home).exists()


def test_dashboard_routes_are_gated_and_local_only(home, monkeypatch):
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    routes = {}
    wf.dashboard_routes(lambda method, rx, fn: routes.setdefault((method, rx), fn))
    active = {"v": False}
    monkeypatch.setattr(feat, "_ctx_features", lambda ctx: SimpleNamespace(active=lambda fid: active["v"] and fid == wf.FEATURE))
    ctx = SimpleNamespace(home=home, principal={"kind": "local"})
    wrong, listing = routes[("POST", r"/api/feedback/wrong")], routes[("GET", r"/api/feedback")]
    assert wrong(ctx, None, {})[0] == 409 and listing(ctx, None, None) == (200, {"active": False, "cases": []})
    active["v"] = True
    code, body = wrong(ctx, None, {"note": "x"})
    assert code == 200 and body["case"]["via"] == "dashboard"
    assert listing(ctx, None, None)[1]["cases"][0]["id"] == body["case"]["id"]
    remove = routes[("POST", r"/api/feedback/(FB-[0-9a-f]{8})/delete")]          # review R9
    m = re.match(r"^/api/feedback/(FB-[0-9a-f]{8})/delete$", f"/api/feedback/{body['case']['id']}/delete")
    assert remove(ctx, m, {})[0] == 200 and listing(ctx, None, None)[1]["cases"] == []
    assert remove(ctx, m, {})[0] == 404
    assert wrong(ctx, None, {})[0] == 200 and wf.cases(home)                     # store still has an exchange
    phone = SimpleNamespace(home=home, principal={"kind": "device"})
    assert wrong(phone, None, {})[0] == 403 and listing(phone, None, None)[0] == 403
    nobody = SimpleNamespace(home=home)                                          # review R2: missing principal = refused
    assert wrong(nobody, None, {})[0] == 403 and listing(nobody, None, None)[0] == 403
    assert remove(nobody, m, {})[0] == 403


def test_dashboard_note_privacy_follows_the_config_like_the_cli(home, monkeypatch):
    """Review round 5 R4: the dashboard reads cfg.log.privacy (the switch updates it), not the process flag."""
    from jarves import logs
    monkeypatch.setattr(logs, "_privacy", False)
    monkeypatch.setattr(feat, "_ctx_features", lambda ctx: SimpleNamespace(active=lambda fid: fid == wf.FEATURE))
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    ctx = SimpleNamespace(home=home, principal={"kind": "local"}, cfg=SimpleNamespace(log=SimpleNamespace(privacy=True)))
    assert wf.api_wrong(ctx, None, {"note": "met Dana"})[1]["case"]["note"] == "<8 chars>"
    ctx.cfg.log.privacy = False
    assert wf.api_wrong(ctx, None, {"note": "met Dana"})[1]["case"]["note"] == "met Dana"


def test_a_hebrew_question_that_starts_with_the_phrase_is_not_a_report(home):
    """Review round 5 R1: "לא נכון ש...?" is a question; the report after it must save that question's exchange."""
    q = "לא נכון שהבירה של אוסטרליה היא סידני?"
    assert not wf.is_feedback_turn(q) and not wf.is_feedback_turn("that was wrong, wasn't it?")
    assert not wf.is_feedback_turn("לא נכון שהשמש קרה " + "ה" * 80)
    assert wf.is_feedback_turn("ג׳רוויס, זה היה לא נכון") and wf.is_feedback_turn("Jarvis, that was wrong.")
    _history(home, [(1, "user", "hi"), (2, "assistant", "hello"), (3, "user", q), (4, "assistant", "נכון, סידני."),
                    (5, "user", "ג׳רוויס, זה היה לא נכון"), (6, "assistant", "סליחה")])
    ex = wf.last_exchange(home)
    assert ex["say"] == q and ex["reply"] == "נכון, סידני."


def test_a_short_turn_that_merely_contains_the_phrase_is_not_a_report(home):
    """Review round 6 R4: only a turn that STARTS with the phrase is skipped as the report."""
    req = "תזכיר לי שזה לא נכון לאכול מאוחר"
    assert not wf.is_feedback_turn(req) and not wf.is_feedback_turn("I think that was wrong")
    assert wf.is_feedback_turn("no, that was wrong") and wf.is_feedback_turn("זה היה לא נכון")
    _history(home, [(1, "user", "hi"), (2, "assistant", "hello"), (3, "user", req), (4, "assistant", "אזכיר")])
    assert wf.last_exchange(home)["say"] == req


def test_privacy_mode_keeps_only_the_note_length(home, monkeypatch):
    """Review R1 (round 3): in log privacy mode the note can quote the turn, so it is kept as its length only."""
    from jarves import logs
    _history(home, [(1, "user", "<9 chars>"), (2, "assistant", "<4 chars>")])
    monkeypatch.setattr(logs, "_privacy", True)
    case = wf.report_wrong(home, via="cli", note="met Dana at the clinic", now=NOW)["case"]
    assert case["note"] == "<22 chars>" and "Dana" not in wf.path(home).read_text(encoding="utf-8")
    monkeypatch.setattr(logs, "_privacy", False)
    assert wf.report_wrong(home, via="cli", note="met Dana", now=NOW)["case"]["note"] == "met Dana"


def test_cli_side_opens_the_history_read_only(home):
    """Review R3: the CLI/dashboard never runs the Store schema/migrations on the Core's store.db."""
    import sqlite3
    db = sqlite3.connect(home / "store.db")
    db.executescript("CREATE TABLE turns (id INTEGER PRIMARY KEY, session_id TEXT, ts REAL, role TEXT, text TEXT);"
                     "CREATE TABLE tool_calls (id INTEGER PRIMARY KEY, session_id TEXT, ts REAL, name TEXT);"
                     "INSERT INTO turns (session_id, ts, role, text) VALUES ('s1', 1, 'user', 'hi'), ('s1', 2, 'assistant', 'yo');")
    db.commit()
    assert wf.last_exchange(home)["reply"] == "yo"
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    db.close()
    assert tables == {"turns", "tool_calls"}                            # no schema or migration was written


def test_list_reads_under_the_writer_lock(home, monkeypatch):
    """Review R2: a reader holding the file open while os.replace runs fails on Windows, so cases() takes the lock."""
    import contextlib
    held = []

    @contextlib.contextmanager
    def lock(p):
        held.append(p)
        yield
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    wf.report_wrong(home, via="cli", now=NOW)
    monkeypatch.setattr(wf, "_file_lock", lock)
    assert len(wf.cases(home)) == 1 and held == [wf.path(home)]


def test_feedback_page_tolerates_a_non_string_exchange_time():
    """Review round 6 R5: a hand-edited row (exchange_at = 5) must not break the Feedback page (no node in CI here)."""
    from pathlib import Path
    js = (Path(wf.__file__).parent / "dashboard_static" / "feedback.js").read_text(encoding="utf-8")
    assert "c.exchange_at.replace(" not in js and js.count("String(c.exchange_at).replace(") == 2


def test_privacy_on_masks_full_text_history_in_every_path(home, capsys, on, monkeypatch):
    """Doctor round: history recorded with privacy off, privacy on at report time: say, reply and note keep lengths."""
    from jarves import logs
    monkeypatch.setattr(logs, "_privacy", False)
    _history(home, [(1, "user", "met Dana at the clinic"), (2, "assistant", "noted Dana")])
    case = wf.report_wrong(home, via="cli", note="Dana", now=NOW, privacy=True)["case"]
    assert (case["say"], case["reply"], case["note"], case["text_kept"]) == ("<22 chars>", "<10 chars>", "<4 chars>", False)
    monkeypatch.setattr(feat, "_ctx_features", lambda ctx: SimpleNamespace(active=lambda fid: fid == wf.FEATURE))
    ctx = SimpleNamespace(home=home, principal={"kind": "local"}, cfg=SimpleNamespace(log=SimpleNamespace(privacy=True)))
    assert wf.api_wrong(ctx, None, {})[1]["case"]["say"] == "<22 chars>"
    cli_cfg = SimpleNamespace(log=SimpleNamespace(privacy=True))
    assert wf.cmd_feedback(SimpleNamespace(action="wrong", target="", note="", json=True), cli_cfg, home) == 0
    assert "Dana" not in wf.path(home).read_text(encoding="utf-8")


def test_the_exchange_never_mixes_interleaved_sessions(home):
    """Doctor round: question, answer and tools come from ONE session, also when another session talks in between."""
    st = Store(home / "store.db")
    for s in ("s1", "s2"):
        st.x("INSERT OR IGNORE INTO sessions (id, kind, started) VALUES (?, 'live', 1)", (s,))
    for s, ts, role, text in [("s1", 1, "user", "q one"), ("s2", 2, "user", "q two"), ("s1", 3, "assistant", "a one"),
                              ("s2", 4, "assistant", "a two"), ("s1", 5, "user", "that was wrong")]:
        st.x("INSERT INTO turns (session_id, ts, role, text, chars) VALUES (?, ?, ?, ?, ?)", (s, ts, role, text, len(text)))
    st.x("INSERT INTO tool_calls (session_id, ts, name) VALUES ('s2', 2.5, 'two_tool')")
    st.x("INSERT INTO tool_calls (session_id, ts, name) VALUES ('s1', 1.5, 'one_tool')")
    st.commit()
    st.close()
    ex = wf.last_exchange(home)
    assert (ex["say"], ex["reply"], ex["tools"]) == ("q one", "a one", ["one_tool"])


def test_an_unopenable_history_is_logged_not_raised(home, monkeypatch):
    (home / "store.db").write_bytes(b"")
    def boom(*a, **k):
        raise wf.sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(wf.sqlite3, "connect", boom)
    seen = []
    monkeypatch.setattr(wf.logs, "warn", lambda *a, **k: seen.append(a[1]))
    assert wf.last_exchange(home) is None and seen == ["FEEDBACK_HISTORY_UNREADABLE"]
