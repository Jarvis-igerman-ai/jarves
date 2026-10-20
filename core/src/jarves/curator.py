"""Background memory curator: after a conversation (session end, or a quiet typed chat) it extracts DURABLE facts
and preferences the user said about themself and stores them with provenance (session id, date, confidence).

1. Local rules (always, free, offline): Hebrew + English patterns for name, how to be addressed, where the user
   lives, work, birthday, family/pet names, favourites, likes/dislikes, preferred language. Only the USER's own
   words are used; questions, "remember that..." (already handled by `remember`) and anything that looks like a
   secret (passwords, codes, card/ID numbers) are skipped.
2. Optional LLM pass (setting curator.llm = on, default off): ONE short backend request per conversation through
   the shared Budget (ai_requests) and at most LLM_DAILY_MAX a day; only on the API-key route.

Facts are deduplicated (MemoryStore.upsert), listed by `recall` / the dashboard with source "auto", deletable
one by one (`forget`) or per conversation (`forget_session`), and summarised in workspace memory/YYYY-MM-DD.md.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from . import logs
from .memory import MAX_TEXT, MemoryStore

MIN_CONFIDENCE = 0.6        # below this a candidate is dropped
MAX_TURNS = 200             # transcript kept per conversation (RAM only, never written to disk)
MAX_CHARS = 20_000
IDLE_S = 120.0              # typed chat: curate after this much quiet
JOIN_S = 2.5               # live: a user transcript tail arriving this soon is glued to the previous line
LLM_DAILY_MAX = 10
LLM_INPUT_CHARS = 6000

_SENSITIVE = re.compile(r"סיסמ|קוד\s|קוד$|הקוד|פין|אשראי|תעודת\s+זהות|ת\"ז|חשבון\s+(?:ה)?בנק|password|passcode|"
                        r"\bpin\b|credit\s*card|\bssn\b|\d{6,}|\d[\d -]{10,}\d", re.I)
_SKIP = re.compile(r"^(?:תזכור|זכור|תשכח|שכח|remember|forget)\b", re.I)
_STOP_WORDS = {"ואני", "אני", "ו", "אבל", "וגם", "גם", "היום", "עכשיו", "כי", "and", "but", "i", "today", "now"}
_TAIL = re.compile(r"\s+(?:אבל|כי|ואני|וגם|ו?בגלל|but|because|and\s+i)\b.*$", re.I)
_W = r"[א-תA-Za-z][א-תA-Za-z'\-]*"          # one Hebrew/Latin word
_POSSESSIVE = {"אשתי": "אישה", "בעלי": "בעל", "אחי": "אח", "אחותי": "אחות", "בני": "בן", "בתי": "בת",
               "חברתי": "חברה", "חברי": "חבר"}


@dataclass
class Candidate:
    text: str
    kind: str              # identity | preference | relation | fact
    confidence: float


def _words(s: str, n: int) -> str:
    out = []
    for w in _TAIL.sub("", s.strip()).split()[:n]:
        # a 2nd word starting with ו is "and ..." (real run 19 Oct: "קוראים לי איגל והכלב שלי..." -> "איגל והכלב")
        if out and (w.lower() in _STOP_WORDS or (w.startswith("ו") and len(w) > 2)):
            break
        out.append(w)
    return " ".join(out).strip(" .,!?:;\"'")


def _rel_noun(word: str) -> str:
    return _POSSESSIVE.get(word, word)


def _bare(word: str) -> str:
    """'שהצבע' / 'הצבע' -> 'צבע' (drops the attached ש/ו/ה prefixes)."""
    for p in ("ש", "ו", "ה"):
        word = word.removeprefix(p) if len(word) > 3 else word
    return word


# (pattern, builder(match) -> Candidate | None)
_RULES: list[tuple[re.Pattern, Callable[[re.Match], Candidate | None]]] = [
    (re.compile(rf"(?:קוראים\s+לי|השם\s+שלי(?:\s+הוא|\s+זה)?|שמי)\s+({_W}(?:\s+{_W})?)"),
     lambda m: Candidate(f"שם המשתמש: {_words(m.group(1), 2)}", "identity", 0.9)),
    (re.compile(rf"\bmy name is ({_W}(?:\s+{_W})?)", re.I),
     lambda m: Candidate(f"The user's name is {_words(m.group(1), 2)}", "identity", 0.9)),
    (re.compile(r"(?:תקרא|תקראי|תפנה|תפני)\s+(?:לי|אליי|אלי)\s+(?:בשם\s+)?(.{2,30})"),
     lambda m: Candidate(f"המשתמש רוצה שיפנו אליו: {_words(m.group(1), 3)}", "preference", 0.85)),
    (re.compile(rf"\bcall me ({_W})", re.I),
     lambda m: Candidate(f"The user wants to be called {_words(m.group(1), 1)}", "preference", 0.85)),
    (re.compile(r"אני\s+(?:גר|גרה|מתגורר|מתגוררת)\s+(ב-?|באזור\s+|ליד\s+)\s*(.{2,40})"),
     lambda m: Candidate(f"המשתמש גר {m.group(1).strip().rstrip('-')}{_words(m.group(2), 3)}", "fact", 0.8)),
    (re.compile(r"\bi live in (.{2,40})", re.I),
     lambda m: Candidate(f"The user lives in {_words(m.group(1), 3)}", "fact", 0.8)),
    (re.compile(r"אני\s+(?:עובד|עובדת)\s+(ב-?|כ-?)\s*(.{2,40})"),
     lambda m: Candidate(f"המשתמש עובד {m.group(1).rstrip('-')}{_words(m.group(2), 4)}", "fact", 0.75)),
    (re.compile(r"\bi work (as|at|in) (.{2,40})", re.I),
     lambda m: Candidate(f"The user works {m.group(1).lower()} {_words(m.group(2), 4)}", "fact", 0.75)),
    (re.compile(r"(?:יום\s+ה?הולדת\s+שלי|נולדתי)\s+(?:הוא\s+|זה\s+)?(?:ב-?\s*)?(.{2,25})"),
     lambda m: Candidate(f"יום ההולדת של המשתמש: {_words(m.group(1), 4)}", "fact", 0.85)),
    (re.compile(r"\bmy birthday is (?:on )?(.{2,25})", re.I),
     lambda m: Candidate(f"The user's birthday is {_words(m.group(1), 4)}", "fact", 0.85)),
    (re.compile(rf"(?:(?:ל)?(?:ה)?(כלב|כלבה|חתול|חתולה|בן|בת|ילד|ילדה|אח|אחות|חבר|חברה)\s+שלי|"
                rf"(אשתי|בעלי|אחי|אחותי|בני|בתי|חברתי|חברי))\s+(?:נקרא|נקראת|קוראים(?:\s+(?:לו|לה))?|שמו|שמה)\s+({_W})"),
     lambda m: Candidate(f"ל{_rel_noun(m.group(1) or m.group(2))} של המשתמש קוראים {_words(m.group(3), 1)}",
                         "relation", 0.85)),
    (re.compile(rf"יש\s+לי\s+(כלב|כלבה|חתול|חתולה|בן|בת|ילד|ילדה)\s+(?:בשם|שקוראים\s+(?:לו|לה)|שנקרא|שנקראת)\s+({_W})"),
     lambda m: Candidate(f"ל{m.group(1)} של המשתמש קוראים {_words(m.group(2), 1)}", "relation", 0.85)),
    (re.compile(rf"\bmy (dog|cat|son|daughter|wife|husband|brother|sister)(?:'s name)? is (?:called |named )?({_W})", re.I),
     lambda m: Candidate(f"The user's {m.group(1).lower()} is called {_words(m.group(2), 1)}", "relation", 0.85)),
    (re.compile(r"(?:ה)?(\S{2,15})\s+(?:ה)?(אהוב|אהובה|האהוב|האהובה)\s+(?:עליי|עלי|שלי)\s+(?:הוא\s+|היא\s+|זה\s+|זו\s+)?(.{1,40})"),
     lambda m: Candidate(f"ה{_bare(m.group(1))} ה{_bare(m.group(2))} על המשתמש: {_words(m.group(3), 4)}",
                         "preference", 0.85)),
    (re.compile(r"\bmy fav(?:ou?rite)? (\w+) is (.{1,40})", re.I),
     lambda m: Candidate(f"The user's favourite {m.group(1).lower()} is {_words(m.group(2), 4)}", "preference", 0.85)),
    (re.compile(r"(?:תדבר|תדברי|תענה|תעני)\s+(?:איתי|אליי|לי)\s+(?:תמיד\s+)?ב(עברית|אנגלית|רוסית|ערבית|צרפתית)"),
     lambda m: Candidate(f"המשתמש מעדיף שידברו איתו ב{m.group(1)}", "preference", 0.85)),
    (re.compile(r"אני\s+(?:ממש\s+|מאוד\s+)?(אוהב|אוהבת|לא\s+אוהב|לא\s+אוהבת|שונא|שונאת|מעדיף|מעדיפה|מת\s+על|מתה\s+על)\s+(.{2,60})"),
     lambda m: _like(m.group(1), m.group(2), "he")),
    (re.compile(r"\bi (?:really )?(like|love|hate|prefer|don't like|do not like) (.{2,60})", re.I),
     lambda m: _like(m.group(1), m.group(2), "en")),
]
_NOT_AN_OBJECT = re.compile(r"^(?:אותך|אותו|אותה|אותם|את\s+זה|זה|כשאתה|כשאת|לשמוע\s+אותך|you|it|that|this|when you)\b", re.I)
_HE_VERB = {"אוהב": "אוהב", "אוהבת": "אוהב", "לא אוהב": "לא אוהב", "לא אוהבת": "לא אוהב", "שונא": "לא אוהב",
            "שונאת": "לא אוהב", "מעדיף": "מעדיף", "מעדיפה": "מעדיף", "מת על": "אוהב מאוד", "מתה על": "אוהב מאוד"}
_EN_VERB = {"like": "likes", "love": "loves", "hate": "does not like", "prefer": "prefers",
            "don't like": "does not like", "do not like": "does not like"}


def _like(verb: str, obj: str, lang: str) -> Candidate | None:
    obj = _words(obj, 6)
    if not obj or _NOT_AN_OBJECT.match(obj):
        return None
    if lang == "he":
        return Candidate(f"המשתמש {_HE_VERB[' '.join(verb.split())]} {obj}", "preference", 0.7)
    return Candidate(f"The user {_EN_VERB[verb.lower()]} {obj}", "preference", 0.7)


def extract_rules(user_texts: list[str]) -> list[Candidate]:
    """Durable facts from what the USER said (pure function; no network)."""
    out: list[Candidate] = []
    for utterance in user_texts:
        for sentence in re.split(r"(?<=[.!?\n])\s+|[.!\n]+", utterance):
            s = re.sub(r"^(?:jarves|jarvis|ג'?ארו?ו?יס|ג'?ארבי?ס)[,\s]+", "", sentence.strip(), flags=re.I)
            if len(s) < 4 or "?" in s or _SKIP.match(s) or _SENSITIVE.search(s):
                continue
            for pat, build in _RULES:
                m = pat.search(s)
                if not m:
                    continue
                c = build(m)
                if c and len(c.text) <= MAX_TEXT and not c.text.rstrip().endswith(":") and not _SENSITIVE.search(c.text):
                    out.append(c)
    return out


LLM_INSTRUCTIONS = """You curate a home assistant's long-term memory. From the conversation, list ONLY durable
facts or preferences the USER stated about themself or their household (name, family, pets, where they live,
work, likes/dislikes, how they want to be spoken to). Ignore questions, one-off requests, news, anything the
assistant said, and anything secret (passwords, codes, numbers, health or money details).
Write each fact as one short third-person sentence in the conversation's language ("המשתמש אוהב קפה שחור").
Answer with JSON only: {"facts": [{"fact": "...", "confidence": 0.0-1.0}]} (empty list if nothing)."""

LLMFn = Callable[[str], Awaitable[list[dict]]]


def openai_extractor(model: str, budget, *, timeout_s: float = 20.0) -> LLMFn:
    """One Responses request per call, reserved from the shared Budget first (raises BudgetExceeded)."""
    async def run(transcript: str) -> list[dict]:
        import openai
        client = openai.AsyncOpenAI(timeout=timeout_s)
        with budget.reserve("ai_requests"):
            r = await client.responses.create(model=model, instructions=LLM_INSTRUCTIONS, input=transcript,
                                              max_output_tokens=400, reasoning={"effort": "none"}, store=False)
        raw = (getattr(r, "output_text", "") or "").strip()
        raw = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else "{}"
        facts = json.loads(raw).get("facts", [])
        return [f for f in facts if isinstance(f, dict)]
    return run


class Curator:
    def __init__(self, memory: MemoryStore, workspace=None, *, llm: LLMFn | None = None,
                 enabled: Callable[[str], bool] | None = None, idle_s: float = IDLE_S) -> None:
        """enabled(key) answers "curator.auto" / "curator.llm" (the Settings overrides); default: rules on, LLM off."""
        self.memory, self.workspace, self.llm = memory, workspace, llm
        self.enabled = enabled or (lambda key: key == "curator.auto")
        self.idle_s = idle_s
        self.turns: list[tuple[str, str]] = []
        self.session = self._new_session()
        self._chars = 0
        self._last_user: tuple[int, float] | None = None   # (index in turns, time) of the last live user line
        self._idle: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.last: list[dict] = []          # what the last run saved (diagnostics / dashboard)

    @staticmethod
    def _new_session() -> str:
        return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"

    # ------------------------------------------------------------------ input
    def observe(self, role: str, text: str, *, idle: bool = False, live: bool = False) -> None:
        """Add one transcript line (RAM only). idle=True: curate after IDLE_S of quiet (typed chat)."""
        text = (text or "").strip()
        if not text or role not in ("user", "assistant"):
            return
        if len(self.turns) >= MAX_TURNS or self._chars + len(text) > MAX_CHARS:
            self.turns.pop(0) if self.turns else None
            self._last_user = None
        now = time.monotonic()
        if live and role == "user" and self._last_user is not None and now - self._last_user[1] <= JOIN_S:
            # GPT-Live's input transcript is cut when the answer starts and its tail arrives as a separate line,
            # even mid-word (real run 19 Oct: "...הכלב שלי נקרא בנ" + "זו"): glue the tail back on.
            i, _ = self._last_user
            prev = self.turns[i][1] if i < len(self.turns) and self.turns[i][0] == "user" else None
            if prev is not None and not prev.endswith((".", "!", "?", ",")):
                glue = "" if len(text.split()) == 1 and len(text) <= 4 else " "
                self.turns[i] = ("user", (prev + glue + text)[:2000])
                self._last_user = (i, now)
                self._chars = sum(len(t) for _, t in self.turns)
                if idle:
                    self._arm_idle()
                return
        self.turns.append((role, text[:2000]))
        if role == "user" and live:
            self._last_user = (len(self.turns) - 1, now)
        self._chars = sum(len(t) for _, t in self.turns)
        if idle:
            self._arm_idle()

    def _arm_idle(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._idle is not None and not self._idle.done():
            self._idle.cancel()

        async def later():
            await asyncio.sleep(self.idle_s)
            await self.run("idle")
        self._idle = loop.create_task(later())

    def cancel(self) -> None:
        if self._idle is not None and not self._idle.done():
            self._idle.cancel()

    # ------------------------------------------------------------------ curate
    async def run(self, reason: str = "session_end") -> list[dict]:
        """Curate the buffered conversation, then start a new one. Never raises (a broken pass loses nothing
        the user explicitly asked to keep)."""
        async with self._lock:
            turns, session = self.turns, self.session
            self.turns, self._chars, self.session = [], 0, self._new_session()
            self._last_user = None
            if not turns:
                return []
            saved: list[dict] = []
            try:
                cands: list[Candidate] = []
                if self.enabled("curator.auto"):
                    cands += extract_rules([t for r, t in turns if r == "user"])
                if self.llm is not None and self.enabled("curator.llm"):
                    cands += await self._llm_pass(turns)
                for c in cands:
                    if c.confidence < MIN_CONFIDENCE:
                        continue
                    mid, created = self.memory.upsert(c.text, source="auto", session=session,
                                                      confidence=c.confidence, kind=c.kind)
                    if created:
                        saved.append({"id": mid, "text": c.text, "kind": c.kind, "confidence": c.confidence,
                                      "session": session})
                if saved and self.workspace is not None:
                    self.workspace.daily_note(
                        f"[{session}] learned ({s['kind']}, {s['confidence']:.2f}, memory #{s['id']}): {s['text']}"
                        for s in saved)
            except Exception:
                logs.error("memory", "CURATOR_FAILED", "memory curator failed", exc=True)
            self.last = saved
            logs.info("memory", "CURATOR_DONE", f"curator saved {len(saved)} new fact(s)", reason=reason,
                      session=session, turns=len(turns), saved=len(saved), facts=[s["text"] for s in saved])
            return saved

    async def _llm_pass(self, turns: list[tuple[str, str]]) -> list[Candidate]:
        user_chars = sum(len(t) for r, t in turns if r == "user")
        if user_chars < 40 or self.memory.usage_today("curator_llm") >= LLM_DAILY_MAX:
            return []
        transcript = "\n".join(f"{'USER' if r == 'user' else 'ASSISTANT'}: {t}" for r, t in turns)[-LLM_INPUT_CHARS:]
        self.memory.bump_usage("curator_llm")
        try:
            facts = await self.llm(transcript)
        except Exception as e:
            logs.warn("memory", "CURATOR_LLM_FAILED", f"LLM memory pass failed: {type(e).__name__}")
            return []
        out = []
        for f in facts[:10]:
            text = " ".join(str(f.get("fact", "")).split())[:MAX_TEXT]
            try:
                conf = float(f.get("confidence", 0))
            except (TypeError, ValueError):
                conf = 0.0
            if text and not _SENSITIVE.search(text):
                out.append(Candidate(text, "fact", max(0.0, min(0.95, conf))))   # never as sure as the user's word
        return out

    def forget_session(self, session: str) -> int:
        return self.memory.delete_session(session)
