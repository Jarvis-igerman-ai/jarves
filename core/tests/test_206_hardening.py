"""#206 V5 security P3 list: the items still open on main (F13 text at rest in privacy mode, H2 phone edit keeps a
confirmed preference, H3/H4 guide notes). Each test fails on the code before the fix."""
from __future__ import annotations

import pytest

from jarves import guides, logs, mobile
from jarves.preferences import Preferences
from jarves.userdata import open_db

from test_v5_goal_plans import Env, steps


# ---------------------------------------------------------------- F13: no text at rest in privacy mode
def test_f13_privacy_mode_chat_ledger_keeps_no_reply_text(home, monkeypatch):
    m = mobile.MobileAccess(home)
    monkeypatch.setattr(logs, "privacy", lambda: True)
    m.remember("dev1", "mid-1", {"reply": "your bank PIN is 4321", "ok": True})
    rows = m.cont._rows("SELECT body FROM ledger")
    assert rows and not any("4321" in r["body"] for r in rows)
    again = mobile.MobileAccess(home).replay("dev1", "mid-1")     # a PC restart: only the ledger copy is left
    assert again["ok"] is True and again["not_kept"] is True and "4321" not in again["reply"] and again["reply"]


def test_f13_privacy_off_chat_ledger_still_replays_the_answer(home, monkeypatch):
    monkeypatch.setattr(logs, "privacy", lambda: False)
    mobile.MobileAccess(home).remember("dev1", "mid-2", {"reply": "hello there", "ok": True})
    assert mobile.MobileAccess(home).replay("dev1", "mid-2") == {"reply": "hello there", "ok": True}


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


async def test_f13_privacy_mode_plan_keeps_no_tool_text_in_evidence_error_or_result(env, monkeypatch):
    monkeypatch.setattr(logs, "privacy", lambda: True)
    p = env.store.propose("p", steps(("fake_read", {"text": "secret-a"})))
    env.store.approve(p["id"])
    await env.runner.tick()
    d = env.store.get(p["id"])
    assert d["state"] == "done" and d["steps"][0]["evidence"] == "" and "secret-a" not in d["result"]
    q = env.store.propose("q", steps(("fake_bad", {})))
    env.store.approve(q["id"])
    await env.runner.tick()
    s = env.store.get(q["id"])["steps"][0]
    assert s["status"] == "failed" and "X_BROKE" in s["error"] and "it broke" not in s["error"] + s["evidence"]


# ---------------------------------------------------------------- H2: a phone edit needs a fresh confirm
def test_h2_reproposing_edit_cannot_leave_a_confirm_that_raced_it(home, monkeypatch):
    p = Preferences(open_db(home))
    pid = p.suggest("Play jazz while cooking", "I love jazz when I cook", "music")["item"]["id"]
    real_get = p.get

    def stale_get(i):                       # the item is confirmed between edit()'s read and its write
        row = real_get(i)
        monkeypatch.setattr(p, "get", real_get)
        p.confirm(i, by="test")
        return row
    monkeypatch.setattr(p, "get", stale_get)
    p.edit(pid, "Play loud jazz while cooking", by="phone", repropose=True)
    r = p.get(pid)
    assert r["text"] == "Play loud jazz while cooking" and r["state"] == "proposed"


# ---------------------------------------------------------------- H3 / H4: stated in the guides
def test_h3_mobile_guide_says_the_status_scope_shows_presence():
    g = guides.find("mobile_access")
    assert "status permission" in g.privacy_en and "microphone" in g.privacy_en and "home" in g.privacy_en
    assert "הרשאת המצב" in g.privacy_he and "מיקרופון" in g.privacy_he
    assert "status permission" in guides.help_text(g)


def test_h4_project_guide_says_the_check_is_not_sandboxed():
    g = guides.find("project_manager")
    assert "sandbox" in g.privacy_en.lower() and "ארגז חול" in g.privacy_he
    assert "sandbox" in guides.help_text(g).lower()
