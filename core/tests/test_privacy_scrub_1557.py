"""#1557: switching privacy mode on scrubs text stored BEFORE it was on (continuity ledger answers, goal plan step
evidence and error text), not only new writes. Status, codes and the resend ledger rows themselves stay."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarves import logs, mobile, privacy
from jarves.app import Core
from jarves.config import Config
from jarves.continuity import Continuity
from jarves.skills.music import FakePlayer
from jarves.userdata import UserDB
from jarves.voice.audio import SimAudio

from conftest import FakeSTT, FakeTTS

SECRET = "the answer mentions my sister Dana"


@pytest.fixture
def ctx(home):
    (home / "config.toml").write_text("[log]\nprivacy = false\n", encoding="utf-8")
    cfg = Config()
    cfg.log.privacy = False
    logs.setup(home / "logs", "INFO", console=False, privacy=False)
    yield SimpleNamespace(home=home, cfg=cfg, config_path=home / "config.toml")
    logs.setup(home / "logs", "INFO", console=False, privacy=True)


def new_core(cfg, home):
    """A Core with virtual audio and fake speech (R5: no import from another test module)."""
    return Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
                use_real_audio=False)


def _seed(home):
    Continuity(home).store("phone1", "c1", "chat", 200, {"reply": SECRET, "ok": True})
    db = UserDB(home / "memory.db")
    db.x("INSERT INTO goal_plans (title, state, created, updated) VALUES ('t', 'failed', 0, 0)")
    db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, evidence, error) VALUES "
         "(1, 0, 'web_search', 'failed', ?, ?)", (SECRET, "TOOL_TIMEOUT: " + SECRET))
    for err in ("Dana's phone: unreachable", "no colon, Dana did not answer", "DANA: unreachable",
                "CALL_DANA: no answer"):                      # not known codes (R5: CALL_DANA is code-shaped)
        db.x("INSERT INTO goal_plan_steps (plan_id, idx, tool, status, error) VALUES (1, 1, 'call', 'failed', ?)",
             (err,))
    return db


def _dump(home, ledger=True) -> str:
    out = []
    for name, sql in ((("continuity.db", "SELECT status, body FROM ledger"),) if ledger else ()) + (
                      ("memory.db", "SELECT status, evidence, error FROM goal_plan_steps"),):
        cx = sqlite3.connect(home / name)
        try:
            out += [json.dumps(list(r)) for r in cx.execute(sql)]
        finally:
            cx.close()
    return "\n".join(out)


def _wal_clean(home) -> bool:
    """No old page copy of the text in either database's -wal file (a file a checkpoint removed is clean too)."""
    for name in ("continuity.db-wal", "memory.db-wal"):
        p = home / name
        if p.exists() and "Dana" in p.read_bytes().decode("utf-8", "replace"):
            return False
    return True


def test_switching_privacy_on_scrubs_older_text(ctx, home):
    db = _seed(home)                                          # kept open, like the running Core: the -wal file stays
    assert "Dana" in _dump(home)
    got = privacy.set_privacy(ctx, True)
    assert got["scrub"] == {"ledger": 1, "plan_steps": 5, "partial": []} and got["saved_in_config"]
    dump = _dump(home)
    assert "Dana" not in dump
    assert "TOOL_TIMEOUT" in dump and "failed" in dump        # the known code and status stay
    assert "DANA" not in dump                                 # an upper-case name is not kept as a code
    assert "Dana" not in (home / "memory.db-wal").read_bytes().decode("utf-8", "replace")   # no old page copy
    assert _wal_clean(home)
    assert db.row("SELECT error FROM goal_plan_steps WHERE tool='call' AND error != ''") is None
    seen = Continuity(home).seen("phone1", "c1")              # a resend is still answered, not run again
    assert seen == mobile.chat_at_rest({"ok": True})          # R4: the body #206 F13 stores in privacy mode
    assert seen["reply"] and seen["reply_he"]                 # a resend client still gets a reply text


def test_a_scrubbed_goal_row_has_the_privacy_mode_goal_shape(ctx, home):
    body = {"ok": True, "goal": {"id": 3, "title": SECRET, "status": "active", "percent": 50,
                                 "steps": [{"id": 1, "text": SECRET, "done": True}]}}
    Continuity(home).store("phone1", "g1", "goal", 200, body)
    privacy.set_privacy(ctx, True)
    seen = Continuity(home).seen("phone1", "g1")
    assert seen == mobile.goal_at_rest(body) and "Dana" not in json.dumps(seen)   # R4: same shape as #206 F13
    assert privacy.scrub_at_rest(home)["ledger"] == 0         # already at rest: not rewritten again


def test_ending_full_text_scrubs_too(ctx, home):
    db = _seed(home)
    got = privacy.set_full_text(ctx, False)
    assert got["scrub"] == {"ledger": 1, "plan_steps": 5, "partial": []}
    assert "Dana" not in _dump(home) and _wal_clean(home)
    db.close()


def test_switching_privacy_off_keeps_text(ctx, home):
    _seed(home)
    privacy.set_privacy(ctx, False)
    assert "Dana" in _dump(home)


def test_scrub_without_databases_is_harmless(home):
    assert privacy.scrub_at_rest(home) == {"ledger": 0, "plan_steps": 0, "partial": []}
    assert not (home / "continuity.db").exists() and not (home / "memory.db").exists()


def test_unreadable_database_is_logged_and_the_other_is_still_scrubbed(home, monkeypatch):
    _seed(home)
    (home / "continuity.db").write_bytes(b"not a database" * 100)
    seen = []
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append((a[1], k)))
    out = privacy.scrub_at_rest(home)
    assert out == {"ledger": 0, "plan_steps": 5, "partial": ["continuity.db"]}
    assert "Dana" not in _dump(home, ledger=False)
    assert seen == [("PRIVACY_SCRUB_PARTIAL", {"target": "continuity.db", "error_type": "DatabaseError"})]
    assert privacy.scrub_at_rest(home)["plan_steps"] == 0          # already scrubbed: nothing rewritten again


def test_malformed_ledger_rows_are_replaced_and_the_switch_still_saves(ctx, home):
    Continuity(home).store("phone1", "c1", "chat", 200, {"ok": True})
    cx = sqlite3.connect(home / "continuity.db")
    cx.execute("INSERT INTO ledger (device, cid, kind, status, body, ts) VALUES ('p', 'c2', 'chat', 'x', 'Dana{', 0)")
    cx.commit()
    cx.close()
    got = privacy.set_privacy(ctx, True)
    assert got["saved_in_config"] and got["scrub"]["partial"] == []
    cx = sqlite3.connect(home / "continuity.db")
    assert "Dana" not in json.dumps(cx.execute("SELECT body FROM ledger").fetchall())
    cx.close()
    assert Continuity(home).seen("p", "c2", max_age=1e12) == mobile.chat_at_rest({"ok": False})


def test_a_locked_database_is_reported_partial_to_the_owner(ctx, home, monkeypatch):
    _seed(home)
    monkeypatch.setattr(privacy, "SCRUB_TIMEOUT_S", 0.05)
    lock = sqlite3.connect(home / "continuity.db", isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        got = privacy.set_privacy(ctx, True)
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    assert got["scrub"]["partial"] == ["continuity.db"] and got["saved_in_config"]
    assert got["scrub_note_en"] == privacy.SCRUB_PARTIAL_NOTE and got["scrub_note_he"] == privacy.SCRUB_PARTIAL_NOTE_HE


def test_a_full_scrub_has_no_partial_note(ctx, home):
    _seed(home)
    got = privacy.set_privacy(ctx, True)
    assert got["scrub"]["partial"] == [] and "scrub_note_en" not in got


def test_the_dashboard_switch_scrubs_on_a_request_thread_not_the_caller(ctx, home, monkeypatch):
    """R2: the dashboard is a ThreadingHTTPServer, so the scrub runs on the request's own thread, never a Core loop."""
    import threading
    from jarves import dashboard
    from test_dashboard import req
    ran = []
    monkeypatch.setattr(privacy, "scrub_at_rest", lambda h: ran.append(threading.current_thread()) or
                        {"ledger": 0, "plan_steps": 0, "partial": []})
    d = dashboard.Dashboard(home, ctx.cfg, config_path=ctx.config_path).start()
    try:
        r, _ = req(d, "POST", "/api/privacy", headers={"X-Jarves": "1"}, body={"privacy": True})
    finally:
        d.stop()
    assert r.status == 200 and len(ran) == 1 and ran[0] is not threading.main_thread()


def test_an_os_error_in_the_scrub_is_partial_and_the_switch_still_saves(ctx, home, monkeypatch):
    _seed(home)
    real = privacy._scrub_db

    def boom(path, sql, fix):
        if path.name == "continuity.db":
            raise PermissionError("denied")
        return real(path, sql, fix)
    seen = []
    monkeypatch.setattr(privacy, "_scrub_db", boom)
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append((a[1], k)))
    got = privacy.set_privacy(ctx, True)                       # R1: no exception after write_privacy
    assert got["saved_in_config"] and got["scrub"]["partial"] == ["continuity.db"]
    assert "privacy = true" in (home / "config.toml").read_text(encoding="utf-8")
    assert seen == [("PRIVACY_SCRUB_PARTIAL", {"target": "continuity.db", "error_type": "PermissionError"})]


def test_join_background_waits_for_a_running_scrub_and_logs_one_still_running(home, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(privacy, "scrub_at_rest", lambda h: gate.wait(10))
    seen = []
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append(a[1]))
    t = privacy.scrub_in_background(home)
    assert privacy.join_background(0.05) is False and seen == ["PRIVACY_SCRUB_STILL_RUNNING"]
    gate.set()
    assert privacy.join_background(5) is True and not t.is_alive()


async def test_core_stop_joins_the_background_scrub(home, cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(privacy, "join_background", lambda: calls.append(1) or True)
    c = new_core(cfg, home)
    await c.start()
    await c.stop()
    assert calls == [1]                                       # R3: stop() waits for a running scrub


async def test_core_start_with_privacy_on_scrubs_older_text(home, cfg):
    _seed(home)
    cfg.log.privacy = True                                    # set in config.toml, no dashboard switch
    c = new_core(cfg, home)
    await c.start()
    try:
        assert "Dana" not in _dump(home)
    finally:
        await c.stop()


async def test_core_start_with_privacy_off_keeps_text(home, cfg):
    _seed(home)
    cfg.log.privacy = False
    c = new_core(cfg, home)
    await c.start()
    try:
        assert "Dana" in _dump(home)
    finally:
        await c.stop()


async def test_core_start_scrubs_when_only_the_flag_file_says_privacy_on(home, cfg):
    _seed(home)
    (home / "logs").mkdir(exist_ok=True)
    (home / "logs" / logs.PRIVACY_FILE).write_text(json.dumps({"privacy": True, "at": 0}), encoding="utf-8")
    cfg.log.privacy = False                                   # config.toml was not saved by the switch
    c = new_core(cfg, home)
    await c.start()
    try:
        assert "Dana" not in _dump(home)
    finally:
        await c.stop()


async def test_running_core_scrubs_when_another_process_turns_privacy_on(home, cfg, monkeypatch):
    import threading
    started, real = [], privacy.scrub_in_background
    monkeypatch.setattr(privacy, "scrub_in_background", lambda h: started.append(real(h)) or started[-1])
    cfg.log.privacy = False
    c = new_core(cfg, home)
    await c.start()
    try:
        _seed(home)                                           # written in full before the Core saw the switch
        assert "Dana" in _dump(home)
        (home / "logs" / logs.PRIVACY_FILE).write_text(json.dumps({"privacy": True, "at": 1}), encoding="utf-8")
        logs._poll_privacy(force=True)                        # the Core reads the dashboard's flag file
        assert len(started) == 1 and started[0] is not threading.current_thread()   # off the event loop (R2)
        started[0].join(10)
        assert logs.privacy() and "Dana" not in _dump(home)
    finally:
        await c.stop()
        assert c._privacy_hook not in logs._on_privacy_on     # a stopped Core's hook no longer fires (R6)
        logs.setup(home / "logs", "INFO", console=False, privacy=True)


def test_a_reader_blocking_the_checkpoint_is_reported_partial(home, monkeypatch):
    db = _seed(home)
    reader = sqlite3.connect(home / "memory.db", isolation_level=None)
    assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM goal_plan_steps").fetchall()   # holds a read snapshot: the checkpoint stops
    seen = []
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append((a[1], k)))
    try:
        got = privacy.scrub_at_rest(home)
    finally:
        reader.execute("ROLLBACK")
        reader.close()
        db.close()
    assert got["partial"] == ["memory.db"]
    assert ("PRIVACY_SCRUB_PARTIAL", {"target": "memory.db", "reason": "wal_pending"}) in seen
    assert privacy.scrub_at_rest(home)["partial"] == []       # reader gone: the next scrub completes the checkpoint
    assert "Dana" not in (home / "memory.db").read_bytes().decode("utf-8", "replace")


def test_a_failing_privacy_hook_is_logged_and_privacy_still_turns_on(home, monkeypatch):
    logs.setup(home / "logs", "INFO", console=False, privacy=False)
    seen = []
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append((a[1], k)))
    def broken():
        return 1 / 0
    logs.on_privacy_on(broken)
    try:
        logs.set_privacy(True)
        assert logs.privacy() and seen == [("PRIVACY_HOOK_FAILED", {"error_type": "ZeroDivisionError"})]
    finally:
        logs.off_privacy_on(broken)                          # never left in the process-wide list (#1625 R3)
        logs.setup(home / "logs", "INFO", console=False, privacy=True)


def test_an_expired_full_text_window_runs_the_privacy_hook(ctx, home, monkeypatch):
    """#1625 R2: the 24 h window ending by itself (not the owner) turns privacy on through set_privacy: hooks run."""
    t0 = logs._clock()
    logs.write_privacy(home / "logs", False, until=t0 + 100)
    ran = []

    def hook():
        ran.append(logs.privacy())
    logs.on_privacy_on(hook)
    try:
        monkeypatch.setattr(logs, "_clock", lambda: t0 + 200)  # the window is over
        logs._poll_privacy(force=True)
        assert ran == [True] and privacy.flag_on(home)
    finally:
        logs.off_privacy_on(hook)


def test_a_memory_db_without_the_plan_table_is_not_partial(home):
    """#1625 R5: a database made before goal_plan_steps existed has none of that text: 0 rows, no partial note."""
    cx = sqlite3.connect(home / "memory.db")
    cx.execute("CREATE TABLE other (x)")
    cx.commit()
    cx.close()
    assert privacy.scrub_at_rest(home) == {"ledger": 0, "plan_steps": 0, "partial": []}


def test_kept_codes_match_their_owners():
    """#1625 R4: _KEPT_CODES is every ToolResult code literal of the tool runner and goal plans (no silent drift)."""
    import ast
    import jarves.goal_plans
    import jarves.skills
    found = set()
    for mod in (jarves.skills, jarves.goal_plans):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "ToolResult":
                found |= {k.value.value for k in n.keywords
                          if k.arg == "code" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)}
    assert found == privacy._KEPT_CODES


async def test_a_privacy_on_start_backup_has_no_older_text(home, cfg):
    """#1625 R1: a start with privacy on scrubs BEFORE the version checkpoint, so the new backup holds no old text."""
    import zipfile
    from jarves import backup
    _seed(home)
    backup._save_state(home, {"version": "0.0.1"})           # a version change: the start writes a checkpoint
    cfg.log.privacy = True
    c = new_core(cfg, home)
    await c.start()
    await c.stop()
    zips = sorted(backup.backup_dir(home).glob("jarves-backup-*.zip"))
    assert zips, "no checkpoint was written"
    with zipfile.ZipFile(zips[-1]) as z:
        assert not [n for n in z.namelist() if b"Dana" in z.read(n)]


def test_a_logging_reconfigure_keeps_the_privacy_hook(home):
    """R3: a later logs.setup() in a running process must not drop the Core's scrub hook."""
    hook = lambda: None                                       # noqa: E731
    logs.on_privacy_on(hook)
    logs.on_privacy_on(hook)                                  # added once only
    try:
        logs.setup(home / "logs", "INFO", console=False, privacy=True)
        assert logs._on_privacy_on.count(hook) == 1
    finally:
        logs.off_privacy_on(hook)


def _boom(row):
    raise TypeError("a ledger body of an unexpected shape")


def test_a_row_fix_crash_is_partial_and_the_switch_still_saves(ctx, home, monkeypatch):
    _seed(home)
    monkeypatch.setattr(privacy, "_ledger_fix", _boom)        # #1625 R1: not only sqlite3/OS errors
    seen = []
    monkeypatch.setattr(logs, "warn", lambda *a, **k: seen.append((a[1], k)))
    got = privacy.set_privacy(ctx, True)
    assert got["saved_in_config"] and got["scrub"]["partial"] == ["continuity.db"] and got["scrub"]["plan_steps"]
    assert "privacy = true" in (home / "config.toml").read_text(encoding="utf-8")
    assert seen == [("PRIVACY_SCRUB_PARTIAL", {"target": "continuity.db", "error_type": "TypeError"})]


async def test_core_start_completes_when_a_row_fix_crashes(home, cfg, monkeypatch):
    _seed(home)
    monkeypatch.setattr(privacy, "_ledger_fix", _boom)
    cfg.log.privacy = True
    c = new_core(cfg, home)
    await c.start()                                           # #1625 R1: the start is not aborted
    try:
        assert "Dana" not in _dump(home, ledger=False)       # memory.db is still scrubbed
    finally:
        await c.stop()


def test_a_null_evidence_step_with_a_kept_code_is_not_rewritten_again():
    # #1625 R2: the schema says NOT NULL, but a NULL (hand-edited or older file) is no text: no rewrite at each start
    assert privacy._step_fix((1, None, "TOOL_TIMEOUT")) is None
    assert privacy._step_fix((1, None, "TOOL_TIMEOUT: Dana")) == (
        "UPDATE goal_plan_steps SET evidence = '', error = ? WHERE rowid = ?", ("TOOL_TIMEOUT", 1))


def test_join_background_waits_for_every_running_scrub(home, monkeypatch):
    import threading
    first, second = threading.Event(), threading.Event()
    monkeypatch.setattr(privacy, "scrub_at_rest", lambda h: first.wait(10))
    t1 = privacy.scrub_in_background(home)
    monkeypatch.setattr(privacy, "scrub_at_rest", lambda h: second.wait(10))
    t2 = privacy.scrub_in_background(home)
    monkeypatch.setattr(logs, "warn", lambda *a, **k: None)
    second.set()                                              # #1625 R5: the later one ends first
    t2.join(5)
    assert privacy.join_background(0.05) is False and t1.is_alive()
    first.set()
    assert privacy.join_background(5) is True and not t1.is_alive()
