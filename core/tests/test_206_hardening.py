"""Issue #206 (V5 security P3 list): F13 resend ledger at rest in privacy mode, H2 phone edit re-proposes,
H3/H4 guide notes. Hermetic: tmp homes, no network, no clock."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarves import guides, logs, mobile, mobile_prefs
from jarves.continuity import Continuity
from jarves.preferences import PrefChanged, Preferences
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
    long = "old secret " * 2000                                 # spills onto overflow pages that the scrub frees
    Continuity(tmp_path).store("ph", "m1", "chat", 200, {"reply": long, "ok": True})
    logs.set_privacy(True)
    c = Continuity(tmp_path)                                    # a Core start with privacy on cleans the ledger
    assert not any("secret" in b for b in _bodies(c))
    assert c.seen("ph", "m1") == {"not_kept": True}
    assert c._cx().execute("PRAGMA secure_delete").fetchone()[0] == 1
    assert c._cx().execute("PRAGMA journal_mode").fetchone()[0] == "delete"   # no -wal file can keep old pages
    assert not list(tmp_path.glob("continuity.db-*"))                          # no journal left behind
    assert b"old secret" not in c.path.read_bytes()                           # the raw file holds no old words


def test_f13_the_phone_ledger_is_opened_lazily_so_the_privacy_setting_is_loaded_first(tmp_path, privacy_on):
    logs.set_privacy(False)
    Continuity(tmp_path).store("ph", "m1", "chat", 200, {"reply": "old secret", "ok": True})
    logs.set_privacy(True)                       # the Core's logs.setup(privacy=...) runs before any ledger use
    mob = mobile.MobileAccess(tmp_path)
    assert mob._cont is None                     # building MobileAccess does not open the ledger
    assert mob.cont.seen("ph", "m1") == {"not_kept": True}


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


def test_f13_a_privacy_mode_resend_keeps_the_code_it_kept(tmp_path, privacy_on):
    out = mobile._again(429, {"not_kept": True, "code": "CHAT_RATE"})
    assert out["code"] == "CHAT_RATE" and out["error_he"] == mobile._MSG["CHAT_RATE"][1]
    out = mobile._again(409, {"not_kept": True, "code": "NEEDS_PC"})           # a code without a phone message
    assert out["code"] == "NEEDS_PC" and out["error"] == mobile._MSG["NOT_KEPT"][0]
    mob = mobile.MobileAccess(tmp_path)
    mob.cont.store("ph", "m1", "chat", 200, {"reply": "words", "code": "CHAT_RATE"})
    got = mob.replay("ph", "m1")
    assert got["code"] == "CHAT_RATE" and got["reply"] == mobile._MSG["CHAT_RATE"][1]


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


def _flipping(home, pid):
    """A clock hook: before every write edit() makes, another connection flips the row confirmed <-> proposed."""
    other = open_db(home)

    def clock():
        st = other.row("SELECT state FROM learned_prefs WHERE id=?", (pid,))["state"]
        other.x("UPDATE learned_prefs SET state=? WHERE id=?", ("proposed" if st == "confirmed" else "confirmed", pid))
        return 1000.0
    return clock


def test_h2_an_edit_whose_row_keeps_flipping_gives_up_with_a_try_again_error(home):
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers", "communication")["item"]["id"]
    racing = Preferences(open_db(home), clock=_flipping(home, pid))
    with pytest.raises(PrefChanged, match="try again"):
        racing.edit(pid, "I like long answers", repropose=True)
    assert p.get(pid)["text"] == "I like short answers"


def test_h2_the_phone_gets_a_defined_409_when_the_edit_loses_the_race(home):
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers", "communication")["item"]["id"]
    mob = SimpleNamespace(bridge=None, _prefs=Preferences(open_db(home), clock=_flipping(home, pid)), home=home)
    st, out = mobile_prefs.action(mob, {"op": "edit", "pref": pid, "text": "I like long answers"}, "ph")
    assert st == 409 and out["code"] == "PREF_CHANGED" and "try again" in out["error"]


def test_h2_an_edit_of_a_row_deleted_meanwhile_is_not_found(home):
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers", "communication")["item"]["id"]
    other = open_db(home)
    racing = Preferences(open_db(home), clock=lambda: other.x("DELETE FROM learned_prefs WHERE id=?", (pid,)) and 1.0)
    with pytest.raises(KeyError):
        racing.edit(pid, "I like long answers")


def test_f13_phone_pages_read_code_only_from_failed_answers():
    """A 2xx resend carries code=NOT_KEPT: the shared api() reads d.code only when !r.ok, prefs.js ignores the body."""
    base = Path(mobile.__file__).parent / "dashboard_static" / "mobile"
    reads = [(f.name, ln) for f in base.glob("*.js") for ln in f.read_text(encoding="utf-8").splitlines()
             if re.search(r"\bd\.code\b", ln)]
    assert reads == [("app.js", next(ln for ln in (base / "app.js").read_text(encoding="utf-8").splitlines()
                                     if "e.code = d.code" in ln))]
    assert reads[0][1].lstrip().startswith("if (!r.ok)")
