"""#345: the bug-report counter is honest: red only for NEW P1/P2, mark-all-seen, auto-expiry, voice limits and
Pear-not-running are information, conversation issues collapse per cause and expire. Fake data only."""
from __future__ import annotations

import time

from jarves import diagnose, issue_registry as ir, issue_text as it
from jarves.store import Store
from test_issue_registry import Clock, reg  # noqa: F401


def test_urgent_is_new_p1_p2_only_and_seen_clears_it(reg):
    reg.record("tools", "TOOL_A_FAIL", "P2")
    reg.record("core", "ERR_B", "P3")
    assert reg.counts()["urgent"] == 1 and reg.counts()["open"] == 2
    assert reg.mark_all_seen() == 2
    c = reg.counts()
    assert c["urgent"] == 0 and c["new"] == 0 and c["open"] == 2      # still listed, no longer red


def test_open_issue_expires_and_comes_back_on_recurrence(reg):
    clock = reg.clock
    reg.record("tools", "TOOL_A_FAIL", "P2")
    clock.t += (ir.EXPIRE_DAYS + 1) * 86400
    reg.tidy("")
    assert reg.counts()["open"] == 0
    r = reg.get(ir.issue_id("tools", "TOOL_A_FAIL"))
    assert r["status"] == "ignored" and r["closed_reason"] == "expired"
    reg.record("tools", "TOOL_A_FAIL", "P2")                         # happened again
    assert reg.counts()["open"] == 1


def test_voice_idle_disconnect_and_pear_are_information(reg):
    reg.record("health", "FINDING_log:WARNING:VOICE_IDLE_DISCONNECT", "P3",
               {"finding": "log:WARNING:VOICE_IDLE_DISCONNECT"})
    reg.record("health", "FINDING_avail:pear", "P3", {"finding": "avail:pear"})
    c = reg.counts()
    assert c["open"] == 0 and c["info"] == 2
    d = it.describe(reg.list(None)[0])
    assert d["kind"] == "info" and d["hint"] and d["hint_he"]
    assert it.classify({"code": "x", "evidence": {"finding": "Pear Desktop is not running (idle, not an error)"}}) == "info"


def _issue(st, fp, sev, ts):
    st._db.execute("INSERT INTO issues (fingerprint, kind, severity, title, first_seen, last_seen, count) VALUES (?,?,?,?,?,?,3)",
                   (fp, "k", sev, f"title {fp}", ts, ts))
    st._db.commit()


def test_conversation_issues_collapse_expire_and_mark_seen(tmp_path):
    st = Store(tmp_path / "s.db")
    now = time.time()
    _issue(st, "silent_wait", "high", now - 100)
    _issue(st, "dead_air", "medium", now - 100)                     # same cause as silent_wait
    _issue(st, "tool_fail:x", "low", now - 100)
    _issue(st, "tool_repeat:x", "low", now - 100)                   # same cause as tool_fail:x
    _issue(st, "old_one", "high", now - 30 * 86400)
    assert diagnose.expire(st) == 1
    assert len(diagnose.by_cause(st.issues("open"))["problems"]) == 2
    assert diagnose.mark_all_seen(st) == 4
    assert st.issues("open") == []
