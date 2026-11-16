"""JAR-230: 'Jarvis, that was wrong' saves the last exchange, redacted, as a local feedback case (voice, dashboard,
CLI). Hermetic: tmp home, a store.db written directly, injected clock; nothing is sent."""
from __future__ import annotations

import json
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
    st.x("INSERT INTO sessions (id, kind, started) VALUES ('s1', 'live', 1)")
    for ts, role, text in turns:
        st.x("INSERT INTO turns (session_id, ts, role, text, chars) VALUES ('s1', ?, ?, ?, ?)", (ts, role, text, len(text)))
    for ts, name in tools:
        st.x("INSERT INTO tool_calls (session_id, ts, name) VALUES ('s1', ?, ?)", (ts, name))
    st.commit()
    st.close()


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


def test_no_history_means_no_case_and_no_store_is_created(home):
    r = wf.report_wrong(home, via="cli")
    assert r == {"ok": False, "code": "FEEDBACK_NO_EXCHANGE"}
    assert not (home / "store.db").exists() and not wf.path(home).exists()


def test_report_saves_a_redacted_eval_shaped_case_and_counts_a_correction(home):
    _history(home, [(1, "user", "Call Dana at 050-123-4567"), (2, "assistant", "Calling Dana at 050-123-4567")],
             tools=[(1.5, "phone_call"), (1.6, "call Dana now")])
    r = wf.report_wrong(home, via="voice", note="wrong person, mail dana@x.io", failure_log_on=True, now=NOW)
    case = r["case"]
    assert r["ok"] and wf.cases(home) == [case]
    assert case["lang"] == "en" and case["via"] == "voice" and case["saved"] == "2026-11-16T12:00:00"
    assert case["tools"] == ["phone_call", "other"] and case["text_kept"] is True
    raw = wf.path(home).read_text(encoding="utf-8")
    assert "050" not in raw and "dana@x.io" not in raw
    assert fl.weekly(home, now=NOW) == ["1x user_correction"]


def test_cases_are_bounded_and_deletable(home, monkeypatch):
    monkeypatch.setattr(wf, "MAX_CASES", 3)
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    ids = [wf.report_wrong(home, via="cli")["case"]["id"] for _ in range(5)]
    assert [c["id"] for c in wf.cases(home)] == ids[-3:]
    assert wf.delete(home, ids[-1]) and not wf.delete(home, ids[-1])
    assert [c["id"] for c in wf.cases(home)] == ids[2:4]
    wf.path(home).write_text(wf.path(home).read_text(encoding="utf-8") + "not json\n[1]\n", encoding="utf-8")
    assert len(wf.cases(home)) == 2                                     # corrupt rows skipped, never a crash


async def test_voice_tool_is_lazy_and_saves_from_the_core_store(home):
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
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    assert cli.main(["feedback", "wrong", "--note", "rude"]) == 0
    cid = wf.cases(home)[0]["id"]
    assert cid in capsys.readouterr().out
    assert cli.main(["feedback", "list"]) == 0 and cid in capsys.readouterr().out
    assert cli.main(["feedback", "show", cid, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["note"] == "rude"
    assert cli.main(["feedback", "show", "nope"]) == 2
    assert cli.main(["feedback", "delete", cid]) == 0 and wf.cases(home) == []


def test_cli_wrong_refuses_while_off(home, capsys):
    _history(home, [(1, "user", "hi"), (2, "assistant", "bye")])
    assert cli.main(["feedback", "wrong"]) == 2
    assert not wf.path(home).exists()


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
    phone = SimpleNamespace(home=home, principal={"kind": "device"})
    assert wrong(phone, None, {})[0] == 403 and listing(phone, None, None)[0] == 403
