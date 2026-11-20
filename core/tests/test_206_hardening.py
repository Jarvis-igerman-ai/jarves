"""Issue #206 (V5 security P3 list): F13 resend ledger at rest in privacy mode, H2 phone edit re-proposes,
H3/H4 guide notes. Hermetic: tmp homes, no network, no clock."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from jarves import guides, logs, mobile, mobile_prefs
from jarves.continuity import Continuity
from jarves.preferences import Preferences
from jarves.userdata import open_db


@pytest.fixture
def privacy_on():
    before = logs.privacy()
    logs.set_privacy(True)
    yield
    logs.set_privacy(before)


def _bodies(c: Continuity) -> list[str]:
    with sqlite3.connect(c.path) as cx:
        return [r[0] for r in cx.execute("SELECT body FROM ledger")]


# ---------------------------------------------------------------- F13
def test_f13_privacy_on_ledger_keeps_no_words_but_still_dedupes(tmp_path, privacy_on):
    c = Continuity(tmp_path)
    c.store("ph", "m1", "chat", 200, {"reply": "your PIN is 4321", "ok": True})
    runs = []
    st, body, again = c.execute("ph", "c1", "plan", lambda: (runs.append(1) or 200, {"ok": True, "state": "done",
                                                                                     "note": "secret words", "n": 2}))
    assert body["note"] == "secret words"                       # the live answer is unchanged
    st, body, again = c.execute("ph", "c1", "plan", lambda: (runs.append(1) or 200, {}))
    assert again and runs == [1] and body == {"not_kept": True, "ok": True, "state": "done", "n": 2}
    assert not any("4321" in b or "secret" in b for b in _bodies(c))


def test_f13_rows_from_before_privacy_lose_their_words_and_freed_bytes_are_overwritten(tmp_path, privacy_on):
    logs.set_privacy(False)
    Continuity(tmp_path).store("ph", "m1", "chat", 200, {"reply": "old secret", "ok": True})
    logs.set_privacy(True)
    c = Continuity(tmp_path)                                    # a Core start with privacy on cleans the ledger
    assert not any("secret" in b for b in _bodies(c))
    assert c.seen("ph", "m1") == {"not_kept": True}
    assert c._cx().execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_f13_privacy_on_chat_resend_gets_a_defined_message(tmp_path, privacy_on):
    mob = mobile.MobileAccess(tmp_path)
    mob.cont.store("ph", "m1", "chat", 200, {"reply": "words", "ok": True})
    got = mob.replay("ph", "m1")
    assert got["code"] == "NOT_KEPT" and got["reply"] == mobile._MSG["NOT_KEPT"][1]
    assert got["reply_en"] == mobile._MSG["NOT_KEPT"][0] and "words" not in json.dumps(got)


def test_f13_privacy_on_command_resend_of_an_error_shows_a_message(tmp_path, privacy_on):
    c = Continuity(tmp_path)
    c.execute("ph", "c1", "pref", lambda: (400, {"error": "bad hunter2"}))
    st, obj, again = c.execute("ph", "c1", "pref", lambda: (200, {}))
    out = mobile._again(st, obj)
    assert again and st == 400 and out["replay"] is True and out["code"] == "NOT_KEPT"
    assert out["error_he"] == mobile._MSG["NOT_KEPT"][1] and "hunter2" not in json.dumps(out)
    assert mobile._again(200, {"ok": True}) == {"ok": True, "replay": True}     # privacy off: unchanged


# ---------------------------------------------------------------- H2
def test_h2_a_phone_edit_of_a_confirmed_item_needs_a_new_confirm(home):
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers in the morning", "communication")["item"]["id"]
    mob = SimpleNamespace(bridge=None, _prefs=p, home=home)
    st, out = mobile_prefs.action(mob, {"op": "edit", "pref": pid, "text": "I like long answers"}, "ph")
    assert st == 200 and p.get(pid)["state"] == "proposed" and p.get(pid)["text"] == "I like long answers"


def test_h2_a_confirm_racing_the_edit_cannot_make_the_new_wording_active(home):
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers", "communication")["item"]["id"]
    p.db.x("UPDATE learned_prefs SET state='proposed' WHERE id=?", (pid,))
    real_get = p.get

    def stale_get(i):                       # another process confirms between edit()'s read and its write
        row = real_get(i)
        p.db.x("UPDATE learned_prefs SET state='confirmed' WHERE id=?", (pid,))
        p.get = real_get
        return row
    p.get = stale_get
    p.edit(pid, "I like long answers", repropose=True)
    assert p.get(pid)["state"] == "proposed"


# ---------------------------------------------------------------- H3 / H4
def test_h3_mobile_guide_says_the_status_permission_shows_the_pc_mic_state():
    g = guides.find("mobile_access")
    assert "status" in g.privacy_en and "microphone" in g.privacy_en and "status" in g.privacy_he
    assert g.privacy_en in guides.help_text(g)


def test_h4_project_guide_says_the_check_command_runs_without_a_sandbox():
    g = guides.find("project_manager")
    assert "check command" in g.privacy_en and "sandbox" in g.privacy_en and "ארגז חול" in g.privacy_he
    assert g.privacy_en in guides.help_text(g)
