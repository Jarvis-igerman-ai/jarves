"""Issue #206 (V5 security P3 list): F13 resend ledger at rest in privacy mode, H2 phone edit re-proposes,
H3/H4 guide notes. Hermetic: tmp homes, no network, no clock."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
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
    with closing(sqlite3.connect(c.path)) as cx:
        return [r[0] for r in cx.execute("SELECT body FROM ledger")]


# ---------------------------------------------------------------- F13
def test_f13_privacy_on_ledger_keeps_no_words_but_still_dedupes(tmp_path, privacy_on):
    c = Continuity(tmp_path)
    c.store("ph", "m1", "chat", 200, {"reply": "your PIN is 4321", "ok": True})
    runs = []
    st, body, again = c.execute("ph", "c1", "plan", lambda: (runs.append(1) or 200, {"ok": True, "state": "done",
                                                                                     "note": "secret words", "pref": 2}))
    assert body["note"] == "secret words"                       # the live answer is unchanged
    st, body, again = c.execute("ph", "c1", "plan", lambda: (runs.append(1) or 200, {}))
    assert again and runs == [1] and body == {"not_kept": True, "ok": True, "state": "done", "pref": 2}
    assert not any("4321" in b or "secret" in b for b in _bodies(c))


def test_f13_rows_from_before_privacy_lose_their_words_and_freed_bytes_are_overwritten(tmp_path, privacy_on):
    logs.set_privacy(False)
    long = "old secret " * 2000                                 # spills onto overflow pages that the scrub frees
    Continuity(tmp_path).store("ph", "m1", "chat", 200, {"reply": long, "ok": True})
    logs.set_privacy(True)
    c = Continuity(tmp_path)                                    # a Core start with privacy on cleans the ledger
    assert not any("secret" in b for b in _bodies(c))
    assert c.seen("ph", "m1") == {"not_kept": True}
    with closing(c._cx()) as cx:
        assert cx.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert cx.execute("PRAGMA journal_mode").fetchone()[0] == "delete"   # no -wal file can keep old pages
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
    assert got["reply"] == mobile._MSG["NOT_KEPT"][1] and "code" not in got
    assert got["reply_en"] == mobile._MSG["NOT_KEPT"][0] and "words" not in json.dumps(got)
    assert "not_kept" not in got                                                 # #1478 R3: internal marker


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
    assert got["code"] == "CHAT_RATE" and got["error_he"] == mobile._MSG["CHAT_RATE"][1]   # #1478 R2: an error,
    assert got["reply"] == mobile._MSG["NOT_KEPT"][1]                                      # not a JARVES reply


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


def test_f13_a_privacy_mode_2xx_resend_gets_no_error_code(tmp_path, privacy_on):
    """R1 (#1453): a success resend must not carry code=NOT_KEPT, which the phone could read as a failure."""
    c = Continuity(tmp_path)
    c.execute("ph", "c1", "plan", lambda: (200, {"ok": True, "note": "words"}))
    st, obj, again = c.execute("ph", "c1", "plan", lambda: (200, {}))
    assert again and st == 200 and mobile._again(st, obj) == {"ok": True, "replay": True}   # #1478 R3: no marker


def test_h2_the_409_has_hebrew_and_its_privacy_resend_keeps_that_message(home, privacy_on):
    """R2 (#1453): PREF_CHANGED has a Hebrew text and a resend of the kept 409 shows it, not the generic NOT_KEPT."""
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers", "communication")["item"]["id"]
    mob = SimpleNamespace(bridge=None, _prefs=Preferences(open_db(home), clock=_flipping(home, pid)), home=home)
    c = Continuity(home)
    d = {"op": "edit", "pref": pid, "text": "I like long answers"}
    st, out, _ = c.execute("ph", "c9", "pref", lambda: mobile_prefs.action(mob, d, "ph"))
    assert st == 409 and out["error_he"] == mobile._MSG["PREF_CHANGED"][1]
    st, obj, again = c.execute("ph", "c9", "pref", lambda: (200, {}))
    out = mobile._again(st, obj)
    assert again and out["code"] == "PREF_CHANGED" and out["error_he"] == mobile._MSG["PREF_CHANGED"][1]


def test_f13_the_scrub_runs_once_not_before_every_ledger_write(tmp_path, privacy_on, monkeypatch):
    """R3 (#1453): with privacy on, the full-table scrub runs at start, then only after a privacy-off write."""
    c = Continuity(tmp_path)
    sql = []
    real = c._x
    monkeypatch.setattr(c, "_x", lambda q, a=(): sql.append(q) or real(q, a))
    c.store("ph", "m1", "chat", 200, {"ok": True})
    c.execute("ph", "c1", "plan", lambda: (200, {"ok": True}))
    assert not [q for q in sql if q.startswith("UPDATE ledger")]
    logs.set_privacy(False)
    c.store("ph", "m2", "chat", 200, {"reply": "old secret"})
    logs.set_privacy(True)
    c.store("ph", "m3", "chat", 200, {"ok": True})
    assert len([q for q in sql if q.startswith("UPDATE ledger")]) == 1 and not any("secret" in b for b in _bodies(c))


def test_f13_one_write_reads_privacy_once_so_a_toggle_cannot_leave_words(tmp_path, privacy_on, monkeypatch):
    """#1478 R1: privacy flipping off and on during one write must not store words while the ledger counts as clean."""
    c = Continuity(tmp_path)
    reads = iter([True, False, True])
    monkeypatch.setattr(logs, "privacy", lambda: next(reads, True))
    for i in range(3):
        c.store("ph", f"m{i}", "chat", 200, {"reply": "secret words"})
    assert not any("secret" in b for b in _bodies(c))


def test_f13_a_privacy_mode_approval_resend_keeps_ok_and_the_refusal_code(tmp_path, privacy_on, monkeypatch):
    """#1478 R3: approval answers carry only ok, or error + code; a privacy-mode resend keeps ok and the code."""
    from jarves import approvals
    monkeypatch.setattr(approvals, "read_pending_file", lambda home, reveal=False: [])
    monkeypatch.setattr(approvals, "submit_answer", lambda home, aid, ok, channel=None: (True, ""))
    on = {"phone_approvals": True}
    mob = SimpleNamespace(home=tmp_path, settings=lambda: on, audit=lambda *a, **k: None)
    c = Continuity(tmp_path)
    c.execute("ph", "a1", "approval", lambda: mobile.answer_approval(mob, "x1", False, "ph"))
    st, obj, again = c.execute("ph", "a1", "approval", lambda: (500, {}))
    assert again and st == 200 and mobile._again(st, obj) == {"ok": True, "replay": True}
    on["phone_approvals"] = False
    c.execute("ph", "a2", "approval", lambda: mobile.answer_approval(mob, "x2", True, "ph"))
    st, obj, again = c.execute("ph", "a2", "approval", lambda: (200, {}))
    out = mobile._again(st, obj)
    assert again and st == 403 and out["code"] == "PHONE_APPROVALS_OFF" and out["error"] and out["error_he"]


def test_f13_privacy_mode_keeps_numbers_only_under_known_keys(tmp_path, privacy_on):
    """#1478 R2: a number under an unlisted key (a code, an amount) is not kept at rest; ids, counts, flags are."""
    c = Continuity(tmp_path)
    c.store("ph", "m1", "pref", 200, {"ok": True, "pref": 7, "conflicts": 1, "pin": 4321, "amount": 99.5, "x": None})
    assert c.seen("ph", "m1") == {"not_kept": True, "ok": True, "pref": 7, "conflicts": 1}
    assert not any("4321" in b or "99.5" in b for b in _bodies(c))


def test_f13_a_privacy_off_store_cannot_land_after_a_privacy_on_scrub(tmp_path, privacy_on, monkeypatch):
    """#1478 R1: store() holds the lock across scrub, flag and INSERT, so a privacy-on writer cannot scrub in between."""
    import threading
    c = Continuity(tmp_path)
    main = threading.current_thread()
    monkeypatch.setattr(logs, "privacy", lambda: threading.current_thread() is not main)  # main writes with it off
    real, done = c._x, threading.Event()
    other = threading.Thread(target=lambda: (c.store("ph", "m2", "chat", 200, {"ok": True}), done.set()))

    def slow_insert(q, a=()):
        if q.startswith("INSERT") and threading.current_thread() is main and not other.is_alive() and not done.is_set():
            other.start()                        # the privacy-on writer runs while this INSERT is pending
            done.wait(0.3)                       # with the lock it cannot finish yet; without it, it scrubs first
        return real(q, a)
    monkeypatch.setattr(c, "_x", slow_insert)
    c.store("ph", "m1", "chat", 200, {"reply": "secret words"})
    other.join(5)
    assert done.is_set() and not any("secret" in b for b in _bodies(c))
