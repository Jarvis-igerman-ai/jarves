"""`jarvis budget status | test-cap | set | reset-ladder` (SW-V4 D, #14): the delegation budget from the command line.

status       today's delegated API spend (used / held by live reservations of ALL processes / limit), the request
             counter, the live reservations, the delegation mode and the fallback-ladder step of the running Core.
test-cap     a FREE offline proof that the strict cap holds: a real LiveVoice + delegation engine + Budget on a temp
             database run random overlapping delegated turns against a local fake Responses API.
set          the caps and the fallback (config.toml through config_edit: validated, backed up) and the mode switch
             (the `client_delegation` feature). Same values as the dashboard Settings and Features pages.
reset-ladder after a client-delegation failure: try client delegation again from the next conversation.
"""
from __future__ import annotations

import asyncio
import json
import sys

from . import clilang


def _budget(cfg, home):
    from .budget import Budget
    from .memory import MemoryStore
    return Budget(MemoryStore(home / "memory.db"), cfg.ai)


def status_view(cfg, home) -> dict:
    from .voice.delegation_client import read_state, usd_status
    b = _budget(cfg, home)
    try:
        out = usd_status(b)
        from . import limits as _limits
        out["limits"] = _limits.views_from(b.used, b.limit)
        out["limit_lines"] = _limits.status_lines(out["limits"])
    finally:
        b.close()
        b.memory.close()          # Budget does not own its MemoryStore: a leaked handle kept memory.db locked (Windows)
    st = read_state(home)
    try:
        from .cli import _cli_features
        mode = "client" if _cli_features(cfg, home).enabled("client_delegation") else "server"
    except Exception:
        mode = cfg.ai.delegation_mode
    step = "server" if mode != "client" else (st.get("step") or "client")
    out.update(mode=mode, step=step, ladder_reason=st.get("reason", ""), in_flight=st.get("in_flight") or [],
               counters=st.get("counters") or {}, fallback=cfg.ai.delegation_fallback,
               caps={"daily_usd": cfg.ai.delegation_daily_usd, "turn_usd": cfg.ai.delegation_turn_usd,
                     "turn_requests": cfg.ai.delegation_turn_requests, "max_searches": cfg.ai.delegation_max_searches,
                     "timeout_s": cfg.ai.delegation_timeout_s})
    return out


def cmd_budget(a, cfg, home) -> int:
    if a.action == "status":
        v = status_view(cfg, home)
        if a.json:
            print(json.dumps(v, ensure_ascii=False, indent=1))
            return 0
        print(clilang.fmt(f"מצב העברה: {v['mode']} (בפועל: {v['step']})", f"delegation mode: {v['mode']} (in use: {v['step']})"))
        if v["ladder_reason"]:
            print(clilang.fmt(f"  ירד בסולם הגיבוי: {v['ladder_reason']}", f"  fallback ladder stepped down: {v['ladder_reason']}"))
        print(clilang.fmt(f"חיפושים היום: ${v['used_usd']:.4f} נוצלו, ${v['held_usd']:.4f} שמורים כעת, תקרה ${v['limit_usd']:.2f}, "
                          f"נשאר ${v['remaining_usd']:.4f}",
                          f"delegated lookups today: ${v['used_usd']:.4f} used, ${v['held_usd']:.4f} held now, cap "
                          f"${v['limit_usd']:.2f}, ${v['remaining_usd']:.4f} left"))
        for ln in v["limit_lines"]:
            print(ln)
        print(clilang.fmt(f"בקשות AI היום: {v['requests_today']} מתוך {v['requests_limit']}",
                          f"AI requests today: {v['requests_today']} of {v['requests_limit']}"))
        c = v["caps"]
        print(clilang.fmt(f"לשאלה: עד ${c['turn_usd']:.2f}, {c['turn_requests']} בקשות, {c['max_searches']} חיפושים לבקשה, "
                          f"{c['timeout_s']:g} שנ׳; גיבוי: {v['fallback']}",
                          f"per question: up to ${c['turn_usd']:.2f}, {c['turn_requests']} requests, {c['max_searches']} "
                          f"searches per request, {c['timeout_s']:g} s; fallback: {v['fallback']}"))
        for r in v["reservations"]:
            n = f"${r['n'] / 1e6:.4f}" if r["kind"] == "delegation_usd_micros" else str(r["n"])
            print(f"  reservation {r['id']} {r['kind']:<22} {n:>10}  pid {r['pid']}  {r['age_s']:.0f} s")
        if not v["reservations"]:
            print(clilang.fmt("  (אין הזמנות פתוחות)", "  (no open reservations)"))
        return 0
    if a.action == "test-cap":
        from .voice.delegation_selftest import run_cap_test
        r = asyncio.run(run_cap_test(cap_usd=a.cap, turn_usd=min(a.turn_usd, a.cap), turns=a.turns, seed=a.seed,
                                     schedules=a.schedules))
        print(clilang.fmt(
            f"בדיקת תקרה (חינם, מקומית): {r['schedules']} תרחישים × {r['turns']} שאלות חופפות, תקרה ${r['cap_usd']:.2f}: "
            f"הוצאה מקסימלית ${r['worst_spent_usd']:.4f}, נחסמו לפני שליחה {r['refused_before_sending']}, "
            f"הזמנות פתוחות {r['unsettled']} -> {'עבר' if r['ok'] else 'נכשל'}",
            f"cap test (free, offline): {r['schedules']} schedules x {r['turns']} overlapping questions, cap "
            f"${r['cap_usd']:.2f}: worst spend ${r['worst_spent_usd']:.4f}, refused before sending "
            f"{r['refused_before_sending']}, open reservations {r['unsettled']} -> {'PASS' if r['ok'] else 'FAIL'}"))
        return 0 if r["ok"] else 1
    if a.action == "set":
        from . import config_edit
        upd = {k: v for k, v in (("ai.daily_realtime_minutes", a.voice_minutes), ("ai.daily_request_limit", a.requests),
                                 ("ai.delegation_daily_usd", a.daily_usd), ("ai.delegation_turn_usd", a.turn_usd),
                                 ("ai.delegation_turn_requests", a.turn_requests),
                                 ("ai.delegation_max_searches", a.searches), ("ai.delegation_timeout_s", a.timeout),
                                 ("ai.delegation_fallback", a.fallback)) if v is not None}
        if upd:
            try:
                config_edit.apply(getattr(a, "config", None) or home / "config.toml", upd)
            except Exception as e:                 # invalid value / unreadable file: nothing written
                print(f"budget: {e}", file=sys.stderr)
                return 2
            print(clilang.fmt(f"נשמר: {', '.join(upd)} (חל מההפעלה הבאה)", f"saved: {', '.join(upd)} (applies at the next start)"))
        if a.mode:
            from .cli import _cli_features
            f = _cli_features(cfg, home)
            f.set("client_delegation", a.mode == "client", by="cli")
            print(clilang.fmt(f"מצב העברה: {a.mode} (מהשיחה הבאה)", f"delegation mode: {a.mode} (from the next conversation)"))
        if not upd and not a.mode:
            print(clilang.fmt("לא ניתן מה לשנות (ראו --help)", "nothing to change (see --help)"), file=sys.stderr)
            return 2
        return 0
    if a.action == "reset-ladder":
        from .voice.delegation_client import STATE_FILE, read_state
        st = read_state(home)
        st.update(step="client", reason="", reset_by="cli", changed=__import__("time").time())
        (home / STATE_FILE).write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        print(clilang.fmt("סולם הגיבוי אופס: השיחה הבאה מנסה שוב העברה בצד הלקוח",
                          "fallback ladder reset: the next conversation tries client delegation again"))
        return 0
    return 2


def add_cli(sub) -> None:
    b = sub.add_parser("budget", help="delegation budget: status / test-cap / set / reset-ladder (strict cap for "
                                      "spoken lookups)")
    bs = b.add_subparsers(dest="action", required=True)
    st = bs.add_parser("status", help="today's delegated spend, live reservations, mode and fallback step")
    st.add_argument("--json", action="store_true")
    tc = bs.add_parser("test-cap", help="free offline proof that the strict cap holds under overlapping questions")
    tc.add_argument("--cap", type=float, default=0.10, help="daily cap for the test in USD (default 0.10)")
    tc.add_argument("--turn-usd", type=float, default=0.03, help="per-question cap for the test (default 0.03)")
    tc.add_argument("--turns", type=int, default=30)
    tc.add_argument("--schedules", type=int, default=5)
    tc.add_argument("--seed", type=int, default=7)
    se = bs.add_parser("set", help="caps / fallback / mode (same as the dashboard Settings + Features pages)")
    se.add_argument("--voice-minutes", type=int, help="live-voice minutes per day (ai.daily_realtime_minutes)")
    se.add_argument("--requests", type=int, help="AI requests per day (ai.daily_request_limit)")
    se.add_argument("--daily-usd", type=float, help="strict daily cap for spoken lookups (USD)")
    se.add_argument("--turn-usd", type=float, help="strict cap for one spoken question (USD)")
    se.add_argument("--turn-requests", type=int, help="model requests for one spoken question")
    se.add_argument("--searches", type=int, help="web searches per request")
    se.add_argument("--timeout", type=float, help="stop a spoken lookup after this many seconds")
    se.add_argument("--fallback", choices=["local", "server"], help="if client delegation fails")
    se.add_argument("--mode", choices=["client", "server"], help="client = strict cap (feature client_delegation)")
    bs.add_parser("reset-ladder", help="after a client-delegation failure: try client mode again")


COMMANDS = {"budget": cmd_budget}
