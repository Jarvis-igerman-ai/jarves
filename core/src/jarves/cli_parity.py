"""CLI / dashboard parity (V4 P0.3, issue #17): `jarvis timers|lists|notes|jobs|reports`, and the PARITY tables.

The commands use the same store classes as the voice tools and the dashboard (timers.Timers, lists.Lists, notes.Notes,
scheduler.Scheduler) on the same memory.db, so a timer added here is rung by the running Core (it polls the table
every second) and shows on the dashboard Timers page. Printed text follows `clilang` (English by default).

PARITY: every feature switch and every user-facing tool group must name its command-line surface OR carry a
documented 'dashboard only' marker. tests/test_cli_parity.py fails when a new feature or tool group has neither.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime

from . import clilang


# ---------------------------------------------------------------------- the parity tables
# feature id -> "jarvis <command>" that manages/uses it, or ("dashboard", reason). Every switch itself is always
# available as `jarvis features on|off|reset ID` (tested for the whole catalog).
D = "dashboard"
FEATURES: dict[str, object] = {
    "music": "music", "web_search": "ask", "background_tasks": (D, "the Background tasks page shows live research runs"),
    "memory_curator": "memory", "memory_llm_pass": "memory", "diagnosis_llm_pass": "diagnose",
    "google_calendar": "calendar", "google_contacts": "contacts", "gmail_read": "mail", "gmail_send": "mail",
    "camera": (D, "camera status lives in `python -m jarves.camera`; enabling it is `jarvis features on camera`"),
    "face_recognition": (D, "enrolment needs the camera preview; switch via `jarvis features`"),
    "camera_when_away": (D, "a pure switch; change it with `jarvis features`"),
    "camera_preview": (D, "the preview is a window of the face app; switch via `jarvis features`"),
    "wake_word": "wake", "greeting": (D, "a pure switch; change it with `jarvis features`"),
    "chimes": (D, "a pure switch; change it with `jarvis features`"), "dashboard": "dashboard",
    "workspace_agent_edit": (D, "persona-file edits are reviewed on the Persona files page"),
    "voice_change": "setup", "calendar_write": "calendar", "dashboard_lan": "dashboard", "mobile_access": "dashboard",
    "assistant_identity": "setup", "telegram": "telegram", "instagram": "instagram", "usage_monitor": "usage", "usage_admin_key": "connect",
    "backup": "backup", "appearance": (D, "face theme and avatar are picked on the Appearance page"),
    "skill_creation": (D, "proposed Skills are reviewed on the New Skills page (risky: approval on screen)"),
    "guided_setup": "setup", "self_check": "diagnose", "scheduler": "jobs", "reports": "reports", "notes": "notes",
    "timers": "timers", "lists": "lists", "goals": "goals", "adaptive_memory": "prefs", "prefs_in_prompt": "prefs",
    "goal_done_line": (D, "a pure switch; change it with `jarvis features`"), "proactive_intel": "proactive",
    "dev_manager": "dev", "dev_status": "dev", "files": (D, "the file workspace is browsed on the My files page"),
    "proactive_alerts": (D, "alerts are read and answered on the Alerts page"),
    "autonomy": (D, "autonomy proposals are approved on the Autonomy page"),
    "background_core": "start", "display_control": "show", "tray_icon": "start", "autostart": "autostart",
    "update_check": "update", "proactive_help": (D, "a pure switch; change it with `jarvis features`"),
    "profiles": "profile", "context_trace": (D, "the context trace is a dashboard view of one conversation"),
    "browser": "browser", "helper": "approvals", "helper_readonly_auto": (D, "a pure switch; `jarvis features`"),
    "whatsapp_handoff": (D, "WhatsApp opens a draft in the app; no CLI needed, switch via `jarvis features`"),
    "morning_briefing": "jobs", "home_assistant": "home", "conversation_recall": "recall",
    "self_diagnostics_2": "health", "client_delegation": "budget",
    "whatsapp_bridge": "whatsapp", "whatsapp_send": "whatsapp",          # SW-V4 F: jarvis whatsapp ...
    "agent_orchestrator": "agents",
    "assisted_dialing": "dial",
    "self_tests": "selftest",
}

# Skill (tool group) name -> "jarvis <command>" or ("dashboard", reason) or ("voice", reason) for groups that only
# make sense inside a conversation.
V = "voice"
TOOL_GROUPS: dict[str, object] = {
    "alerts": (D, "alerts are read and answered on the Alerts page"), "app": (V, "internal test skill"),
    "appearance": (D, "the Appearance page"), "autonomy": (D, "the Autonomy page"), "briefing": "jobs",
    "browser": "browser", "calc": (V, "arithmetic inside a conversation"), "calendar": "calendar",
    "calendar_write": "calendar", "camera": (D, "see features camera"), "clock": (V, "the time inside a conversation"),
    "contacts": "contacts", "dashboard": "dashboard", "display": "show", "features": "features",
    "files": (D, "the My files page"), "gmail": "mail", "google_connect": "google",
    "google_contacts": "contacts", "help": "help", "helper": "approvals", "home": "home",
    "goals": "goals", "preferences": "prefs", "proactive": "proactive", "dev": "dev", "devstatus": "dev", "identity": "setup", "instagram": "instagram", "lists": "lists", "memory": "memory", "music": "music",
    "notes": "notes", "profiles": "profile", "reports": "reports", "scheduler": "jobs", "self_check": "diagnose",
    "settings": (D, "settings are edited on the Settings page"), "skill_builder": (D, "the New Skills page"),
    "status": "status", "tasks": (D, "the Background tasks page"), "telegram": "telegram", "timers": "timers",
    "usage": "usage", "whatsapp": (D, "WhatsApp opens a draft in the app"), "conversation_recall": "recall",
    "health": "health",
    "whatsapp_bridge": "whatsapp",                                       # SW-V4 F: inbox / send / status
    "agents": "agents",
    "dialing": "dial",
    "selfspeed": "selftest",
}


# ---------------------------------------------------------------------- helpers
def _db(home):
    from .userdata import open_db
    return open_db(home)


def parse_duration(text: str) -> float:
    """'10' (minutes), '10m', '90s', '1h30m', '1h', '0.5m' -> seconds."""
    t = str(text or "").strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return float(t) * 60
    parts = re.findall(r"(\d+(?:\.\d+)?)([hms])", t)
    if not parts or "".join(f"{n}{u}" for n, u in parts) != t:
        raise ValueError("duration like 10m, 90s, 1h30m (a bare number is minutes)")
    return sum(float(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in parts)


def _left(s: int) -> str:
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return (f"{h}h " if h else "") + (f"{m}m " if m or h else "") + f"{sec}s"


def _warn_if_off(a, cfg, home, fid: str) -> None:
    try:
        from .cli import _cli_features
        f = _cli_features(cfg, home)
        if not f.enabled(fid):
            print(clilang.fmt(f"שים לב: {fid} כבוי, לכן הליבה לא תפעיל את זה. להפעלה: jarvis features on {fid}",
                              f"note: {fid} is switched off, so the Core will not ring/act on it. "
                              f"Turn it on: jarvis features on {fid}"), file=sys.stderr)
    except Exception:
        pass


def _confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        print(clilang.fmt("נדרש אישור: הוסף --yes", "approval needed: add --yes"), file=sys.stderr)
        return False
    return input(prompt + " type yes: ").strip().lower() in ("yes", "y", "כן")


# ---------------------------------------------------------------------- timers
def cmd_timers(a, cfg, home) -> int:
    from .timers import Timers
    db = _db(home)
    t = Timers(db)
    try:
        if a.action == "list":
            rows = t.pending()
            for r in rows:
                v = t.view(r)
                when = f"at {v['at']}" if r["kind"] == "alarm" else f"in {_left(v['seconds_left'])}"
                print(f"{r['id']:>4}  {r['kind']:<6} {when:<18} {r['label']}")
            print(clilang.fmt(f"({len(rows)} פעילים)", f"({len(rows)} running)"))
            return 0
        if a.action == "add":
            _warn_if_off(a, cfg, home, "timers")
            if a.at:
                if a.duration and not a.label:          # `timers add --at 07:30 wake up`: the first word is the label
                    a.label, a.duration = a.duration, ""
                r = t.set_alarm(a.at, a.label, a.date or "", by="cli")
                print(clilang.fmt(f"שעון מעורר {r['id']} נקבע ל-{t.view(r)['at']}", f"alarm {r['id']} set for {t.view(r)['at']}"))
            else:
                r = t.start_timer(parse_duration(a.duration or ""), a.label, by="cli")
                print(clilang.fmt(f"טיימר {r['id']} נקבע", f"timer {r['id']} set for {_left(t.view(r)['seconds_left'])}"))
            return 0
        if a.action == "cancel":
            if not (a.id or a.label or a.all):
                print(clilang.fmt("ציין מספר, תווית או --all", "give an id, a label or --all"), file=sys.stderr)
                return 2
            hit = t.cancel(int(a.id or 0), a.label or "", by="cli")
            print(clilang.fmt(f"בוטלו {len(hit)}", f"cancelled {len(hit)}"))
            return 0 if hit else 1
        if a.action == "snooze":                         # SW-V4 K: same op as the dashboard Snooze button
            r = t.snooze(int(a.id), float(a.minutes), by="cli")
            print(clilang.fmt(f"נדחה: {_left(t.view(r)['seconds_left'])}", f"snoozed: {_left(t.view(r)['seconds_left'])} left"))
            return 0
    except ValueError as e:
        print(f"timers: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()
    return 2


# ---------------------------------------------------------------------- lists
def cmd_lists(a, cfg, home) -> int:
    from .lists import Lists, list_key
    db = _db(home)
    ls = Lists(db)
    try:
        if a.action == "show":
            if not a.name:
                names = ls.names()
                for n in names:
                    print(f"{n['name']:<20} {n['open']} open, {n['done']} done")
                if not names:
                    print(clilang.fmt("אין רשימות", "no lists yet"))
                return 0
            rows = ls.items(a.name, include_done=a.all)
            print(f"{list_key(a.name)}:")
            for r in rows:
                print(f"  [{'x' if r['done'] else ' '}] {r['text']}")
            if not rows:
                print(clilang.fmt("  (ריקה)", "  (empty)"))
            return 0
        if a.action == "add":
            _warn_if_off(a, cfg, home, "lists")
            r = ls.add(a.name, a.items, by="cli")
            print(clilang.fmt(f"נוסף: {', '.join(r['added']) or '-'}", f"added: {', '.join(r['added']) or '-'}")
                  + (f"  (already there: {', '.join(r['already_there'])})" if r["already_there"] else ""))
            return 0
        if a.action in ("check", "remove"):
            hit = (ls.check if a.action == "check" else ls.remove)(a.name, a.item, by="cli")
            print(f"{'ticked off' if a.action == 'check' else 'removed'}: {', '.join(hit)}" if hit
                  else clilang.fmt("לא נמצא", "no such item on the list"))
            return 0 if hit else 1
        if a.action == "clear":      # destructive: asks first, like the dashboard button and the voice tool
            only_done = not a.everything
            n = len([r for r in ls.items(a.name, include_done=True) if r["done"] or not only_done])
            what = "ticked-off items" if only_done else "ALL items"
            if not _confirm(f"Clear {n} {what} from {list_key(a.name)}?", a.yes):
                print(clilang.fmt("לא נמחק", "nothing cleared"))
                return 1
            cleared = ls.clear(a.name, only_done=only_done, by="cli")
            print(clilang.fmt(f"נמחקו {cleared}", f"cleared {cleared}"))
            return 0
    except ValueError as e:
        print(f"lists: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()
    return 2


# ---------------------------------------------------------------------- goals / learned preferences (V5 pillars)
def cmd_goals(a, cfg, home) -> int:
    from .goals import Goals, checkin_text
    db = _db(home)
    g = Goals(db)
    try:
        if a.action == "list":
            rows = g.list("" if a.all else "active")
            for r in rows:
                due = f"  by {r['target_date']}" if r["target_date"] else ""
                print(f"{r['id']:>3} [{r['status']}] {r['progress']:>3}%  {r['title']}{due}")
                for s in r["steps"]:
                    print(f"      [{'x' if s['done'] else ' '}] {s['id']}: {s['text']}" + ("  (outward)" if s["outward"] else ""))
            if not rows:
                print(clilang.fmt("אין יעדים", "no goals yet"))
            return 0
        if a.action == "add":
            _warn_if_off(a, cfg, home, "goals")
            r = g.add(a.title, a.details, a.by_date, a.step, by="cli")
            print(clilang.fmt(f"יעד {r['id']} נוסף", f"goal {r['id']} added"))
            return 0
        if a.action == "step":
            r = g.add_step(a.id, a.text, a.outward, by="cli")
            print(clilang.fmt(f"התקדמות {r['progress']}%", f"progress {r['progress']}%"))
            return 0
        if a.action in ("done", "undone"):
            r = g.set_step(a.step_id, a.action == "done", by="cli")
            print(f"{r['title']}: {r['progress']}%")
            return 0
        if a.action == "status":
            r = g.set_status(a.id, a.status, by="cli")
            print(f"{r['title']}: {r['status']}")
            return 0
        if a.action == "checkin":          # only when you run it: nothing schedules this
            print(checkin_text(g.checkin(by="cli")))
            return 0
        if a.action == "delete":
            goal = g.get(a.id)
            if not goal:
                print(clilang.fmt("אין יעד כזה", "no such goal"), file=sys.stderr)
                return 1
            if not _confirm(f"Delete goal '{goal['title']}' and its steps?", a.yes):
                print(clilang.fmt("לא נמחק", "nothing deleted"))
                return 1
            g.delete(a.id, by="cli")
            print(clilang.fmt("נמחק", "deleted"))
            return 0
    except KeyError:
        print(clilang.fmt("אין יעד או צעד כזה", "no such goal or step"), file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"goals: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()
    return 2


def cmd_prefs(a, cfg, home) -> int:
    from .preferences import Preferences
    db = _db(home)
    p = Preferences(db)
    try:
        if a.action == "list":
            rows = p.items()
            for r in rows:
                flag = " (stale: reaffirm it)" if r["stale"] else ""
                print(f"{r['id']:>3} [{r['state']}] {r['topic']}: {r['text']}{flag}")
                if r["state"] == "proposed":
                    print(f"      you said: {r['evidence']}")
            if not rows:
                print(clilang.fmt("אין העדפות נלמדות", "no learned preferences"))
            return 0
        if a.action == "prompt":
            from .features import switch
            from .cli import _cli_features
            from .preferences import FEATURE, prompt_block
            if a.state in ("on", "off"):
                switch(_cli_features(cfg, home), FEATURE, a.state == "on", by="cli")
            on = _cli_features(cfg, home).enabled(FEATURE)
            print(clilang.fmt("שימוש בהעדפות בשיחות: " + ("פעיל" if on else "כבוי"),
                              "preferences in conversations: " + ("on" if on else "off")))
            if a.state == "show" or a.state is None:
                print(prompt_block(p) or clilang.fmt("(אין העדפות שמאושרות לשליחה)", "(no confirmed preferences to send)"))
            return 0
        if a.action == "add":
            _warn_if_off(a, cfg, home, "adaptive_memory")
            r = p.add_confirmed(a.text, a.topic, by="cli")
            print(clilang.fmt(f"נשמר: {r['item']['id']}", f"saved: {r['item']['id']}"))
            return 0
        if not p.get(a.id):
            print(clilang.fmt("אין העדפה כזאת", "no such preference"), file=sys.stderr)
            return 1
        if a.action == "confirm":
            r = p.confirm(a.id, by="cli")
            print(clilang.fmt("נשמר", "remembered")
                  + ("; may conflict with: " + "; ".join(c["text"] for c in r["conflicts"]) if r["conflicts"] else ""))
        elif a.action == "decline":
            p.decline(a.id, by="cli")
            print(clilang.fmt("לא אזכור", "will not remember"))
        elif a.action == "reaffirm":
            p.reaffirm(a.id, by="cli")
            print(clilang.fmt("אושר מחדש", "reaffirmed"))
        elif a.action == "edit":
            p.edit(a.id, a.text, by="cli")
            print(clilang.fmt("עודכן", "updated"))
        elif a.action == "remove":
            p.remove(a.id, by="cli")
            print(clilang.fmt("הוסר", "removed"))
        return 0
    except ValueError as e:
        print(f"prefs: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()


# ---------------------------------------------------------------------- proactive offers / development drafts
def cmd_proactive(a, cfg, home) -> int:
    from .goals import Goals
    from .proactive import Proactive
    db = _db(home)
    p = Proactive(db, Goals(db))
    try:
        if a.action == "mode":
            if a.value:
                p.set_mode(a.value, by="cli")
            print(clilang.fmt("מצב: ", "mode: ") + p.mode())
            return 0
        if a.action == "dismiss":
            print(clilang.fmt("נסגר", "dismissed") if p.dismiss(a.id, by="cli") else clilang.fmt("אין פריט כזה", "no such item"))
            return 0
        rows = p.refresh()
        for r in rows:
            print(f"{r['id']:>3} [{r['state']}] {r['text']}")
        if not rows:
            print(clilang.fmt("אין מה להציע", "nothing to mention"))
        return 0
    except ValueError as e:
        print(f"proactive: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()


def _dev_status(a, home) -> int:
    from . import devstatus as ds
    try:
        st = ds.build(home, ds.repo_of(home))
    except ValueError as e:
        print(f"dev: {e}", file=sys.stderr)
        return 2
    lang = clilang.current()
    if a.action == "digest":
        print(ds.digest(st, lang))
    elif a.action == "proposals":
        for p in ds.proposals(st):
            print(f"{p['id']:<18} {p['item']:<10} {p['command']}")
        if not ds.proposals(st):
            print(clilang.fmt("אין הצעות לפעולה", "no proposals"))
    elif a.action == "apply":
        res = ds.apply(home, st, a.proposal)
        print(f"dev: {res['status']} {res.get('command') or res.get('reason') or ''}")
        return 0 if res["ok"] else 1
    else:
        print(ds.sentence(st, lang))
        print(f"{'':<5}{'STATE':<12}{'BLOCKER':<34}NEXT")
        for it in st["items"]:
            print(f"{'PR' if it['type'] == 'pr' else 'IS'}#{it['number']:<4}{it['state']:<12}{it['blocker'][:32]:<34}"
                  f"{it['suggestion']['kind']}: {it['title'][:50]}")
        for n in st["notes"]:
            print(clilang.fmt(f"הערה: {n}", f"note: {n}"))
    return 0


def cmd_dev(a, cfg, home) -> int:
    from . import devmanager as dm
    from .diag import db as diag_db
    from .store import Store
    st = Store(home / "store.db")
    try:
        diag_db.ensure(st)
        if a.action in ("status", "digest", "proposals", "apply"):
            return _dev_status(a, home)
        if a.action == "suggest":
            _warn_if_off(a, cfg, home, "dev_manager")
            d = dm.suggest(st, a.title, a.body, a.kind, by="cli")
            print(clilang.fmt(f"טיוטה {d['id']} נשמרה מקומית, שום דבר לא נשלח",
                              f"draft {d['id']} saved locally, nothing was posted"))
        elif a.action == "list":
            rows = dm.listing(st)
            for r in rows:
                print(f"{r['id']:>3} [{r['severity']}] {r['title']}")
            if not rows:
                print(clilang.fmt("אין טיוטות", "no drafts"))
        elif a.action == "show":
            d = dm.get(st, a.id)
            if d is None:
                print(clilang.fmt("אין טיוטה כזאת", "no such draft"), file=sys.stderr)
                return 1
            print(f"# {d['title']}\n\n{d['body']}")
        elif a.action == "discard":
            print(clilang.fmt("נמחקה", "discarded") if dm.discard(st, a.id, by="cli") else clilang.fmt("אין טיוטה כזאת", "no such draft"))
        elif a.action == "file":
            if dm.get(st, a.id) is None:
                print(clilang.fmt("אין טיוטה כזאת", "no such draft"), file=sys.stderr)
                return 1
            import asyncio
            from .diag import tickets
            from .diag.service import _tty_ask
            from .userdata import open_db
            dbs = open_db(home)
            try:
                res = asyncio.run(tickets.file(st, a.id, ask=_tty_ask(home), repo=str(dbs.setting("health.ticket_repo") or ""),
                                               by="cli"))
            finally:
                dbs.close()
            print(f"dev: {res['status']} {res.get('issue_url') or res.get('reason') or ''}")
        return 0
    except ValueError as e:
        print(f"dev: {e}", file=sys.stderr)
        return 2
    finally:
        st.close()


# ---------------------------------------------------------------------- notes / jobs / reports (read-only)
def cmd_notes(a, cfg, home) -> int:
    from .notes import Notes
    db = _db(home)
    try:
        n = Notes(db)
        if a.action == "show":
            note = n.get(a.id)
            if not note:
                print(clilang.fmt("אין פתק כזה", "no such note"), file=sys.stderr)
                return 1
            print(f"#{note['id']} {note['title']}  {' '.join('#' + t for t in note['tags'])}\n\n{note['text']}")
            return 0
        rows = n.search(a.query or "", a.tag or "", 200)
        for r in rows:
            print(f"{r['id']:>4}  {datetime.fromtimestamp(r['updated']):%Y-%m-%d}  {r['title']}"
                  + (f"  [{', '.join(r['tags'])}]" if r["tags"] else ""))
        print(clilang.fmt(f"({len(rows)} פתקים)", f"({len(rows)} notes)"))
        return 0
    finally:
        db.close()


def cmd_jobs(a, cfg, home) -> int:
    from .scheduler import Scheduler
    db = _db(home)
    try:
        s = Scheduler(db)
        rows = s.jobs()
        for j in rows:
            v = s.view(j)
            print(f"{j['id'][:8]:<9} {j['status']:<8} {v['human']:<32} next: {v['next_run_local'] or '-':<18} {j['name']}")
        print(clilang.fmt(f"({len(rows)} משימות קבועות; ניהול בלוח הבקרה)",
                          f"({len(rows)} scheduled jobs; manage them on the dashboard Scheduled jobs page)"))
        return 0
    finally:
        db.close()


def cmd_reports(a, cfg, home) -> int:
    from .scheduler import PERIODS, Scheduler, find_reports, report_get
    db = _db(home)
    try:
        s = Scheduler(db)
        if a.action == "show":
            r = report_get(db, a.id, s.tz())
            if not r:
                print(clilang.fmt("אין דוח כזה", "no such report"), file=sys.stderr)
                return 1
            print(f"{r['title']}\n\n{r['text']}")
            return 0
        rows = find_reports(db, period=a.period if a.period in PERIODS else "latest", query=a.query or "",
                            tz=s.tz(), now=time.time(), limit=50)
        for r in rows:
            print(f"{r['id']:>4}  {r.get('status', ''):<8} {r['title']}")
        print(clilang.fmt(f"({len(rows)} דוחות)", f"({len(rows)} reports)"))
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------- argparse
def add_cli(sub) -> None:
    tm = sub.add_parser("timers", help="timers and alarms: list / add 10m 'pasta' / add --at 07:30 / cancel ID")
    ts = tm.add_subparsers(dest="action", required=True)
    ts.add_parser("list", help="the running timers and alarms")
    ta = ts.add_parser("add", help="start a timer (10m, 90s, 1h30m; a bare number is minutes) or an alarm (--at)")
    ta.add_argument("duration", nargs="?", default="")
    ta.add_argument("label", nargs="?", default="")
    ta.add_argument("--at", default="", metavar="HH:MM", help="an alarm at this clock time instead of a timer")
    ta.add_argument("--date", default="", metavar="YYYY-MM-DD")
    tc = ts.add_parser("cancel", help="cancel by id or label, or --all")
    tc.add_argument("id", nargs="?", default="")
    tc.add_argument("--label", default="")
    tc.add_argument("--all", action="store_true")
    tz = ts.add_parser("snooze", help="push a running timer later (or restart one that rang): snooze ID [MINUTES]")
    tz.add_argument("id")
    tz.add_argument("minutes", nargs="?", default="5")

    li = sub.add_parser("lists", help="shopping / to-do lists: show / add / check / remove / clear")
    lsub = li.add_subparsers(dest="action", required=True)
    sh = lsub.add_parser("show", help="the lists, or one list's items")
    sh.add_argument("name", nargs="?", default="")
    sh.add_argument("--all", action="store_true", help="include ticked-off items")
    ad = lsub.add_parser("add", help="add items: jarvis lists add shopping milk bread")
    ad.add_argument("name")
    ad.add_argument("items", nargs="+")
    for act in ("check", "remove"):
        p = lsub.add_parser(act, help=f"{act} one item")
        p.add_argument("name")
        p.add_argument("item")
    cl = lsub.add_parser("clear", help="clear the ticked-off items (asks first); --everything = the whole list")
    cl.add_argument("name")
    cl.add_argument("--everything", action="store_true")
    cl.add_argument("--yes", action="store_true", help="you already approved (no question)")

    go = sub.add_parser("goals", help="your goals: list / add / step / done / undone / status / checkin / delete")
    gs = go.add_subparsers(dest="action", required=True)
    gl = gs.add_parser("list")
    gl.add_argument("--all", action="store_true", help="include done / dropped goals")
    ga = gs.add_parser("add", help="jarvis goals add \"run a 10k\" --by 2026-12-31 --step \"buy shoes\"")
    ga.add_argument("title")
    ga.add_argument("--details", default="")
    ga.add_argument("--by", dest="by_date", default="", help="target date YYYY-MM-DD")
    ga.add_argument("--step", action="append", default=[])
    gst = gs.add_parser("step", help="add a step: step GOAL_ID TEXT [--outward]")
    gst.add_argument("id", type=int)
    gst.add_argument("text")
    gst.add_argument("--outward", action="store_true", help="involves other people or the outside world")
    for act in ("done", "undone"):
        gd = gs.add_parser(act, help=f"mark a step {act}: {act} STEP_ID")
        gd.add_argument("step_id", type=int)
    gss = gs.add_parser("status", help="status GOAL_ID active|paused|done|dropped")
    gss.add_argument("id", type=int)
    gss.add_argument("status")
    gs.add_parser("checkin", help="your check-in, produced only when you run this")
    gx = gs.add_parser("delete", help="delete a goal and its steps (asks first)")
    gx.add_argument("id", type=int)
    gx.add_argument("--yes", action="store_true", help="you already approved (no question)")

    pr = sub.add_parser("prefs", help="learned preferences: list / add / confirm / decline / reaffirm / edit / remove")
    ps = pr.add_subparsers(dest="action", required=True)
    ps.add_parser("list")
    pa = ps.add_parser("add", help="a preference you type yourself (saved as confirmed)")
    pa.add_argument("text")
    pa.add_argument("--topic", default="other")
    for act in ("confirm", "decline", "reaffirm", "remove"):
        pp = ps.add_parser(act, help=f"{act} one item: {act} ID")
        pp.add_argument("id", type=int)
    pp2 = ps.add_parser("prompt", help="use confirmed preferences in every conversation: prompt [on|off|show]")
    pp2.add_argument("state", nargs="?", choices=["on", "off", "show"])
    pe = ps.add_parser("edit", help="edit ID TEXT")
    pe.add_argument("id", type=int)
    pe.add_argument("text")

    pi = sub.add_parser("proactive", help="proactive offers: list / mode [on_request|in_conversation] / dismiss ID")
    pis = pi.add_subparsers(dest="action", required=True)
    pis.add_parser("list")
    pim = pis.add_parser("mode", help="show or set: on_request (default) or in_conversation")
    pim.add_argument("value", nargs="?", choices=["on_request", "in_conversation"])
    pid_ = pis.add_parser("dismiss", help="dismiss ID")
    pid_.add_argument("id", type=int)

    dv = sub.add_parser("dev", help="development drafts, kept local: list / suggest / show / discard / file (asks first)")
    dvs = dv.add_subparsers(dest="action", required=True)
    dvs.add_parser("list")
    dvs.add_parser("status", help="read-only: agents, pull requests, issues and CI of JARVES with the suggested next step")
    dvs.add_parser("digest", help="the same as a short text, on request only")
    dvs.add_parser("proposals", help="list the GitHub changes that could be proposed (nothing is run)")
    dap = dvs.add_parser("apply", help="apply PROPOSAL_ID after you approve its exact command on the approval popup")
    dap.add_argument("proposal")
    dsg = dvs.add_parser("suggest", help="jarvis dev suggest \"title\" --body \"text\" [--kind ticket]")
    dsg.add_argument("title")
    dsg.add_argument("--body", default="")
    dsg.add_argument("--kind", choices=["suggestion", "ticket"], default="suggestion")
    for act in ("show", "discard", "file"):
        dd = dvs.add_parser(act, help=f"{act} ID" + (" (asks on the approval popup; needs health.ticket_repo)" if act == "file" else ""))
        dd.add_argument("id", type=int)

    nt = sub.add_parser("notes", help="your notes (read-only): list [--query Q] [--tag T] / show ID")
    ns = nt.add_subparsers(dest="action", required=True)
    nl = ns.add_parser("list")
    nl.add_argument("--query", default="")
    nl.add_argument("--tag", default="")
    nsh = ns.add_parser("show")
    nsh.add_argument("id", type=int)
    jb = sub.add_parser("jobs", help="scheduled jobs (read-only): list")
    jbs = jb.add_subparsers(dest="action", required=True)
    jbs.add_parser("list")
    rp = sub.add_parser("reports", help="saved job reports (read-only): list [--period P] / show ID")
    rps = rp.add_subparsers(dest="action", required=True)
    rl = rps.add_parser("list")
    rl.add_argument("--period", default="latest", help="latest, this_morning, today, yesterday, this_week")
    rl.add_argument("--query", default="")
    rsh = rps.add_parser("show")
    rsh.add_argument("id", type=int)


COMMANDS = {"timers": cmd_timers, "lists": cmd_lists, "goals": cmd_goals, "prefs": cmd_prefs, "proactive": cmd_proactive, "dev": cmd_dev, "notes": cmd_notes, "jobs": cmd_jobs, "reports": cmd_reports}
