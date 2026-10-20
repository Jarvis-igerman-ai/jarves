"""Text brain on the user's ChatGPT plan (Sign in with ChatGPT), same interface as AgentBrain.

The plan route is a restricted `POST /v1/responses` (preview, checked 2026-10-18):
`store:false` + `stream:true` on every request, full context in `input`, system prompt via `instructions`,
function tools grouped in a namespace, no previous_response_id / temperature / max_output_tokens / metadata,
no audio. Web search only where the model and account policy allow it: if the route reports it unsupported,
JARVES drops it for this session and says so in health. Never falls back to the billable API key silently.
"""
from __future__ import annotations

import asyncio
import json
import random

import httpx

from . import logs, usage
from .brain import INSTRUCTIONS, BackendTrace, BrainUnavailable, billing_summary
from .budget import Budget, BudgetExceeded
from .chatgpt_auth import RESOURCE, ChatGPTAuth, ChatGPTAuthError
from .config import AIConfig
from .memory import MemoryStore
from .skills import SkillRegistry, bounded_ai

NAMESPACE = "jarves"
MAX_HISTORY_ITEMS = 40


def parse_sse(lines: list[str]) -> list[dict]:
    out, data = [], []
    for line in lines + [""]:
        if line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and data:
            raw = "\n".join(data)
            data = []
            if raw and raw != "[DONE]":
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    pass
    return out


def plain_tool_name(item: dict) -> str:
    name = item.get("name", "")
    return name.split(".", 1)[1] if name.startswith(NAMESPACE + ".") else name


class ChatGPTBrain:
    connection = "chatgpt"

    def __init__(self, cfg: AIConfig, registry: SkillRegistry, memory: MemoryStore, auth: ChatGPTAuth,
                 name: str = "JARVES", *, transport: httpx.AsyncBaseTransport | None = None,
                 budget: Budget | None = None) -> None:
        self.cfg, self.registry, self.memory, self.auth, self.name = cfg, registry, memory, auth, name
        self.budget = budget or Budget(memory, cfg)     # shared Core Budget; None = own one on the same counter
        self.connection = cfg.connection if cfg.connection in ("chatgpt", "combined") else "chatgpt"
        self._transport = transport
        self.history: list[dict] = []
        self.web_search = cfg.web_search
        self.web_search_note = ""
        self.paused_code = ""           # set after a plan usage limit; cleared on restart or reconnect
        self.model = cfg.chatgpt_model

    # ------------------------------------------------------------------ status
    def available(self) -> tuple[bool, str, str]:
        if self.cfg.provider != "openai":
            return False, "AI_DISABLED", "AI provider is set to offline in config"
        st = self.auth.status()
        if not st["signed_in"]:
            return False, "CHATGPT_SIGNED_OUT", "run `jarves chatgpt login` (or set ai.connection = \"api_key\")"
        if not st["plan_usage"]:
            return False, "CHATGPT_NO_PLAN_USAGE", "run `jarves chatgpt login` and allow plan usage, or use an API key"
        if self.paused_code:
            return False, self.paused_code, "ChatGPT plan limit reached: see ChatGPT Settings > Usage; switch to the API key only if you choose to"
        if self.cfg.daily_request_limit == 0 or self.budget.exhausted("ai_requests"):
            return False, "AI_BUDGET", f"daily AI request limit ({self.cfg.daily_request_limit}) reached; raise ai.daily_request_limit"
        return True, "ok", ""

    def describe(self) -> str:
        st = self.auth.status()
        ws = "on" if self.web_search else f"off{self.web_search_note}"
        cfg = self.cfg.model_copy(update={"connection": self.connection})
        return (f"ChatGPT plan ({st['email'] or 'signed in'}), model {self.model or 'auto'}, web search {ws} — "
                f"{billing_summary(cfg)}")

    def instructions(self) -> str:
        mems = self.memory.list(20)
        return INSTRUCTIONS.format(name=self.name, memories="\n".join(f"- {m['text']}" for m in mems) or "- (none)")

    def reset_conversation(self) -> None:
        self.history = []

    # ------------------------------------------------------------------ HTTP
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=RESOURCE, transport=self._transport,
                                 timeout=httpx.Timeout(self.cfg.timeout_s, connect=10))

    async def list_models(self) -> list[dict]:
        token = await self.auth.access_token()
        async with self._client() as c:
            r = await c.get("/models", headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            raise BrainUnavailable("CHATGPT_MODELS", f"model list failed (HTTP {r.status_code})", "see logs")
        models = [{"slug": m["slug"], "display_name": m.get("display_name", m["slug"])}
                  for m in r.json().get("models", []) if m.get("visibility") == "list"]
        self.auth.save_models(models)
        return models

    async def _pick_model(self) -> str:
        if self.model:
            return self.model
        cached = self.auth.models()
        models = cached or await self.list_models()
        if not models:
            raise BrainUnavailable("CHATGPT_NO_MODELS", "your ChatGPT plan lists no models for this app",
                                   "check your plan, or use an API key")
        self.model = models[0]["slug"]     # server order is preserved; first listed = default
        return self.model

    def _tools(self) -> list[dict]:
        fns = [{"type": "function", "name": t.name, "description": t.description,
                "parameters": t.args.model_json_schema()} for t in self.registry.tools()]
        tools: list[dict] = [{"type": "namespace", "name": NAMESPACE,
                              "description": "JARVES device skills: music, memory, time, status.", "tools": fns}]
        if self.web_search:
            tools.append({"type": "web_search"})
        return tools

    async def _stream(self, body: dict, on_event=None) -> dict:
        """One streamed request; returns the completed response object, raises BrainUnavailable otherwise.
        Each event is traced (BackendTrace: logged stages, usage, duration) as it ARRIVES and passed to
        on_event(event) for live progress (background tasks).

        Budget: reserve 1 ai_requests unit BEFORE sending (BudgetExceeded propagates; nothing is sent). The unit is
        released only when the request provably never left (sign-in/token failure, TCP connect failure); once sent
        it is committed whatever happens next (HTTP error, broken stream, cancellation)."""
        res = self.budget.reserve("ai_requests")
        try:
            token = await self.auth.access_token()
        except BaseException:
            res.release()
            raise
        headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
        trace = BackendTrace("chatgpt", "CHATGPT_BACKEND", model=body.get("model", ""))
        events: list[dict] = []
        data: list[str] = []

        def take(line: str) -> None:
            if line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and data:
                for ev in parse_sse(["data: " + "\n".join(data)]):
                    events.append(ev)
                    trace.feed(ev)
                    if on_event is not None:
                        on_event(ev)
                data.clear()
        try:
            async with self._client() as c:
                async with c.stream("POST", "/responses", json=body, headers=headers) as r:
                    usage.observe_headers("chatgpt_plan", r.headers)    # M5: rate-limit headers, if any
                    if r.status_code != 200:
                        await r.aread()
                        self._raise_http(r)
                    async for line in r.aiter_lines():
                        take(line)
                    take("")
        except httpx.ConnectError:
            res.release()                 # connection never established: the request was not sent
            raise
        finally:
            res.commit()                  # no-op after release
        final = None
        items: dict[int, dict] = {}        # output_index -> finished item (message / function_call / reasoning)
        for ev in events:
            t = ev.get("type", "")
            if t == "response.output_item.done" and isinstance(ev.get("item"), dict):
                items[int(ev.get("output_index", len(items)))] = ev["item"]
            elif t == "response.completed":
                final = ev["response"]
            elif t in ("response.failed", "error"):
                err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
                self._raise_code(err.get("code") or "", err.get("param") or "", err.get("message") or "", 0)
            elif t == "response.incomplete":
                why = ((ev.get("response") or {}).get("incomplete_details") or {}).get("reason", "unknown")
                raise BrainUnavailable("AI_INCOMPLETE", f"the answer was cut off ({why})", "ask more simply")
        if final is None:
            raise BrainUnavailable("AI_OFFLINE", "the answer stream ended early", "try again; check the network")
        if not final.get("output") and items:
            # Verified live 19 Oct 2026: on the ChatGPT-plan route `response.completed` carries output: [] and the
            # finished items only arrive as response.output_item.done events. Assemble the output from those.
            final = {**final, "output": [items[k] for k in sorted(items)]}
        return final

    def _raise_http(self, r: httpx.Response) -> None:
        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            self._raise_code(err.get("code") or "", err.get("param") or "", err.get("message") or "", r.status_code)
        self._raise_code("", "", str(body.get("detail", "")) if isinstance(body, dict) else "", r.status_code)

    def _raise_code(self, code: str, param: str, message: str, status: int) -> None:
        logs.warn("chatgpt", code or f"HTTP_{status}", "ChatGPT plan request failed", param=param, status=status,
                  provider_error=message[:200])
        if code == "subscription_sharing_usage_limit_exceeded":
            self.paused_code = "CHATGPT_LIMIT"
            raise BrainUnavailable("CHATGPT_LIMIT", "ChatGPT plan usage limit reached",
                                   "see ChatGPT Settings > Usage; or switch to the API key yourself")
        if code == "subscription_sharing_unsupported_capability":
            raise BrainUnavailable("CHATGPT_UNSUPPORTED", f"not supported on the ChatGPT plan: {param or message}", param)
        if code in ("subscription_sharing_user_not_eligible", "chatpass_v2_scope_not_authorized",
                    "chatpass_v2_invalid_authorization_context", "subscription_sharing_route_not_supported"):
            raise BrainUnavailable("CHATGPT_NOT_ELIGIBLE", "ChatGPT plan usage is not available for this account/app",
                                   "use an API key (ai.connection = \"api_key\")")
        if code in ("subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable") or status == 503:
            raise BrainUnavailable("CHATGPT_UNAVAILABLE", "ChatGPT plan service is temporarily unavailable", "try again later")
        if code == "subscription_sharing_invalid_user" or status == 401:
            raise BrainUnavailable("CHATGPT_AUTH", "ChatGPT rejected the sign-in", "run `jarves chatgpt login` if it repeats")
        if status == 403:
            raise BrainUnavailable("CHATGPT_NOT_ELIGIBLE", "ChatGPT plan request blocked by policy", "see logs")
        if status == 429:
            raise BrainUnavailable("AI_RATE_LIMIT", "ChatGPT rate limit reached", "wait a minute")
        raise BrainUnavailable("AI_ERROR", f"ChatGPT plan error {code or status}", "see logs")

    async def _request(self, body: dict, on_event=None) -> dict:
        """Bounded backoff for temporary unavailability; drops web search once if the route rejects it."""
        for attempt in range(3):
            try:
                return await self._stream(body, on_event)
            except BrainUnavailable as e:
                if e.code == "CHATGPT_UNSUPPORTED" and self.web_search and "web_search" in (e.action + e.message):
                    self.web_search, self.web_search_note = False, " (not allowed on your plan)"
                    body = {**body, "tools": [t for t in body.get("tools", []) if t.get("type") != "web_search"]}
                    logs.info("chatgpt", "CHATGPT_NO_WEB_SEARCH", "web search not allowed on this plan; continuing without")
                    continue
                if e.code == "CHATGPT_UNAVAILABLE" and attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1) + random.random())
                    continue
                raise
        raise BrainUnavailable("CHATGPT_UNAVAILABLE", "ChatGPT plan service is temporarily unavailable", "try again later")

    # ------------------------------------------------------------------ conversation
    async def reply(self, text: str) -> str:
        ok, code, action = self.available()
        if not ok:
            raise BrainUnavailable(code, action, action)
        try:
            return await bounded_ai(self._turns(text), self.cfg.timeout_s * 2, self.registry)
        except asyncio.TimeoutError as e:
            raise BrainUnavailable("AI_TIMEOUT", "the AI did not answer in time", "try again; check the network") from e
        except BudgetExceeded as e:       # next /responses request (incl. a retry) would pass the daily ceiling
            raise BrainUnavailable("AI_BUDGET", str(e), e.action) from e
        except ChatGPTAuthError as e:
            raise BrainUnavailable(e.code, e.message, e.action) from e
        except httpx.HTTPError as e:
            raise BrainUnavailable("AI_OFFLINE", "cannot reach OpenAI", "check the internet connection") from e

    async def _turns(self, text: str) -> str:
        model = await self._pick_model()
        turn: list[dict] = [{"role": "user", "content": text}]
        calls = 0
        for _ in range(self.cfg.max_turns):
            body = {"model": model, "instructions": self.instructions(), "input": self.history + turn,
                    "tools": self._tools(), "store": False, "stream": True}
            resp = await self._request(body)          # each attempt reserved + counted inside _stream
            calls += 1
            used = self.budget.used("ai_requests")
            outputs = [o for o in resp.get("output", []) if o.get("type") in ("message", "function_call")]
            fcalls = [o for o in outputs if o["type"] == "function_call"]
            turn += [self._clean(o) for o in outputs]
            if not fcalls:
                self.history = (self.history + turn)[-MAX_HISTORY_ITEMS:]
                while self.history and self.history[0].get("role") != "user":
                    self.history.pop(0)       # keep the window starting at a user message
                logs.info("ai", "AI_REPLY", "ChatGPT plan replied", model_calls=calls, used_today=used)
                return self._text(outputs)
            for fc in fcalls:
                res = await self.registry.call(plain_tool_name(fc), fc.get("arguments") or "{}")
                turn.append({"type": "function_call_output", "call_id": fc["call_id"], "output": res.to_model()})
        raise BrainUnavailable("AI_MAX_TURNS", "the request needed too many steps", "ask more simply")

    async def lookup(self, question: str, instructions: str, on_event=None) -> str:
        """One stateless web-search request on the plan (background tasks, tasks.PlanResearchRunner): no history,
        no device tools, nothing added to the conversation."""
        ok, code, action = self.available()
        if not ok:
            raise BrainUnavailable(code, action, action)
        body = {"model": await self._pick_model(), "instructions": instructions,
                "input": [{"role": "user", "content": question}], "store": False, "stream": True,
                "tools": [{"type": "web_search"}] if self.web_search else []}
        resp = await self._request(body, on_event)
        return self._text([o for o in resp.get("output", []) if o.get("type") == "message"])

    @staticmethod
    def _clean(o: dict) -> dict:
        if o["type"] == "function_call":
            keep = {k: o[k] for k in ("type", "call_id", "name", "arguments", "namespace") if k in o}
            return keep
        return {"role": "assistant", "content": ChatGPTBrain._text([o])}

    @staticmethod
    def _text(outputs: list[dict]) -> str:
        parts = []
        for o in outputs:
            if o.get("type") == "message":
                parts += [c.get("text", "") for c in o.get("content", []) if c.get("type") == "output_text"]
        return "".join(parts).strip()
