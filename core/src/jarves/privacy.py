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
from pathlib import Path

from . import config_edit, logs

_TEXT_KEYS = ("description", "result", "queries", "steps")       # tasks.jsonl content fields


def state(ctx) -> dict:
    if logs._flag == ctx.home / "logs" / logs.PRIVACY_FILE and logs.runtime_privacy() is not None:
        ctx.cfg.log.privacy = logs.runtime_privacy()  # switched meanwhile by another dashboard / process
    on = bool(ctx.cfg.log.privacy)
    return {"privacy": on,
            "label_he": "פרטיות פועלת: מה שנאמר לא נשמר" if on else "פרטיות כבויה: הטקסט המלא נשמר (לפיתוח)",
            "label_en": "Privacy ON: what is said is not kept" if on else "Privacy OFF: full text is kept (development)",
            "explain_en": ("New log lines and conversation history keep only lengths and timings, never the words. "
                           "Older entries stay as they were until you mask them." if on else
                           "Everything you and JARVES say, and typed chat, is written in full to the logs and the "
                           "conversation history on this computer. Turn privacy on for daily use."),
            "explain_he": ("מעכשיו נשמרים רק אורך ותזמון, לא המילים. מה שנשמר קודם נשאר עד שמסתירים אותו."
                           if on else "כל מה שנאמר ונכתב נשמר במלואו ביומנים ובהיסטוריה במחשב הזה."),
            "applies": "now"}


def set_privacy(ctx, on: bool) -> dict:
    """Flip it now (this process + the Core via the flag file) and in config.toml for the next start."""
    logs.write_privacy(ctx.home / "logs", on)
    ctx.cfg.log.privacy = bool(on)                  # this dashboard's reads (logs view, tasks, notes) follow at once
    saved, err = True, ""
    try:
        config_edit.apply(ctx.config_path, {"log.privacy": bool(on)})
    except Exception as e:                          # unreadable config.toml: the switch still works until restart
        saved, err = False, str(e)[:200]
    logs.info("privacy", "PRIVACY_SET", f"privacy {'on' if on else 'off'} (dashboard)", on=bool(on), saved=saved)
    return {**state(ctx), "saved_in_config": saved, "config_error": err}


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
                    out.append(line)
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
            n += _rewrite_jsonl(p, lambda d: logs.scrub(d, private=True))
    tasks = home / "tasks.jsonl"
    if tasks.is_file():
        n += _rewrite_jsonl(tasks, lambda d: {**d, **{k: logs._mask(d[k]) for k in _TEXT_KEYS if d.get(k)}})
    return n


def mask_history(ctx) -> dict:
    st = ctx.store
    with st._lock:
        turns = st.x("UPDATE turns SET text = NULL WHERE text IS NOT NULL").rowcount
        args = st.x("UPDATE tool_calls SET args = NULL WHERE args IS NOT NULL").rowcount
        ev = 0
        for r in st.rows("SELECT id, fields FROM events WHERE fields IS NOT NULL"):
            try:
                f = json.loads(r["fields"])
            except ValueError:
                continue
            new = logs.scrub(f, private=True)
            if new != f:
                st.x("UPDATE events SET fields = ? WHERE id = ?", (json.dumps(new, ensure_ascii=False), r["id"]))
                ev += 1
        st.commit()
    lines = mask_logs(ctx.home)
    logs.info("privacy", "PRIVACY_MASKED_HISTORY", "existing history masked", turns=turns, tool_args=args,
              events=ev, log_lines=lines)
    return {"ok": True, "turns": turns, "tool_args": args, "events": ev, "log_lines": lines}


def delete_conversations(ctx) -> dict:
    st = ctx.store
    with st._lock:
        n = st.rows("SELECT COUNT(*) n FROM sessions")[0]["n"]
        for table in ("turns", "tool_calls", "issue_hits", "events"):
            st.x(f"DELETE FROM {table}")
        st.x("UPDATE delegations SET session_id = NULL")          # costs stay true
        st.x("DELETE FROM sessions")
        st.commit()
    lines = mask_logs(ctx.home)
    logs.info("privacy", "PRIVACY_DELETED_CONVERSATIONS", "all stored conversations deleted", sessions=n,
              log_lines=lines)
    return {"ok": True, "deleted": n, "log_lines": lines}


# ---------------------------------------------------------------------- dashboard routes
def dashboard_routes(register) -> None:
    def get(ctx, _m, _body):
        return 200, state(ctx)

    def post(ctx, _m, body):
        on = (body or {}).get("privacy")
        if not isinstance(on, bool):
            return 400, {"error": "send {\"privacy\": true|false}"}
        return 200, set_privacy(ctx, on)

    def confirmed(fn):
        def h(ctx, _m, body):
            if (body or {}).get("confirm") is not True:
                return 400, {"error": "this cannot be undone: send {\"confirm\": true}"}
            return 200, fn(ctx)
        return h
    register("GET", r"/api/privacy", get)
    register("POST", r"/api/privacy", post)
    register("POST", r"/api/privacy/mask-history", confirmed(mask_history))
    register("POST", r"/api/privacy/delete-conversations", confirmed(delete_conversations))
