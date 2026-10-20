"""SW-V4 D (#14) live voice scenarios 51-62: client-mode GPT-Live delegation with the strict budget, on the REAL
GPT-Live with the virtual mic/speaker of suite_voice.py (silent, headless). Registered into suite_v3_voice.py:

    uv --directory core run --no-sync python tools/suite_v3_voice.py --group delegation [--max-minutes 30]

Every Core is a TEST-profile temp home (suite_v3_voice.write_cfg) with the `client_delegation` switch on. The API key
comes from the owner's environment and is never printed. Fake backends (network drop, 30 s slow backend, plan 429)
are local servers (tests/openai_fakes.py); GPT-Live stays real. Combined-mode scenario 56 uses a COPY of the owner's
ChatGPT sign-in whose refresh is disabled (the owner's rotating refresh token is never used by the copy).

Also: `python tools/suite_v4_delegation.py --latency 10` measures the added latency of client vs server delegation
(question end -> start of the spoken answer, p50/p95) and writes it to docs/reviews/SWV4_D_DELEGATION_LATENCY.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import suite_voice as sv  # noqa: E402
from suite_voice import has, scenario, stamp, wait_for  # noqa: E402

GROUP = "delegation"
NUMBERS = list(range(51, 63))
CLIENT = {"ai": {"delegation_mode": "client", "delegation_daily_usd": 1.0, "delegation_turn_usd": 0.05},
          "_features": {"client_delegation": True, "web_search": True}}


def _over(**ai) -> dict:
    o = json.loads(json.dumps(CLIENT))
    o["ai"].update(ai)
    return o


def usd_used(home: Path) -> float:
    try:
        db = sqlite3.connect(f"file:{home / 'memory.db'}?mode=ro", uri=True)
        r = db.execute("SELECT count FROM usage WHERE day = ? AND kind = 'delegation_usd_micros'",
                       (date.today().isoformat(),)).fetchone()
        held = db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        db.close()
        return (r[0] if r else 0) / 1e6, held
    except sqlite3.Error:
        return 0.0, -1


def deleg(s, code, mark=0):
    return s.log.codes(code, mark)


def said_since(s, mark) -> str:
    return " / ".join(e.get("text", "") for e in s.log.since(mark) if e["code"] == "LIVE_JARVES_SAID")


async def settle(h, s, sec=2.5, timeout=40):
    await asyncio.sleep(1.0)
    await h.quiet(s, sec, timeout)


# ------------------------------------------------------------------------------------------------ 51-55 key route
@scenario(51, "deleg_simple_lookup", "Client delegation: a simple lookup is reserved before sending, answered, settled",
          "DELEG_MODE client; DELEG_START + DELEG_DONE; answer spoken (>= 0.3 s); delegated USD > 0; 0 open reservations")
async def s51(h, r):
    s = await h.get("deleg", over=_over())
    mark = s.log.mark()
    row = await h.turn(s, "מה מזג האוויר היום בתל אביב?", label="weather")
    await settle(h, s)
    used, held = usd_used(s.home)
    mode = deleg(s, "DELEG_MODE")
    done = deleg(s, "DELEG_DONE", mark)
    r.ok = bool(mode) and bool(done) and sv.answered(row) and used > 0 and held == 0
    r.key = (f"mode {bool(mode)}, done {len(done)}, deleg_s {[d.get('deleg_s') for d in done]}, spent ${used:.4f}, "
             f"open {held}; said: {row['said'][:100]}")
    r.details = [row]


@scenario(52, "deleg_web_search", "Client delegation with hosted web search: a fresh fact",
          "a web search ran (LIVE_BACKEND_WEB_SEARCH*); answer spoken with a number or name; DELEG_DONE")
async def s52(h, r):
    s = await h.get("deleg", over=_over())
    mark = s.log.mark()
    row = await h.turn(s, "מה שער הדולר מול השקל היום?", label="usd")
    await settle(h, s)
    ws = [e for e in s.log.since(mark) if str(e.get("code", "")).startswith("LIVE_BACKEND_WEB_SEARCH")]
    done = deleg(s, "DELEG_DONE", mark)
    r.ok = bool(ws) and bool(done) and sv.answered(row) and any(ch.isdigit() for ch in row["said"] + row["heard"]) or (
        bool(ws) and bool(done) and has(row["said"], "שקל", "שקלים", "דולר"))
    r.key = f"web search events {len(ws)}, done {len(done)}; said: {row['said'][:120]}"
    r.details = [row]


@scenario(53, "deleg_two_overlapping", "Two questions in a row before the first answer: both answered, no double "
          "billing, nothing left reserved",
          "both topics in what JARVES said; every DELEG_START ends in DELEG_DONE or DELEG_CANCELLED; 0 open reservations")
async def s53(h, r):
    over = _over()
    over["voice"] = {"half_duplex": False}           # overlapping speech needs an open mic (half duplex cuts it)
    s = await h.get("deleg_fd", over=over)
    mark = s.log.mark()
    await h.say("מה מזג האוויר עכשיו בחיפה?")
    await asyncio.sleep(1.2)
    row = await h.turn(s, "וגם, מה השעה עכשיו בניו יורק?", label="overlap")
    await settle(h, s, 3.0, 60)
    said = said_since(s, mark)
    starts, done, canc = (len(deleg(s, c, mark)) for c in ("DELEG_START", "DELEG_DONE", "DELEG_CANCELLED"))
    used, held = usd_used(s.home)
    # both ANSWERED in what JARVES said (not only named: "ובניו יורק לבדוק גם?" is a question back, not an answer)
    r.ok = (has(said, "חיפה") and has(said, "מעלות") and has(said, "יורק")
            and has(said, "בבוקר", "בערב", "בצהריים", "אחר הצהריים", "בלילה", "השעה", "שעה")
            and starts >= 1 and starts == done + canc and held == 0)
    r.key = f"starts {starts}, done {done}, cancelled {canc}, open {held}; said: {said[:160]}"
    r.details = [row]


@scenario(54, "deleg_barge_in_cancel", "Barge-in during a lookup: 'עזוב' stops it; the request is cancelled and its "
          "reservation released (counted only if sent)",
          "DELEG_CANCELLED why user_cancel; 0 open reservations; no answer for the cancelled lookup spoken afterwards")
async def s54(h, r):
    over = _over()
    over["voice"] = {"half_duplex": False}           # barge-in needs an open mic while JARVES speaks (headphones)
    # a deterministic 10 s backend (local server; GPT-Live stays real): the lookup is surely still running at "עזוב"
    # (the real backend answered in 2.7 s once, before the user could say it)
    s, srv = await _with_fake_backend(h, "deleg_barge", [{"delay_s": 10, "text": "late"}], over=over)
    mark = s.log.mark()
    await h.say("מה מחיר מניית אנבידיה עכשיו, ומה קרה לה השבוע?")
    await wait_for(lambda: bool(deleg(s, "DELEG_START", mark)), 15)
    await asyncio.sleep(0.3)
    await h.say("עזוב, לא משנה")
    await wait_for(lambda: bool(deleg(s, "DELEG_CANCELLED", mark)) or bool(deleg(s, "DELEG_DONE", mark)), 20)
    await settle(h, s, 3.0, 30)
    canc = deleg(s, "DELEG_CANCELLED", mark)
    used, held = usd_used(s.home)
    redo = [e for e in s.log.since(mark) if e["code"] == "LIVE_REDELEGATED"]
    r.ok = any(c.get("why") == "user_cancel" for c in canc) and held == 0 and not redo
    r.key = f"cancelled {[c.get('why') for c in canc]}, done {len(deleg(s, 'DELEG_DONE', mark))}, redelegated {len(redo)}, open {held}, counted ${used:.4f}; said: {said_since(s, mark)[:140]}"
    await h.shutdown(s)
    srv.close()


@scenario(55, "deleg_cap_mid_conversation", "Daily delegation cap reached mid-conversation: the request is NEVER sent "
          "and JARVES says so honestly",
          "first lookup answered; a later one refused (DELEG_CAP, no LIVE_BACKEND_START after it); spoken honestly "
          "(תקציב/budget); spend <= cap + 0")
async def s55(h, r):
    s = await h.get("deleg_cap", over=_over(delegation_max_searches=2))
    mark = s.log.mark()
    first = await h.turn(s, "מה מזג האוויר היום באילת?", label="cap-1")
    await settle(h, s)
    used0, _ = usd_used(s.home)
    cap = round(used0 + 0.01, 4)                     # mid-conversation: less than one more worst-case request
    s.core.cfg.ai.delegation_daily_usd = cap         # the shared Budget reads the cap live
    mark2 = s.log.mark()
    rows = [first] + [await h.turn(s, q, label="cap") for q in ("ומה החדשות הגדולות היום בעולם?",
                                                                 "ומה מזג האוויר בירושלים?")]
    await settle(h, s)
    refused = deleg(s, "DELEG_CAP", mark2)
    sent_after = [e for e in s.log.since(mark2) if e["code"] == "LIVE_BACKEND_START"]
    used, held = usd_used(s.home)
    said = " / ".join(x["said"] for x in rows[1:])
    r.ok = (bool(deleg(s, "DELEG_DONE", mark)) and sv.answered(first) and len(refused) >= 1 and not sent_after
            and used <= cap and held == 0 and has(said, "תקציב", "budget", "מגבלה", "תקרה"))
    r.key = (f"first answered (${used0:.4f}); cap set to ${cap:.4f}; refused {len(refused)}, sent after "
             f"{len(sent_after)}, spent ${used:.4f} <= cap {used <= cap}, open {held}; said: {said[:160]}")
    r.details = rows

# ------------------------------------------------------------------------------------------------ 56-57 plan route
async def _prepare_plan_copy(home: Path) -> str:
    from jarves.chatgpt_auth import ChatGPTAuth
    real = ChatGPTAuth(sv.REAL)
    if not real.status()["signed_in"]:
        return "BLOCKED: the owner is not signed in to ChatGPT"
    await real.access_token()                        # refresh (if due) in the REAL home, under its lock
    (home / "chatgpt").mkdir(exist_ok=True)
    acct = real.load()
    acct = {**acct, "refresh_token": "copy-cannot-refresh", "expires_at": acct.get("expires_at", 0)}
    from jarves.chatgpt_auth import _write_private
    _write_private(home / "chatgpt" / "account.json", acct)
    for f in ("models.json", "host_id"):
        if (sv.REAL / "chatgpt" / f).exists():
            shutil.copy2(sv.REAL / "chatgpt" / f, home / "chatgpt" / f)
    return ""


@scenario(56, "deleg_plan_combined", "Combined mode: GPT-Live voice on the key, the delegated reasoning on the ChatGPT "
          "plan (official OAuth route): API cost $0 for the reasoning",
          "DELEG_MODE route chatgpt_plan; answer spoken; CHATGPT_BACKEND_DONE; delegated API USD == 0; no LIVE_BACKEND_START")
async def s56(h, r):
    over = _over()
    over["ai"]["connection"] = "combined"
    import jarves.app as app_mod
    orig = app_mod.Core.start
    note = {"v": ""}

    async def start(core, *a, **k):                  # the copy must exist before the Core reads its sign-in
        note["v"] = await _prepare_plan_copy(core.home)
        return await orig(core, *a, **k)
    app_mod.Core.start = start
    try:
        s = await h.boot("deleg_plan", over=over)
    finally:
        app_mod.Core.start = orig
    if note["v"]:
        r.ok, r.key = False, note["v"]
        return
    mark = s.log.mark()
    row = await h.turn(s, "מה מזג האוויר היום בתל אביב?", label="plan")     # needs a lookup: really delegated
    await settle(h, s)
    mode = deleg(s, "DELEG_MODE")
    plan = [e for e in s.log.since(mark) if e["code"] == "CHATGPT_BACKEND_DONE"]
    api = [e for e in s.log.since(mark) if e["code"] == "LIVE_BACKEND_START"]
    used, held = usd_used(s.home)
    r.ok = (bool(mode) and mode[-1].get("route") == "chatgpt_plan" and sv.answered(row) and bool(plan) and not api
            and bool(deleg(s, "DELEG_DONE", mark))
            and used == 0 and held == 0)
    r.key = f"route {mode[-1].get('route') if mode else None}, plan responses {len(plan)}, API backend {len(api)}, API USD ${used:.4f}; said: {row['said'][:120]}"
    r.details = [row]
    await h.shutdown(s)


class _PlanStub:
    def status(self):
        return {"signed_in": True, "plan_usage": True, "email": "test@example.invalid"}

    async def access_token(self):
        return "fake-plan-token"

    def models(self):
        return [{"slug": "gpt-6.1-sol", "display_name": "x"}]

    def save_models(self, m):
        pass


@scenario(57, "deleg_plan_limit", "Plan limit reached (fake 429 from the plan endpoint): AI lookups pause honestly and "
          "NEVER move to the paid API",
          "DELEG_FAILED CHATGPT_LIMIT; spoken honestly; no LIVE_BACKEND_START (API); delegated API USD == 0")
async def s57(h, r):
    import httpx
    over = _over()
    over["ai"]["connection"] = "combined"
    s = await h.boot("deleg_plan_limit", over=over)
    hits = []

    def handler(req):
        hits.append(req.url.path)
        return httpx.Response(429, json={"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}})
    s.core.agent.auth = _PlanStub()
    s.core.agent._transport = httpx.MockTransport(handler)
    s.core.agent.model = "gpt-6.1-sol"
    mark = s.log.mark()
    row = await h.turn(s, "מה החדשות היום?", label="plan429")
    await settle(h, s)
    fail = [e for e in deleg(s, "DELEG_FAILED", mark) if e.get("err_code") == "CHATGPT_LIMIT"]
    api = [e for e in s.log.since(mark) if e["code"] == "LIVE_BACKEND_START"]
    used, _ = usd_used(s.home)
    r.ok = bool(fail) and not api and used == 0 and bool(hits) and has(row["said"], "מנוי", "ChatGPT", "מגבלה", "תוכנית",
                                                                         "צ'אט", "לא יכול", "מוגבל")
    r.key = f"plan 429 hits {len(hits)}, limit events {len(fail)}, API backend {len(api)}, API USD ${used:.4f}; said: {row['said'][:140]}"
    r.details = [row]
    await h.shutdown(s)


# ------------------------------------------------------------------------------------------------ 58-59 fake backend
async def _with_fake_backend(h, name, scripts, over=None):
    from openai_fakes import ResponsesHTTPServer
    import jarves.app as app_mod
    srv = ResponsesHTTPServer(scripts)
    app_mod.Core._delegation_client = srv.client()
    try:
        s = await h.boot(name, over=over or _over())
    finally:
        del app_mod.Core._delegation_client
    return s, srv


@scenario(58, "deleg_network_drop", "Network drop mid-request (local backend closes the stream): honest failure, "
          "counted once, never retried behind the budget's back",
          "DELEG_FAILED; exactly 1 backend request; spoken honest failure; 0 open reservations")
async def s58(h, r):
    s, srv = await _with_fake_backend(h, "deleg_drop", [{"drop": True, "searches": 1}])
    try:
        mark = s.log.mark()
        row = await h.turn(s, "מה קרה היום בבורסה בתל אביב?", label="drop")
        await settle(h, s)
        fail = deleg(s, "DELEG_FAILED", mark)
        used, held = usd_used(s.home)
        r.ok = bool(fail) and len(srv.bodies) == 1 and held == 0 and has(row["said"], "לא הצלחתי", "לא מצליח", "תקלה",
                                                                         "לא יכול", "בעיה", "couldn't")
        r.key = f"failed {len(fail)}, requests {len(srv.bodies)}, open {held}, counted ${used:.4f}; said: {row['said'][:140]}"
        r.details = [row]
    finally:
        await h.shutdown(s)
        srv.close()


@scenario(59, "deleg_slow_backend_30s", "A 30 s slow backend: a progress note meanwhile, then cancelled at the 25 s "
          "limit with an honest answer",
          "DELEG_TIMEOUT; a spoken 'still checking' before it; the honest 'לא הצלחתי להביא את זה בזמן'; 0 open")
async def s59(h, r):
    s, srv = await _with_fake_backend(h, "deleg_slow", [{"delay_s": 30, "text": "late"}])
    try:
        mark = s.log.mark()
        await h.say("תבדוק לי מה מזג האוויר בפריז עכשיו")
        await wait_for(lambda: bool(deleg(s, "DELEG_TIMEOUT", mark)), 45)
        await settle(h, s, 3.0, 30)
        said = said_since(s, mark)
        used, held = usd_used(s.home)
        to = deleg(s, "DELEG_TIMEOUT", mark)
        r.ok = bool(to) and held == 0 and has(said, "בודק", "רגע", "שנייה", "לוקח") and has(
            said, "לא הצלחתי", "בזמן", "לנסות שוב", "לא הספקתי")
        r.key = f"timeout {len(to)}, open {held}, counted ${used:.4f}; said: {said[:200]}"
    finally:
        await h.shutdown(s)
        srv.close()


# ------------------------------------------------------------------------------------------------ 60-62
@scenario(60, "deleg_tool_call", "A Skill tool call inside a delegated turn (remember) runs in the Core; its result is "
          "spoken; the continuation is reserved before it is sent",
          "LIVE_TOOL_CALL remember; memory saved; >= 2 backend requests in the turn; spoken confirmation")
async def s60(h, r):
    s = await h.get("deleg", over=_over())
    mark = s.log.mark()
    row = await h.turn(s, "תזכור בבקשה שמספר החניה שלי הוא שבע", label="remember")
    await settle(h, s)
    tools = [e.get("tool") or (e["msg"].replace("backend called ", "")) for e in s.log.since(mark) if e["code"] == "LIVE_TOOL_CALL"]
    saved = [m["text"] for m in s.core.memory.list(50) if has(m["text"], "חניה", "שבע", "7")]
    done = deleg(s, "DELEG_DONE", mark)
    r.ok = "remember" in tools and bool(saved) and any((d.get("requests") or 0) >= 2 for d in done) and sv.answered(row)
    r.key = f"tools {tools}, saved {saved[:1]}, requests {[d.get('requests') for d in done]}; said: {row['said'][:100]}"
    r.details = [row]


@scenario(61, "deleg_hebrew_english", "Hebrew and English through client delegation: each answered in its language",
          "both answered; the English answer is mostly Latin letters, the Hebrew one mostly Hebrew")
async def s61(h, r):
    s = await h.get("deleg", over=_over())
    he = await h.turn(s, "מה בירת אוסטרליה ומה מזג האוויר שם היום?", label="he")
    en = await h.turn(s, "What is the weather in London right now?", lang="en", label="en")
    await settle(h, s)
    he_r, en_r = sv.lang_ratio(he["said"]), sv.lang_ratio(en["said"])
    r.ok = sv.answered(he) and sv.answered(en) and he_r[0] > he_r[1] and en_r[1] > en_r[0]
    r.key = f"he {he_r}, en {en_r}; said he: {he['said'][:70]} | en: {en['said'][:70]}"
    r.details = [he, en]


@scenario(62, "deleg_cost_reconciliation", "JARVES's delegated-cost estimate vs OpenAI's usage report (Costs/Usage API,"
          " needs the read-only admin key OD-05)",
          "with OPENAI_ADMIN_KEY: |JARVES - OpenAI| <= 10% over the suite's delegated requests; without it: BLOCKED (OD-05)")
async def s62(h, r):
    s = await h.get("deleg", over=_over())
    used, held = usd_used(s.home)
    reserved = sum(float(e.get("reserved_usd") or 0) for e in s.log.codes("DELEG_SETTLED"))
    spent = sum(float(e.get("spent_usd") or 0) for e in s.log.codes("DELEG_DONE"))
    if not os.environ.get("OPENAI_ADMIN_KEY"):
        r.ok = False
        r.key = (f"BLOCKED (OD-05: no read-only admin key for the Usage API). JARVES side: settled ${used:.4f} "
                 f"(reserved worst case ${reserved:.4f}, answered turns ${spent:.4f}); every settled value is the "
                 "provider's per-response usage x [pricing]")
        r.details = [{"settled_usd": used, "reserved_usd": reserved, "open": held}]
        return
    from jarves import usage
    rep = await usage.fetch_costs_today() if hasattr(usage, "fetch_costs_today") else None
    r.ok = False
    r.key = f"admin key present but no reconciliation helper in usage.py ({rep}); JARVES settled ${used:.4f}"


sv.GROUPS[GROUP] = "51-62"
sv.LIVE_COST.update({51: 1.0, 52: 1.0, 53: 1.5, 54: 1.0, 55: 2.0, 56: 1.5, 57: 1.0, 58: 1.0, 59: 1.5, 60: 1.0,
                     61: 1.5, 62: 0})


# ------------------------------------------------------------------------------------------------ latency
async def measure_latency(n: int, max_minutes: float) -> dict:
    """Question end -> start of the spoken ANSWER (the first assistant transcript after the backend result), client
    vs server delegation, the same questions in both modes."""
    import suite_v3_voice  # noqa: F401  (TEST-profile homes)
    from jarves.config import load_secrets
    load_secrets(sv.REAL)
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("no OPENAI_API_KEY")

    class A:
        pass
    args = A()
    args.max_minutes = max_minutes
    h = sv.Harness(args)
    meter = asyncio.create_task(h.meter())
    qs = ["מה מזג האוויר עכשיו בתל אביב?", "מה שער הדולר היום?", "מי זכה אתמול במשחק של מכבי תל אביב?",
          "מה מזג האוויר בירושלים מחר?", "מה המחיר של מניית אינטל עכשיו?", "מה מזג האוויר עכשיו בלונדון?",
          "מתי השקיעה היום בתל אביב?", "מה החדשות הגדולות היום?", "מה מזג האוויר עכשיו בחיפה?",
          "כמה עולה ביטקוין עכשיו?"][:n]
    out = {}
    for mode in ("server", "client"):
        over = _over() if mode == "client" else {"_features": {"web_search": True}}
        s = await h.boot(f"lat_{mode}", over=over)
        lats = []
        for q in qs:
            if h.minutes_left() < 0.5:
                break
            mark = s.log.mark()
            row = await h.turn(s, q, label=mode)
            await sv.Harness.quiet(h, s, 2.5, 40)
            ev = s.log.since(mark)
            done_code = "DELEG_DONE" if mode == "client" else "LIVE_BACKEND_DONE"
            done = [e for e in ev if e["code"] == done_code]
            last_in = [e for e in ev if e["code"] == "LIVE_EVENT" and (e.get("event") or e.get("msg")) == "session.input_transcript.delta"]
            if not done or not last_in:
                lats.append({"q": q, "skipped": "no delegation"})
                continue
            t_done = stamp(done[-1])
            q_end = max(stamp(e) for e in last_in if stamp(e) <= t_done)
            ans = [stamp(e) for e in ev if e["code"] == "LIVE_EVENT" and (e.get("event") or e.get("msg")) == "session.output_transcript.delta"
                   and stamp(e) >= t_done]
            lats.append({"q": q, "answer_after_s": round((ans[0] if ans else t_done) - q_end, 2),
                         "backend_s": round(t_done - q_end, 2), "dead_air_s": row["dead_air_s"]})
        await h.shutdown(s)
        vals = sorted(x["answer_after_s"] for x in lats if "answer_after_s" in x)
        out[mode] = {"runs": lats, "n": len(vals),
                     "p50": vals[len(vals) // 2] if vals else None,
                     "p95": vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))] if vals else None}
    meter.cancel()
    out["added_p50_s"] = (round(out["client"]["p50"] - out["server"]["p50"], 2)
                          if out["client"]["p50"] is not None and out["server"]["p50"] is not None else None)
    out["added_p95_s"] = (round(out["client"]["p95"] - out["server"]["p95"], 2)
                          if out["client"]["p95"] is not None and out["server"]["p95"] is not None else None)
    out["voice_minutes"] = round(h.voice_wall_s / 60, 1)
    out["measured"] = time.strftime("%Y-%m-%d %H:%M")
    dst = sv.REPO / "docs" / "reviews" / "SWV4_D_DELEGATION_LATENCY.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("server", "client")}, indent=1),
          {m: (out[m]["p50"], out[m]["p95"], out[m]["n"]) for m in ("server", "client")})
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency", type=int, default=10)
    ap.add_argument("--max-minutes", type=float, default=12)
    a = ap.parse_args()
    asyncio.run(measure_latency(a.latency, a.max_minutes))
