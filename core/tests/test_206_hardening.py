"""#206 V5 security P3 list: the items still open on main (F13 text at rest in privacy mode, H2 phone edit keeps a
confirmed preference, H3/H4 guide notes). Each test fails on the code before the fix."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from jarves import guides, logs, mobile
from jarves.preferences import PrefConflict, Preferences, make_skill
from jarves.userdata import open_db


# ---------------------------------------------------------------- F13: no text at rest in privacy mode
def test_f13_privacy_mode_chat_ledger_keeps_no_reply_text(home, monkeypatch):
    m = mobile.MobileAccess(home)
    monkeypatch.setattr(logs, "privacy", lambda: True)
    m.remember("dev1", "mid-1", {"reply": "your bank PIN is 4321", "ok": True})
    rows = m.cont._rows("SELECT body FROM ledger")
    assert rows and not any("4321" in r["body"] for r in rows)
    again = mobile.MobileAccess(home).replay("dev1", "mid-1")     # a PC restart: only the ledger copy is left
    assert again["ok"] is True and again["not_kept"] is True and "4321" not in again["reply"] and again["reply"]
    assert again["code"] == "NOT_KEPT"            # review R2: a code a client can translate; the text is only a fallback
    assert (again["reply"], again["reply_he"]) == mobile._MSG["NOT_KEPT"]   # review R3: one text per language, not mixed


def test_f13_privacy_off_chat_ledger_still_replays_the_answer(home, monkeypatch):
    monkeypatch.setattr(logs, "privacy", lambda: False)
    mobile.MobileAccess(home).remember("dev1", "mid-2", {"reply": "hello there", "ok": True})
    assert mobile.MobileAccess(home).replay("dev1", "mid-2") == {"reply": "hello there", "ok": True}


# ---------------------------------------------------------------- H2: a phone edit needs a fresh confirm
def test_h2_reproposing_edit_of_a_removed_pref_writes_nothing_and_raises_keyerror(home, monkeypatch):
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers in the morning", "communication")["item"]["id"]
    real_get = p.get

    def gone_get(i):                        # removed between edit()'s read and its write
        row = real_get(i)
        monkeypatch.setattr(p, "get", real_get)
        p.remove(i, by="test")
        return row
    monkeypatch.setattr(p, "get", gone_get)
    with pytest.raises(KeyError):
        p.edit(pid, "I like long answers", by="phone", repropose=True)
    assert p.get(pid) is None


def test_h2_pref_removed_right_after_the_guarded_write_is_keyerror_not_a_crash(home, monkeypatch):
    """Review R1: edit() re-reads the row it just wrote; a remove landing between the write and that read is KeyError."""
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers in the morning", "communication")["item"]["id"]
    real_get, calls = p.get, []

    def get(i):                                # 1st: edit()'s read; 2nd: the re-read after the write (removed meanwhile)
        calls.append(i)
        if len(calls) == 2:
            p.remove(i, by="test")
        return real_get(i)
    monkeypatch.setattr(p, "get", get)
    with pytest.raises(KeyError):
        p.edit(pid, "I like long answers", by="phone", repropose=True)


@pytest.mark.skipif(not shutil.which("node") and not os.environ.get("CI"), reason="node is not installed (CI must have it)")
def test_h2_phone_note_asks_for_a_confirm_whenever_the_server_says_proposed():
    """Review R2: a listed-as-proposed item that a racing confirm made confirmed, then this edit re-proposed, still asks."""
    js = Path(__file__).resolve().parents[1] / "src/jarves/dashboard_static/mobile/prefs.js"
    probe = ("global.window = {}; require(process.argv[1]); const f = window.JarvesPrefs.editNote;"
             "console.log(JSON.stringify([f({state: 'proposed'}), f({state: 'declined'}), f({state: 'confirmed'}), f({}), f(null)]))")
    out = subprocess.run(["node", "-e", probe, str(js)], capture_output=True, text=True, timeout=30, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["recheck", "saved", "saved", "saved", "saved"]


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


def test_h2_confirm_for_old_words_landing_after_the_edit_leaves_the_new_words_proposed(home, monkeypatch):
    """Review blocker (#1558): the confirm for the old text is read before the edit but writes after it."""
    p = Preferences(open_db(home))
    old = "Play jazz while cooking"
    pid = p.suggest(old, "I love jazz when I cook", "music")["item"]["id"]
    real_get = p.get

    def edit_lands_first(i):                # confirm() has read the row; the phone edit writes before confirm's UPDATE
        row = real_get(i)
        monkeypatch.setattr(p, "get", real_get)
        p.edit(i, "Play loud jazz while cooking", by="phone", repropose=True)
        return row
    monkeypatch.setattr(p, "get", edit_lands_first)
    with pytest.raises(PrefConflict):
        p.confirm(pid, by="phone", expect=old)
    r = p.get(pid)
    assert r["text"] == "Play loud jazz while cooking" and r["state"] == "proposed" and not r["confirmed_at"]


def test_h2_confirm_with_the_current_words_still_confirms(home):
    p = Preferences(open_db(home))
    pid = p.suggest("Play jazz while cooking", "I love jazz when I cook", "music")["item"]["id"]
    assert p.confirm(pid, by="phone", expect="  play JAZZ while cooking ")["item"]["state"] == "confirmed"


def test_h2_confirm_of_a_pref_removed_meanwhile_is_keyerror(home, monkeypatch):
    p = Preferences(open_db(home))
    pid = p.suggest("Play jazz while cooking", "I love jazz when I cook", "music")["item"]["id"]
    real_get = p.get

    def gone(i):
        row = real_get(i)
        monkeypatch.setattr(p, "get", real_get)
        p.remove(i, by="test")
        return row
    monkeypatch.setattr(p, "get", gone)
    with pytest.raises(KeyError):
        p.confirm(pid, by="phone", expect="Play jazz while cooking")


def test_h2_phone_confirm_needs_the_shown_words_and_refuses_stale_ones(home):
    from jarves import mobile_prefs
    m = mobile.MobileAccess(home)
    p = mobile_prefs.store(m)
    pid = p.suggest("Play jazz while cooking", "I love jazz when I cook", "music")["item"]["id"]
    st, out = mobile_prefs.action(m, {"op": "confirm", "pref": pid}, "dev1")
    assert st == 400 and out["code"] == "PREF_TEXT_REQUIRED" and p.get(pid)["state"] == "proposed"
    p.edit(pid, "Play loud jazz while cooking", by="pc", repropose=True)          # another screen changed the words
    st, out = mobile_prefs.action(m, {"op": "confirm", "pref": pid, "text": "Play jazz while cooking"}, "dev1")
    assert st == 409 and out["code"] == "PREF_CONFLICT" and p.get(pid)["state"] == "proposed"
    st, out = mobile_prefs.action(m, {"op": "confirm", "pref": pid, "text": "Play loud jazz while cooking"}, "dev1")
    assert st == 200 and out["state"] == "confirmed"


class _LosesEveryRace:
    """Wraps the db so every guarded UPDATE of learned_prefs finds the state changed (a confirm/decline each time)."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, k):
        return getattr(self._db, k)

    def x(self, sql, args=()):
        if sql.startswith("UPDATE learned_prefs SET text="):
            return type("R", (), {"rowcount": 0})()
        return self._db.x(sql, args)


async def test_h2_edit_that_keeps_losing_the_race_is_a_conflict_not_not_found(home):
    p = Preferences(open_db(home))
    pid = p.add_confirmed("I like short answers in the morning", "communication")["item"]["id"]
    p.db = _LosesEveryRace(p.db)
    with pytest.raises(PrefConflict):
        p.edit(pid, "I like long answers", by="phone", repropose=True)
    assert p.get(pid)["text"] == "I like short answers in the morning"          # nothing written
    tool = next(t for t in make_skill(p).tools() if t.name == "pref_edit")
    res = await tool.handler(tool.args(id=pid, text="I like long answers"))
    assert res.ok is False and res.code == "PREF_CONFLICT"


def test_f13_goal_ledger_copy_is_an_allow_list_not_the_whole_answer():
    """Review R3: only the reduced goal and a fixed error note are stored; any other key or error text stays off disk."""
    body = {"goal": {"id": 1, "title": "Run 5k", "status": "paused", "percent": 0,
                     "steps": [{"id": 3, "text": "week 1", "done": False}, "junk"]}, "note": "secret note"}
    assert mobile._goal_at_rest(body) == {"goal": {"id": 1, "status": "paused", "percent": 0, "steps": [{"id": 3, "done": False}]}}
    stored = mobile._goal_at_rest({"error": "bad status 'Run 5k'", "note": "secret note", "ok": False, "code": "BAD_STATUS"})
    assert stored == {"error": mobile.GOAL_ERROR_NOT_KEPT, "ok": False, "code": "BAD_STATUS"}    # review R1: non-text keys stay
    assert "code" not in mobile._goal_at_rest({"error": "x", "code": "Run 5k please"})            # free text in `code` does not


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


def test_h4_guide_claim_check_runs_with_an_allow_listed_env_and_a_time_limit(tmp_path, monkeypatch):
    """Review R3: the H4 guide sentence says 'only allow-listed environment variables and a time limit'; pin both."""
    from jarves.projectmgr import gitops
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    (tmp_path / ".git").mkdir()                       # #1592: a check runs only where the shared config can be guarded
    (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
    monkeypatch.setattr(gitops.subprocess, "run", fake_run)
    assert gitops.run_check(tmp_path, ["check"], timeout=7) == (False, "check timed out")
    assert seen["timeout"] == 7 and "OPENAI_API_KEY" not in seen["env"] and seen.get("shell") is not True
    assert seen["env"] and all(k.upper() in gitops.ENV_KEEP for k in seen["env"])     # only allow-listed names

