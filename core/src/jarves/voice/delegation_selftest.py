"""Offline, free proof of the client-delegation strict cap (`jarvis budget test-cap`, SW-V4 D #14).

A REAL LiveVoice + ClientDelegation + Budget (temporary SQLite file) run many overlapping delegated turns against a
local stand-in for the Responses API (no network, no key, no cost). Each fake response reports usage up to the
request's own worst-case allowance, so the test is as hard as a real day can be. PASS = the settled spend never
exceeds the cap and every reservation is settled or released afterwards."""
from __future__ import annotations

import asyncio
import random
import tempfile
from pathlib import Path


class FakeStream:
    def __init__(self, events, delay: float, gate: asyncio.Event | None = None):
        self.events, self.delay, self.gate = events, delay, gate

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for i, e in enumerate(self.events):
            if i == 1 and self.delay:
                await asyncio.sleep(self.delay)          # the fake's own simulated backend time
            if i == 1 and self.gate is not None:
                await self.gate.wait()
            yield e


class FakeResponsesAPI:
    """`client.responses` stand-in: `create(stream=True, **body)` records the body and streams a scripted answer.
    usage = fraction of the request's worst-case allowance (tests pick it), so actual <= reserved."""

    def __init__(self, *, text: str = "בתל אביב עכשיו 24 מעלות.", delay: float = 0.0, out_tokens: int = 40,
                 in_tokens: int = 900, searches: int = 0, fail: str = "", gate: asyncio.Event | None = None,
                 rng: random.Random | None = None) -> None:
        self.text, self.delay, self.out_tokens, self.in_tokens = text, delay, out_tokens, in_tokens
        self.searches, self.fail, self.gate, self.rng = searches, fail, gate, rng
        self.bodies: list[dict] = []
        self.sent = 0

    async def create(self, stream: bool = True, **body):
        self.bodies.append(body)
        self.sent += 1
        if self.fail == "connect":
            import httpx
            raise httpx.ConnectError("fake network drop")
        n = len(self.bodies)
        delay = self.delay if self.rng is None else self.rng.uniform(0, self.delay)
        evs: list[dict] = [{"type": "response.created", "response": {"id": f"resp_{n}", "model": body["model"]}}]
        for i in range(self.searches):
            evs.append({"type": "response.output_item.done", "output_index": i,
                        "item": {"type": "web_search_call", "id": f"ws_{n}_{i}", "status": "completed",
                                 "action": {"type": "search", "query": "weather"}}})
        if self.fail == "midstream":
            evs.append({"type": "error", "error": {"code": "server_error", "message": "stream broke"}})
            return FakeStream(evs, delay, self.gate)
        evs += [{"type": "response.output_text.delta", "delta": self.text},
                {"type": "response.output_item.done", "output_index": self.searches,
                 "item": {"type": "message", "role": "assistant",
                          "content": [{"type": "output_text", "text": self.text}]}},
                {"type": "response.completed", "response": {
                    "id": f"resp_{n}", "status": "completed", "model": body["model"],
                    "service_tier": body.get("service_tier", ""),
                    "usage": {"input_tokens": self.in_tokens, "output_tokens": self.out_tokens,
                              "input_tokens_details": {"cached_tokens": 0},
                              "output_tokens_details": {"reasoning_tokens": 0}}}}]
        return FakeStream(evs, delay, self.gate)


class FakeClient:
    def __init__(self, responses: FakeResponsesAPI) -> None:
        self.responses = responses

    def with_options(self, **_):
        return self


class FakeLiveConn:
    """GPT-Live connection stand-in: records every append; `reject` = delegation ids the server refuses."""

    def __init__(self) -> None:
        self.appends: list[tuple[str, str | None, str, str]] = []
        outer = self

        class _Append:
            def __init__(self, kind):
                self.kind = kind

            async def append(self, content, delegation_id=None, event_id=None):
                outer.appends.append((self.kind, delegation_id, content, event_id or ""))

        class _Session:
            commentary, thinking, instructions = _Append("commentary"), _Append("thinking"), _Append("instructions")
        self.session = _Session()


def make_live(budget, ai, pricing, api: FakeResponsesAPI, home: Path | None = None):
    """A real LiveVoice in client mode wired to fakes (no audio, no network)."""
    from ..skills import SkillRegistry
    from .delegation_client import ClientDelegation, DelegationState
    from .live import LiveVoice
    state = DelegationState(home, ai)
    engine = ClientDelegation(ai, pricing, budget, state, route="api_key", client=FakeClient(api),
                              max_output_tokens=ai.voice_max_output_tokens, service_tier=ai.voice_service_tier)
    lv = LiveVoice(SkillRegistry(), lambda: "You are the JARVES backend.", delegation_mode="client",
                   delegation=engine, budget=budget, progress_after_s=0)
    lv.conn = FakeLiveConn()
    lv.running = True
    lv._active = True
    return lv, engine, state


async def stop_tasks(lv) -> None:
    """Cancel the helper tasks a test LiveVoice spawned (delivery watches, activity clears)."""
    tasks, lv._tasks = list(lv._tasks), []
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=5)


async def run_cap_test(cap_usd: float = 0.10, turn_usd: float = 0.03, turns: int = 30, seed: int = 7,
                       schedules: int = 1) -> dict:
    """`schedules` random overlapping schedules; returns the worst spend seen and whether the cap ever broke."""
    from ..budget import MICRO, USD_KIND, Budget
    from ..config import Config
    from ..memory import MemoryStore
    worst, broke, unsettled, refused, answered = 0, False, 0, 0, 0
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="jarves-captest-", ignore_cleanup_errors=True) as tmp:
        for k in range(schedules):
            cfg = Config()
            cfg.ai.delegation_daily_usd, cfg.ai.delegation_turn_usd = cap_usd, turn_usd
            cfg.ai.daily_request_limit = 10_000
            mem = MemoryStore(Path(tmp) / f"m{k}.db")
            budget = Budget(mem, cfg.ai)
            api = FakeResponsesAPI(delay=0.04, rng=rng, searches=rng.randint(0, 2),
                                   out_tokens=rng.randint(10, cfg.ai.voice_max_output_tokens))
            lv, engine, _ = make_live(budget, cfg.ai, cfg.pricing, api)
            for t in range(turns):
                # a new user turn (supersedes older lookups) or another delegation of the same turn (overlap)
                lv._user_turn_n = lv._user_turn_n + 1 if rng.random() < 0.4 else lv._user_turn_n
                await engine.on_created(f"item_{k}_{t}", lv._user_turn_n, [{"role": "user", "text": f"q{t}"}], "he")
                await asyncio.sleep(rng.uniform(0, 0.03))
            for _ in range(500):
                if not engine.jobs:
                    break
                await asyncio.sleep(0.01)
            await engine.close()
            await stop_tasks(lv)
            spent = budget.used(USD_KIND)
            worst = max(worst, spent)
            broke = broke or spent > budget.limit(USD_KIND)
            unsettled += len(budget.reservations())
            refused += engine.state.counters["cap_refused"]
            answered += engine.state.counters["completed"]
            budget.close()
            getattr(mem, "close", lambda: None)()
    return {"cap_usd": cap_usd, "worst_spent_usd": worst / MICRO, "cap_exceeded": broke, "unsettled": unsettled,
            "refused_before_sending": refused, "answered": answered, "schedules": schedules, "turns": turns,
            "seed": seed, "ok": not broke and unsettled == 0 and refused > 0 and answered > 0}
