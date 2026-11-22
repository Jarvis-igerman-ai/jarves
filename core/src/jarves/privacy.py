"""Privacy switch for the dashboard (SW-V2 WP-K, Issue #17): one click turns log.privacy on or off.

On  = what the user and JARVES said (and typed chat) is masked in the log files and the conversation history from
      the next line on: only lengths and timings are kept.
Off = full text, for development.
The switch takes effect at once in every process logging into <home>/logs (logs.write_privacy: the Core follows the
flag file within a second, no restart) and is saved in config.toml (config_edit, backed up) so the next start agrees.

Two one-way actions (each needs {"confirm": true}):
- mask existing history: removes the words from store.db (turns, tool arguments, event fields), from the log files
  (jarves.log and its rotations) and from tasks.jsonl; lengths, timings, costs and issues stay.
- delete all stored conversations: removes the conversations from store.db (cost rows stay, detached, so the cost
  totals remain true) and masks the words in the log files too (a re-import cannot bring them back).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config_edit, logs

_TEXT_KEYS = ("description", "result", "queries", "steps")       # tasks.jsonl content fields


def state(ctx) -> dict:
    if logs._flag == ctx.home / "logs" / logs.PRIVACY_FILE and logs.runtime_privacy() is not None:
        ctx.cfg.log.privacy = logs.runtime_privacy()  # switched meanwhile by another dashboard / process
    on = bool(ctx.cfg.log.privacy)
    until = _window_until(ctx.home) if not on else 0.0
    return {"privacy": on, "full_text_until": until or None, "full_text_active": bool(until),
            "full_text_note_en": (f"Full text on request: ends at {time.strftime('%H:%M on %d %b', time.localtime(until))}, "
                                  "then privacy turns on by itself" if until else ""),
            "full_text_note_he": (f"טקסט מלא לפי בקשה: מסתיים ב-{time.strftime('%H:%M %d/%m', time.localtime(until))}, "
                                  "ואז הפרטיות חוזרת לבד" if until else ""),
            "label_he": "פרטיות פועלת: מה שנאמר לא נשמר" if on else "פרטיות כבויה: הטקסט המלא נשמר (לפיתוח)",
            "label_en": "Privacy ON: what is said is not kept" if on else "Privacy OFF: full text is kept (development)",
            "explain_en": ("New log lines and conversation history keep only lengths and timings, never the words. "
                           "Older entries stay as they were until you mask them." if on else
                           "Everything you and JARVES say, and typed chat, is written in full to the logs and the "
                           "conversation history on this computer. Turn privacy on for daily use."),
            "explain_he": ("מעכשיו נשמרים רק אורך ותזמון, לא המילים. מה שנשמר קודם נשאר עד שמסתירים אותו."
                           if on else "כל מה שנאמר ונכתב נשמר במלואו ביומנים ובהיסטוריה במחשב הזה."),
            "applies": "now",
            # SW-V4 B: the plain state line shown by the dashboard banner and `jarvis status` (empty when on)
            "banner_en": "" if on else logs.PRIVACY_OFF_NOTE, "banner_he": "" if on else logs.PRIVACY_OFF_NOTE_HE}


def flag_on(home: Path) -> bool:
    """#1557: the runtime switch file says privacy is on (read directly; False when missing or unreadable)."""
    try:
        d = json.loads((Path(home) / "logs" / logs.PRIVACY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("privacy") is True


def _window_until(home: Path) -> float:
    """The end of a running full-text window from the flag file (read directly, so any process sees it), else 0."""
    try:
        d = json.loads((home / "logs" / logs.PRIVACY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    u = d.get("until") if isinstance(d, dict) and d.get("privacy") is False else None
    return float(u) if isinstance(u, (int, float)) and not isinstance(u, bool) and u > time.time() else 0.0


def set_full_text(ctx, on: bool) -> dict:
    """"Full text on request" (owner decision OD-12, 2026-10-21): full text for 24 hours ONLY, then privacy turns on
    again by itself (logs._expire_full_text, in every process). config.toml is not changed: a restart also ends it."""
    if on:
        until = time.time() + logs.FULL_TEXT_HOURS * 3600
        logs.write_privacy(ctx.home / "logs", False, until=until)
        ctx.cfg.log.privacy = False
        logs.info("privacy", "PRIVACY_FULL_TEXT_ON", "full text switched on for a limited time", hours=logs.FULL_TEXT_HOURS)
    else:
        logs.write_privacy(ctx.home / "logs", True)
        ctx.cfg.log.privacy = True
        scrub = scrub_at_rest(ctx.home)
        logs.info("privacy", "PRIVACY_FULL_TEXT_OFF", "full text switched off by the owner", enabled=False)
        return {**state(ctx), **_scrub_answer(scrub)}
    return state(ctx)


_NOT_KEPT = {"not_kept": True, "code": "NOT_KEPT"}
# #1557 R5: only the generic tool-runner and plan codes stay (skills/__init__.py, goal_plans.py); any other
# code-shaped text such as CALL_DANA could carry personal words, so it is cleared.
_KEPT_CODES = frozenset({"TOOL_FORBIDDEN", "TOOL_UNTRUSTED_TURN", "TOOL_DECLINED", "TOOL_UNKNOWN", "SKILL_DISABLED",
                         "FEATURE_OFF", "TOOL_BAD_ARGS", "TOOL_TIMEOUT", "TOOL_CRASH", "PLAN_BAD", "PLAN_NOT_FOUND",
                         "NOT_APPROVED"})
SCRUB_TIMEOUT_S = 2.0                               # a Core holding the lock: logged as partial, not a stuck request


class WalPending(sqlite3.Error):
    """The rows were rewritten, but a reader kept the checkpoint from copying them into the main file."""


def _scrub_db(path: Path, sql: str, fix) -> int:
    """Rewrite the rows `sql` selects (rowid first) with fix(row) -> (update sql, args) or None. 0 if no file.
    Raises WalPending when the old page copies may still be in the main file (checkpoint busy or incomplete)."""
    if not path.is_file():
        return 0
    cx = sqlite3.connect(path, timeout=SCRUB_TIMEOUT_S)
    try:
        with cx:                                    # one transaction: all rows or none
            cx.execute("PRAGMA secure_delete=ON")
            n = 0
            for row in cx.execute(sql).fetchall():
                upd = fix(row)
                if upd:
                    n += cx.execute(*upd).rowcount
        # WAL: the old plaintext page stays in the main file until a checkpoint copies the new one over it. A busy
        # checkpoint is a result row (busy, log, checkpointed), not an exception, so the row is read (#1557 R1).
        row = cx.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row and (row[0] != 0 or (row[1] >= 0 and row[2] < row[1])):
            raise WalPending(f"checkpoint busy={row[0]} log={row[1]} checkpointed={row[2]}")
        return n
    finally:
        cx.close()


def _ledger_fix(row):
    """R4: chat and goal rows get the same body #206 F13 stores in privacy mode, so a resend sees a known shape."""
    from . import mobile                           # late: mobile imports much of the Core
    rid, kind, status, body = row
    try:
        old = json.loads(body)
    except (TypeError, ValueError):                 # NULL or malformed body: replaced too
        old = None
    ok = old.get("ok") if isinstance(old, dict) and isinstance(old.get("ok"), bool) else \
        isinstance(status, int) and status < 400
    if kind == "goal":
        new = mobile.goal_at_rest(old if isinstance(old, dict) else {"error": ""})
        new.setdefault("ok", ok)
    elif kind == "chat":
        new = mobile.chat_at_rest({"ok": ok})
    else:
        new = {**_NOT_KEPT, "ok": ok}
    if new == old:
        return None
    return "UPDATE ledger SET body = ? WHERE rowid = ?", (json.dumps(new), rid)


def _step_fix(row):
    rid, evidence, error = row
    error = error if isinstance(error, str) else ""
    head = error.split(":", 1)[0].strip()
    code = head if head in _KEPT_CODES else ""     # only a known code like TOOL_TIMEOUT stays, never free text
    if evidence == "" and code == error:
        return None
    return "UPDATE goal_plan_steps SET evidence = '', error = ? WHERE rowid = ?", (code, rid)


_SCRUB_LOCK = threading.Lock()                     # one scrub at a time in this process
_last_scrub: threading.Thread | None = None
SCRUB_PARTIAL_NOTE = "Some older text could not be cleared yet; it is tried again next time privacy turns on."
SCRUB_PARTIAL_NOTE_HE = "חלק מהטקסט הישן עוד לא נמחק; ננסה שוב בפעם הבאה שהפרטיות תופעל."


def _scrub_answer(scrub: dict) -> dict:
    """The switch's JSON answer: the scrub result, plus a short EN/HE note when part of it failed (#1557 R2)."""
    if not scrub["partial"]:
        return {"scrub": scrub}
    return {"scrub": scrub, "scrub_note_en": SCRUB_PARTIAL_NOTE, "scrub_note_he": SCRUB_PARTIAL_NOTE_HE}


def scrub_in_background(home: Path) -> threading.Thread:
    """#1557 R2: the privacy-on hook may run on the Core's event loop (flag file poll); scrub on a thread instead."""
    global _last_scrub
    t = threading.Thread(target=scrub_at_rest, args=(Path(home),), name="privacy-scrub", daemon=True)
    t.start()
    _last_scrub = t
    return t


def join_background(timeout: float = SCRUB_TIMEOUT_S * 2) -> bool:
    """#1557 R3: Core stop waits briefly for a running background scrub. False (logged) if it is still running."""
    t = _last_scrub
    if t is None:
        return True
    t.join(timeout)
    if t.is_alive():
        logs.warn("privacy", "PRIVACY_SCRUB_STILL_RUNNING", "a privacy scrub was still running at stop")
        return False
    return True


def scrub_at_rest(home: Path) -> dict:
    """#1557: when privacy turns on, text stored before it was on is scrubbed too (#206 F13 covered new writes only):
    continuity.db ledger answers become a fixed NOT_KEPT note (a resend is still answered, not run again) and
    memory.db goal plan steps keep status and an upper-case error code only. Best effort: a database that cannot be
    scrubbed, or whose checkpoint a reader blocked, is logged and named in "partial" of the returned dict (the
    dashboard switch's JSON answer). Out of scope: the owner's own plan text (title, args, why, kept in privacy mode
    by #206 too) and backups made before privacy was on."""
    with _SCRUB_LOCK:
        return _scrub_all(Path(home))


def _scrub_all(home: Path) -> dict:
    out: dict = {"ledger": 0, "plan_steps": 0, "partial": []}
    for key, name, sql, fix in (
            ("ledger", "continuity.db", "SELECT rowid, kind, status, body FROM ledger", _ledger_fix),
            ("plan_steps", "memory.db", "SELECT rowid, evidence, error FROM goal_plan_steps "
                                        "WHERE evidence != '' OR error != ''", _step_fix)):
        try:
            out[key] = _scrub_db(home / name, sql, fix)
        except WalPending:
            out["partial"].append(name)
            logs.warn("privacy", "PRIVACY_SCRUB_PARTIAL", "older text may still be in the database file",
                      target=name, reason="wal_pending")
        except (sqlite3.Error, OSError) as e:      # R1: e.g. an unreadable folder; the switch still saves
            out["partial"].append(name)
            logs.warn("privacy", "PRIVACY_SCRUB_PARTIAL", "older text could not be scrubbed",
                      target=name, error_type=type(e).__name__)
    if out["ledger"] or out["plan_steps"]:
        logs.info("privacy", "PRIVACY_SCRUBBED", "text stored before privacy mode was scrubbed",
                  count=out["ledger"] + out["plan_steps"],
                  kind=",".join(k for k in ("ledger", "plan_steps") if out[k]))
    return out


def set_privacy(ctx, on: bool) -> dict:
    """Flip it now (this process + the Core via the flag file) and in config.toml for the next start."""
    logs.write_privacy(ctx.home / "logs", on)
    ctx.cfg.log.privacy = bool(on)                  # this dashboard's reads (logs view, tasks, notes) follow at once
    scrub = scrub_at_rest(ctx.home) if on else None
    saved, err = True, ""
    try:
        config_edit.apply(ctx.config_path, {"log.privacy": bool(on)})
    except Exception as e:                          # unreadable config.toml: the switch still works until restart
        saved, err = False, str(e)[:200]
    logs.info("privacy", "PRIVACY_SET", "privacy switched from the dashboard", on=bool(on), saved=saved)
    return {**state(ctx), "saved_in_config": saved, "config_error": err, **(_scrub_answer(scrub) if scrub else {})}


# ---------------------------------------------------------------------- one-way actions
def _rewrite_jsonl(path: Path, fix) -> int:
    """Rewrite a JSON-lines file in place (r+ and truncate: the Core may hold it open for appending on Windows)."""
    try:
        with open(path, "r+", encoding="utf-8", newline="") as f:
            lines = f.read().splitlines()
            out, n = [], 0
            for line in lines:
                try:
                    d = json.loads(line)
                except ValueError:
                    d = None
                if not isinstance(d, dict):         # a malformed line may hold anything: only its length stays
                    masked = json.dumps({"unparsed": f"<{len(line)} chars>"}) if line.strip() else line
                    n += masked != line
                    out.append(masked)
                    continue
                new = fix(d)
                n += new != d
                out.append(json.dumps(new, ensure_ascii=False, default=str))
            f.seek(0)
            f.write("".join(x + "\n" for x in out))
            f.truncate()
        return n
    except OSError:
        return 0


def mask_logs(home: Path) -> int:
    n = 0
    for p in sorted((home / "logs").glob("jarves.log*")):
        if p.suffix != ".tmp":
            n += _rewrite_jsonl(p, logs.private_record)
    tasks = home / "tasks.jsonl"
    if tasks.is_file():
        n += _rewrite_jsonl(tasks, lambda d: {**d, **{k: logs._mask(d[k]) for k in _TEXT_KEYS if d.get(k)}})
    for audit in sorted((home / "approvals").glob("audit*.jsonl")):     # SW-V4 B: titles / details of old answers
        n += _rewrite_jsonl(audit, _mask_audit)
    return n


def _mask_audit(d: dict) -> dict:
    """An approvals audit record as privacy mode writes it (approvals._private_audit), applied to an old one."""
    if d.get("kind") in ("shell", "install"):
        return d
    out = dict(d)
    for k in ("title", "prompt"):
        if out.get(k) and not str(out[k]).startswith("<"):
            out[k] = logs._mask(out[k])
    if isinstance(out.get("details"), dict):
        out["details"] = {k: logs.render_field(k, v, private=True) for k, v in out["details"].items()}
    return out


def _secure_purge(st) -> None:
    """After masking / deleting: drop the undo journal (it still held the removed text), overwrite freed pages
    (secure_delete), compact the file (VACUUM) and truncate the write-ahead log, so the words do not survive in
    the meta journal, in free pages or in store.db-wal. Best effort: a running Core may hold a lock (logged)."""
    with st._lock:
        st.x("DELETE FROM meta WHERE key = 'memory_undo'")        # old fact text / deleted memory rows for undo
        st.commit()
        try:
            st.x("PRAGMA secure_delete=ON")
            st._db.execute("VACUUM")
            st.x("PRAGMA wal_checkpoint(TRUNCATE)")
            st.commit()
        except sqlite3.Error as e:
            logs.warn("privacy", "PRIVACY_PURGE_PARTIAL", "could not fully compact store.db (Core may hold it)",
                      error_type=type(e).__name__)


def _backup_residue(home: Path, prune: bool) -> dict:
    """Backups taken before the purge still hold the old text. Report how many, and delete them if the user asked
    (masking the store.db snapshot inside each archive is not attempted; pruning is the honest, reliable option)."""
    from . import backup
    items = backup.list_backups(home)
    removed: list[str] = []
    if prune:
        for b in items:
            try:
                (backup.backup_dir(home) / b["name"]).unlink()
                removed.append(b["name"])
            except OSError:
                pass
        if removed:
            logs.info("privacy", "PRIVACY_BACKUPS_PRUNED", "backups holding the removed data deleted",
                      removed=len(removed))
    return {"backups_with_old_data": len(items), "backups_removed": len(removed),
            "backups_hint": None if (prune or not items) else
            f"{len(items)} backup(s) still contain this data; re-run with prune_backups to delete them"}


def mask_history(ctx, *, prune_backups: bool = False) -> dict:
    st = ctx.store
    with st._lock:
        st.x("PRAGMA secure_delete=ON")
        turns = st.x("UPDATE turns SET text = NULL WHERE text IS NOT NULL").rowcount
        args = st.x("UPDATE tool_calls SET args = NULL WHERE args IS NOT NULL").rowcount
        ev = 0
        for r in st.rows("SELECT id, msg, fields FROM events"):
            try:
                f = json.loads(r["fields"]) if r["fields"] else {}
            except ValueError:
                f = {"unparsed": r["fields"]}
            f = f if isinstance(f, dict) else {"unparsed": f}
            new = logs.private_record(f)
            # SW-V4 B: a pre-v2 event message is free text (it may quote what was said): masked too
            msg = r["msg"] if f.get("v") == logs.FORMAT_VERSION or r["msg"] is None else logs._mask(r["msg"])
            if new != f or msg != r["msg"]:
                st.x("UPDATE events SET fields = ?, msg = ? WHERE id = ?",
                     (json.dumps(new, ensure_ascii=False), msg, r["id"]))
                ev += 1
        from . import recall                        # SW-V4 G: the search index held the words too
        purged = recall.purge_conversations(st._db)
        st._db.commit()
        from . import context_trace                 # the conversation context: sizes and hashes stay
        context_trace.mask_all(st._db)
    _secure_purge(st)
    lines = mask_logs(ctx.home)
    backups = _backup_residue(ctx.home, prune_backups)
    logs.info("privacy", "PRIVACY_MASKED_HISTORY", "existing history masked", turns=turns, tool_args=args,
              events=ev, log_lines=lines, index_rows=purged)
    return {"ok": True, "turns": turns, "tool_args": args, "events": ev, "log_lines": lines, "index_rows": purged,
            **backups}


def delete_conversations(ctx, *, prune_backups: bool = False) -> dict:
    st = ctx.store
    with st._lock:
        st.x("PRAGMA secure_delete=ON")
        n = st.rows("SELECT COUNT(*) n FROM sessions")[0]["n"]
        for table in ("turns", "tool_calls", "issue_hits", "events"):
            st.x(f"DELETE FROM {table}")
        st.x("UPDATE delegations SET session_id = NULL")          # costs stay true
        st.x("DELETE FROM sessions")
        from . import recall                        # SW-V4 G: and their rows in the search index
        purged = recall.purge_conversations(st._db)
        st._db.commit()
        from . import context_trace                 # and what he was given in them
        context_trace.delete_all(st._db)
    _secure_purge(st)
    lines = mask_logs(ctx.home)
    backups = _backup_residue(ctx.home, prune_backups)
    logs.info("privacy", "PRIVACY_DELETED_CONVERSATIONS", "all stored conversations deleted", sessions=n,
              log_lines=lines, index_rows=purged)
    return {"ok": True, "deleted": n, "log_lines": lines, "index_rows": purged, **backups}


# ---------------------------------------------------------------------- dashboard routes
def dashboard_routes(register) -> None:
    def get(ctx, _m, _body):
        return 200, state(ctx)

    def post(ctx, _m, body):
        on = (body or {}).get("privacy")
        if not isinstance(on, bool):
            return 400, {"error": "send {\"privacy\": true|false}"}
        return 200, set_privacy(ctx, on)

    def confirmed(fn, label, title):
        def h(ctx, _m, body):
            body = body or {}
            if body.get("confirm") is not True:
                return 400, {"error": "this cannot be undone: send {\"confirm\": true}"}
            from . import approvals
            ok, _w = approvals.ask_owner(ctx.home, "This cannot be undone: " + label + "?",
                                         title=title, risk="high",
                                         why="You pressed this on the dashboard Privacy page.")
            if not ok:
                return 403, dict(approvals.APPROVAL_REFUSED)
            return 200, fn(ctx, prune_backups=bool(body.get("prune_backups")))
        return h
    def full_text(ctx, _m, body):
        on = (body or {}).get("full_text")
        if not isinstance(on, bool):
            return 400, {"error": "send {\"full_text\": true|false}"}
        return 200, set_full_text(ctx, on)
    register("POST", r"/api/privacy/full-text", full_text)
    register("GET", r"/api/privacy", get)
    register("POST", r"/api/privacy", post)
    register("POST", r"/api/privacy/mask-history", confirmed(mask_history, "mask the conversation history", "dashboard_mask_history"))
    register("POST", r"/api/privacy/delete-conversations", confirmed(delete_conversations, "delete all conversations", "dashboard_delete_conversations"))


# ---------------------------------------------------------------------- CLI: jarvis privacy status | full-text on|off
def cmd_privacy(a, cfg, home) -> int:
    from types import SimpleNamespace
    from . import clilang
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    ctx = SimpleNamespace(home=home, cfg=cfg, config_path=home / "config.toml")
    if a.action == "full-text":
        st = set_full_text(ctx, a.state == "on")
    else:
        flag = _window_until(home)
        if flag:                                     # another process switched it: the file is the truth
            ctx.cfg.log.privacy = False
        st = state(ctx)
    if st["privacy"]:
        print(clilang.fmt("פרטיות פועלת: מה שנאמר לא נשמר.", "Privacy is on: what is said is not kept."))
    elif st["full_text_active"]:
        print(clilang.fmt(st["full_text_note_he"], st["full_text_note_en"]))
    else:
        print(clilang.fmt(logs.PRIVACY_OFF_NOTE_HE, logs.PRIVACY_OFF_NOTE))
    return 0


def add_cli(sub) -> None:
    p = sub.add_parser("privacy", help="privacy status; 'full-text on' keeps full text for 24 hours only, then privacy "
                                       "turns on again by itself")
    ps = p.add_subparsers(dest="action", required=True)
    ps.add_parser("status", help="is privacy on, or is a full-text window running (and until when)")
    ft = ps.add_parser("full-text", help="full text on request: on = 24 hours then automatic revert, off = back now")
    ft.add_argument("state", choices=["on", "off"])


COMMANDS = {"privacy": cmd_privacy}
