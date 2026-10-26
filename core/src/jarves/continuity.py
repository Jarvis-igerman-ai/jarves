"""V5 continuity and proactive signals (docs/plans/V5_PILLARS.md, ROADMAP "Continuity and resilience").

One small SQLite file `continuity.db` in the profile home (the PC is the only source of truth; phones and the web chat
only read and send commands):

- command ledger   a client command id (chat, approval answer, goal action) is executed ONCE per device; a resend after a
                   Wi-Fi change, sleep or a PC restart gets the stored answer. A command that the client says is older
                   than REPLAY_WINDOW_S is refused as `STALE` (never silently replayed late).
- journal          the shared conversation: every typed/spoken turn of the phone, the desktop and the web chat in one
                   ordered list (`seq`), so any client can catch up with `turns(since)`.
- presence         which device the owner last used (interaction) and which are reachable (heartbeat).
- notices          a reminder or a proactive item is delivered ONCE, to the device that is active; the other device never
                   gets it (dedupe key), and a lost response is re-sent only to the same device until it acknowledges.
- signals          proactive signals are EVENT-driven (scheduler / calendar / timer / goal events); a gate applies the
                   opt-in switch, stop, snooze, quiet hours 22:00-07:00, frequency limits and a spending cap. No LLM call
                   happens here: a signal is a fixed text; one that would cost model requests must fit the daily cap
                   (default 0 = none).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from . import calm, features as feat, logs

FEATURE = "proactive_signals"
feat.register(FEATURE, "אותות יזומים", "Proactive signals", False, applies="live", skills=[],
              about="off by default: when ON, an event (a goal stalling, a calendar event, a reminder) may queue ONE short "
                    "note for the device you are using; never 22:00-07:00, with snooze, stop, hourly and daily limits; "
                    "it only tells you, it never does anything and never calls the AI on its own",
              targets=calm.targets('test_v5_continuity.py'))

DB_FILE = "continuity.db"
REPLAY_WINDOW_S = 120          # a queued command older than this is refused (the user must send it again on purpose)
LEDGER_TTL_S = 24 * 3600       # how long an answer is kept for a resend (survives a PC restart)
ACTIVE_WINDOW_S = 300          # the last interaction counts as "active" for this long
BEAT_WINDOW_S = 60             # a device that polled within this time is reachable
SENT_TIMEOUT_S = 15 * 60       # a notice sent to a device that never acknowledged and went silent is re-routed
DEDUPE_S = 24 * 3600
JOURNAL_KEEP = 500
NOTICE_KEEP_S = 7 * 86400
MAX_TEXT = 1000
DEVICES = ("desktop", "phone")

# signal gate defaults (owner-changeable through settings)
DEFAULT_HOURLY = 2
DEFAULT_DAILY = 6
SNOOZE_MAX_MIN = 24 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (device TEXT NOT NULL, cid TEXT NOT NULL, kind TEXT NOT NULL, status INTEGER NOT NULL,
    body TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (device, cid));
CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, device TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, cid TEXT, UNIQUE (device, cid, role));
CREATE TABLE IF NOT EXISTS presence (device TEXT PRIMARY KEY, kind TEXT NOT NULL, beat REAL NOT NULL DEFAULT 0,
    used REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS notices (id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe TEXT NOT NULL, kind TEXT NOT NULL,
    text TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', device TEXT, sent_at REAL,
    acked_at REAL, UNIQUE (dedupe));
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signal_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
    cost INTEGER NOT NULL DEFAULT 0);
"""


class Continuity:
    def __init__(self, home: Path, *, clock: Callable[[], float] = time.time,
                 quiet: Callable[[float], bool] | None = None) -> None:
        self.home, self.clock = Path(home), clock
        self.quiet = quiet or (lambda ts: False)       # the Core passes calm.quiet_hours_now
        self.path = self.home / DB_FILE
        self._lock = threading.RLock()
        self.home.mkdir(parents=True, exist_ok=True)
        with self._cx() as cx:
            cx.executescript(SCHEMA)

    def _cx(self) -> sqlite3.Connection:
        cx = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        cx.row_factory = sqlite3.Row
        return cx

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._cx() as cx:
            return [dict(r) for r in cx.execute(sql, args).fetchall()]

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self._cx() as cx:
            return cx.execute(sql, args).rowcount

    # ------------------------------------------------------------------ idempotent commands
    def execute(self, device: str, cid: str | None, kind: str, fn: Callable[[], tuple[int, dict]],
                *, age_s: float | None = None) -> tuple[int, dict, bool]:
        """Run `fn` (returns (status, body)) once per (device, cid). Returns (status, body, replayed).
        Without a cid the command simply runs. A resend gets the stored answer and does not run again. A command the
        client queued longer than REPLAY_WINDOW_S ago is refused with code STALE (and not stored: the user may resend)."""
        now = self.clock()
        if not cid:
            st, body = fn()
            return st, body, False
        with self._lock:
            hit = self._rows("SELECT status, body, ts FROM ledger WHERE device=? AND cid=?", (device, cid))
            if hit and now - hit[0]["ts"] <= LEDGER_TTL_S:
                logs.info("continuity", "COMMAND_REPLAYED", "a resent command was answered from the ledger",
                          kind=kind, device=device)
                return hit[0]["status"], json.loads(hit[0]["body"]), True
            if self.stale(age_s):
                logs.warn("continuity", "COMMAND_STALE", "a queued command was too old to run", kind=kind, device=device)
                return 410, {"error": "this message waited too long while offline: send it again if you still want it",
                             "code": "STALE"}, False
            st, body = fn()
            if st < 500:                                      # a server failure may be retried; a decision is final
                self._x("INSERT OR REPLACE INTO ledger (device, cid, kind, status, body, ts) VALUES (?,?,?,?,?,?)",
                        (device, cid, kind, int(st), json.dumps(body), now))
                self._x("DELETE FROM ledger WHERE ts < ?", (now - LEDGER_TTL_S,))
            return st, body, False

    def store(self, device: str, cid: str, kind: str, status: int, body: dict) -> None:
        """Remember an answer that a caller produced itself (chat keeps its own busy/rate rules)."""
        self._x("INSERT OR REPLACE INTO ledger (device, cid, kind, status, body, ts) VALUES (?,?,?,?,?,?)",
                (device, cid, kind, int(status), json.dumps(body), self.clock()))

    @staticmethod
    def stale(age_s) -> bool:
        try:
            return age_s is not None and float(age_s) > REPLAY_WINDOW_S
        except (TypeError, ValueError):
            return False

    def seen(self, device: str, cid: str, max_age: float = LEDGER_TTL_S) -> dict | None:
        r = self._rows("SELECT status, body, ts FROM ledger WHERE device=? AND cid=?", (device, cid))
        if r and self.clock() - r[0]["ts"] <= max_age:
            return json.loads(r[0]["body"])
        return None

    # ------------------------------------------------------------------ the shared conversation
    def add_turn(self, device: str, role: str, text: str, cid: str | None = None) -> int:
        """Append one turn (role user|assistant). The same (device, cid, role) is stored once."""
        text = str(text or "")[:4000]
        if logs.privacy():                       # privacy on: the journal keeps who spoke and when, never the words
            text = ""
        with self._lock:
            if cid:
                got = self._rows("SELECT seq FROM journal WHERE device=? AND cid=? AND role=?", (device, cid, role))
                if got:
                    return got[0]["seq"]
            with self._cx() as cx:
                cur = cx.execute("INSERT INTO journal (ts, device, role, text, cid) VALUES (?,?,?,?,?)",
                                 (self.clock(), device, role, text, cid))
                cx.execute("DELETE FROM journal WHERE seq <= ?", (cur.lastrowid - JOURNAL_KEEP,))
                return int(cur.lastrowid)

    def turns(self, since: int = 0, limit: int = 100) -> list[dict]:
        return self._rows("SELECT seq, ts, device, role, text FROM journal WHERE seq > ? ORDER BY seq LIMIT ?",
                          (int(since), int(limit)))

    def head(self) -> int:
        r = self._rows("SELECT MAX(seq) AS m FROM journal")
        return int(r[0]["m"] or 0)

    # ------------------------------------------------------------------ presence and routing
    def beat(self, device: str, kind: str = "phone", *, used: bool = False) -> None:
        """`beat`: the device is reachable (it polled). `used`: the owner just did something on it."""
        now = self.clock()
        with self._lock:
            self._x("INSERT OR IGNORE INTO presence (device, kind) VALUES (?,?)", (device, kind))
            self._x("UPDATE presence SET beat=?, kind=? WHERE device=?", (now, kind, device))
            if used:
                self._x("UPDATE presence SET used=? WHERE device=?", (now, device))

    def active_device(self, *, desktop_present: bool = False) -> str | None:
        """The ONE device that should get something now: the last one the owner used within ACTIVE_WINDOW_S
        (the desktop counts as used right now while `desktop_present`); else the desktop if it is reachable, else the
        phone that polled most recently; else None (nobody is there: it waits)."""
        now = self.clock()
        rows = self._rows("SELECT device, kind, beat, used FROM presence")
        used = {r["device"]: r["used"] for r in rows if r["used"] and now - r["used"] <= ACTIVE_WINDOW_S}
        if desktop_present:
            used["desktop"] = now                                  # the owner is at the PC right now
        if used:
            return max(used, key=lambda d: (used[d], d == "desktop"))
        live = [r for r in rows if r["beat"] and now - r["beat"] <= BEAT_WINDOW_S]
        live.sort(key=lambda r: (r["kind"] != "desktop", -r["beat"]))
        return live[0]["device"] if live else None

    def reachable(self, device: str) -> bool:
        r = self._rows("SELECT beat FROM presence WHERE device=?", (device,))
        return bool(r and r[0]["beat"] and self.clock() - r[0]["beat"] <= BEAT_WINDOW_S)

    # ------------------------------------------------------------------ notices (reminders and proactive items)
    def post_notice(self, dedupe: str, kind: str, text: str) -> int | None:
        """Queue a notice once per dedupe key (within DEDUPE_S). None = already known (nothing is created twice)."""
        now = self.clock()
        text = str(text or "")[:MAX_TEXT]
        with self._lock:
            self._x("DELETE FROM notices WHERE created < ?", (now - NOTICE_KEEP_S,))
            old = self._rows("SELECT id, created FROM notices WHERE dedupe=?", (dedupe,))
            if old:
                if now - old[0]["created"] <= DEDUPE_S:
                    return None
                self._x("DELETE FROM notices WHERE id=?", (old[0]["id"],))
            with self._cx() as cx:
                cur = cx.execute("INSERT INTO notices (dedupe, kind, text, created) VALUES (?,?,?,?)",
                                 (dedupe, kind, text, now))
                return int(cur.lastrowid)

    def route_now(self, dedupe: str, kind: str, text: str, *, desktop_present: bool) -> str | None:
        """For a reminder that fires NOW: returns the device that must announce it ("desktop", a phone id) or None
        (nobody reachable: the caller keeps its usual behaviour). The notice is recorded as delivered/queued so the
        other device never repeats it."""
        target = self.active_device(desktop_present=desktop_present)
        nid = self.post_notice(dedupe, kind, text)
        if nid is None:
            old = self._rows("SELECT device FROM notices WHERE dedupe=?", (dedupe,))
            return "desktop" if old and old[0]["device"] == "desktop" else "duplicate"   # a retry of a desktop ring
        if target is None:
            return None
        if target == "desktop":
            self._x("UPDATE notices SET state='acked', device='desktop', sent_at=?, acked_at=? WHERE id=?",
                    (self.clock(), self.clock(), nid))
        else:
            self._x("UPDATE notices SET device=? WHERE id=?", (target, nid))   # the phone fetches and acknowledges it
        logs.info("continuity", "NOTICE_ROUTED", "a reminder was routed to one device", kind=kind, device=target)
        return target

    def fetch_notices(self, device: str) -> list[dict]:
        """What THIS device should show now. A pending notice goes to the active device only; one already sent to this
        device and not yet acknowledged is returned again (the response may have been lost); other devices get nothing."""
        now = self.clock()
        with self._lock:
            rows = self._rows("SELECT id, kind, text, created, state, device, sent_at FROM notices "
                              "WHERE state IN ('pending','sent') ORDER BY id")
            out = []
            target = None
            for r in rows:
                if r["state"] == "sent" and r["device"] != device:
                    if r["sent_at"] and now - r["sent_at"] > SENT_TIMEOUT_S and not self.reachable(r["device"]):
                        self._x("UPDATE notices SET state='pending', device=NULL WHERE id=?", (r["id"],))
                        r["state"], r["device"] = "pending", None
                    else:
                        continue
                if r["state"] == "pending":
                    if r["device"] and r["device"] != device:
                        continue                                  # already routed to another device
                    if not r["device"]:
                        if target is None:
                            target = self.active_device() or device
                        if target != device:
                            continue
                    self._x("UPDATE notices SET state='sent', device=?, sent_at=? WHERE id=?", (device, now, r["id"]))
                out.append({"id": r["id"], "kind": r["kind"], "text": r["text"], "created": r["created"]})
            return out

    def ack_notice(self, device: str, nid: int) -> bool:
        n = self._x("UPDATE notices SET state='acked', acked_at=? WHERE id=? AND device=? AND state<>'acked'",
                    (self.clock(), int(nid), device))
        return bool(n)

    def notice_states(self) -> list[dict]:
        return self._rows("SELECT id, dedupe, kind, state, device FROM notices ORDER BY id")

    # ------------------------------------------------------------------ proactive signal gate
    def _kv(self, k: str, default: str = "") -> str:
        r = self._rows("SELECT v FROM kv WHERE k=?", (k,))
        return r[0]["v"] if r else default

    def _set(self, k: str, v) -> None:
        self._x("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (k, str(v)))

    def signals_state(self) -> dict:
        now = self.clock()
        snooze = float(self._kv("sig.snooze_until", "0") or 0)
        return {"on": self._kv("sig.on", "0") == "1", "stopped": self._kv("sig.stopped", "0") == "1",
                "snoozed_until": snooze if snooze > now else 0, "quiet_now": bool(self.quiet(now)),
                "hourly": int(self._kv("sig.hourly", str(DEFAULT_HOURLY))),
                "daily": int(self._kv("sig.daily", str(DEFAULT_DAILY))),
                "daily_cost_cap": int(self._kv("sig.cost_cap", "0")),
                "sent_hour": self._count(3600), "sent_day": self._count(86400)}

    def _count(self, window: float) -> int:
        return int(self._rows("SELECT COUNT(*) AS n FROM signal_log WHERE ts > ?", (self.clock() - window,))[0]["n"])

    def configure_signals(self, *, on: bool | None = None, hourly: int | None = None, daily: int | None = None,
                          cost_cap: int | None = None, by: str = "user") -> dict:
        if on is not None:
            self._set("sig.on", "1" if on else "0")
            if on:
                self._set("sig.stopped", "0")
        if hourly is not None:
            self._set("sig.hourly", max(0, min(20, int(hourly))))
        if daily is not None:
            self._set("sig.daily", max(0, min(50, int(daily))))
        if cost_cap is not None:
            self._set("sig.cost_cap", max(0, min(100, int(cost_cap))))
        logs.info("continuity", "SIGNALS_CONFIGURED", "proactive signal settings changed", by=by)
        return self.signals_state()

    def snooze(self, minutes: float, *, by: str = "user") -> float:
        until = self.clock() + max(1.0, min(float(minutes), SNOOZE_MAX_MIN)) * 60
        self._set("sig.snooze_until", until)
        logs.info("continuity", "SIGNALS_SNOOZED", "proactive signals snoozed", by=by, minutes=round(float(minutes)))
        return until

    def stop_signals(self, *, by: str = "user") -> None:
        """Stop: nothing proactive is posted until the owner switches signals on again."""
        self._set("sig.stopped", "1")
        self._x("UPDATE notices SET state='acked', acked_at=? WHERE kind='signal' AND state IN ('pending','sent')",
                (self.clock(),))
        logs.info("continuity", "SIGNALS_STOPPED", "proactive signals stopped", by=by)

    def emit(self, kind: str, ref: str, text: str, *, cost: int = 0) -> str:
        """An EVENT happened (scheduler, calendar, timer, goal). Returns the outcome: queued | off | stopped | snoozed |
        quiet | frequency | cap | duplicate. Nothing is executed and no model is called: the owner is only told."""
        s, now = self.signals_state(), self.clock()
        why = ("off" if not s["on"] else "stopped" if s["stopped"] else "snoozed" if s["snoozed_until"] else
               "quiet" if s["quiet_now"] else
               "frequency" if s["sent_hour"] >= s["hourly"] or s["sent_day"] >= s["daily"] else
               "cap" if cost and self._cost_today() + cost > s["daily_cost_cap"] else "")
        if why:
            logs.info("continuity", "SIGNAL_SUPPRESSED", "a proactive signal was held back", kind=kind, reason=why)
            return why
        nid = self.post_notice(f"signal:{kind}:{ref}", "signal", text)
        if nid is None:
            return "duplicate"
        self._x("INSERT INTO signal_log (ts, kind, cost) VALUES (?,?,?)", (now, kind, int(cost)))
        logs.info("continuity", "SIGNAL_QUEUED", "a proactive signal was queued for the active device", kind=kind, id=nid)
        return "queued"

    def _cost_today(self) -> int:
        return int(self._rows("SELECT COALESCE(SUM(cost),0) AS n FROM signal_log WHERE ts > ?",
                              (self.clock() - 86400,))[0]["n"])

    # event adapters ----------------------------------------------------------------------------------
    def on_goal_event(self, goal: dict, event: str) -> str:
        ref = f"{goal['id']}:{event}:{int(goal.get('updated') or 0)}"
        return self.emit("goal_" + event, ref, f"{goal['title']}: {event.replace('_', ' ')}")

    def on_calendar_event(self, ev_id: str, title: str, minutes_before: int) -> str:
        return self.emit("calendar", f"{ev_id}:{minutes_before}", f"{title} in {minutes_before} min")

    def on_timer_event(self, timer_id: int, text: str, *, desktop_present: bool) -> str | None:
        return self.route_now(f"timer:{timer_id}", "timer", text, desktop_present=desktop_present)

    def scan_proactive(self, proactive) -> int:
        """Scheduler tick: turn the (already computed, LLM-free) proactive items into signals. Returns how many queued."""
        n = 0
        for it in proactive.refresh():
            if it["state"] == "new" and self.emit(it["kind"], str(it["id"]), it["text"]) == "queued":
                n += 1
        return n


# ---------------------------------------------------------------------- dashboard (desktop = a device like the phone)
def _cont(ctx) -> Continuity:
    c = getattr(ctx, "_continuity", None)
    if c is None:
        from .userdata import ctx_db
        db = ctx_db(ctx)
        c = Continuity(ctx.home, quiet=lambda ts: calm.quiet_hours_now(db, ts))
        ctx._continuity = c
    return c


def api_continuity(ctx, m, body, q=None):
    c = _cont(ctx)
    c.beat("desktop", "desktop", used=True)
    return 200, {"signals": c.signals_state(), "notices": c.fetch_notices("desktop"), "head": c.head(),
                 "turns": c.turns(max(0, c.head() - 20))}


def api_continuity_op(ctx, m, body):
    c, b = _cont(ctx), body or {}
    op = b.get("op")
    try:
        if op == "config":
            return 200, c.configure_signals(on=b.get("on"), hourly=b.get("hourly"), daily=b.get("daily"),
                                            cost_cap=b.get("cost_cap"), by="dashboard")
        if op == "snooze":
            return 200, {"snoozed_until": c.snooze(float(b.get("minutes", 60)), by="dashboard")}
        if op == "stop":
            c.stop_signals(by="dashboard")
            return 200, c.signals_state()
        if op == "ack":
            return 200, {"ok": c.ack_notice("desktop", int(b.get("id", 0) or 0))}
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}
    return 400, {"error": "op must be config, snooze, stop or ack"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/continuity", api_continuity)
    register("POST", r"/api/continuity/op", api_continuity_op)
