"""Conversation brain.

1. CommandRouter: deterministic Hebrew/English commands (music, memory, time, status) -> Skill tools directly.
   Free, instant, works with no internet or AI key.
2. AgentBrain: OpenAI Agents SDK agent (Responses API) with the same Skill tools + hosted web search.
   Metered by a persisted daily request ceiling; SDK trace upload disabled for privacy.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Callable

from . import logs
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
    ("get_time", re.compile(r"^(?:מה\s+השעה|כמה\s+השעה|what time is it|what'?s the time)\??$", _FLAGS), lambda m: {}),
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
keep them short (1-3 sentences), natural, no markdown, no lists, no URLs read out.
Use tools for actions: music_* to control the user's YouTube Music player, get_time, get_status.
Memory: call `remember` ONLY when the user explicitly asks you to remember something; use `recall`/`forget` on
request. Never claim an action succeeded unless the tool result says ok=true; if a tool fails, say what to try.
Use web search for current facts (news, weather, prices) and say briefly where the answer came from.
Facts the user asked you to remember:
{memories}"""


class AgentBrain:
    def __init__(self, cfg: AIConfig, registry: SkillRegistry, memory: MemoryStore, name: str = "JARVES") -> None:
        self.cfg, self.registry, self.memory, self.name = cfg, registry, memory, name
        self._session = None
        self._agent = None

    def available(self) -> tuple[bool, str, str]:
        if self.cfg.provider != "openai":
            return False, "AI_DISABLED", "AI provider is set to offline in config"
        if not openai_key_present():
            return False, "AI_NO_KEY", "set the OPENAI_API_KEY environment variable and restart"
        if self.cfg.daily_request_limit and self.memory.usage_today("ai_requests") >= self.cfg.daily_request_limit:
            return False, "AI_BUDGET", f"daily AI request limit ({self.cfg.daily_request_limit}) reached; raise ai.daily_request_limit"
        if self.cfg.daily_request_limit == 0:
            return False, "AI_BUDGET", "ai.daily_request_limit is 0"
        return True, "ok", ""

    def instructions(self) -> str:
        mems = self.memory.list(20)
        return INSTRUCTIONS.format(name=self.name, memories="\n".join(f"- {m['text']}" for m in mems) or "- (none)")

    def _build(self):
        from agents import Agent, SQLiteSession, WebSearchTool, set_tracing_disabled
        set_tracing_disabled(True)
        tools = self.registry.agent_tools()
        if self.cfg.web_search:
            tools.append(WebSearchTool())
        self._agent = Agent(name=self.name, instructions=lambda ctx, agent: self.instructions(),
                            model=self.cfg.text_model, tools=tools)
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
        finally:
            pass
        n = max(1, len(getattr(res, "raw_responses", []) or []))
        used = self.memory.bump_usage("ai_requests", n)
        logs.info("ai", "AI_REPLY", "agent replied", model_calls=n, used_today=used)
        return str(res.final_output or "").strip()


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
            res: ToolResult = await self.registry.call(intent.tool, intent.args)
            return Reply(res.message, "command", intent.tool, res.ok, res.code)
        if self.agent is None:
            return Reply(OFFLINE_HELP, "offline", ok=False, code="AI_DISABLED")
        try:
            return Reply(await self.agent.reply(text) or "…", "ai")
        except BrainUnavailable as e:
            logs.warn("ai", e.code, e.message, action=e.action)
            return Reply(OFFLINE_HELP, "offline", ok=False, code=e.code)
