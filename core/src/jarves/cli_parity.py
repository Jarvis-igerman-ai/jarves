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
    "timers": "timers", "lists": "lists", "files": (D, "the file workspace is browsed on the My files page"),
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
    "identity": "setup", "instagram": "instagram", "lists": "lists", "memory": "memory", "music": "music",
    "notes": "notes", "profiles": "profile", "reports": "reports", "scheduler": "jobs", "self_check": "diagnose",
    "settings": (D, "settings are edited on the Settings page"), "skill_builder": (D, "the New Skills page"),
    "status": "status", "tasks": (D, "the Background tasks page"), "telegram": "telegram", "timers": "timers",
    "usage": "usage", "whatsapp": (D, "WhatsApp opens a draft in the app"), "conversation_recall": "recall",
    "health": "health",
    "whatsapp_bridge": "whatsapp",                                       # SW-V4 F: inbox / send / status
    "agents": "agents",
    "dialing": "dial",
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


COMMANDS = {"timers": cmd_timers, "lists": cmd_lists, "notes": cmd_notes, "jobs": cmd_jobs, "reports": cmd_reports}
