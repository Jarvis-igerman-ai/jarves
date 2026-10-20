"""The dashboard privacy switch (WP-K): one click flips log.privacy for NEW log lines and history rows at once (no
restart, also in another process via logs/privacy.json), saves it in config.toml, and offers the two one-way actions
(mask existing history, delete all stored conversations). Typed chat text is kept only with privacy OFF."""
import json
import os
import time
import tomllib

import pytest

from jarves import dashboard, diagnose, logs, store
from jarves.config import Config

from test_dashboard import req

W = {"X-Jarves": "1"}


def send(d, method, path, body=None):
    r, data = req(d, method, path, headers=W, body=body if body is not None else {})
    return r.status, json.loads(data or b"{}")


@pytest.fixture
def env(home):
    (home / "config.toml").write_text("[log]\nprivacy = false\n", encoding="utf-8")
    cfg = Config()
    cfg.log.privacy = False
    logs.setup(home / "logs", "INFO", console=False, privacy=False)
    sink = store.attach(home, cfg)
    d = dashboard.Dashboard(home, cfg, config_path=home / "config.toml").start()
    yield d, sink
    d.stop()
    store.detach(sink)
    logs.setup(home / "logs", "INFO", console=False, privacy=True)


def typed(text, reply):
    logs.info("core", "REPLY", "reply via ai", tool=None, ok=True, result="ok", ms=50, text=reply, typed=True,
              utterance=text)


def lines(home):
    return (home / "logs" / "jarves.log").read_text(encoding="utf-8")


def flush(sink):
    for _ in range(1500):
        if sink._q.empty():
            break
        time.sleep(0.02)
    time.sleep(0.1)
    sink.store.commit()


def test_switch_takes_effect_now_and_is_saved(env, home):
    d, sink = env
    typed("מה השעה בטוקיו", "שתיים בלילה")
    st, p = send(d, "GET", "/api/privacy")
    assert st == 200 and p["privacy"] is False and "OFF" in p["label_en"] and p["label_he"]
    st, p = send(d, "POST", "/api/privacy", {"privacy": True})
    assert st == 200 and p["privacy"] is True and p["saved_in_config"] and p["applies"] == "now"
    assert tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))["log"]["privacy"] is True
    typed("מה מזג האוויר בחיפה", "חם")
    flush(sink)
    log = lines(home)
    assert "מה השעה בטוקיו" in log and "מה מזג האוויר בחיפה" not in log and "חם\"" not in log   # new lines masked
    turns = sink.store.rows("SELECT role, text, chars FROM turns ORDER BY id")
    assert turns[0]["text"] == "מה השעה בטוקיו" and turns[2]["text"] is None and turns[2]["chars"] == 19
    # and back off: full text again for development
    assert send(d, "POST", "/api/privacy", {"privacy": False})[1]["privacy"] is False
    typed("תזכיר לי לקנות חלב", "בסדר")
    flush(sink)
    assert "תזכיר לי לקנות חלב" in lines(home)
    assert send(d, "POST", "/api/privacy", {"privacy": "yes"})[0] == 400


def test_another_process_follows_the_flag_file(home):
    logs.setup(home / "logs", "INFO", console=False, privacy=False)
    assert logs.privacy() is False and logs.runtime_privacy() is None
    # the dashboard in ANOTHER process writes the flag (same file format as logs.write_privacy)
    time.sleep(0.02)
    (home / "logs" / logs.PRIVACY_FILE).write_text(json.dumps({"privacy": True, "at": time.time()}), encoding="utf-8")
    logs._poll_privacy(force=True)
    assert logs.privacy() is True and logs.runtime_privacy() is True
    # a flag older than this process's start is ignored: config.toml is the truth at start
    logs.setup(home / "logs", "INFO", console=False, privacy=False)
    logs._poll_privacy(force=True)
    assert logs.privacy() is False and logs.runtime_privacy() is None
    logs.setup(home / "logs", "INFO", console=False, privacy=True)


def test_ai_review_is_skipped_once_privacy_is_switched_on(env, home):
    d, sink = env
    cfg = Config()
    cfg.log.privacy = False
    send(d, "POST", "/api/privacy", {"privacy": True})
    st = store.Store(":memory:")
    st.x("INSERT INTO sessions (id, kind, started) VALUES ('s1', 'live', ?)", (time.time(),))
    assert "privacy" in (diagnose.review_skip_reason(st, cfg, "s1") or "")


def test_one_way_actions_need_confirmation_mask_and_delete(env, home):
    d, sink = env
    typed("הסוד שלי הוא 1234", "שמרתי")
    logs.info("live", "LIVE_STARTED", "live started", model="gpt-live-1")
    logs.info("live", "LIVE_USER_SAID", "user said", text="קוראים לי דנה")
    logs.info("live", "VOICE_ENDED", "ended", reason="idle", voice_seconds=30)
    flush(sink)
    (home / "tasks.jsonl").write_text(json.dumps({"id": "t1", "description": "חיפוש מתנה לדנה", "status": "done",
                                                  "created": time.time()}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert send(d, "POST", "/api/privacy/mask-history", {})[0] == 400                    # needs {"confirm": true}
    st, r = send(d, "POST", "/api/privacy/mask-history", {"confirm": True})
    assert st == 200 and r["turns"] >= 3 and r["log_lines"] >= 2
    texts = [t["text"] for t in sink.store.rows("SELECT text FROM turns")]
    assert texts and all(t is None for t in texts)
    log = lines(home)
    assert "הסוד שלי" not in log and "קוראים לי דנה" not in log and "LIVE_USER_SAID" in log    # words gone, events stay
    assert "חיפוש מתנה" not in (home / "tasks.jsonl").read_text(encoding="utf-8")
    sessions = sink.store.rows("SELECT COUNT(*) n FROM sessions")[0]["n"]
    sink.store.x("INSERT INTO delegations (session_id, ts, kind, model, input_tokens, output_tokens) "
                 "SELECT id, started, 'voice', 'gpt-6-luna', 1000, 100 FROM sessions LIMIT 1")
    sink.store.commit()
    assert send(d, "POST", "/api/privacy/delete-conversations", {"confirm": False})[0] == 400
    st, r = send(d, "POST", "/api/privacy/delete-conversations", {"confirm": True})
    assert st == 200 and r["deleted"] == sessions >= 2
    assert sink.store.rows("SELECT COUNT(*) n FROM turns")[0]["n"] == 0
    left = sink.store.rows("SELECT session_id FROM delegations")
    assert left and all(x["session_id"] is None for x in left)                            # costs stay
    st, h = send(d, "GET", "/api/home")
    assert h["last_conversation"] is None and h["today"]["cost"] > 0


def test_privacy_purges_the_undo_journal_and_offers_backup_pruning(env, home):
    """Reviewer item 10: deleted / masked text must not survive in the store.db `memory_undo` journal, and the
    user is told that older backups still hold it and can prune them (confirmed)."""
    d, sink = env
    typed("הסוד שלי הוא 1234", "שמרתי")
    flush(sink)
    # the memory edit/forget undo journal holds removed fact text verbatim
    with sink.store._lock:
        sink.store._set_meta("memory_undo", json.dumps([{"op": "delete", "rows": [{"id": 1, "text": "הסוד שלי"}]}]))
        sink.store.commit()
    assert sink.store.meta("memory_undo") is not None
    # a backup taken before the purge still contains the data
    (home / "backups").mkdir(exist_ok=True)
    (home / "backups" / "jarves-backup-20260101-000000-manual.zip").write_bytes(b"PK\x03\x04 old data")
    st, r = send(d, "POST", "/api/privacy/mask-history", {"confirm": True})
    assert st == 200 and r["backups_with_old_data"] == 1 and r["backups_removed"] == 0 and r["backups_hint"]
    assert sink.store.meta("memory_undo") is None                      # the journal is gone
    # ask again, this time pruning the backups
    st, r = send(d, "POST", "/api/privacy/delete-conversations", {"confirm": True, "prune_backups": True})
    assert st == 200 and r["backups_removed"] == 1
    assert not list((home / "backups").glob("*.zip"))


def test_backup_pruning_is_asked_every_time_never_remembered(env, home):
    """Owner decision (C): pruning backups that hold the data needs an explicit prune_backups in EACH request; an
    earlier yes is not remembered, and a plain purge never touches a backup."""
    d, sink = env
    (home / "backups").mkdir(exist_ok=True)
    zips = [home / "backups" / f"jarves-backup-2026010{i}-000000-manual.zip" for i in (1, 2, 3)]
    for z in zips:
        z.write_bytes(b"PK\x03\x04 old data")
    st, r = send(d, "POST", "/api/privacy/delete-conversations", {"confirm": True})
    assert st == 200 and r["backups_removed"] == 0 and r["backups_hint"] and all(z.exists() for z in zips)
    st, r = send(d, "POST", "/api/privacy/mask-history", {"confirm": True, "prune_backups": False})
    assert r["backups_removed"] == 0 and all(z.exists() for z in zips)
    st, r = send(d, "POST", "/api/privacy/mask-history", {"confirm": True, "prune_backups": True})
    assert r["backups_removed"] == 3 and not any(z.exists() for z in zips)
    zips[0].write_bytes(b"PK\x03\x04 newer backup")                     # a later backup is not pruned by the old yes
    st, r = send(d, "POST", "/api/privacy/delete-conversations", {"confirm": True})
    assert r["backups_removed"] == 0 and zips[0].exists()
    assert send(d, "POST", "/api/privacy/delete-conversations", {"prune_backups": True})[0] == 400   # no confirm: no-op


def test_masking_rewrites_a_log_file_the_core_holds_open(home):
    (home / "logs").mkdir()
    p = home / "logs" / "jarves.log"
    p.write_text(json.dumps({"code": "REPLY", "utterance": "שלום", "text": "היי"}, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    with open(p, "a", encoding="utf-8") as held:                  # like the Core's RotatingFileHandler on Windows
        from jarves import privacy
        assert privacy.mask_logs(home) == 1
        held.write(json.dumps({"code": "X"}) + "\n")
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["utterance"] == "<4 chars>" and rows[-1]["code"] == "X"
    assert os.path.getsize(p) > 0


def test_topbar_and_home_show_the_switch(env):
    d, _ = env
    r, page = req(d, "GET", "/")
    assert b'id="topbar"' in page and b"home.js" in page
    r, js = req(d, "GET", "/static/home.js")
    assert b"/api/privacy/mask-history" in js and b"/api/privacy/delete-conversations" in js and b"confirm(" in js
