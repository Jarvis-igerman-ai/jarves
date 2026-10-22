"""SW-V4 D (#14): client-mode GPT-Live delegation with a strict reserve-before-send budget.

Real pieces: LiveVoice event handling, ClientDelegation, Budget (SQLite, cross-process reservations), the real openai
SDK against a local HTTP Responses server (openai_fakes.ResponsesHTTPServer), the ChatGPT-plan brain against its mock
transport, the Core (features, health, snapshot, dashboard API, CLI). Fakes only for OpenAI's servers. Offline, free,
silent. Waits are event-driven (conftest.until)."""
from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from pathlib import Path

import httpx
import pytest

from conftest import until
from openai_fakes import FakeLiveServer, ResponsesHTTPServer
from jarves import budget as budget_mod
from jarves.budget import MICRO, USD_KIND, Budget, BudgetExceeded
from jarves.config import Config
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import ClockSkill
from jarves.voice import delegation_client as dc
from jarves.voice import delegation_selftest as ds
from jarves.voice.live import LiveVoice

FIXTURE = Path(__file__).parent / "fixtures" / "live_client_delegation_events.json"


def make_budget(tmp_path, **ai):
    cfg = Config()
    cfg.ai.daily_request_limit = 1000
    for k, v in ai.items():
        setattr(cfg.ai, k, v)
    return cfg, Budget(MemoryStore(tmp_path / "m.db"), cfg.ai)


def make_voice(tmp_path, cfg, budget, *, client=None, http=None, route="api_key", plan_brain=None, registry=None,
               live_server=None, home=None):
    state = dc.DelegationState(home, cfg.ai)
    engine = dc.ClientDelegation(cfg.ai, cfg.pricing, budget, state, route=route, plan_brain=plan_brain,
                                 client=client or (http.client() if http else None),
                                 max_output_tokens=cfg.ai.voice_max_output_tokens,
                                 service_tier=cfg.ai.voice_service_tier)
    reg = registry or SkillRegistry()
    lv = LiveVoice(reg, lambda: "You are the JARVES backend.", delegation_mode="client", delegation=engine,
                   budget=budget, client=live_server, start_timeout_s=10)
    return lv, engine, state


async def started(lv, srv):
    await lv.start()
    return srv.conn


async def stop(lv):
    await lv.stop()
    await ds.stop_tasks(lv)


def commentary(conn, did=None):
    return [p for k, p in conn.sent if k == "commentary" and (did is None or p["delegation_id"] == did)]


# ------------------------------------------------------------------------------------------------ budget primitives
def test_reserve_many_is_all_or_nothing(tmp_path):
    cfg, b = make_budget(tmp_path, delegation_daily_usd=0.01, daily_request_limit=5)
    r = b.reserve_many([("ai_requests", 1), (USD_KIND, 6000)])
    assert b.held("ai_requests") == 1 and b.held(USD_KIND) == 6000
    with pytest.raises(BudgetExceeded) as e:
        b.reserve_many([("ai_requests", 1), (USD_KIND, 6000)])       # the USD part does not fit
    assert e.value.code == "DELEGATION_BUDGET"
    assert b.held("ai_requests") == 1 and b.held(USD_KIND) == 6000    # nothing of the refused pair is held
    r[0].commit()
    r[1].commit(2500)                                                 # settled with the real (smaller) cost
    assert b.used(USD_KIND) == 2500 and b.held(USD_KIND) == 0 and b.remaining(USD_KIND) == 10000 - 2500
    assert b.limit(USD_KIND) == 10000 and b.reservations() == []


def test_costs_estimate_is_an_upper_bound_of_the_actual_price():
    cfg = Config()
    c = dc.Costs(cfg.pricing, "gpt-6-luna", "priority", 800, 2)
    body = {"instructions": "x" * 3000, "input": [{"role": "user", "content": "מה מזג האוויר?"}], "tools": []}
    est = c.estimate(body)
    worst_real = c.actual({"input_tokens": 3100 + 2 * dc.SEARCH_INPUT_TOKENS, "output_tokens": 800}, 2,
                          "gpt-6-luna", "priority")
    assert est >= worst_real > 0
    assert c.estimate(body, searches=0) < est                         # a cancel before any search counts less
    assert c.actual({"input_tokens": 1000, "output_tokens": 10}, 0, "unknown-model", "") > 0   # unknown = priciest


# ------------------------------------------------------------------------------------------------ property test
async def test_strict_cap_never_exceeded_under_50_random_overlapping_schedules():
    """Acceptance (V4 D): 50 random overlapping-turn schedules, fixed seed: spend never exceeds the cap by even one
    request; every reservation is settled or released afterwards."""
    r = await ds.run_cap_test(cap_usd=0.10, turn_usd=0.03, turns=16, seed=20261020, schedules=50)
    assert not r["cap_exceeded"], r
    assert r["worst_spent_usd"] <= r["cap_usd"]
    assert r["unsettled"] == 0 and r["refused_before_sending"] > 0 and r["answered"] > 0, r


def _mp_worker(path: str, cap: float, n: int, out: str) -> None:
    from jarves.budget import USD_KIND, Budget, BudgetExceeded
    from jarves.config import Config
    from jarves.memory import MemoryStore
    cfg = Config()
    cfg.ai.delegation_daily_usd, cfg.ai.daily_request_limit = cap, 100000
    b = Budget(MemoryStore(Path(path)), cfg.ai)
    got = 0
    for _ in range(n):
        try:
            for r in b.reserve_many([("ai_requests", 1), (USD_KIND, 7000)]):
                r.commit()
            got += 1
        except BudgetExceeded:
            pass
    Path(out).write_text(str(got))


def test_strict_cap_holds_across_processes(tmp_path):
    """Two processes (the Core and e.g. `jarvis ask`) racing for the last dollars never both take them."""
    import multiprocessing as mp
    db = tmp_path / "shared.db"
    MemoryStore(db).close()
    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=_mp_worker, args=(str(db), 0.10, 20, str(tmp_path / f"o{i}"))) for i in range(3)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(120)
    assert all(p.exitcode == 0 for p in ps)
    total = sum(int((tmp_path / f"o{i}").read_text()) for i in range(3))
    assert total == 14                                                # 14 * $0.007 = $0.098 <= $0.10 < 15 * $0.007
    cfg = Config()
    cfg.ai.delegation_daily_usd = 0.10
    b = Budget(MemoryStore(db), cfg.ai)
    assert b.used(USD_KIND) == 14 * 7000 <= b.limit(USD_KIND)


def test_crash_recovery_counts_a_dead_owners_reservation(tmp_path):
    """A Core that crashed mid-request left its reservation: the next start COUNTS it (it may have been billed),
    never frees it silently, and settles it exactly once."""
    cfg, b = make_budget(tmp_path, delegation_daily_usd=0.05)
    dead = Budget(MemoryStore(tmp_path / "m.db"), cfg.ai)
    held = dead.reserve_many([("ai_requests", 1), (USD_KIND, 20000)])
    dead._db.execute("UPDATE reservations SET pid = 999999")           # its process is gone
    after = Budget(MemoryStore(tmp_path / "m.db"), cfg.ai, alive=lambda pid: False)
    after.reserve_many([(USD_KIND, 1)])[0].release()                  # any reservation runs the expiry first
    assert after.used(USD_KIND) == 20000 and after.held(USD_KIND) == 0
    for r in held:                                                     # the "dead" owner's late commit adds nothing
        r.commit()
    assert after.used(USD_KIND) == 20000


# ------------------------------------------------------------------------------------------------ live session flow
async def test_session_config_client_and_text_only(tmp_path):
    lv = LiveVoice(SkillRegistry(), lambda: "", delegation_mode="client")
    assert lv.session_config()["delegation"] == {"type": "client"}
    t = LiveVoice(SkillRegistry(), lambda: "", delegation_mode="text_only").session_config()
    assert "delegation" not in t and "cannot look anything up" in t["instructions"]
    assert LiveVoice(SkillRegistry(), lambda: "").session_config()["delegation"]["type"] == "responses"


async def test_client_delegation_answers_from_the_transcript_via_the_real_sdk(tmp_path):
    cfg, b = make_budget(tmp_path)
    http, live = ResponsesHTTPServer(default={"text": "בתל אביב עכשיו 24 מעלות.", "searches": 1}), FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        assert conn.config["delegation"] == {"type": "client"} and lv._credit is None   # no server-side credit
        did = conn.user_says("מה מזג האוויר עכשיו בתל אביב?")
        await until(lambda: commentary(conn, did))
        assert commentary(conn, did)[0]["content"] == "בתל אביב עכשיו 24 מעלות."
        body = http.bodies[0]
        assert body["input"][0] == {"role": "user", "content": "מה מזג האוויר עכשיו בתל אביב?"}   # from the deltas
        assert body["max_tool_calls"] == cfg.ai.delegation_max_searches and body["store"] is False
        assert body["max_output_tokens"] == cfg.ai.voice_max_output_tokens
        assert {"type": "web_search"} in body["tools"]
        await until(lambda: not engine.jobs)
        assert b.reservations() == [] and b.used("ai_requests") == 1
        spent = b.used(USD_KIND)
        assert spent == engine.costs.actual({"input_tokens": 1200, "output_tokens": 30}, 1, cfg.ai.text_model,
                                            "priority")              # settled with the real usage, once
        assert state.counters["completed"] == 1 and engine.latencies
    finally:
        await stop(lv)
        http.close()


async def test_cap_reached_mid_conversation_is_never_sent_and_said_honestly(tmp_path):
    cfg, b = make_budget(tmp_path, delegation_daily_usd=0.03)        # room for one worst-case request (~$0.024)
    http, live = ResponsesHTTPServer([{"searches": 2, "text": "בתל אביב 24 מעלות."}]), FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה מזג האוויר בתל אביב?")
        await until(lambda: commentary(conn, d1))
        await until(lambda: not engine.jobs)
        held_before = b.used(USD_KIND)
        lv._user_turn_n += 1
        d2 = conn.user_says("ומה בירושלים?")
        await until(lambda: commentary(conn, d2))
        assert len(http.bodies) == 1                                   # the second request NEVER left
        assert "budget" in commentary(conn, d2)[0]["content"] and "הגעתי לתקציב" in commentary(conn, d2)[0]["content"]
        assert state.counters["cap_refused"] == 1 and b.used(USD_KIND) == held_before <= b.limit(USD_KIND)
        assert b.reservations() == []
    finally:
        await stop(lv)
        http.close()


async def test_tool_call_inside_a_delegated_turn_and_per_turn_request_cap(tmp_path):
    # a per-question cap above ONE worst case but below TWO: a tool continuation must still fit (live 20 Oct: get_time
    # + a second request was refused while the worst-case estimates were summed instead of the settled spend)
    cfg, b = make_budget(tmp_path, delegation_turn_requests=2, delegation_turn_usd=0.03)
    reg = SkillRegistry()
    reg.register(ClockSkill())
    http = ResponsesHTTPServer([{"calls": [{"name": "get_time", "call_id": "c1"}]},
                                {"text": "השעה עשר."},
                                {"calls": [{"name": "get_time", "call_id": "c2"}]},
                                {"calls": [{"name": "get_time", "call_id": "c3"}]}])
    live = FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live, registry=reg)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה השעה?")
        await until(lambda: commentary(conn, d1))
        assert commentary(conn, d1)[0]["content"] == "השעה עשר."
        second = http.bodies[1]["input"]
        assert any(i.get("type") == "function_call" and i.get("call_id") == "c1" for i in second)
        out = next(i for i in second if i.get("type") == "function_call_output")
        assert out["call_id"] == "c1" and json.loads(out["output"])["ok"]
        # a turn that keeps calling tools stops at delegation_turn_requests (never a third request)
        lv._user_turn_n += 1
        d2 = conn.user_says("ומה השעה עכשיו?")
        await until(lambda: commentary(conn, d2))
        assert len(http.bodies) == 4 and "per-question cost limit" in commentary(conn, d2)[0]["content"]
        await until(lambda: not engine.jobs)
        assert b.reservations() == []
    finally:
        await stop(lv)
        http.close()


async def test_barge_in_cancel_stops_the_lookup_and_releases_the_reservation(tmp_path):
    cfg, b = make_budget(tmp_path)
    gate = threading.Event()
    http, live = ResponsesHTTPServer([{"gate": gate, "text": "late"}]), FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("תבדוק לי את מחיר המניה של אינטל")
        await until(lambda: http.bodies and b.held(USD_KIND) > 0)     # sent and held
        lv._remember_user("עזוב")                                       # the user: "never mind"
        await until(lambda: not engine.jobs)
        assert state.counters["cancelled"] == 1
        assert b.held(USD_KIND) == 0 and b.reservations() == []        # nothing stays held
        assert 0 < b.used(USD_KIND) <= engine.costs.estimate(http.bodies[0])   # sent: counted conservatively
        thinking = [p for k, p in conn.sent if k == "thinking" and p["delegation_id"] == d1]
        assert thinking and thinking[0]["content"] == dc.CANCEL_NOTE
        assert not commentary(conn, d1)                                # nothing more is said for it
    finally:
        gate.set()
        await stop(lv)
        http.close()


async def test_a_newer_question_supersedes_the_older_lookup(tmp_path):
    cfg, b = make_budget(tmp_path)
    gate = threading.Event()
    http, live = ResponsesHTTPServer([{"gate": gate, "text": "old"}, {"text": "שתי התשובות."}]), FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה מזג האוויר בחיפה?")
        await until(lambda: http.bodies)
        lv._remember_user(lv._in_text)
        lv._in_text = ""
        lv._user_turn_n += 1
        d2 = conn.user_says("ובאילת?")
        await until(lambda: commentary(conn, d2))
        assert commentary(conn, d2)[0]["content"] == "שתי התשובות." and not commentary(conn, d1)
        assert state.counters["cancelled"] == 1
        texts = [i["content"] for i in http.bodies[1]["input"] if i.get("role") == "user"]
        assert "מה מזג האוויר בחיפה?" in texts and "ובאילת?" in texts  # the newer request still sees both questions
        await until(lambda: not engine.jobs)
        assert b.reservations() == []
    finally:
        gate.set()
        await stop(lv)
        http.close()


async def test_two_delegations_of_the_same_turn_both_answer(tmp_path):
    cfg, b = make_budget(tmp_path)
    http, live = ResponsesHTTPServer([{"text": "אחת"}, {"text": "שתיים"}]), FakeLiveServer()
    lv, engine, _ = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה השעה בטוקיו ומה מזג האוויר שם?")
        d2 = conn.user_says("", delegate=True)
        await until(lambda: commentary(conn, d1) and commentary(conn, d2))
        assert {commentary(conn, d1)[0]["content"], commentary(conn, d2)[0]["content"]} == {"אחת", "שתיים"}
        assert b.used("ai_requests") == 2 and b.reservations() == []
    finally:
        await stop(lv)
        http.close()


async def test_slow_backend_times_out_honestly(tmp_path):
    cfg, b = make_budget(tmp_path)
    object.__setattr__(cfg.ai, "delegation_timeout_s", 0.6)          # below the config minimum: test only
    gate = threading.Event()
    http, live = ResponsesHTTPServer([{"gate": gate}]), FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("תסכם לי את כל החדשות של היום")
        await until(lambda: commentary(conn, d1))
        assert commentary(conn, d1)[0]["content"] == dc.TIMEOUT_NOTE and state.counters["timeouts"] == 1
        assert b.reservations() == [] and b.used("ai_requests") == 1
    finally:
        gate.set()
        await stop(lv)
        http.close()


async def test_network_drop_mid_request_is_counted_and_said(tmp_path):
    cfg, b = make_budget(tmp_path)
    http, live = ResponsesHTTPServer([{"drop": True, "searches": 1}]), FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה קרה היום בבורסה?")
        await until(lambda: commentary(conn, d1))
        assert "lookup failed" in commentary(conn, d1)[0]["content"] and state.counters["errors"] == 1
        assert len(http.bodies) == 1                                   # no hidden SDK retry: never sent twice
        assert b.used("ai_requests") == 1 and b.used(USD_KIND) > 0 and b.reservations() == []
    finally:
        await stop(lv)
        http.close()


async def test_http_error_is_not_retried_and_counted_once(tmp_path):
    cfg, b = make_budget(tmp_path)
    http, live = ResponsesHTTPServer([{"status": 500}]), FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה החדשות?")
        await until(lambda: commentary(conn, d1))
        assert len(http.bodies) == 1 and b.used("ai_requests") == 1 and b.reservations() == []
    finally:
        await stop(lv)
        http.close()


# ------------------------------------------------------------------------------------------------ plan route
def _plan(tmp_path, replies):
    from test_chatgpt import ResponsesServer, signed_in_auth
    from jarves.chatgpt_brain import ChatGPTBrain
    cfg, b = make_budget(tmp_path, connection="combined")
    srv = ResponsesServer(replies)
    mem = MemoryStore(tmp_path / "plan.db")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    brain = ChatGPTBrain(cfg.ai, reg, mem, signed_in_auth(tmp_path), transport=httpx.MockTransport(srv.handler),
                         budget=b)
    return cfg, b, srv, brain, reg


async def test_combined_mode_reasoning_runs_on_the_plan_with_zero_api_cost(tmp_path):
    from test_chatgpt import completed, msg, sse
    cfg, b, srv, brain, reg = _plan(tmp_path, [(200, sse({"type": "response.created"},
                                                         completed([msg("ההסבר: כי האור מתפזר.")])))])
    live, api = FakeLiveServer(), ResponsesHTTPServer()
    lv, engine, _ = make_voice(tmp_path, cfg, b, http=api, live_server=live, route="plan", plan_brain=brain,
                               registry=reg)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("למה השמיים כחולים?")
        await until(lambda: commentary(conn, d1))
        assert commentary(conn, d1)[0]["content"] == "ההסבר: כי האור מתפזר."
        assert api.bodies == [] and b.used(USD_KIND) == 0               # API cost for the reasoning: $0
        assert b.used("ai_requests") == 1 and srv.bodies[0]["store"] is False   # still counted against the cap
        assert engine.billed_route() == "chatgpt_plan"
    finally:
        await stop(lv)
        api.close()


async def test_plan_limit_pauses_and_never_moves_to_the_api(tmp_path):
    cfg, b, srv, brain, reg = _plan(tmp_path, [(429, {"error": {"code": "subscription_sharing_usage_limit_exceeded",
                                                                "message": "limit"}})])
    live, api = FakeLiveServer(), ResponsesHTTPServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=api, live_server=live, route="plan", plan_brain=brain,
                                   registry=reg)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה החדשות?")
        await until(lambda: commentary(conn, d1))
        assert commentary(conn, d1)[0]["content"] == dc.PLAN_LIMIT_NOTE and state.counters["plan_paused"] == 1
        assert brain.paused_code == "CHATGPT_LIMIT" and api.bodies == []    # the paid API was never touched
        lv._user_turn_n += 1
        d2 = conn.user_says("ומה מזג האוויר?")
        await until(lambda: commentary(conn, d2))
        assert commentary(conn, d2)[0]["content"] == dc.PLAN_LIMIT_NOTE and len(srv.bodies) == 1 and api.bodies == []
    finally:
        await stop(lv)
        api.close()


# ------------------------------------------------------------------------------------------------ fallback ladder
async def test_rejected_append_steps_down_the_ladder_and_still_delivers(tmp_path):
    cfg, b = make_budget(tmp_path)
    http, live = ResponsesHTTPServer(), FakeLiveServer(reject_appends=True)
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live, home=tmp_path)
    try:
        conn = await started(lv, live)
        conn.user_says("מה מזג האוויר?")
        await until(lambda: any(p["delegation_id"] is None and "24" in p["content"] for p in commentary(conn)))
        assert state.step == "text_only" and state.counters["fallbacks"] == 1   # default fallback: local
        assert dc.read_state(tmp_path)["step"] == "text_only"
    finally:
        await stop(lv)
        http.close()
    cfg.ai.delegation_mode = "client"
    assert state.session_step() == "text_only"
    cfg.ai.delegation_fallback = "server"
    s2 = dc.DelegationState(None, cfg.ai)
    assert s2.fail("append_rejected", "x") == "server" and s2.session_step() == "server"
    s2.reset()
    assert s2.session_step() == "client"


async def test_start_error_about_delegation_marks_the_ladder(tmp_path):
    cfg, b = make_budget(tmp_path)
    live = FakeLiveServer(start_error="delegation type client is not supported")
    lv, engine, state = make_voice(tmp_path, cfg, b, live_server=live)
    try:
        await started(lv, live)
        await until(lambda: state.step != "client")
        assert state.step == "text_only"
    finally:
        await stop(lv)


def test_contract_recorded_real_session_matches_the_sdk_and_our_handler():
    """The event sequence recorded once from the real gpt-live-1 (client delegation, 20 Oct 2026, redacted)."""
    from openai.types.live.delegation_created_event import DelegationCreatedEvent
    rec = json.loads(FIXTURE.read_text(encoding="utf-8"))
    evs = rec["events"]
    created = [e for e in evs if e["type"] == "session.delegation.created"]
    assert len(created) == 1
    m = DelegationCreatedEvent.model_validate({k: v for k, v in created[0].items() if k != "_t"})
    assert m.delegation.target == "client" and m.delegation.response_id is None
    assert set(created[0]["delegation"]) == {"id", "type", "target", "response_id"}          # metadata, no task text
    started_ev = next(e for e in evs if e["type"] == "session.started")
    assert started_ev["session"]["delegation"]["type"] == "client"
    order = [e["type"] for e in evs if e["type"] != "session.output_audio.delta"]
    i = order.index("session.delegation.created")
    assert order[i - 1] == "session.input_transcript.delta"            # the transcript is complete when it arrives
    acks = [e for e in evs if e["type"] in ("session.thinking.appended", "session.commentary.appended")]
    assert {a["client_event_id"] for a in acks} == {"probe_think", "probe_result"}
    spoken = "".join(e["delta"] for e in evs[evs.index(acks[-1]):] if e["type"] == "session.output_transcript.delta")
    assert "24" in spoken                                               # the appended result was spoken


async def test_recorded_events_drive_the_engine(tmp_path):
    cfg, b = make_budget(tmp_path)
    rec = json.loads(FIXTURE.read_text(encoding="utf-8"))["events"]
    http = ResponsesHTTPServer()
    lv, engine, _ = make_voice(tmp_path, cfg, b, http=http)
    sent = []

    class Conn:
        class session:
            class commentary:
                @staticmethod
                async def append(content, delegation_id=None, event_id=None):
                    sent.append(("c", delegation_id, content))

            class thinking:
                @staticmethod
                async def append(content, delegation_id=None, event_id=None):
                    sent.append(("t", delegation_id, content))
    lv.conn, lv.running, lv._active = Conn(), True, True
    try:
        for e in rec:
            if e["type"] in ("session.input_transcript.delta", "session.delegation.created"):
                await lv.handle_event(e)
        did = next(e for e in rec if e["type"] == "session.delegation.created")["delegation"]["id"]
        await until(lambda: any(s[0] == "c" for s in sent))
        assert sent[-1][:2] == ("c", did)
        assert http.bodies[0]["input"][0]["content"] == "מה מזג האוויר עכשיו בתל אביב"
    finally:
        if lv._flush_task is not None:
            lv._flush_task.cancel()
        await ds.stop_tasks(lv)
        http.close()


# ------------------------------------------------------------------------------------------------ surfaces
def test_billing_routes_combined_client_puts_reasoning_on_the_plan():
    from jarves.brain import billing_routes, billing_summary
    cfg = Config()
    cfg.ai.connection = "combined"
    assert billing_routes(cfg.ai)["voice_backend"] == "api_key"
    cfg.ai.delegation_mode = "client"
    r = billing_routes(cfg.ai)
    assert r["voice_backend"] == "chatgpt_plan" and r["spoken_voice"] == "api_key"
    assert "its reasoning/web search: ChatGPT plan" in billing_summary(cfg.ai)


def test_feature_switch_is_the_mode_and_mirrors_the_config(tmp_path):
    from jarves import features as feat
    cfg = Config()
    f = feat.Features(cfg, tmp_path)
    assert not f.enabled("client_delegation") and feat.CATALOG["client_delegation"].risk == "medium"
    f.set("client_delegation", True, by="test")
    f.apply_to_config(cfg)
    assert cfg.ai.delegation_mode == "client"
    cfg2 = Config()
    cfg2.ai.delegation_mode = "client"
    cfg2.ai.model_fields_set.add("delegation_mode")
    assert feat.Features(cfg2, tmp_path / "other").enabled("client_delegation")
    from jarves import cli_parity, setup_wizard
    assert cli_parity.FEATURES["client_delegation"] == "budget"
    assert any("client_delegation" in ids for _, _, _, ids in setup_wizard.GROUPS)


def test_cli_budget_status_test_cap_set_and_reset(home, capsys):
    from jarves import cli
    assert cli.main(["budget", "status", "--json"]) == 0
    v = json.loads(capsys.readouterr().out)
    assert v["mode"] == "client"                                  # owner decision OD-15: new installs start in client mode
    assert v["limit_usd"] == 1.0 and v["reservations"] == []
    assert cli.main(["budget", "set", "--mode", "server"]) == 0
    capsys.readouterr()
    assert cli.main(["budget", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "server"
    assert cli.main(["budget", "set", "--daily-usd", "0.5", "--turn-usd", "0.04", "--fallback", "server",
                     "--mode", "client"]) == 0
    capsys.readouterr()
    assert cli.main(["budget", "status", "--json"]) == 0
    v = json.loads(capsys.readouterr().out)
    assert v["mode"] == "client" and v["limit_usd"] == 0.5 and v["caps"]["turn_usd"] == 0.04
    assert v["fallback"] == "server"
    assert cli.main(["budget", "set", "--daily-usd", "1000"]) == 2      # validated: le=100
    assert cli.main(["budget", "test-cap", "--schedules", "2", "--turns", "30"]) == 0
    assert "PASS" in capsys.readouterr().out
    (home / dc.STATE_FILE).write_text(json.dumps({"step": "text_only", "reason": "x"}), encoding="utf-8")
    assert cli.main(["budget", "reset-ladder"]) == 0
    assert dc.read_state(home)["step"] == "client"


def test_dashboard_delegation_api(home):
    from jarves import dashboard
    ctx = dashboard.Ctx(home, Config())
    code, v = dashboard.api_delegation(ctx, None, None)
    assert code == 200 and v["step"] == "server" and "reservations" in v and "used_usd" in v
    routes = [r[1] for r in dashboard.ROUTES] if hasattr(dashboard, "ROUTES") else []
    assert not routes or any("delegation" in r for r in routes)
    js = (Path(dashboard.__file__).parent / "dashboard_static" / "delegation.js").read_text(encoding="utf-8")
    assert "/api/delegation" in js and "innerHTML" not in js
    html = (Path(dashboard.__file__).parent / "dashboard_static" / "index.html").read_text(encoding="utf-8")
    assert '/static/delegation.js' in html


# ------------------------------------------------------------------------------------------------ real Core
async def test_real_core_client_delegation_end_to_end(home, cfg, monkeypatch):
    """A real Core (features, budget, health, snapshot, store) runs a GPT-Live conversation in client mode against the
    fake Live server and the local Responses HTTP server."""
    from conftest import FakeSTT, FakeTTS
    from jarves.app import Core, _delegation_kwargs
    from jarves.skills.music import FakePlayer
    from jarves.voice.audio import SimAudio
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    cfg.voice.activation, cfg.voice.mode, cfg.voice.greeting = "wake_word", "live", False
    cfg.ai.delegation_mode = "client"
    cfg.ai.model_fields_set.add("delegation_mode")
    live, http = FakeLiveServer(), ResponsesHTTPServer(default={"text": "בחוץ 24 מעלות."})

    def factory(core, **kw):
        kw.pop("greeting", None)
        kw.pop("hold_output", None)
        return LiveVoice(core.registry, core.agent.instructions, client=live, audio_io=None, **kw,
                         **_delegation_kwargs(core))
    core = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(),
                use_real_audio=False, console_logs=False, live_factory=factory)
    core._delegation_client = http.client()
    await core.start()
    try:
        assert core.features.enabled("client_delegation") and core.cfg.ai.delegation_mode == "client"
        assert core.snapshot()["delegation"]["step"] == "client"
        assert core.health.components["delegation"].status == "ok"
        task = asyncio.create_task(core._start_live())
        await until(lambda: live.conns and live.conn.config)
        conn = live.conn
        assert conn.config["delegation"] == {"type": "client"}
        await until(lambda: core.live is not None and core.live.running)
        did = conn.user_says("מה מזג האוויר?")
        await until(lambda: commentary(conn, did))
        assert commentary(conn, did)[0]["content"] == "בחוץ 24 מעלות."
        snap = core.snapshot()["delegation"]
        assert snap["counters"]["completed"] == 1 and snap["budget"]["used_usd"] > 0
        assert snap["budget"]["reservations"] == []
        await task
    finally:
        await core.stop()
        http.close()
    log = (home / "logs" / "jarves.log").read_text(encoding="utf-8")
    for code in ("DELEG_MODE", "DELEG_START", "DELEG_DONE", "LIVE_BACKEND_DONE"):
        assert code in log, code
    assert "test-not-real" not in log


@pytest.mark.parametrize("text,cancels", [("עזוב", True), ("עזוב, לא משנה", True), ("לא משנה.", True),
                                          ("Never mind", True), ("תעזוב את זה", True), ("cancel that please", True),
                                          ("תעצור את המוזיקה", False), ("stop the music", False),
                                          ("עזוב, תבדוק משהו אחר", False)])
def test_cancel_phrases(text, cancels):
    assert bool(dc._CANCEL_RE.match(text)) is cancels


async def test_a_question_asked_while_the_lookup_ran_is_handed_back(tmp_path):
    """Live 20 Oct (scenario 53): the second question, asked while the first lookup ran, got no delegation of its own
    and was dropped; the engine now tells GPT-Live (silently) to delegate it after saying the first result."""
    cfg, b = make_budget(tmp_path)
    gate = threading.Event()
    http, live = ResponsesHTTPServer([{"gate": gate, "text": "בחיפה 24 מעלות."}]), FakeLiveServer()
    lv, engine, _ = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה מזג האוויר עכשיו בחיפה?")
        await until(lambda: http.bodies)
        lv._remember_user(lv._in_text)
        lv._in_text = ""
        conn.user_says("וגם מה השעה בניו יורק?", delegate=False)
        await until(lambda: "השעה" in lv._in_text)
        lv._remember_user(lv._in_text)
        lv._in_text = ""
        gate.set()
        await until(lambda: commentary(conn, d1))
        hints = [p["content"] for k, p in conn.sent if k == "thinking" and p["delegation_id"] == d1]
        assert hints and "וגם מה השעה בניו יורק?" in hints[0] and "delegate that question now" in hints[0]
    finally:
        gate.set()
        await stop(lv)
        http.close()


async def test_an_unsaid_result_is_carried_into_the_next_request(tmp_path):
    cfg, b = make_budget(tmp_path)
    http = ResponsesHTTPServer([{"text": "בחיפה 24 מעלות."}, {"text": "בחיפה 24 מעלות, ובניו יורק עשר בבוקר."}])
    live = FakeLiveServer(mute=True)
    lv, engine, _ = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        d1 = conn.user_says("מה מזג האוויר בחיפה?")
        await until(lambda: commentary(conn, d1))
        lv._remember_user(lv._in_text)
        lv._in_text = ""
        d2 = conn.user_says("ומה השעה בניו יורק?")
        await until(lambda: commentary(conn, d2))
        note = http.bodies[1]["input"][-1]["content"]
        assert "not said to the user yet" in note and "בחיפה 24 מעלות." in note
        assert "not said" not in http.bodies[0]["input"][-1]["content"]
    finally:
        await stop(lv)
        http.close()


async def test_no_hand_over_of_a_cancel_phrase_or_right_after_a_cancel(tmp_path):
    """Live 20 Oct (scenario 54): the CV-15 watchdog re-ran a lookup the user had just cancelled with "עזוב"."""
    cfg, b = make_budget(tmp_path)
    gate = threading.Event()
    http, live = ResponsesHTTPServer([{"gate": gate}]), FakeLiveServer()
    lv, engine, state = make_voice(tmp_path, cfg, b, http=http, live_server=live)
    try:
        conn = await started(lv, live)
        conn.user_says("מה מחיר מניית אנבידיה?")
        await until(lambda: http.bodies)
        lv._remember_user("עזוב, לא משנה")
        await until(lambda: not engine.jobs)
        assert await lv._redelegate("עזוב, לא משנה", lv._user_turn_n, "undelegated") is False
        assert await lv._redelegate("מה מזג האוויר?", lv._user_turn_n + 1, "undelegated") is False   # just cancelled
        assert len(http.bodies) == 1 and state.counters["cancelled"] == 1
    finally:
        gate.set()
        await stop(lv)
        http.close()
