"""Conversation brain.

1. CommandRouter: deterministic Hebrew/English commands (music, memory, time, status) -> Skill tools directly.
   Free, instant, works with no internet or AI key.
2. AgentBrain: OpenAI Agents SDK agent (Responses API) with the same Skill tools + hosted web search.
   Every model call (each SDK attempt) reserves one `ai_requests` unit from the shared Budget before it is sent
   (BudgetedModel), so the daily ceiling holds mid-run and across concurrent turns. SDK trace upload disabled.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from . import logs
from .budget import Budget, BudgetExceeded
from .config import AIConfig, openai_key_present
from .memory import MemoryStore
from .skills import SkillRegistry, ToolResult


class BrainUnavailable(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


# ------------------------------------------------------------------ deterministic commands
@dataclass
class Intent:
    tool: str
    args: dict
    pattern: str


_FLAGS = re.IGNORECASE
_RULES: list[tuple[str, re.Pattern, Callable[[re.Match], dict]]] = [
    ("forget_all", re.compile(r"^(?:תשכח|שכח|תמחק|מחק)\s+(?:את\s+)?(?:הכל|הכול|כל\s+הזיכרונות)$|^forget everything$", _FLAGS),
     lambda m: {}),
    ("recall", re.compile(r"^(?:מה\s+אתה\s+זוכר(?:\s+עליי|\s+עלי)?|what do you remember(?: about me)?)\??$", _FLAGS),
     lambda m: {}),
    ("remember", re.compile(r"^(?:תזכור|זכור)\s+(?:ש(?=\S)|את\s+זה\s+ש)?(.+)$|^remember(?:\s+that)?\s+(.+)$", _FLAGS),
     lambda m: {"text": (m.group(1) or m.group(2)).strip()}),
    ("forget", re.compile(r"^(?:תשכח|שכח)\s+(?:ש(?=\S)|את\s+)?(.+)$|^forget(?:\s+that)?\s+(.+)$", _FLAGS),
     lambda m: {"match": (m.group(1) or m.group(2)).strip()}),
    ("music_volume", re.compile(r"(?:ווליום|וליום|עוצמה|עוצמת\s+(?:ה)?מוזיקה|volume)\D{0,12}(\d{1,3})", _FLAGS),
     lambda m: {"percent": min(100, int(m.group(1)))}),
    ("music_status", re.compile(r"^(?:מה\s+מתנגן(?:\s+עכשיו)?|איזה\s+שיר\s+זה|what'?s playing|what is playing)\??$", _FLAGS),
     lambda m: {}),
    ("music_next", re.compile(r"^(?:(?:ה)?שיר\s+הבא|הבא|דלג|תדלג|next(?: song)?|skip)$", _FLAGS), lambda m: {}),
    ("music_resume", re.compile(r"^(?:המשך|תמשיך|תמשיך\s+לנגן|resume|continue)$", _FLAGS), lambda m: {}),
    ("music_pause", re.compile(r"^(?:עצור|תעצור|השהה|תשהה|עצור\s+את\s+המוזיקה|תעצור\s+את\s+המוזיקה|pause|stop(?: the)?(?: music)?)$", _FLAGS),
     lambda m: {}),
    ("music_play", re.compile(r"^(?:תנגן|נגן|תשים|שים(?!\s+לב)|play)\s+(?:לי\s+)?(?:את\s+)?(?:(?:שירים|שיר|מוזיקה)\s+של\s+|songs?\s+by\s+|music\s+by\s+)?(.+)$", _FLAGS),
     lambda m: {"query": m.group(1).strip()}),
    ("get_time", re.compile(r"^(?:מה\s+(?:ה)?שעה|כמה\s+(?:ה)?שעה|what time is it|what'?s the time)\??$", _FLAGS), lambda m: {}),
    ("get_status", re.compile(r"^(?:מה\s+המצב\s+שלך|סטטוס|בדיקת\s+מערכת|status|self[- ]?check)\??$", _FLAGS),
     lambda m: {}),
]
_STRIP = re.compile(r"^(?:jarves|jarvis|ג'?ארב(?:י|ו)ס)[,\s]+|[.!?,\s]+$", _FLAGS)


def match_intent(text: str) -> Intent | None:
    t = _STRIP.sub("", text.strip())
    t = _STRIP.sub("", t)
    for tool, pat, args in _RULES:
        m = pat.search(t) if tool == "music_volume" else pat.match(t)
        if m:
            return Intent(tool, args(m), pat.pattern)
    return None


# ------------------------------------------------------------------ AI agent
INSTRUCTIONS = """You are {name}, a friendly home assistant robot with a round face, living on a desk.
Speak Hebrew by default (answer in the user's language if they use another). Replies are spoken aloud:
start with the answer itself, 1-2 short sentences (news: the two biggest headlines, under 12 words each), natural spoken Hebrew, no markdown, no
lists, no URLs, no filler or "let me check" (the voice already said it). Write numbers so they read naturally.
Use tools for actions: music_* to control the user's YouTube Music player, get_time, get_status.
Memory: call `remember` ONLY when the user explicitly asks you to remember something; use `recall`/`forget` on
request; for "what do you remember (about me)" call `recall` with NO query (it lists everything; a query is a
literal text match). Never claim an action succeeded unless the tool result says ok=true; if a tool fails, say what to try.
Use web search for current facts (news, weather, prices); one quick search is enough for a spoken answer; name
the source in two or three words only when it matters.
When asked what you can do or about your skills, explain only the tools actually provided to you and web search
if available. Check get_status before claiming a service is connected. Offer a short example and walk through
one skill at a time when asked; distinguish supported abilities from services that still need setup.
Facts the user asked you to remember:
{memories}"""


# ------------------------------------------------------------------ billing truth (CR-04)
def billing_routes(ai: AIConfig, voice: Any = None) -> dict[str, str]:
    """Who pays for what, per configured connection. Values: "api_key" | "chatgpt_plan" | "local" | "off".

    typed_text    - answers to typed questions (deterministic commands are always local and free)
    spoken_voice  - the spoken conversation itself (GPT-Live/Realtime session, or local push-to-talk STT)
    voice_backend - reasoning / tools / web search behind spoken questions
    reply_speech  - speech that reads typed replies aloud
    No silent paid fallback: the chatgpt connection has no API-key route at all.
    `voice` (optional VoiceConfig) refines the voice routes for voice.mode / voice.tts_engine.
    """
    if ai.provider != "openai":
        r = {"typed_text": "local", "spoken_voice": "local", "voice_backend": "local", "reply_speech": "local"}
    elif ai.connection == "api_key":
        r = {"typed_text": "api_key", "spoken_voice": "api_key", "voice_backend": "api_key", "reply_speech": "api_key"}
    elif ai.connection == "chatgpt":
        # the plan has no voice: local push-to-talk STT, transcript answered by the plan text brain, local speech
        r = {"typed_text": "chatgpt_plan", "spoken_voice": "local", "voice_backend": "chatgpt_plan",
             "reply_speech": "local"}
    else:  # combined: GPT-Live delegates its reasoning/web search to the API-key backend, NOT to the plan
        r = {"typed_text": "chatgpt_plan", "spoken_voice": "api_key", "voice_backend": "api_key",
             "reply_speech": "api_key"}
    if voice is not None:
        mode = getattr(voice, "mode", "live")
        if mode == "off":
            r["spoken_voice"] = r["voice_backend"] = "off"
        elif mode == "push_to_talk" and r["spoken_voice"] == "api_key":
            r["spoken_voice"] = "local"                    # local STT; the transcript goes to the typed-text brain
            r["voice_backend"] = r["typed_text"]
        engine = getattr(voice, "tts_engine", "auto")
        if engine == "none":
            r["reply_speech"] = "off"
        elif engine == "local":
            r["reply_speech"] = "local"
    return r


ROUTE_LABEL = {"api_key": "API key, billed", "chatgpt_plan": "ChatGPT plan", "local": "local, free", "off": "off"}


def billing_summary(ai: AIConfig, voice: Any = None) -> str:
    """One human line from billing_routes(), e.g. for health/status text."""
    r = billing_routes(ai, voice)
    if ai.provider == "openai" and ai.connection == "combined" and r["spoken_voice"] == "api_key":
        return ("typed answers: ChatGPT plan; spoken conversation (GPT-Live voice + its backend reasoning/web "
                f"search): API key, billed; speech for typed replies: {ROUTE_LABEL[r['reply_speech']]}")
    return (f"typed answers: {ROUTE_LABEL[r['typed_text']]}; spoken conversation: {ROUTE_LABEL[r['spoken_voice']]}"
            f" (its reasoning/web search: {ROUTE_LABEL[r['voice_backend']]}); "
            f"speech for typed replies: {ROUTE_LABEL[r['reply_speech']]}")


def _budgeted_model_cls():
    """BudgetedModel subclasses the SDK Model ABC; built lazily so importing brain.py stays light."""
    global _BudgetedModel
    if _BudgetedModel is not None:
        return _BudgetedModel
    from agents.models.interface import Model

    class BudgetedModel(Model):
        """Wraps the SDK model: every get_response/stream_response attempt (including SDK-managed retries)
        reserves 1 ai_requests unit before it is sent and commits it afterwards, also on failure/cancel.
        Raises BudgetExceeded instead of sending once used + in-flight reaches the daily ceiling."""

        def __init__(self, inner: Model, budget: Budget) -> None:
            self.inner, self.budget = inner, budget

        async def get_response(self, *args, **kwargs):
            res = self.budget.reserve("ai_requests")
            try:
                return await self.inner.get_response(*args, **kwargs)
            finally:
                res.commit()            # sent or unknown -> counted (conservative)

        async def stream_response(self, *args, **kwargs) -> AsyncIterator[Any]:
            res = self.budget.reserve("ai_requests")
            try:
                async for ev in self.inner.stream_response(*args, **kwargs):
                    yield ev
            finally:
                res.commit()

        def get_retry_advice(self, request):
            return self.inner.get_retry_advice(request)

        async def _cleanup_on_run_end(self, owner: object) -> None:
            await self.inner._cleanup_on_run_end(owner)

        async def close(self) -> None:
            await self.inner.close()

    _BudgetedModel = BudgetedModel
    return BudgetedModel


_BudgetedModel = None


class AgentBrain:
    connection = "api_key"

    def __init__(self, cfg: AIConfig, registry: SkillRegistry, memory: MemoryStore, name: str = "JARVES",
                 budget: Budget | None = None, *, model: Any = None) -> None:
        """budget: the Core's shared Budget (None = own Budget on the same persisted counter).
        model: optional SDK Model to wrap instead of the provider default (tests pass agents.testing models)."""
        self.cfg, self.registry, self.memory, self.name = cfg, registry, memory, name
        self.budget = budget or Budget(memory, cfg)
        self._inner_model = model
        self._session = None
        self._agent = None

    def available(self) -> tuple[bool, str, str]:
        if self.cfg.provider != "openai":
            return False, "AI_DISABLED", "AI provider is set to offline in config"
        if not openai_key_present():
            return False, "AI_NO_KEY", "run `jarvis key` to save your OpenAI API key, then restart"
        if self.cfg.daily_request_limit == 0:
            return False, "AI_BUDGET", "ai.daily_request_limit is 0"
        if self.budget.exhausted("ai_requests"):
            return False, "AI_BUDGET", f"daily AI request limit ({self.cfg.daily_request_limit}) reached; raise ai.daily_request_limit"
        return True, "ok", ""

    def describe(self) -> str:
        ws = "on" if self.cfg.web_search else "off"
        return f"OpenAI API key, {self.cfg.text_model} (web search {ws}) — {billing_summary(self.cfg)}"

    def instructions(self) -> str:
        mems = self.memory.list(20)
        return INSTRUCTIONS.format(name=self.name, memories="\n".join(f"- {m['text']}" for m in mems) or "- (none)")

    def _build(self):
        from agents import Agent, SQLiteSession, WebSearchTool, set_tracing_disabled
        from agents.models.multi_provider import MultiProvider
        set_tracing_disabled(True)
        tools = self.registry.agent_tools()
        if self.cfg.web_search:
            tools.append(WebSearchTool())
        inner = self._inner_model or MultiProvider().get_model(self.cfg.text_model)
        self._agent = Agent(name=self.name, instructions=lambda ctx, agent: self.instructions(),
                            model=_budgeted_model_cls()(inner, self.budget), tools=tools)
        self._session = self._session or SQLiteSession("conversation")   # in RAM only; cleared on restart

    def reset_conversation(self) -> None:
        self._session = None

    async def reply(self, text: str) -> str:
        ok, code, action = self.available()
        if not ok:
            raise BrainUnavailable(code, action, action)
        import openai
        from agents import Runner
        from agents.exceptions import MaxTurnsExceeded
        if self._agent is None:
            self._build()
        try:
            res = await asyncio.wait_for(Runner.run(self._agent, text, session=self._session, max_turns=self.cfg.max_turns),
                                         self.cfg.timeout_s)
        except asyncio.TimeoutError as e:
            raise BrainUnavailable("AI_TIMEOUT", "the AI did not answer in time", "try again; check the network") from e
        except openai.AuthenticationError as e:
            raise BrainUnavailable("AI_AUTH", "OpenAI rejected the API key", "check OPENAI_API_KEY") from e
        except openai.RateLimitError as e:
            raise BrainUnavailable("AI_RATE_LIMIT", "OpenAI rate limit or quota reached", "wait, or check billing") from e
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise BrainUnavailable("AI_OFFLINE", "cannot reach OpenAI", "check the internet connection") from e
        except openai.APIStatusError as e:
            raise BrainUnavailable("AI_ERROR", f"OpenAI error HTTP {e.status_code}", "see logs") from e
        except MaxTurnsExceeded as e:
            raise BrainUnavailable("AI_MAX_TURNS", "the request needed too many steps", "ask more simply") from e
        except BudgetExceeded as e:     # raised by BudgetedModel instead of sending the next model call
            raise BrainUnavailable("AI_BUDGET", str(e), e.action) from e
        n = len(getattr(res, "raw_responses", []) or [])   # already counted per call by BudgetedModel
        logs.info("ai", "AI_REPLY", "agent replied", model_calls=n, used_today=self.budget.used("ai_requests"))
        return str(res.final_output or "").strip()


_CITE = re.compile(r"\s*\(\s*\[[^\]]*\]\(https?://[^)]*\)\s*\)")      # ([gov.uk](https://...))
_LINK = re.compile(r"\[([^\]]+)\]\(https?://[^)]*\)")                      # [text](https://...)
_URL = re.compile(r"https?://\S+")


def speakable(text: str) -> str:
    """Spoken/captioned replies must not contain links or markdown. The ChatGPT-plan route (web search) returns
    citations like ([gov.uk](https://...)): drop them (found 19 Oct), keep the words."""
    t = _CITE.sub("", text or "")
    t = _LINK.sub(lambda m: m.group(1), t)
    t = _URL.sub("", t)
    t = re.sub(r"[*_`#]{1,3}", "", t)
    return re.sub(r"[ ]{2,}", " ", t).strip()


# ------------------------------------------------------------------ facade
OFFLINE_HELP = ("אני עובד כרגע בלי בינה מלאכותית, אז אני מבין רק פקודות: נגן, עצור, הבא, עוצמה, מה השעה, "
                "תזכור ש..., מה אתה זוכר, סטטוס.")


@dataclass
class Reply:
    text: str
    source: str            # "command" | "ai" | "offline"
    tool: str | None = None
    ok: bool = True
    code: str = "ok"


class Brain:
    def __init__(self, registry: SkillRegistry, agent: AgentBrain | None) -> None:
        self.registry, self.agent = registry, agent

    async def respond(self, text: str) -> Reply:
        intent = match_intent(text)
        if intent and self.registry.get(intent.tool):
            # the deterministic parser matched the user's own words -> origin "user" (CR-03 policy)
            res: ToolResult = await self.registry.call(intent.tool, intent.args, origin="user")
            return Reply(res.message, "command", intent.tool, res.ok, res.code)
        if self.agent is None:
            return Reply(OFFLINE_HELP, "offline", ok=False, code="AI_DISABLED")
        try:
            return Reply(speakable(await self.agent.reply(text)) or "…", "ai")
        except BrainUnavailable as e:
            logs.warn("ai", e.code, e.message, action=e.action)
            return Reply(OFFLINE_HELP, "offline", ok=False, code=e.code)
