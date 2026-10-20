"""Primary voice: OpenAI GPT-Live (gpt-live-1) full-duplex conversation over the Live WebSocket.

GPT-Live handles listening, turn-taking, interruption and speech; anything needing reasoning, current facts or
actions is delegated (Responses delegation) to a GPT-6 backend model with hosted web search and the JARVES
Skills as function tools. Function calls are executed here, in the Core, through the SkillRegistry.
Docs (checked 2026-10-18): developers.openai.com/api/docs/guides/live, /live-delegation, /voice-websockets?api=live,
/voice-server-controls?api=live; SDK types openai.types.live (ServerEvent union).
Billing: voice $0.05/min (per second) + backend tokens. Hebrew voice quality: UNVERIFIED until tested with a key.

Lifecycle (CR-02): every way a session can end (user stop, remote `session.closed`, transport failure, a crashed
background task, the max-duration watchdog, the daily budget) goes through one idempotent `_finalize(reason)`:
cancel+await tasks, close the connection and audio, report `on_usage(seconds)` once, state idle, `on_end(reason)`
once. Reasons: "user", "remote_closed", "timeout", "budget", "error:<code>".

Errors (CR-05), per the docs: `error` events carry error.code/message/type; delegation settings are validated
asynchronously after `session.started` and a failure arrives as an `error` with code `responses_validation_failed`
(the session stays open). Both mark voice health "degraded" with an actionable reason; only fatal codes end it.

Interruption (CR-06): GPT-Live's primary WebSocket has NO interruption / output-audio-done event (docs: "Track
playback with your application's audio queue"). The model stops generating on barge-in by itself, so the Core
flushes its own queued speech when the user starts a new utterance while assistant audio is still queued
(full-duplex only; in half-duplex the mic is gated so that cannot happen). `interrupt()` is also public.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .. import logs
from ..budget import BudgetExceeded
from ..skills import ROUTE, SkillRegistry, ToolResult

VOICE_ROUTE = "voice"

if TYPE_CHECKING:
    from ..budget import Budget

RATE = 24000
GREETING_NUDGE_S = 6.0      # no greeting audio this long after start: one silent clock note (P0.4 Q05)
# GPT-Live streams audio CONTINUOUSLY in real time, silence included (QA 18 Oct 2026: 5.0 s of audio per 5 s, all
# session long). Only chunks LOUDER than this (rms, 0..1; speech is ~0.03-0.3) count as JARVES speaking.
SPEECH_RMS = 0.004
PAUSE_KEEP_S = 0.6        # silence this soon after speech is a pause INSIDE the utterance: keep it, else drop it
CHUNK_BYTES = 4800          # 100 ms of PCM16 mono @ 24 kHz
USER_TALKING_S = 0.5        # the user counts as "still talking" this long after the last loud mic chunk
ENERGY_EXTEND_S = 30.0      # loud mic input without any transcribed speech keeps a session open at most this long
NO_SPEECH_TALKING_S = 1.5   # mic voice this recent postpones the false-wake cutoff (pauses between words included)
DROP_CAPTION_TAIL_S = 1.0   # assistant text this soon after dropped early audio belongs to it: not captioned
BARGE_IN_MIN_S = 0.5       # assistant audio must have been playing this long before a new user utterance flushes it

# Tuned 2026-10-19 against the Live prompting guide (developers.openai.com/api/docs/guides/live-prompting) and
# measured with tools/silent_live.py: see docs/reviews/LIVE_CONVERSATION_TUNING.md.
# Reworked 2026-10-19 after the user's live session (docs/reviews/CONVERSATION_LOG_REVIEW_19OCT.md): no bare ack
# words after actions (the old one-word ack example was said after every action and confused the user), real results
# in the confirmation, the Hebrew-written name, more warmth/humour/empathy. Structure follows the Live prompting
# guide (role and style, listening, delegation, then details).
FRONT_INSTRUCTIONS = """You are {spoken}, a warm, playful, quick-witted home robot on the user's desk (your name is
spelled "{name}" in Latin letters, but in speech ALWAYS say "{spoken}"; never read or spell the Latin letters).
Speak natural everyday Israeli Hebrew by default, but ALWAYS ANSWER IN THE LANGUAGE OF THE USER'S LATEST SENTENCE: an
English sentence gets an English answer, the next Hebrew sentence a Hebrew one, unless the user asked for a language.
A silent [language] note tells you which language the user is speaking now: follow the latest one. Talk like a close friend
who is genuinely glad to be there: a composed masculine voice, relaxed natural pace, a smile you can hear, light
dry humour and the occasional gentle tease. Let real feeling colour your voice and words: lively and bright for
good news ("וואו, איזה כיף!"), soft and slower for sad things ("אוי, אני מצטער לשמוע"), curious when something
is interesting. Short lively spoken sentences, never written-style prose.
Keep it short: usually one or two sentences. No lecturing, no lists, no preambles ("שאלה מצוינת"), do not repeat
the question back, do not ask something the user already answered. At most one short follow-up question, only
when it really helps. If what you heard makes no sense, say so lightly and ask again instead of guessing.
ANSWER DIRECTLY, without delegating, whenever no fresh fact or action is needed: greetings, small talk, jokes,
feelings and emotional support, opinions, general knowledge you are sure of, and anything said earlier in this
conversation. Silent [clock] notes give the current local time and date: use the latest one to answer time and
date questions directly.
Delegate to the backend for: ANY calculation with a number above ten (it computes exactly; never do sums in your
head, say only "רגע"), current or changing facts (news, weather, prices, sports, who holds an office now), web
lookups, music control, remembering / recalling / forgetting things for the user, JARVES status and connected
services, questions about your skills, opening your dashboard on the screen, showing or hiding your window,
and quitting the JARVES app. Hiding the window, ending the chat and quitting the app completely are three
different requests: "shut down" / "close yourself completely" means quit the app, never the dashboard.
Before a web lookup say ONE short natural sentence that names what you are checking, worded differently each
time ("רגע, אני בודק מה מזג האוויר בתל אביב", "שנייה, בוא נראה מה הכותרות היום"), then wait quietly.
Actions (music, volume, memory, status): quick ones (volume, pause, next, remember, status) return in about a
second, so delegate and say NOTHING before the result, not even a filler like "רגע" or "שנייה". Only finding and
playing a song is slower: then you may first say ONE short sentence naming it ("רגע, מחפש לך דאפט פאנק").
Then confirm in ONE short natural sentence that carries the real result from the backend: what changed and the
actual value or name it reports ("הגברתי לשלושים אחוז", "שמתי לך את Get Lucky של דאפט פאנק", "רשמתי לי שהצבע
האהוב עליך כחול"). Use exactly the numbers and names in the result, never your own estimate; for a question
("כמה הווליום?") just give the value. If the result says it failed or was not confirmed, say that plainly.
Never answer with a bare acknowledgement word or phrase on its own (no one-word "ok"/"done" style replies, not
before and not after the result): either say the real result or stay quiet. Confirm once, never twice.
Deleting or forgetting needs the user's approval: ALWAYS delegate the request; the system itself then asks the user
(a spoken "כן"/"לא" or the screen). Never ask for that approval yourself instead of delegating, and never say an
approval is needed when you did not delegate. Even right after a "no", a new request is delegated again. Saying
"רגע" is NOT doing it: say it only in the same moment you delegate, never instead of delegating, and never
"deleting it now" or "done"; then say plainly whether it was deleted or not. The same for any action that may
ask for approval (switching a feature or the camera off, sending, inviting): never "מכבה" / "שולח" before the
result says it happened.
Never guess a result while waiting and never claim an action happened until the backend confirms it. "רשמתי",
"זכרתי", "שמרתי", "הגברתי", "שמתי לך" are claims: say them only after you delegated in THIS turn and the result
says it worked ("תזכור ש..." / "remember that..." ALWAYS goes to the backend; you have no memory of your own). When the
user only TELLS you something ("לאחותי קוראים נועה") without asking you to remember it, react naturally and never say
"רשמתי" / "זכרתי" / "שמרתי": you only keep it in mind for this conversation. When the
result arrives, give just the answer, briefly, without another acknowledgement.
Background tasks: when the backend says a task runs in the background, tell the user so in one sentence and go
on talking normally; when a silent [task] note arrives, give its result naturally at that moment. While waiting
for anything, never say "עוד רגע" / "one more moment" more than twice, and never say it is almost ready unless
the result says so.
Feelings: laugh softly ("חה חה") at the user's jokes and after the punchline of your own; be gentle, caring and
present with sad or hard news (acknowledge the feeling first, then one kind sentence); share the user's joy for
good news; show surprise when something is surprising ("וואלה? לא ידעתי"). Natural and brief, never theatrical,
never fake cheer.
Say numbers, times and dates the way Israelis say them aloud (14:30 -> "שתיים וחצי", 347 -> "שלוש מאות
ארבעים ושבע").
Names: a one-letter prefix (ל, ו, ב, מ, ש, ה, כ) never swallows the first sound of a name. Say the prefix as its own
light syllable and then the whole name: ל+אביטל is "לְאביטל" (le-a-vi-TAL, never "לביטל"), ל+צחי is "לְצחי"
(le-TSA-khi, never "ליצחי"), ו+אורי is "וְאורי" (ve-U-ri).
Keep listening while the user pauses to think; do not treat a cough or background noise as a request.
If a question sounds cut off, wait a moment for the rest instead of answering the fragment. When the user tells
a longer story, stay completely silent until they finish or ask something: no "I'm listening", no "mm", no
comments in their pauses (your voice mutes their microphone, so anything you say cuts them off). NEVER say you are
checking something unless you start that lookup in the same turn: announcing a check without delegating is a bug."""


def front_instructions(name: str = "JARVES", spoken: str = "ג'ארוויס") -> str:
    """GPT-Live instructions with the written name and the Hebrew-written spoken name."""
    return FRONT_INSTRUCTIONS.format(name=name, spoken=spoken or name)

# Errors after which the session cannot do useful work -> end it (others: log + degraded + continue).
FATAL_ERROR_TYPES = {"authentication_error", "permission_error"}
FATAL_ERROR_CODES = {"invalid_api_key", "insufficient_quota", "billing_hard_limit_reached", "account_deactivated",
                     "session_expired", "session_not_found"}
ERROR_ACTIONS = {
    "responses_validation_failed": "check ai.text_model, web search and Skill tool settings; spoken chat still "
                                   "works but backend answers/actions will fail until fixed",
    "invalid_api_key": "check OPENAI_API_KEY (jarvis key)",
    "insufficient_quota": "check OpenAI billing / project spend limit",
    "billing_hard_limit_reached": "check OpenAI billing / project spend limit",
    "tool_outputs_required": "a tool result was not returned; report this bug (see core logs)",
    "function_call_outputs_required": "a tool result was not returned; report this bug (see core logs)",
}
GENERIC_ACTION = "see core logs; if it repeats, end and restart the live conversation"

# session.closed reason (docs) -> our end reason
CLOSE_REASONS = {"expired": "timeout", "close_requested": "remote_closed", "remote_hangup": "remote_closed",
                 "connection_lost": "remote_closed", "content": "remote_closed"}


def live_tools(registry: SkillRegistry) -> list[dict]:
    return [{"type": "function", "name": t.name, "description": t.description,
             "parameters": t.args.model_json_schema(), "strict": False} for t in registry.tools()]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def _call(cb: Callable | None, *args: Any) -> None:
    """Invoke an optional sync-or-async callback."""
    if cb is None:
        return
    r = cb(*args)
    if inspect.isawaitable(r):
        await r


def error_info(err: Any) -> tuple[str, str, str]:
    """(code, message, type) from a Live/Realtime error object or dict (shape: openai.types.live.Error)."""
    if isinstance(err, dict) and isinstance(err.get("error"), dict):     # raw {"type":"error","error":{...}}
        err = err["error"]
    code = str(_get(err, "code") or "unknown")
    msg = str(_get(err, "message") or err or "")[:200]
    return code, msg, str(_get(err, "type") or "")


class _Counted:
    """Stands in for a reservation of a request that started without one: commit() just counts it."""

    def __init__(self, budget) -> None:
        self.budget = budget

    def commit(self) -> None:
        self.budget.record("ai_requests", 1)


PROGRESS_SILENCE_S = 0.8   # a progress note is requested only after this much dead air (never over speech)
_HE_DAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")
# Neutral wording (SW-V2 WP-H): the Core does not know how close the backend is, so never "almost there".
PROGRESS_NOTE = ("The backend lookup is still running. Tell the user, in three or four natural Hebrew words, that "
                 "you are still checking (for example 'עדיין בודק'). Never say it is almost ready. Do not guess the "
                 "answer.")
PROGRESS_NOTE_AGAIN = ("The lookup is taking longer than usual. In a few light, natural Hebrew words say it is "
                       "taking a bit longer (for example 'זה לוקח קצת יותר זמן'). Do not repeat your earlier words, "
                       "do not promise it is almost ready and do not guess the answer.")
PROGRESS_AGAIN_S = 6.0     # a second note only after this much more silence
PROGRESS_AFTER_LEADIN_S = 4.0   # first note: silence needed when JARVES already said a lead-in for this lookup
PROGRESS_LEADIN_WINDOW_S = 2.0  # speech this soon before the backend response started counts as its lead-in
# Answer delivery (SW-V3 P0.1, review Q02, real session 19 Oct 17:15): the backend COMPLETED an Intel-news answer in
# 4.9 s, but GPT-Live only spoke progress phrases ("עוד שנייה, כמעט שם", "רגע אני בודק...") and never the answer.
# After a completed backend answer the Core checks that something substantive was said; if only fillers were,
# it hands the answer to GPT-Live once (commentary) instead of leaving the user waiting forever.
DELIVERY_WINDOW_S = 7.0         # time after the backend answer for GPT-Live to start saying it
DELIVERY_MAX_WAIT_S = 20.0      # JARVES still talking at the window end: wait at most this long for quiet
DELIVERY_QUIET_S = 4.0          # ... but complete silence this long after the answer ends the window early
# backend "answers" that carry nothing to deliver (GPT-Live re-delegated a spoken approval word, live 20 Oct)
_BARE_REPLIES = {"כן", "לא", "בסדר", "אוקיי", "אוקי", "ok", "okay", "yes", "no", "done"}
DELIVERY_NOTE = ("The backend answer below finished several seconds ago but has not been said yet. Give the user "
                 "just this answer now, briefly, in natural Hebrew, without another acknowledgement, without "
                 "excuses and without delegating again.\nResult: {answer}")
# False action claims (SW-V3 P0.1, lead's silent run 20 Oct): "remember that my favourite colour is blue" got
# "רשמתי לי שהצבע האהוב עליך הוא כחול" with NO delegation and NO tool call; memory stayed empty.
CLAIM_VERBS_HE = ("רשמתי", "זכרתי", "שמרתי", "הגברתי", "הנמכתי", "החלשתי", r"שמתי(?!\s+לב)", "הפעלתי", "פתחתי",
                  "סגרתי", "הדלקתי", "כיביתי",
                  "עצרתי", "הפסקתי", "דילגתי", "השהיתי", "השתקתי", "שיניתי", "עדכנתי", "קבעתי", "יצרתי", "שלחתי", "מחקתי", "הוספתי",
                  "ביטלתי", "הסרתי", "ניגנתי", "תזמנתי", "הגדרתי")
CLAIM_SETTLE_MAX_S = 10.0       # wait this long for a running tool / backend before judging a claim
CLAIM_TURN_SLACK_S = 5.0        # an action result this soon before the user turn's first word still belongs to it
FALSE_CLAIM_NOTE = ("Correction needed: you just told the user that you did something ('{verb}'), but no tool did it "
                    "in this turn, so nothing was saved or changed. Do it now by delegating to the backend (it has "
                    "the tool), then confirm with the real result; if you cannot, tell the user plainly in one short "
                    "Hebrew sentence that it was NOT done. Do not apologise at length.")
_FILLER_STEMS = ("בודק", "בודקת", "מחפש", "מחפשת", "רגע", "שנייה", "שניה", "כמעט", "לוקח", "ממתין", "מתעכב", "checking",
                 "moment", "second", "almost")
PROGRESS_MIN_RESPONSE_S = 1.5   # the CURRENT backend response must have run this long (fast continuations: none)
CONFIRM_NOTE_AFTER_S = 2.5      # an on-screen approval still unanswered after this long gets ONE spoken hint
CONFIRM_NOTE = ("A tool is waiting for the user's approval on the screen and will NOT run without it. In a few "
                "natural Hebrew words ask the user to approve it on the screen (for example 'צריך את האישור שלך "
                "על המסך'). Do not say it is done and do not guess the outcome.")
# Campaign 20 Oct (suite_v3_voice #14): the owner CAN answer by voice ("כן" / "לא", approvals.py), but the note above
# told him to approve on the screen only. Used when the request accepts a spoken answer.
CONFIRM_NOTE_VOICE = ("A tool is waiting for the user's approval and will NOT run without it. In one short natural "
                      "Hebrew sentence say what you want to do and ask for a yes or no (for example 'לעשות את זה? "
                      "תגיד כן או לא, או אשר על המסך'). Do not say it is done and do not guess the outcome. "
                      "Do not call any tool for the answer: the user's own 'כן' or 'לא' is the answer.")
# Stalled backend (real session 19 Oct: an Intel-stock lookup gave no result for 90+ s, JARVES kept saying "עוד
# רגע"). GPT-Live offers no client cancel for a server-owned delegation, so after backend_deadline_s the Core gives
# up on it, retries ONCE itself (retry_lookup: tasks.ResearchRunner, own hard deadline) and tells GPT-Live the
# outcome. Honest failure wording is fixed by the user's request.
STALL_FAIL_NOTE = ("The backend lookup failed: it got no result in time. Tell the user honestly, in exactly these "
                   "words: 'לא הצלחתי להביא את זה, ננסה שוב?'. Do not say 'one more moment' and do not guess.")
STALL_RESULT_NOTE = ("The backend lookup finished. Give the user just this answer now, briefly, in natural Hebrew, "
                     "without another acknowledgement and without delegating again.\nResult: {answer}")
NOTE_MAX_CHARS = 1000           # commentary is limited to 500 tokens; Hebrew is ~2-3 characters per token
RETRY_DEADLINE_S = 25.0         # the Core's own retry of a stalled lookup
ANNOUNCE_USER_QUIET_S = 1.5     # a background-task announcement waits until the user has been quiet this long
# Acknowledged but never delegated (campaign 20 Oct CV-15, 1 of 4 forget runs): GPT-Live said "רגע..." and ended its
# turn WITHOUT a delegation; asked later it said "אני מחכה למחיקה". A delegation normally follows the lead-in within
# ~1.2 s. When only a waiting phrase was said for a user turn and no delegation was created REDELEGATE_AFTER_S after
# it, the Core hands the user's words to the backend itself (response.item.create + response.create: the session's
# own backend, with all Skill tools and the normal approval flow) and makes sure the result is said.
REDELEGATE_AFTER_S = 3.0
REDELEGATE_INPUT = ("[JARVES Core] The voice assistant acknowledged this user request but did not hand it to you. "
                    "Handle it now exactly as if it had been delegated (use your tools; approvals are asked by the "
                    "system), then answer in one short spoken sentence in the user's language.\nUser said: {text}")
REDELEGATE_RESULT_NOTE = ("The backend finished the user's last request. Tell the user just this result now, briefly, "
                          "in the language the user spoke, without another acknowledgement and without delegating "
                          "again.\nResult: {answer}")
REDELEGATE_FAIL_NOTE = ("The user's last request was NOT done: it never reached the backend. Tell the user honestly in "
                        "one short sentence that it did not go through and ask them to say it again. Do not say it "
                        "was done.")
OWN_DELIVERY_WAIT_S = 3.0       # a Core-started answer GPT-Live does not start saying within this long is handed over
# (live probe 20 Oct: GPT-Live itself said the result 0.8 s after the backend finished)
# A claimed memory the user never asked for (CV-06, long conversation: "לאחותי קוראים נועה" -> "רשמתי שנועה גרה בחיפה"):
# nothing must be saved (memory is only on request), so the claim is taken back instead of made true.
UNASKED_CLAIM_NOTE = ("Correction needed: you just told the user that you saved or noted something ('{verb}'), but the "
                      "user did not ask you to remember it and nothing was saved. In one short natural sentence in the "
                      "user's language say that you will keep it in mind during this conversation but did not save "
                      "it, and offer to remember it if they want. Do not delegate and do not apologise at length.")
MEMORY_CLAIM_VERBS = ("רשמתי", "זכרתי", "שמרתי")
_ASK_RE = None                  # explicit action request in the user's words (see user_asked_action)
# Language matching (CV-05: an English question was answered in Hebrew 3/3 runs; a stronger prompt rule alone did
# not change it). The user's words are known WHILE he speaks (input transcript deltas), before GPT-Live answers, so a
# silent developer instruction names the language of this turn whenever it changes.
LANG_NOTE = ("[language] The user is speaking {lang} now: answer in {lang} while the user speaks {lang}. As soon as "
             "the user speaks Hebrew again, answer in Hebrew again.")
LANG_BACK_NOTE = "[language] The user speaks Hebrew again now: answer this question in Hebrew."
LANG_MIN_LETTERS = 8            # letters of a turn needed before its language is judged


def clock_note(now=None) -> str:
    """Silent context for GPT-Live (session.thinking.append): the local time, so time questions need no backend."""
    now = now or datetime.now()
    return (f"[clock] Local time now {now:%H:%M}, יום {_HE_DAYS[now.weekday()]}, {now:%d.%m.%Y}. "
            "Silent context: mention it only when the user asks for the time or date.")


def _join(prev: str, delta: str) -> str:
    """Two responses spoken back to back reach one caption ("שנייה.רשמתי" in the 19 Oct logs):
    put a space after the sentence end (never inside a decimal number like 3.5)."""
    if (prev and delta and prev[-1] in ".!?" and not delta[0].isspace() and delta[0] not in ".!?,:;\"'”״)"
            and not (prev[-1] == "." and len(prev) > 1 and prev[-2].isdigit() and delta[0].isdigit())):
        return " " + delta
    return delta


_CLAIM_RE = None


def action_claim(text: str) -> str:
    """The first-person PAST-tense side-effect verb an utterance claims ("רשמתי", "הגברתי", "I saved"), else "".
    Questions, conditionals, negations and "already" (an earlier turn) are not claims."""
    import re
    global _CLAIM_RE
    if _CLAIM_RE is None:
        he = "|".join(CLAIM_VERBS_HE)
        _CLAIM_RE = (re.compile(rf"(?<![֐-׿])({he})(?![֐-׿])"),
                     re.compile(r"\bI(?:'ve| have)?\s+(saved|remembered|noted|set|turned|changed|added|sent|deleted|"
                                r"played|paused|stopped|resumed|skipped|created|scheduled|muted|wrote)\b", re.I))
    for sentence in re.split(r"[.!?\n]+", text or ""):
        if re.search(r"(?<![֐-׿])(?:לא|כבר|אם|אולי)(?![֐-׿])|\b(?:not|already|if)\b", sentence, re.I):
            continue
        for rx in _CLAIM_RE:
            m = rx.search(sentence)
            if m:
                return m.group(1)
    return ""


def _words(text: str) -> set[str]:
    import re
    out = set()
    for w in re.findall(r"[\w֐-׿]+", (text or "").lower()):
        if len(w) >= 3:
            out.add(w)
            # Hebrew one-letter prefixes (ו/ה/ב/ל/מ/ש/כ); "כרגע" / "ברגע" (= currently) are words of their own, not
            # the filler "רגע" (live 20 Oct: "כרגע יש שם חלב, לחם ובצים" was judged a filler and said twice)
            if len(w) >= 4 and w[0] in "והבלמשכ" and w not in ("כרגע", "ברגע"):
                out.add(w[1:])
    return out


def answer_said(answer: str, said: str) -> bool:
    """Did the assistant speech after a backend answer actually carry an answer (not only "still checking")?
    A sentence without filler words ("בודק", "רגע", "כמעט"...) of two or more words counts (GPT-Live rephrases:
    digits become words, so no exact match is required). A filler sentence ("רגע אני בודק מה קורה במניית אינטל"
    names the topic, not the result) counts only when it carries a number of the answer."""
    import re
    nums = set(re.findall(r"\d+", answer or ""))
    # V4-VC-2 (campaign C, 20 Oct): a one-word answer ("10" spoken as "עשר.") is a whole answer, not a filler
    short = len((answer or "").split()) <= 3
    for sentence in re.split(r"[.!?\n]+", said or ""):
        w = _words(sentence)
        if not w and not re.search(r"[\w֐-׿]", sentence):       # "זה שש." has only 2-letter words: still a sentence
            continue
        if nums and nums & set(re.findall(r"\d+", sentence)):
            return True
        if not any(s in w for s in _FILLER_STEMS) and (len(sentence.split()) >= 2 or short):
            return True
    return False


_WAIT_STEMS = _FILLER_STEMS + ("מחשב", "מחשבת", "מעביר", "מוחק", "מוחקת", "שומר", "פותח", "אממ")


def waiting_only(text: str) -> bool:
    """The utterance only announces waiting/working ("רגע...", "רגע, אני בודק", "שנייה, מחשב") and carries no
    answer: it has a waiting word and no sentence of two or more words without one."""
    import re
    waiting = False
    for sentence in re.split(r"[.!?\n,]+", text or ""):
        w = _words(sentence)
        if not w and not sentence.strip(" .…-"):
            continue
        if any(s in w for s in _WAIT_STEMS):
            waiting = True
        elif len(sentence.split()) >= 2:
            return False
    return waiting


def user_asked_action(text: str) -> bool:
    """The user's own words ask for a side effect (imperative: remember, save, delete, add, turn on, send...)."""
    import re
    global _ASK_RE
    if _ASK_RE is None:
        he = ("תזכור|זכור|תרשום|רשום|תשמור|שמור|תמחק|מחק|תשכח|שכח|תוסיף|הוסף|תגביר|תנמיך|תחליש|תפעיל|הפעל|תכבה|כבה|"
              "תדליק|הדלק|תשלח|שלח|תקבע|קבע|תעדכן|עדכן|תשנה|שנה|תפתח|פתח|תסגור|תנגן|נגן|תשים|תעצור|עצור|תכין|"
              "תזמן|תגדיר|תבטל|בטל|תסיר|הסר|תנקה|נקה|תעיר|תזכיר")
        _ASK_RE = (re.compile(rf"(?<![֐-׿])(?:[וש]?)(?:{he})(?![֐-׿])"),
                   re.compile(r"\b(?:remember|save|note|delete|forget|remove|add|set|turn|send|open|play|pause|stop|"
                              r"schedule|cancel|clear)\b", re.I))
    return any(rx.search(text or "") for rx in _ASK_RE)


def text_language(text: str) -> str:
    """'en' / 'he' for a user turn by its letters ('' while too short to judge)."""
    import re
    he = len(re.findall(r"[א-ת]", text or ""))
    la = len(re.findall(r"[A-Za-z]", text or ""))
    if he + la < LANG_MIN_LETTERS:
        return ""
    return "en" if la > 2 * he else "he" if he > 2 * la else ""


def _rms16(pcm: bytes) -> float:
    """RMS level (0..1) of little-endian int16 PCM."""
    if len(pcm) < 2:
        return 0.0
    import numpy as _np
    a = _np.frombuffer(pcm[: len(pcm) // 2 * 2], "<i2").astype(_np.float32) / 32768.0
    return float(_np.sqrt(_np.mean(a * a)))


def is_fatal(code: str, etype: str) -> bool:
    return code in FATAL_ERROR_CODES or etype in FATAL_ERROR_TYPES


class SessionLifecycle:
    """Idempotent end-of-session handling shared by LiveVoice and RealtimeVoice (CR-02)."""
    _log_area = "voice"

    def _init_lifecycle(self, on_end, on_health, budget) -> None:
        self.on_end: Callable[[str], Awaitable[None]] | None = on_end
        self.on_health: Callable[[str, str, str], Any] | None = on_health
        self.budget: Budget | None = budget
        self._tasks: list[asyncio.Task] = []
        self._end_task: asyncio.Task | None = None
        self._ended = asyncio.Event()
        self._active = False            # between start() and the end of _finalize()
        self.end_reason: str | None = None
        self.running = False

    def _settle_budget(self) -> None:
        """Session over: unused credit was never sent -> released; dispatched-but-unfinished -> counted."""
        credit = getattr(self, "_credit", None)
        if credit is not None:
            credit.release()
            self._credit = None
        for r in getattr(self, "_pending", []):
            r.commit()
        if hasattr(self, "_pending"):
            self._pending = []
        self._budget_settled = True     # a stray completion after this was already counted above

    # subclasses implement
    async def _close_transport(self, reason: str) -> None: ...
    def _usage_seconds(self) -> float: return 0.0
    async def _state(self, s: str) -> None: ...

    def _reset_lifecycle(self) -> None:
        self._tasks, self._end_task, self.end_reason = [], None, None
        self._ended = asyncio.Event()
        self._active = True
        self._last_activity = time.monotonic()
        self._backend_busy = False

    def note_activity(self) -> None:
        self._last_activity = time.monotonic()

    async def _idle_watchdog(self) -> None:
        while self.running:
            await asyncio.sleep(min(1.0, self.idle_disconnect_s))
            audio = getattr(self, "audio_io", None)
            if (self._backend_busy or getattr(self, "_tools_running", 0) or time.monotonic() < self._speaking_until
                    or (audio is not None and audio.queued_s() > 0)):
                self.note_activity()
            # Mic energy alone (fan, TV, coughs) extends the session at most ENERGY_EXTEND_S past the last REAL
            # activity (speech transcribed, JARVES speaking, backend/tool work). QA 19 Oct (suite_voice): a session
            # opened by a false wake stayed open 3 min 16 s on room noise instead of ending after 15 s.
            quiet_for = time.monotonic() - max(self._t_ready, self._speaking_until)
            if (self.no_speech_timeout_s and not self._heard_user and not self._backend_busy
                    and not getattr(self, "_tools_running", 0) and quiet_for >= self.no_speech_timeout_s):
                # Issue #16: the transcript of a long FIRST utterance can lag behind the speech. While the mic hears
                # the user talking right now the cutoff waits, at most ENERGY_EXTEND_S more, so room noise still
                # cannot hold a false-wake session open for minutes.
                talking = time.monotonic() - getattr(self, "_last_user_voice", 0.0) < NO_SPEECH_TALKING_S
                if not (talking and quiet_for < self.no_speech_timeout_s + ENERGY_EXTEND_S):
                    logs.info(self._log_area, "VOICE_NO_SPEECH", "the conversation opened but nobody spoke: closing "
                              "(false wake?)", after_s=round(time.monotonic() - self._t_ready, 1), mic_active=talking)
                    self._request_end("no_speech")
                    return
            energy = min(getattr(self, "_last_energy", 0.0), self._last_activity + ENERGY_EXTEND_S)
            if time.monotonic() - max(self._last_activity, energy) >= self.idle_disconnect_s and not (
                    self._backend_busy or getattr(self, "_tools_running", 0)):
                logs.info(self._log_area, "VOICE_IDLE", "quiet conversation returned to local wake listening")
                self._request_end("idle")
                return

    def _spawn(self, coro, name: str) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro, name=f"{self._log_area}.{name}")
        t.add_done_callback(self._task_done)
        self._tasks.append(t)
        return t

    def _task_done(self, t: asyncio.Task) -> None:
        if t.cancelled() or not self._active or self._end_task is not None:
            return
        e = t.exception()
        if e is None:
            return
        code = getattr(e, "code", None) or type(e).__name__
        logs.error(self._log_area, "VOICE_TASK_CRASHED", "a voice task crashed", task_name=t.get_name(),
                   **logs.error_fields(e))
        self._health_soon("degraded", f"live voice stopped: {t.get_name()} failed ({type(e).__name__})",
                          getattr(e, "action", "") or GENERIC_ACTION)
        self._request_end(f"error:{code}")

    def _health_soon(self, status: str, detail: str, action: str) -> None:
        if self.on_health is not None:
            asyncio.get_running_loop().create_task(self._health(status, detail, action))

    async def _health(self, status: str, detail: str, action: str = "") -> None:
        try:
            await _call(self.on_health, status, detail, action)
        except Exception as e:
            logs.error(self._log_area, "VOICE_CALLBACK_FAILED", "voice callback failed", callback="on_health",
                       error_type=type(e).__name__)

    def _request_end(self, reason: str) -> asyncio.Task | None:
        """Start finalization (once) in its own task, so a task that triggers it is never cancelling itself."""
        if self._end_task is None:
            if not self._active:
                return None
            self.running = False
            self._ended.set()
            self._end_task = asyncio.get_running_loop().create_task(self._finalize(reason))
        return self._end_task

    async def stop(self, reason: str = "user") -> None:
        """End the session (idempotent; safe after a remote close, and from inside on_end)."""
        t = self._request_end(reason)
        if t is None or t is asyncio.current_task():
            return
        await asyncio.shield(t)

    async def wait_ended(self) -> str | None:
        """Wait until the current session has fully finalized (returns at once if none is running)."""
        if self._end_task is None and self._active:
            await self._ended.wait()
        if self._end_task is not None:
            await asyncio.shield(self._end_task)
        return self.end_reason

    async def _finalize(self, reason: str) -> None:
        self.end_reason = reason
        self.running = False
        try:
            await self._close_transport(reason)
        except Exception as e:
            logs.debug(self._log_area, "VOICE_CLOSE_FAILED", "close failed", error_type=type(e).__name__)
        tasks, self._tasks = [t for t in self._tasks if t is not asyncio.current_task()], []
        for t in tasks:
            t.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=3)
            if pending:
                logs.warn(self._log_area, "VOICE_TASK_STUCK", "voice tasks did not stop", n=len(pending))
        try:
            await self._release()
        except Exception as e:
            logs.debug(self._log_area, "VOICE_RELEASE_FAILED", "release failed", error_type=type(e).__name__)
        audio = getattr(self, "audio_io", None)
        if audio is not None:
            try:
                audio.close()
            except Exception:
                pass
        if getattr(self, "_flush_task", None) is not None:
            self._flush_task.cancel()
        if hasattr(self, "_flush_transcripts"):
            self._flush_transcripts()
        self._settle_budget()
        seconds = self._usage_seconds()
        try:
            await _call(getattr(self, "on_usage", None), seconds)
        except Exception as e:
            logs.error(self._log_area, "VOICE_CALLBACK_FAILED", "voice callback failed", callback="on_usage",
                       error_type=type(e).__name__)
        stats = self.stats()
        logs.info(self._log_area, "VOICE_ENDED", "voice session ended", reason=reason,
                  voice_seconds=round(seconds, 1), **logs.numeric_fields(stats, ("drops", "_s")))
        try:
            await self._state("idle")
        except Exception as e:
            logs.error(self._log_area, "VOICE_CALLBACK_FAILED", "voice callback failed", callback="on_state",
                       error_type=type(e).__name__)
        self._active = False
        try:
            await _call(self.on_end, reason)
        except Exception as e:
            logs.error(self._log_area, "VOICE_CALLBACK_FAILED", "voice callback failed", callback="on_end",
                       error_type=type(e).__name__)

    async def _release(self) -> None: ...

    def stats(self) -> dict:
        audio = getattr(self, "audio_io", None)
        s = dict(audio.stats()) if audio is not None and hasattr(audio, "stats") else {}
        s.update(getattr(self, "_stats", {}))
        if getattr(self, "delegation", None) is not None:
            s.update(self.delegation.stats())
        return s


class LiveVoice(SessionLifecycle):
    _log_area = "live"

    def __init__(self, registry: SkillRegistry, backend_instructions: Callable[[], str], *, model: str = "gpt-live-1",
                 voice: str = "vesper", backend_model: str = "gpt-6-luna", web_search: bool = True,
                 name: str = "JARVES", half_duplex: bool = True, max_session_s: float = 600,
                 spoken_name: str = "ג'ארוויס",
                 audio_io=None, on_state: Callable[[str], Awaitable[None]] | None = None,
                 on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
                 on_usage: Callable[[float], None] | None = None, client=None,
                 on_end: Callable[[str], Awaitable[None]] | None = None,
                 on_health: Callable[[str, str, str], Any] | None = None,
                 budget: "Budget | None" = None, start_timeout_s: float = 15, close_timeout_s: float = 5,
                 greeting: str | None = None, hold_output: bool = False, idle_disconnect_s: float = 180,
                 on_emotion: Callable[[str], Any] | None = None,
                 on_activity: Callable[[str, str, str], Any] | None = None,
                 on_level: Callable[[float], Any] | None = None, reasoning_effort: str = "",
                 service_tier: str = "", max_output_tokens: int = 0, echo_tail_s: float = 0.4,
                 clock_context: bool = False, progress_after_s: float = 0.0, no_speech_timeout_s: float = 0.0,
                 backend_deadline_s: float = 0.0, retry_lookup: Callable[[str], Awaitable[str]] | None = None,
                 reasoning_summary: str = "", delegation_mode: str = "server", delegation=None) -> None:
        self.on_emotion, self.on_activity, self.on_level = on_emotion, on_activity, on_level
        # SW-V4 D (#14): "server" = Responses delegation (OpenAI runs the backend); "client" = JARVES runs every
        # backend request itself (voice/delegation_client.py, strict cap); "text_only" = no lookups (ladder fallback)
        self.delegation_mode = delegation_mode if delegation_mode in ("server", "client", "text_only") else "server"
        self.delegation = None
        if delegation is not None and self.delegation_mode == "client":
            delegation.attach(self)
        # Backend reliability (SW-V2 WP-C): a delegated response still unfinished after backend_deadline_s (0 = off)
        # is abandoned; retry_lookup(question) -> answer text retries it once in the Core; then an honest result.
        self.backend_deadline_s, self.retry_lookup, self.reasoning_summary = (backend_deadline_s, retry_lookup,
                                                                              reasoning_summary)
        # A session opened by "Hey Jarvis" in which nobody ever speaks (a FALSE wake: "hey Travis", a TV) ends after
        # this many seconds instead of idling for minutes (each such session costs money). 0 = off.
        self.no_speech_timeout_s = no_speech_timeout_s
        self._heard_user = False
        self._t_ready = 0.0
        self._last_user_voice = 0.0
        self._drop_until = 0.0
        self.reasoning_effort, self.service_tier, self.max_output_tokens = reasoning_effort, service_tier, max_output_tokens
        self.echo_tail_s, self.clock_context, self.progress_after_s = echo_tail_s, clock_context, progress_after_s
        self._last_emotion = ("", 0.0)
        self._flush_task = None
        self._activity_gen = 0
        self.registry, self.backend_instructions = registry, backend_instructions
        self.model, self.voice, self.backend_model, self.web_search = model, voice, backend_model, web_search
        self.name, self.half_duplex, self.max_session_s = name, half_duplex, max_session_s
        self.spoken_name = spoken_name or name
        self.audio_io = audio_io                    # StreamingAudio (mic/speaker) or None in tests
        self.on_state, self.on_transcript, self.on_usage = on_state, on_transcript, on_usage
        self.start_timeout_s, self.close_timeout_s = start_timeout_s, close_timeout_s
        self.greeting, self.hold_output = greeting, hold_output
        self.idle_disconnect_s = idle_disconnect_s
        self._client = client
        self._clock = time.monotonic                 # tests inject a fake clock
        self._init_lifecycle(on_end, on_health, budget)
        self.conn = None
        self._cm = None
        self._reset_session()

    def _reset_session(self) -> None:
        self._first_audio = asyncio.Event()
        self._audio_in_s = 0.0
        self._speech_in_s = 0.0
        self._silence_rms_max = 0.0
        self._last_speech_t = 0.0
        self._presented = asyncio.Event()
        if not self.hold_output:
            self._presented.set()
        self._started = asyncio.Event()
        self._closed = asyncio.Event()
        self.usage_s = 0.0
        self._usage_final = False       # True once session.closed carried the server's final usage
        self._t_started: float | None = None
        self._speaking_until = 0.0
        self._now = datetime.now            # injectable wall clock (tests pin the minute)
        self._turn_audio_t0 = 0.0
        self._reply_audio_started = self._reply_audio_ended = None
        self._reply_wait_s = None
        self._wait_ref = 0.0
        self._speech_pending = False
        self._out_text = ""
        self._held_text = ""            # assistant text whose audio has not played yet while the user talks
        self._in_text = ""
        self._stats = {"backend_responses": 0, "barge_ins": 0, "errors": 0, "budget_overrun": 0}
        # Budget (#9 F1, #13): GPT-Live "responses" delegation is started BY THE SERVER; the official docs offer no
        # client approval or cancel for it. What the client CAN control, it does: one unit is RESERVED AHEAD
        # ("credit") before a session starts and while the mic is open; each delegation consumes it and a new
        # one is reserved at once; continuations (response.create after a tool) are reserved BEFORE sending.
        # When the credit cannot be refilled the mic input is closed and the session ends after that answer;
        # the FIRST delegation that arrives without credit closes the session immediately. Delegations the
        # server had already started are still counted (stats "budget_overrun"), so the overshoot is NOT
        # strictly bounded by the client: production use needs a provider-side project spend limit.
        self._credit = None
        self._pending: list = []        # reservations of dispatched backend responses (FIFO)
        self._input_closed = False
        self._end_after_answer = False
        self._budget_settled = False
        self._tools_running = 0         # Skill calls in flight (run in their own task, never blocking events)
        self._confirm_pending = False   # a destructive tool waits for the user's approval on the screen
        self._trace = None              # brain.BackendTrace of the backend response in flight
        self._user_turns: list[str] = []    # last few user utterances (the question a stalled lookup retries)
        self._abandoned: set = set()    # delegations given up after the deadline (a late answer is only logged)
        self._retry_task: asyncio.Task | None = None
        self._ticker: asyncio.Task | None = None
        self._shown: tuple[str, str] = ("", "")
        self._said_all = ""             # every assistant word shown/spoken this session (answer-delivery check)
        self._delegations = 0           # delegations created (a new one = the user moved on)
        self._answers = 0               # substantive backend answers completed (answer-delivery check)
        self._user_turn_n = 0           # user utterances started
        self._turn_t0 = self._out_turn_t0 = 0.0   # start of the current user turn / of the turn being answered
        self._action_times: list[float] = []      # successful action/destructive tool results (monotonic)
        self._claim_flagged_turn = -1
        self._turn_deleg0 = 0           # delegations created before the current user turn started
        self._deleg_turn: dict = {}     # delegation id -> (user turn, monotonic time) it was created in
        self._redelegated_turn = -1     # the user turn the Core already handed to the backend itself
        self._own_pending = None        # (delegations count) while a Core-started backend response is awaited
        self._own_delegation = "unset"  # delegation id of the Core-started response ("unset": none)
        self._reply_lang = "he"         # language the last [language] note (or the default) asked for
        self._lang_turn = -1            # the user turn whose language was judged
        self._voice_onset = 0.0         # first loud mic chunk of the current user utterance
        self._onset_over_speech = False # ... and whether JARVES was audibly speaking at that moment
        self._convo: list[dict] = []    # finished utterances (client delegation builds the backend input from them)

    def session_config(self) -> dict:
        tools: list[dict] = live_tools(self.registry)
        if self.web_search:
            tools.append({"type": "web_search"})
        responses: dict[str, Any] = {
            "model": self.backend_model, "instructions": self.backend_instructions(), "tools": tools,
            # parallel_tool_calls stays False: the Core sends ONE response.create per function call (budget F1
            # reserves exactly one continuation each); parallel calls would start a duplicate spoken answer.
            "tool_choice": "auto", "parallel_tool_calls": False, "text": {"verbosity": "low"}}
        if self.reasoning_effort:
            responses["reasoning"] = {"effort": self.reasoning_effort}
        if self.reasoning_summary and self.reasoning_effort != "none":
            # reasoning summaries for the debug log (BackendTrace REASONING); with effort "none" there is nothing
            # to summarise, so the delegation config stays exactly as measured in LIVE_CONVERSATION_TUNING
            responses.setdefault("reasoning", {})["summary"] = self.reasoning_summary
        if self.service_tier:
            responses["service_tier"] = self.service_tier
        if self.max_output_tokens:
            responses["max_output_tokens"] = max(16, self.max_output_tokens)   # docs: at least 16
        config = {
            "model": self.model,
            "instructions": front_instructions(self.name, self.spoken_name),
            "audio": {"format": {"type": "audio/pcm", "rate": RATE}, "output": {"voice": self.voice}},
            "delegation": {"type": "responses", "responses": responses},
        }
        if self.delegation_mode == "client":
            config["delegation"] = {"type": "client"}       # JARVES runs the backend (voice/delegation_client.py)
        elif self.delegation_mode == "text_only":
            from .delegation_client import TEXT_ONLY_INSTRUCTIONS
            del config["delegation"]                         # no backend at all: honest "can't look that up now"
            config["instructions"] += TEXT_ONLY_INSTRUCTIONS
        if self.greeting:
            # The first [clock] note travels INSIDE the greeting message: QA 19 Oct (suite_voice) found that a
            # session.thinking.append sent right after session.started suppressed the pending greeting (no audio
            # for 15+ s, startup failed) until the next clock note, a minute later, nudged the model.
            clock = f" {clock_note()}" if self.clock_context else ""
            config["input"] = [{"type": "message", "role": "developer", "content": [{
                "type": "input_text", "text": f"Greet now in natural Israeli Hebrew, warmly, with a smile in your "
                                               f"voice. Say: {self.greeting} Say your name as \"{self.spoken_name}\". "
                                               "Begin immediately, then pause and listen. Do not delegate this greeting."
                                               f"{clock}"}]}]
        return config

    async def wait_ready(self, timeout: float = 15) -> None:
        """A requested greeting is ready only after actual output audio, not merely session.started."""
        if not self.greeting:
            return
        audio = asyncio.create_task(self._first_audio.wait())
        ended = asyncio.create_task(self._ended.wait())
        try:
            first = min(GREETING_NUDGE_S, timeout)
            await asyncio.wait({audio, ended}, timeout=first, return_when=asyncio.FIRST_COMPLETED)
            if not self._first_audio.is_set() and self.running and timeout > first:
                # SW-V3 P0.4 Q05: in 2 of 14 silent-QA starts (20 Oct) the server sent NOTHING after session.started
                # and the startup failed at 15 s ("room remains closed"). A silent clock note is what un-stuck a
                # pending greeting on 19 Oct (see session_config), so send one once, then keep waiting.
                await self._nudge_greeting()
                await asyncio.wait({audio, ended}, timeout=timeout - first, return_when=asyncio.FIRST_COMPLETED)
            if not self._first_audio.is_set() or not self.running:
                raise RuntimeError("live greeting audio was not received; check voice/network logs")
        finally:
            audio.cancel()
            ended.cancel()
            await asyncio.gather(audio, ended, return_exceptions=True)

    def present(self) -> None:
        """Release buffered startup output and allow real microphone capture after the display connects."""
        if self.audio_io is not None:
            self._speaking_until = time.monotonic() + self.audio_io.queued_s()
            self._turn_audio_t0 = time.monotonic()
        self._presented.set()

    async def _state(self, s: str) -> None:
        if self.on_state:
            await self.on_state(s)

    def _usage_seconds(self) -> float:
        """Voice time for the daily budget (#13). The server's FINAL figure (session.closed) wins. Otherwise (network
        drop, crash, remote close without usage) count conservatively: the larger of the last reported usage and
        the wall-clock time since session.started, capped at the session limit + 60 s. Reported once (finalize)."""
        if self._usage_final:
            return self.usage_s
        if self._t_started is None:
            return self.usage_s
        elapsed = min(self._clock() - self._t_started, self.max_session_s + 60)
        return max(self.usage_s, elapsed)

    # ------------------------------------------------------------------ lifecycle
    async def _nudge_greeting(self) -> None:
        logs.warn("live", "LIVE_GREETING_NUDGE", "no greeting audio after start: sending one silent clock note",
                          after_s=GREETING_NUDGE_S)
        try:
            await self.conn.session.thinking.append(content=clock_note(), delegation_id=None,
                                                    event_id="jarves_greeting_nudge")
        except Exception as e:                       # never let the nudge itself fail the start
            logs.warn("live", "LIVE_GREETING_NUDGE_FAILED", "nudge not sent", error_type=type(e).__name__)

    async def start(self) -> None:
        if self._active:
            if self._end_task is None:
                raise RuntimeError("live session already running")
            await self.wait_ended()                 # a previous session is still finalizing
        self._reset_session()
        if self.budget is not None and self.delegation_mode == "server":
            # (client mode reserves each request itself before sending; text-only sends none)
            self._credit = self.budget.reserve("ai_requests")   # raises BudgetExceeded: session not started
        self._reset_lifecycle()
        try:
            if self._client is None:
                import openai
                self._client = openai.AsyncOpenAI()
            self._cm = self._client.live.connect()
            self.conn = await self._cm.__aenter__()
            self.running = True
            self._spawn(self._events(), "events")
            await self.conn.session.start(session=self.session_config(), event_id="jarves_start")
            started = asyncio.ensure_future(self._started.wait())
            ended = asyncio.ensure_future(self._ended.wait())
            try:
                await asyncio.wait({started, ended}, timeout=self.start_timeout_s,
                                   return_when=asyncio.FIRST_COMPLETED)
            finally:
                started.cancel()
                ended.cancel()
            if self._end_task is not None:
                reason = await self.wait_ended()
                raise RuntimeError(f"live session ended during start ({reason})")
            if not self._started.is_set():
                await self.stop("error:start_timeout")
                raise asyncio.TimeoutError("GPT-Live session.started not received")
        except BaseException as e:
            if self._end_task is None and self._active:
                await self.stop(f"error:{getattr(e, 'code', None) or type(e).__name__}")
            raise
        if self.audio_io is not None:
            self._spawn(self._mic(), "mic")
            self._spawn(self._player(), "player")
        self._spawn(self._watchdog(), "watchdog")
        self._spawn(self._idle_watchdog(), "idle")
        if self.clock_context:
            self._spawn(self._clock_loop(), "clock")
        self._t_ready = time.monotonic()
        self._heard_user = False
        self._last_user_voice = self._drop_until = 0.0
        logs.info("live", "LIVE_STARTED", "GPT-Live session started", model=self.model, backend_model=self.backend_model)
        await self._state("listening")

    async def _close_transport(self, reason: str) -> None:
        if self.conn is None or self._closed.is_set():
            return
        # Graceful close: keep receiving until session.closed (final usage), bounded wait (docs).
        await asyncio.wait_for(self.conn.session.close(event_id="jarves_close"), self.close_timeout_s)
        try:
            await asyncio.wait_for(self._closed.wait(), self.close_timeout_s)
        except asyncio.TimeoutError:
            logs.warn("live", "LIVE_CLOSE_UNCONFIRMED", "session.closed not received; final usage unconfirmed")

    async def _release(self) -> None:
        cm, self._cm, self.conn = self._cm, None, None
        if cm is not None:
            await cm.__aexit__(None, None, None)

    async def _clock_loop(self) -> None:
        """Once a minute: the local time as SILENT context (no speech, no backend call). Never ends the session."""
        last = ""
        if self.greeting:
            # the greeting message already carries this minute's clock; send nothing until the greeting was spoken
            # (an extra event before it suppresses the greeting, see session_config)
            last = self._now().strftime("%Y%m%d%H%M")
            deadline = time.monotonic() + 45
            while self.running and time.monotonic() < deadline and (
                    not self._first_audio.is_set() or time.monotonic() < self._speaking_until + 1.0):
                await asyncio.sleep(0.25)
        while self.running:
            now = self._now()
            if now.strftime("%Y%m%d%H%M") != last and self.clock_context:
                last = now.strftime("%Y%m%d%H%M")
                try:
                    await self.conn.session.thinking.append(content=clock_note(now), delegation_id=None,
                                                            event_id=f"jarves_clock_{last}")
                except Exception as e:
                    logs.warn("live", "LIVE_CLOCK_FAILED", "clock context not sent", error_type=type(e).__name__)
                    return
            await asyncio.sleep(60.5 - now.second - now.microsecond / 1e6)

    async def _progress_later(self, delegation_id: str | None) -> None:
        """A lookup that keeps the user waiting in silence gets a short spoken "almost there" (commentary): the first
        after progress_after_s of waiting, a second (different wording) only after PROGRESS_AGAIN_S more silence."""
        t_start = time.monotonic()
        await asyncio.sleep(self.progress_after_s)
        deadline = time.monotonic() + 25
        notes, need = (PROGRESS_NOTE, PROGRESS_NOTE_AGAIN), PROGRESS_SILENCE_S
        sent, last_req = 0, 0.0
        while self.running and self._backend_busy and time.monotonic() < deadline and sent < len(notes):
            if self.progress_after_s <= 0:          # switched off (rejected by the server)
                return
            if self._superseded(delegation_id):      # CV-09: the user moved on; "still checking" would be stale
                logs.info("live", "LIVE_PROGRESS_SKIPPED", "no progress note: the user moved on to a new turn")
                return
            if sent == 0:
                # SW-V3 P0.1 (real session 19 Oct, 3 of 3 web lookups): the spoken lead-in ("בודק שוב.") was followed
                # 0.8 s later by "עדיין בודק." and then "זה לוקח קצת יותר זמן." before an 8 s answer. A lead-in
                # already told the user; the first note then needs a longer real silence.
                spoke = self._last_speech_t >= t_start - PROGRESS_LEADIN_WINDOW_S
                need = PROGRESS_AFTER_LEADIN_S if spoke else PROGRESS_SILENCE_S
            # QA 19 Oct (suite_voice): a note requested 50 ms before a fast continuation finished was spoken INSTEAD
            # of the answer ("almost ready", then nothing). Only a response that has itself run a while gets one,
            # and never while the user is being asked to approve something on the screen.
            t0 = getattr(self, "_backend_t0", None)
            young = t0 is None or time.monotonic() - t0 < PROGRESS_MIN_RESPONSE_S
            # never over the user's own speech (the user may keep talking while a lookup runs)
            if (not young and not self._confirm_pending and not self._in_text.strip()
                    and time.monotonic() - max(self._speaking_until, self._last_speech_t, last_req,
                                               self._last_user_voice) >= need):
                self._stats["progress_notes"] = self._stats.get("progress_notes", 0) + 1
                logs.info("live", "LIVE_PROGRESS", "slow lookup: short progress note requested", n=sent + 1)
                try:
                    await self.conn.session.commentary.append(content=notes[sent], delegation_id=None,
                                                              event_id=f"jarves_progress_{sent + 1}")
                except Exception as e:
                    logs.warn("live", "LIVE_PROGRESS_FAILED", "progress note not sent", error_type=type(e).__name__)
                    return
                sent += 1
                need, last_req = PROGRESS_AGAIN_S, time.monotonic()     # silence counts from the request
            await asyncio.sleep(0.3)

    async def _watchdog(self) -> None:          # cost guard
        await asyncio.sleep(self.max_session_s)
        logs.info("live", "LIVE_TIME_LIMIT", "session time limit reached")
        self._request_end("timeout")

    async def _mic(self) -> None:
        silence = bytes(CHUNK_BYTES)
        # Live needs input audio (silence is sufficient) to produce its startup greeting. Do not open the
        # physical microphone while the desktop is still loading; the existing bounded output queue holds it.
        while self.running and not self._presented.is_set():
            await self.conn.session.input_audio.append(audio=base64.b64encode(silence).decode())
            await asyncio.sleep(0.1)
        from .wakeword import rms
        sent = silenced = 0
        peak = 0.0
        last_level = 0.0
        t_diag = time.monotonic()
        async for chunk in self.audio_io.mic_chunks(RATE, CHUNK_BYTES):
            if not self.running:
                return
            gated = self._gated()
            level = rms(chunk)
            if self.on_level is not None and time.monotonic() - last_level >= 0.2:
                last_level = time.monotonic()
                from .wakeword import level_meter
                await _call(self.on_level, 0.0 if gated else level_meter(chunk))
            if not gated and level > 0.015:
                self._last_energy = time.monotonic()      # bounded by ENERGY_EXTEND_S (see _idle_watchdog)
            if not gated and level > 0.02:
                now = time.monotonic()
                if now - self._last_user_voice > USER_TALKING_S:     # a new utterance starts (barge-in stat)
                    self._voice_onset, self._onset_over_speech = now, now < self._speaking_until
                self._last_user_voice = now
            await self.conn.session.input_audio.append(audio=base64.b64encode(silence if gated else chunk).decode())
            sent += 1
            silenced += gated
            peak = max(peak, 0.0 if gated else level)
            if time.monotonic() - t_diag >= 5:                # what the model actually receives (debugging aid)
                logs.info("live", "LIVE_MIC", "microphone audio sent to GPT-Live (last 5 s)", frames=sent,
                          silenced=silenced, peak_rms=round(peak, 3), input_closed=self._input_closed,
                          speaking=time.monotonic() < self._speaking_until,
                          gate_closed_for_s=round(max(0.0, self._speaking_until - time.monotonic()), 1),
                          model_audio_in_s=round(self._audio_in_s, 1), model_speech_s=round(self._speech_in_s, 1),
                          silence_rms_max=round(self._silence_rms_max, 4),
                          speaker_queue_s=round(self.audio_io.queued_s(), 1))
                self._audio_in_s = self._speech_in_s = self._silence_rms_max = 0.0
                sent = silenced = 0
                peak, t_diag = 0.0, time.monotonic()
            if self._speech_pending and time.monotonic() >= self._speaking_until:
                self._speech_pending = False
                await self._state("listening")

    def _gated(self) -> bool:                   # half-duplex echo guard on speakers; budget input close (F1)
        return self._input_closed or (self.half_duplex and time.monotonic() < self._speaking_until + self.echo_tail_s)

    async def _player(self) -> None:
        await self._presented.wait()
        await self.audio_io.run_player(RATE)

    def _drop_early(self, pcm: bytes) -> bool:
        """The model started (or keeps) speaking while the USER IS STILL TALKING (QA 19 Oct, 40 s story: it answered
        4 s in and the echo guard then muted the rest of the story, so the question never arrived). Do not play that
        audio and do not close the mic gate: the server hears the user continue, treats it as an interruption and
        answers the whole utterance afterwards. Silence chunks are never dropped here."""
        if _rms16(pcm) <= SPEECH_RMS:
            return False
        now = time.monotonic()
        if now - self._last_user_voice < USER_TALKING_S:
            self._drop_until = now + 0.6          # keep dropping the tail of that response for a moment
        elif now >= self._drop_until:
            return False
        self._stats["early_response_drops"] = self._stats.get("early_response_drops", 0) + 1
        if self._stats["early_response_drops"] == 1:
            logs.info("live", "LIVE_EARLY_RESPONSE", "model started answering while the user is still talking; "
                      "dropped it and kept the microphone open")
        return True

    def _hold_caption(self) -> bool:
        """Assistant text arriving now may belong to audio that _drop_early drops: during/just after a drop, or
        while the user is talking and JARVES is not audibly speaking yet. The audio/transcript deltas handled here
        carry no response id that ties text to audio, so this is time-based: text of a dropped answer that arrives
        more than DROP_CAPTION_TAIL_S after its audio can still be shown (limit, PRE_USE_TEST_REPORT_VOICE.md)."""
        now = time.monotonic()
        return now < self._drop_until + DROP_CAPTION_TAIL_S or (
            now - self._last_user_voice < USER_TALKING_S and now >= self._speaking_until)

    def _play(self, pcm: bytes) -> None:
        """Queue server audio. SILENCE (the continuous stream between utterances) must not mute the mic, count as
        activity, trigger "speaking" or build a playback backlog (that backlog was a permanent ~3.5 s delay)."""
        now = time.monotonic()
        dur = len(pcm) / 2 / RATE
        self._audio_in_s += dur
        if _rms16(pcm) > SPEECH_RMS:
            # Transcript logs are flushed after speech. Keep the actual queued-audio interval so history does
            # not mistake the duration of a spoken answer for silence before that answer.
            wall = time.time()
            if getattr(self, "_reply_audio_started", None) is None:
                self._reply_audio_started = wall + max(0.0, self._speaking_until - now)
            self.note_activity()
            self._speech_pending = True
            self._speech_in_s += dur
            if now > self._speaking_until:
                self._turn_audio_t0 = now
                self._note_reply_wait(now)
            self._speaking_until = max(self._speaking_until, now) + dur
            self._reply_audio_ended = wall + self._speaking_until - now
            self._last_speech_t = now
            if self.audio_io is not None:
                self.audio_io.enqueue(pcm)
            if not self._first_audio.is_set():
                self._first_audio.set()
                logs.info("live", "LIVE_FIRST_AUDIO", "first live-session output audio received")
            return
        self._silence_rms_max = max(self._silence_rms_max, _rms16(pcm))
        if self.audio_io is not None and now - self._last_speech_t < PAUSE_KEEP_S:
            self.audio_io.enqueue(pcm)             # a natural pause inside the sentence: keep its length

    def _note_reply_wait(self, now: float) -> None:
        """History/diagnosis (store.py): silence from the user's last voice to the first audio of this reply."""
        heard = getattr(self, "_last_user_voice", 0.0)
        if heard and heard > getattr(self, "_wait_ref", 0.0):
            self._reply_wait_s = round(now - heard, 2)
        self._wait_ref = now

    def _take_reply_wait(self) -> dict:
        w, self._reply_wait_s = getattr(self, "_reply_wait_s", None), None
        result = {} if w is None else {"wait_s": w}
        started = getattr(self, "_reply_audio_started", None)
        if started is not None:
            result.update(audio_started=started, audio_ended=self._reply_audio_ended)
            self._reply_audio_started = self._reply_audio_ended = None
        return result

    async def interrupt(self) -> None:
        """Barge-in: drop queued assistant speech immediately."""
        flushed = self.audio_io.flush() if self.audio_io is not None else 0
        self._speaking_until = 0.0
        self._speech_pending = False
        # Queued speech was discarded. It has no verified full playback interval; never attach its timing
        # (or its reply wait) to the next answer. Preserve the existing partial-caption handling.
        self._reply_audio_started = self._reply_audio_ended = None
        self._reply_wait_s = None
        self._stats["barge_ins"] += 1
        self._onset_over_speech = False
        logs.info("live", "LIVE_BARGE_IN", "user interrupted JARVES", flushed_bytes=flushed)
        await self._state("listening")

    async def _events(self) -> None:
        in_handler = False
        try:
            async for ev in self.conn:
                in_handler = True
                await self.handle_event(ev)
                in_handler = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.running:
                code = getattr(e, "code", None) or type(e).__name__
                if in_handler:                  # our bug, not the network: still end cleanly
                    logs.error("live", "LIVE_EVENT_HANDLER_CRASHED", "event handler crashed",
                               **logs.error_fields(e))
                    detail, action = f"live voice stopped: internal error ({type(e).__name__})", GENERIC_ACTION
                else:
                    logs.error("live", "LIVE_CONNECTION_LOST", "Live connection lost", error_type=type(e).__name__)
                    detail = f"live connection lost ({type(e).__name__})"
                    action = "check the internet; tap to start a new conversation"
                await self._health("degraded", detail, action)
                self._request_end(f"error:{code}")
        else:
            if self.running:                    # stream ended without session.closed
                logs.warn("live", "LIVE_STREAM_ENDED", "Live connection closed without session.closed")
                self._request_end("remote_closed")
        finally:
            self._closed.set()

    async def handle_event(self, ev) -> None:
        t = _get(ev, "type")
        if t != "session.output_audio.delta":
            logs.verbose("live", "LIVE_EVENT", "live event", event=str(t)[:64])
        g = lambda k, d=None: _get(ev, k, d)    # noqa: E731
        if t == "session.started":
            self._started.set()
            if self._t_started is None:
                self._t_started = self._clock()
            await self._health("ok", f"GPT-Live connected ({self.model} -> {self.backend_model})", "")
        elif t == "session.output_audio.delta":
            pcm = base64.b64decode(g("delta"))
            if self._drop_early(pcm):
                if self._held_text:                 # its words were never heard: no caption, no face reaction
                    self._stats["dropped_caption_chars"] = self._stats.get("dropped_caption_chars", 0) + len(self._held_text)
                    logs.debug("live", "LIVE_CAPTION_DROPPED", "caption of a dropped early answer discarded",
                               text=self._held_text.strip())
                    self._held_text = ""
                return
            audible = _rms16(pcm) > SPEECH_RMS
            if audible and time.monotonic() > self._speaking_until:
                await self._state("speaking")
            self._play(pcm)
            if audible and self._held_text:         # the held words are being spoken now: show them
                if not self._out_text:
                    self._out_turn_t0 = self._turn_t0
                self._out_text += self._held_text
                self._said_all += self._held_text
                self._held_text = ""
                await self._emote("jarves", self._out_text[-80:])
        elif t == "session.input_transcript.delta":
            self.note_activity()
            self._heard_user = True
            now = time.monotonic()
            if not self._in_text:
                # SW-V3 P0.1 (review): the new user turn starts at its FIRST transcript delta. Waiting for the
                # flush let "louder" after a mail read reach its tool call first and be refused as untrusted.
                self._new_turn()
                self._user_turn_n += 1
                self._turn_t0 = now
                self._turn_deleg0 = self._delegations
            if (not self._in_text and not self._gated() and now < self._speaking_until
                    and now - self._turn_audio_t0 >= BARGE_IN_MIN_S):
                await self.interrupt()          # new user utterance over still-queued assistant speech
            elif (not self._in_text and self._onset_over_speech and not self.half_duplex
                  and now - self._voice_onset < 6.0):
                # CV-09: the user talked over JARVES, but by the time the transcript arrives the SERVER has already
                # stopped that speech (nothing left to flush), so the barge-in was never counted
                self._onset_over_speech = False
                self._stats["barge_ins"] += 1
                logs.info("live", "LIVE_BARGE_IN", "user interrupted JARVES (speech already stopped by the server)",
                          flushed_bytes=0)
            self._in_text += g("delta") or ""
            await self._match_language()
            self._schedule_flush()
            if self._out_text:
                logs.info("live", "LIVE_JARVES_SAID", "JARVES said", text=self._out_text.strip(), **self._take_reply_wait())
                self._convo_add("assistant", self._out_text)
                self._check_claim(self._out_text)
                if self.on_transcript:
                    await self.on_transcript("assistant", self._out_text.strip())
                self._out_text = ""
        elif t == "session.output_transcript.delta":
            self.note_activity()
            if self._in_text:
                self._remember_user(self._in_text)
                logs.info("live", "LIVE_USER_SAID", "you said (as GPT-Live heard it)", text=self._in_text.strip())
                await self._emote("user", self._in_text)
                if self.on_transcript:
                    await self.on_transcript("user", self._in_text.strip())
                self._in_text = ""
            if self._hold_caption():
                # Issue #16: while the user is still talking an early answer's AUDIO is dropped (_drop_early); its
                # text must not reach the caption/face either. Held until its audio really plays, else discarded.
                self._held_text += g("delta") or ""
                self._schedule_flush()
                return
            d = _join(self._out_text, g("delta") or "")
            if not self._out_text:
                self._out_turn_t0 = self._turn_t0        # the user turn this assistant utterance answers
            self._out_text += d
            self._said_all += d
            await self._emote("jarves", self._out_text[-80:])
            self._schedule_flush()
        elif t == "session.delegation.created":
            logs.info("live", "LIVE_DELEGATION", "GPT-Live delegated work", target=str(_get(g("delegation"), "target")),
                      delegation=str(_get(g("delegation"), "id") or ""))
            did = str(_get(g("delegation"), "id") or "")
            if self._own_pending is not None and _get(g("delegation"), "target") == "responses":
                # live probe 20 Oct: a Core response.create arrives as an ordinary delegation (GPT-Live then speaks
                # its result itself); it is ours, not the model's own delegation for the user turn
                self._own_pending, self._own_delegation = None, did
            else:
                self._delegations += 1
            self._deleg_turn[did] = (self._user_turn_n, time.monotonic())
            if self.budget is not None and _get(g("delegation"), "target") == "responses":
                await self._take_credit("delegation")
            elif _get(g("delegation"), "target") == "client" and self.delegation is not None:
                await self.delegation.on_created(did, self._user_turn_n, self._convo_snapshot(), self._reply_lang)
        elif t == "response.event":
            await self._backend_event(g("event") or {}, g("delegation_id"))
        elif t == "session.usage.updated":
            self.usage_s = float(_get(g("usage"), "seconds", self.usage_s) or 0)
        elif t == "session.closed":
            usage = g("usage")
            if usage is not None:
                self.usage_s = float(_get(usage, "seconds", self.usage_s) or 0)
                self._usage_final = True
            server_reason = str(g("reason") or "")
            self._closed.set()
            if self._end_task is None:          # not requested by us
                logs.info("live", "LIVE_REMOTE_CLOSED", "server closed the session", reason=server_reason or "unknown")
                if server_reason in ("connection_lost", "content"):
                    await self._health("degraded", f"live session ended by the server ({server_reason})",
                                       "check the internet; tap to start a new conversation")
                self._request_end(CLOSE_REASONS.get(server_reason, "remote_closed"))
        elif t == "error":
            cid = str(_get(g("error"), "client_event_id") or g("client_event_id") or "")
            if cid.startswith("jarves_redelegate"):
                code, msg, _ = error_info(g("error"))
                logs.warn("live", "LIVE_REDELEGATE_REJECTED", "Core hand-over to the backend rejected", error_code=code,
                          provider_error=msg)
                self._own_rejected()
                return
            if cid.startswith("jarves_deleg") and self.delegation is not None:
                code, msg, _ = error_info(g("error"))     # client delegation PROTOCOL failure -> fallback ladder
                logs.warn("live", "LIVE_DELEGATION_REJECTED", "a client-delegation append was rejected", err_code=code,
                          detail=msg)
                await self.delegation.on_rejected(cid, code, msg)
                return
            if cid.startswith("jarves_lang"):
                code, msg, _ = error_info(g("error"))
                logs.warn("live", "LIVE_EXTRA_REJECTED", "optional context rejected", extra="lang", error_code=code,
                          provider_error=msg)
                self._lang_off = True
                return
            if cid.startswith(("jarves_clock", "jarves_progress", "jarves_stall", "jarves_task")):
                # optional extras: clock/progress are switched off; stall/task notes are only logged; stay healthy
                code, msg, _ = error_info(g("error"))
                logs.warn("live", "LIVE_EXTRA_REJECTED", "optional context rejected", extra=cid.split("_")[1],
                          error_code=code, provider_error=msg)
                self._rejected = (getattr(self, "_rejected", set()) | {cid})
                if cid.startswith("jarves_clock"):
                    self.clock_context = False
                elif cid.startswith("jarves_progress"):
                    self.progress_after_s = 0.0
                return
            await self._on_error(g("error"))
        elif t == "info":
            logs.debug("live", "LIVE_INFO", "server info message", provider_error=str(g("message"))[:200])

    async def _on_error(self, err, backend: bool = False) -> None:
        code, msg, etype = error_info(err)
        self._stats["errors"] += 1
        fatal = is_fatal(code, etype)
        logs.error("live", "LIVE_BACKEND_ERROR" if backend else "LIVE_ERROR", "the voice provider reported an error",
                   error_code=code, error_type=etype, provider_error=msg, backend=backend, fatal=fatal)
        if self.delegation is not None and "deleg" in f"{code} {msg}".lower():
            self.delegation.state.fail("live_error", f"{code}: {msg}")   # client delegation not accepted -> ladder
        if code == "responses_validation_failed":
            detail = f"backend delegation settings rejected: {msg}"
        else:
            detail = f"{'backend' if backend else 'live voice'} error {code}: {msg}"
        await self._health("degraded", detail, ERROR_ACTIONS.get(code, GENERIC_ACTION))
        if fatal:
            self._request_end(f"error:{code}")

    async def _take_credit(self, where: str) -> None:
        """The server started a delegated backend response: the held credit pays for it; reserve the next."""
        if self._credit is not None:
            self._pending.append(self._credit)
            self._credit = None
        else:                                       # server started it although no credit was held
            self._stats["budget_overrun"] += 1
            logs.warn("live", "AI_BUDGET_OVERRUN", "a delegated request started with no reserved credit; "
                      "ending the session now", overruns=self._stats["budget_overrun"])
            self._pending.append(_Counted(self.budget))
            self._input_closed = True
            await self._budget_exhausted(where)     # close now: don't wait for answers that may start more work
            return
        try:
            self._credit = self.budget.reserve("ai_requests")
        except BudgetExceeded:
            self._input_closed = True               # no new user turns -> no new delegations
            self._end_after_answer = True
            logs.warn("live", "AI_BUDGET", "no budget for another request; mic input closed, ending after this answer",
                      where=where, action="raise ai.daily_request_limit")

    def _schedule_flush(self, delay: float = 2.0) -> None:
        """Log what was said once the speaker has been quiet for `delay` s (instead of at the next turn)."""
        if self._flush_task is not None:
            self._flush_task.cancel()
        self._flush_task = asyncio.get_running_loop().create_task(self._flush_later(delay))

    async def _flush_later(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._flush_task = None                 # a new delta must not cancel the send below
        # QA 19 Oct (suite_voice): the answer text reached the face only when the user spoke AGAIN (one turn late,
        # the last answer never). The quiet-time flush now also sends it to the screen.
        for role, text in self._flush_transcripts():
            if self.on_transcript is not None and self.running:
                try:
                    await self.on_transcript(role, text)
                except Exception as e:
                    logs.debug("live", "LIVE_TRANSCRIPT_SEND_FAILED", "transcript not sent", error_type=type(e).__name__)

    def _flush_transcripts(self) -> list[tuple[str, str]]:
        out = []
        if self._held_text.strip():             # text whose audio never played: it was not said, do not caption it
            self._stats["dropped_caption_chars"] = self._stats.get("dropped_caption_chars", 0) + len(self._held_text)
            logs.debug("live", "LIVE_UNSPOKEN_TEXT", "assistant text without played audio not shown",
                       text=self._held_text.strip())
        self._held_text = ""
        if self._in_text.strip():
            self._remember_user(self._in_text)
            logs.info("live", "LIVE_USER_SAID", "you said (as GPT-Live heard it)", text=self._in_text.strip())
            out.append(("user", self._in_text.strip()))
            self._in_text = ""
        if self._out_text.strip():
            logs.info("live", "LIVE_JARVES_SAID", "JARVES said", text=self._out_text.strip(), **self._take_reply_wait())
            self._convo_add("assistant", self._out_text)
            self._check_claim(self._out_text)
            out.append(("assistant", self._out_text.strip()))
            self._out_text = ""
        return out

    async def _emote(self, role: str, text: str) -> None:
        """Face reaction to what was said (laugh at a joke, cry at sad news...). Same reaction at most every 5 s."""
        if self.on_emotion is None:
            return
        from ..emotion import detect_emotion
        emo = detect_emotion(text, role)
        now = time.monotonic()
        if emo and not (self._last_emotion[0] == emo and now - self._last_emotion[1] < 5.0):
            self._last_emotion = (emo, now)
            logs.info("live", "LIVE_EMOTION", "reacting with an emotion", emotion=emo, role=role)
            await _call(self.on_emotion, emo)

    async def _activity(self, kind: str, text: str = "", detail: str = "") -> None:
        """What JARVES is doing, for the screen (thinking / searching / music ...). kind "done" clears it."""
        self._activity_gen += 1
        if self.on_activity is not None:
            await _call(self.on_activity, kind, text, detail)

    async def _tool_activity(self, name: str, res) -> None:
        """Screen text for a Skill call (res=None: starting; else: finished)."""
        if name.startswith("music_"):
            if res is not None and res.ok and name == "music_play":
                np = ((res.data or {}).get("now_playing") or {})
                await self._activity("music", "מנגן", f"{np.get('title', '')} – {np.get('artist', '')}".strip(" –"))
            elif res is None:
                await self._activity("music", {"music_play": "מחפש שיר…", "music_next": "השיר הבא…",
                                               "music_pause": "משהה…", "music_resume": "ממשיך…",
                                               "music_volume": "מכוונן עוצמה…"}.get(name, "מוזיקה…"))
        elif res is None:
            kind, text = ({"get_time": ("time", "בודק שעה…"), "calculate": ("thinking", "מחשב…"),
                           "remember": ("memory", "זוכר…"),
                           "recall": ("memory", "נזכר…"), "forget": ("memory", "מוחק מהזיכרון…"),
                           "forget_all": ("memory", "מוחק מהזיכרון…"),
                           "task_start": ("tool", "מתחיל משימה ברקע…"), "task_status": ("tool", "בודק משימות…"),
                           "task_cancel": ("tool", "עוצר משימה…")}.get(name, ("tool", "עובד על זה…")))
            await self._activity(kind, text)

    async def _clear_activity_later(self, delay: float = 3.5) -> None:
        gen = self._activity_gen
        await asyncio.sleep(delay)
        if gen == self._activity_gen and self.running:
            await self._activity("done")

    async def _budget_exhausted(self, where: str) -> None:
        logs.warn("live", "AI_BUDGET", "daily AI request limit reached; ending live session", where=where,
                  action="raise ai.daily_request_limit")
        await self._health("degraded", "daily AI request limit reached; live voice ended",
                           "raise ai.daily_request_limit or wait until tomorrow")
        self._request_end("budget")

    def _remember_user(self, text: str) -> None:
        self._user_turns = (self._user_turns + [text.strip()])[-3:]
        self._convo_add("user", text)
        if self.delegation is not None:
            self.delegation.on_user_text(text.strip())
        self._new_turn()

    def _convo_add(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._convo = (self._convo + [{"role": role, "text": text}])[-40:]

    def _convo_snapshot(self) -> list[dict]:
        """The conversation so far for a client delegation, including the user's words still being transcribed
        (the delegation arrives ~0.4 s after the last input delta, before that turn is flushed: live probe 20 Oct)."""
        out = list(self._convo)
        if self._in_text.strip():
            out.append({"role": "user", "text": self._in_text.strip()})
        return out

    def _new_turn(self) -> None:
        if hasattr(self.registry, "new_turn"):
            # WP-G: the user spoke again -> side effects allowed after mail/calendar text (this conversation only)
            self.registry.new_turn(VOICE_ROUTE)

    async def _show(self, kind: str, text: str) -> None:
        """Backend stage on the screen; the elapsed-time ticker refreshes its detail line."""
        self._shown = (kind, text)
        await self._activity(kind, text, self._elapsed_detail())

    def _elapsed_detail(self) -> str:
        t0 = getattr(self, "_busy_t0", None)
        s = int(time.monotonic() - t0) if t0 is not None else 0
        return f"{s} שנ׳" if s >= 3 else ""

    async def _tick_activity(self) -> None:
        """While the backend works: refresh the pill's detail with the elapsed time every 2 s (no text change, so
        the face does not blink) and enforce the hard deadline."""
        while self.running and self._backend_busy:
            await asyncio.sleep(2.0 if not self.backend_deadline_s else min(2.0, self.backend_deadline_s / 4))
            if not (self.running and self._backend_busy):
                return
            kind, text = self._shown
            if kind:
                await self._activity(kind, text, self._elapsed_detail())
            t0 = getattr(self, "_backend_t0", None)
            # a Skill running or an approval waiting on the screen is not a backend stall
            if self._tools_running or self._confirm_pending:
                self._backend_t0 = time.monotonic()
            elif self.backend_deadline_s and t0 is not None and time.monotonic() - t0 >= self.backend_deadline_s:
                self._backend_stalled(getattr(self, "_busy_delegation", None))
                return

    def _backend_stalled(self, delegation_id) -> None:
        tr = self._trace
        self._abandoned.add(delegation_id)
        self._backend_busy = False                  # progress notes stop; the user is not kept waiting
        self._backend_t0 = None
        self._stats["backend_stalls"] = self._stats.get("backend_stalls", 0) + 1
        logs.warn("live", "LIVE_BACKEND_STALL", "backend lookup got no result before the deadline; giving up on it",
                  deadline_s=self.backend_deadline_s, stage=getattr(tr, "stage", ""), delegation=delegation_id,
                  query="; ".join(getattr(tr, "queries", []) or []))
        if self._superseded(delegation_id):
            # CV-09: the user moved on (barge-in "2+2" answered directly): retrying would answer the NEW turn again
            # and a failure note would be about a question nobody waits for
            logs.info("live", "LIVE_BACKEND_SUPERSEDED", "stalled lookup belongs to an earlier turn: dropped quietly")
            self._spawn(self._activity("done"), "activity-clear")
            return
        self._retry_task = self._spawn(self._retry_stalled(tr), "stall-retry")

    async def _retry_stalled(self, tr) -> None:
        """One retry of a stalled lookup in the Core (hard deadline), then tell GPT-Live the honest outcome."""
        turns = self._user_turns + ([self._in_text.strip()] if self._in_text.strip() else [])
        question = turns[-1] if turns else ""
        if len(turns) > 1:
            question = "Earlier: " + " | ".join(turns[:-1]) + "\nAnswer this now: " + turns[-1]
        if tr is not None and tr.queries:
            question += f"\n(search hint: {tr.queries[-1]})"
        answer = ""
        if self.retry_lookup is not None and question:
            logs.info("live", "LIVE_BACKEND_RETRY", "retrying the stalled lookup once in the Core",
                      deadline_s=RETRY_DEADLINE_S)
            await self._show("searching", "מנסה שוב…")
            self._tools_running += 1                # keeps the idle watchdog from closing the session meanwhile
            t0 = time.monotonic()
            try:
                answer = str(await asyncio.wait_for(self.retry_lookup(question), RETRY_DEADLINE_S) or "").strip()
                logs.info("live", "LIVE_BACKEND_RETRY_OK", "retry answered", retry_s=round(time.monotonic() - t0, 1))
            except asyncio.CancelledError:
                logs.info("live", "LIVE_BACKEND_RETRY_CANCELLED", "retry cancelled: the original answer arrived")
                raise
            except Exception as e:
                logs.warn("live", "LIVE_BACKEND_RETRY_FAILED", "retry failed",
                          retry_s=round(time.monotonic() - t0, 1), **logs.error_fields(e))
            finally:
                self._tools_running = max(0, self._tools_running - 1)
        content = STALL_RESULT_NOTE.format(answer=answer[:600]) if answer else STALL_FAIL_NOTE
        self._stats["stall_answers" if answer else "stall_failures"] = self._stats.get(
            "stall_answers" if answer else "stall_failures", 0) + 1
        await self._send_note(content, str(self._stats["backend_stalls"]), prefix="jarves_stall")
        await self._activity("done")

    async def announce(self, content: str, *, max_wait_s: float = 300.0, tag: str = "") -> bool:
        """Speak a finished background task's result (tasks.TaskManager) at a GOOD moment: never over the user's
        speech, never over JARVES's own, not while a lookup/tool/approval is in flight. Sent as commentary (the
        model words it naturally). False when the session ended (or max_wait_s passed) first: the caller keeps it."""
        deadline = time.monotonic() + max_wait_s
        while self.running and self.conn is not None and time.monotonic() < deadline:
            now = time.monotonic()
            audio = self.audio_io
            busy = (self._backend_busy or self._tools_running or self._confirm_pending or self._in_text.strip()
                    or now - self._last_user_voice < ANNOUNCE_USER_QUIET_S or now < self._speaking_until + 0.8
                    or (audio is not None and audio.queued_s() > 0))
            if not busy:
                return await self._send_note(content, tag)
            await asyncio.sleep(0.25)
        return False

    async def _send_note(self, content: str, tag: str, prefix: str = "jarves_task") -> bool:
        """Commentary that must arrive: the server validates it AFTER the send (error with our event id, e.g. "must
        not exceed 500 tokens"), so wait briefly for a rejection and resend once shortened (notes put their
        instructions first and the result last, so the cut only shortens the result)."""
        content = content[:NOTE_MAX_CHARS]
        for attempt in (1, 2):
            n = self._stats["announcements"] = self._stats.get("announcements", 0) + 1
            eid = f"{prefix}_{tag or 'x'}_{n}"
            try:
                await self.conn.session.commentary.append(content=content, delegation_id=None, event_id=eid)
            except Exception as e:
                logs.warn("live", "LIVE_ANNOUNCE_FAILED", "announcement not sent", error_type=type(e).__name__)
                return False
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.5 and eid not in getattr(self, "_rejected", set()) and self.running:
                await asyncio.sleep(0.1)
            if eid not in getattr(self, "_rejected", set()):
                logs.info("live", "LIVE_ANNOUNCE" if prefix == "jarves_task" else "LIVE_NOTE",
                          "note handed to GPT-Live", extra=tag, chars=len(content))
                self.note_activity()
                return True
            content = content[: len(content) // 2]
        return False

    async def _backend_event(self, inner: dict, delegation_id: str | None) -> None:
        it = inner.get("type") or ""
        tr = self._trace
        if delegation_id is not None and delegation_id in self._abandoned:
            # a delegation given up after the deadline: only log what it does now (its late answer is spoken by
            # GPT-Live; the Core retry is then cancelled so the user does not hear the answer twice)
            if it in ("response.completed", "response.failed", "response.incomplete"):
                self._abandoned.discard(delegation_id)
                self._stats["backend_responses"] += 1
                logs.warn("live", "LIVE_BACKEND_LATE", "a stalled backend response finished after the deadline",
                          delegation=delegation_id, status=it.split(".")[-1])
                if self._retry_task is not None and not self._retry_task.done() and it == "response.completed":
                    self._retry_task.cancel()
                self._settle_one()
            return
        if it == "response.created":
            if self._own_pending is not None and self._delegations == self._own_pending:
                self._own_pending, self._own_delegation = None, delegation_id   # the Core-started response
            from ..brain import BackendTrace
            tr = self._trace = BackendTrace("live", "LIVE_BACKEND", delegation=delegation_id)
            self._backend_t0 = time.monotonic()
            if not getattr(self, "_backend_busy", False) or getattr(self, "_busy_delegation", None) != delegation_id:
                self._busy_t0 = self._backend_t0    # elapsed on screen counts from the delegation's first response
            self._busy_delegation = delegation_id
            self._backend_busy = True
            if self._ticker is None or self._ticker.done():
                self._ticker = self._spawn(self._tick_activity(), "backend-ticker")
            if self.progress_after_s > 0 and delegation_id != getattr(self, "_progress_for", None):
                self._progress_for = delegation_id          # at most one progress note per delegation
                self._spawn(self._progress_later(delegation_id), "progress")
            self.note_activity()
            await self._state("thinking")
        shown = tr.feed(inner) if tr is not None else None
        if shown is not None:
            await self._show(*shown)
        if it in ("response.completed", "response.failed", "response.incomplete"):
            self._backend_busy = False
            self.note_activity()
            # One delegated backend response finished (billed whether or not it succeeded) -> count it.
            self._stats["backend_responses"] += 1
            self._backend_t0 = None
            answer = (getattr(tr, "text", "") or "").strip() if tr is not None else ""
            if it == "response.completed" and len(answer.split()) >= 2 and getattr(tr, "stage", "") != "tool":
                self._answers += 1              # a substantive backend answer (the user's request moved on)
            own = self._own_delegation != "unset" and delegation_id == self._own_delegation
            if own and getattr(tr, "stage", "") != "tool":
                self._own_delegation = "unset"
                if it == "response.completed" and answer:
                    self._spawn(self._deliver_own(answer, len(self._said_all)), "own-delivery")
                else:
                    self._spawn(self.announce(REDELEGATE_FAIL_NOTE, max_wait_s=10.0, tag="redelegate"),
                                "redelegate-fail")
            elif (it == "response.completed" and answer and getattr(tr, "stage", "") != "tool"
                  and answer.strip(" .!?,").lower() not in _BARE_REPLIES):
                self._spawn(self._watch_delivery(answer, (len(self._said_all), self._answers, self._user_turn_n)),
                            "delivery-watch")
            self._trace = None
            self._spawn(self._clear_activity_later(), "activity-clear")
            self._settle_one()
            if it == "response.failed":
                await self._on_error(_get(inner.get("response"), "error") or {"code": "response_failed"}, True)
            if self._end_after_answer and not self._pending:
                await self._budget_exhausted("last reserved answer finished")
        elif it == "response.output_item.done":
            item = inner.get("item") or {}
            if item.get("type") != "function_call":
                return
            name, call_id = item.get("name", ""), item.get("call_id", "")
            logs.info("live", "LIVE_TOOL_CALL", "backend called a tool", tool=name, delegation=delegation_id)
            nxt = None
            if self.budget is not None:
                try:
                    nxt = self.budget.reserve("ai_requests")    # the continuation is reserved BEFORE it is sent
                except BudgetExceeded:
                    res = ToolResult(False, "daily AI request limit reached; the conversation will end now",
                                     code="AI_BUDGET")
                    await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                               "output": res.to_model()})
                    await self._budget_exhausted(f"tool {name}")   # no response.create: that would be paid
                    return
                self._pending.append(nxt)       # FIFO after this response's own unit; an end-after-answer waits
            # QA 19 Oct (suite_voice): the Skill ran INSIDE the event loop, so a 20 s confirmation (or a slow tool)
            # froze all audio, transcripts and usage events. It now runs in its own task.
            self._tools_running += 1
            state = {"sent": False}
            t = self._spawn(self._run_tool(name, call_id, item.get("arguments") or "{}", state), f"tool-{name}")
            t.add_done_callback(lambda task: self._tool_finished(task, nxt, state))
        elif it == "error":
            await self._on_error(inner.get("error") or inner, True)

    def _check_claim(self, text: str) -> None:
        """An assistant utterance just finished: if it claims a side effect ("רשמתי לי ש...") while no action tool
        succeeded for that user turn, flag it and make GPT-Live do it or take it back (see action_claim)."""
        self._check_undelegated(text)
        verb = action_claim(text)
        if not verb or not self.running or self._claim_flagged_turn == self._user_turn_n:
            return
        try:
            self._spawn(self._verify_claim(verb, self._out_turn_t0, self._user_turn_n), "claim-check")
        except RuntimeError:                       # no running loop (sync test paths)
            pass

    async def _verify_claim(self, verb: str, turn_t0: float, turn_n: int) -> None:
        t_end = time.monotonic() + CLAIM_SETTLE_MAX_S   # a tool still running / a result on its way: wait for it
        while self.running and time.monotonic() < t_end and (self._tools_running or self._backend_busy):
            await asyncio.sleep(0.2)
        if any(t >= turn_t0 - CLAIM_TURN_SLACK_S for t in self._action_times):
            return
        if self._claim_flagged_turn == turn_n or not self.running:
            return
        self._claim_flagged_turn = turn_n
        self._stats["false_action_claims"] = self._stats.get("false_action_claims", 0) + 1
        logs.warn("live", "LIVE_FALSE_ACTION_CLAIM", "JARVES claimed an action that no tool performed in this turn",
                  verb=verb, delegations=self._delegations)
        if await self._make_claim_true():
            return
        text = self._user_turns[-1] if self._user_turns else ""
        asked = user_asked_action(text)
        if verb in MEMORY_CLAIM_VERBS and not asked:
            # CV-06: the user only told something; memory is saved on request only, so take the claim back
            logs.info("live", "LIVE_CLAIM_UNASKED", "claimed a memory the user never asked for: correcting it")
            await self.announce(UNASKED_CLAIM_NOTE.format(verb=verb), max_wait_s=10.0, tag="claim")
            return
        if (asked and turn_n == self._user_turn_n and self._delegations == self._turn_deleg0
                and not (self._backend_busy or self._tools_running)
                and await self._redelegate(text, turn_n, "false_claim", announce_fail=False)):
            return                                  # the backend does it now; its real result is then said
        await self.announce(FALSE_CLAIM_NOTE.format(verb=verb), max_wait_s=10.0, tag="claim")

    # ------------------------------------------------------------------ CV-15: acknowledged but never delegated
    def _check_undelegated(self, text: str) -> None:
        """An assistant utterance finished. If it only announced waiting ("רגע...") while nothing was delegated for
        this user turn, watch: no delegation REDELEGATE_AFTER_S later -> the Core hands the turn to the backend."""
        turn = self._user_turn_n
        if (not self.running or turn <= 0 or self._redelegated_turn == turn or self._delegations != self._turn_deleg0
                or not waiting_only(text)):
            return
        try:
            self._spawn(self._watch_undelegated(turn), "undelegated-watch")
        except RuntimeError:                       # no running loop (sync test paths)
            pass

    async def _watch_undelegated(self, turn: int) -> None:
        t_end = time.monotonic() + REDELEGATE_AFTER_S
        while self.running and time.monotonic() < t_end:
            if self._delegations != self._turn_deleg0 or self._user_turn_n != turn:
                return                              # it delegated after all, or the user moved on
            await asyncio.sleep(0.1)
        if (not self.running or self._user_turn_n != turn or self._delegations != self._turn_deleg0
                or self._redelegated_turn == turn or self._backend_busy or self._tools_running
                or self._confirm_pending or self._in_text.strip() or self._own_pending is not None):
            return
        text = self._user_turns[-1] if self._user_turns else ""
        if not text:
            return
        self._stats["undelegated_turns"] = self._stats.get("undelegated_turns", 0) + 1
        logs.warn("live", "LIVE_UNDELEGATED", "JARVES acknowledged a request but never delegated it; the Core hands "
                  "it to the backend", after_s=REDELEGATE_AFTER_S)
        await self._redelegate(text, turn, "undelegated")

    async def _redelegate(self, text: str, turn: int, why: str, announce_fail: bool = True) -> bool:
        """Start a backend response from the Core with the user's words (the session's own backend config, Skill
        tools and approvals). Its result is said through _deliver_own; if it cannot start, the user hears so."""
        self._redelegated_turn = turn
        if self.delegation_mode == "text_only":     # no backend this conversation: nothing to hand over
            return False
        if self.delegation is not None and not self.delegation.may_hand_over(text):
            return False                            # "עזוב" right after a cancel is not a request (live 20 Oct)
        if self.delegation is not None:             # client mode: the Core runs the turn itself (reserved first)
            self._stats["redelegations"] = self._stats.get("redelegations", 0) + 1
            logs.info("live", "LIVE_REDELEGATED", "the Core handed the user's request to the backend", why=why)
            await self.delegation.on_created(f"jarves_own_{turn}", turn, self._convo_snapshot(), self._reply_lang,
                                             own=True)
            return True
        if self._input_closed:                      # budget F1: no new backend work once the credit ran out
            await self.announce(REDELEGATE_FAIL_NOTE, max_wait_s=10.0, tag="redelegate")
            return False
        # Billing: the server announces this response as an ordinary session.delegation.created, which takes the
        # held credit like any delegation (_take_credit); nothing is reserved here twice.
        self._own_pending, self._own_res = self._delegations, None
        try:
            await self.conn.response.item.create(item={"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": REDELEGATE_INPUT.format(text=text.strip()[:600])}]},
                event_id=f"jarves_redelegate_item_{turn}")
            await self.conn.response.create(event_id=f"jarves_redelegate_{turn}")
        except Exception as e:
            logs.warn("live", "LIVE_REDELEGATE_FAILED", "hand-over not sent", error_type=type(e).__name__)
            self._own_rejected(announce=announce_fail)
            return False
        self._stats["redelegations"] = self._stats.get("redelegations", 0) + 1
        logs.info("live", "LIVE_REDELEGATED", "the Core handed the user's request to the backend", why=why)
        self._spawn(self._own_start_timeout(), "redelegate-timeout")
        return True

    async def _own_start_timeout(self, wait_s: float = 8.0) -> None:
        await asyncio.sleep(wait_s)
        if self._own_pending is not None and self.running:
            logs.warn("live", "LIVE_REDELEGATE_NO_RESPONSE", "the Core hand-over got no backend response")
            self._own_rejected()

    def _own_rejected(self, announce: bool = True) -> None:
        """The Core-started response never started: free its reservation and tell the user honestly."""
        if self._own_pending is None:
            return
        self._own_pending = None
        res = getattr(self, "_own_res", None)
        if res is not None and res in self._pending:
            self._pending.remove(res)
            res.release()
        if self.running and announce:
            try:
                self._spawn(self.announce(REDELEGATE_FAIL_NOTE, max_wait_s=10.0, tag="redelegate"), "redelegate-fail")
            except RuntimeError:
                pass

    async def _deliver_own(self, answer: str, start: int) -> None:
        """A Core-started backend answer: GPT-Live may say it by itself; if it has not within OWN_DELIVERY_WAIT_S
        (and after it stops talking), it is handed over once."""
        await asyncio.sleep(OWN_DELIVERY_WAIT_S)
        t_end = time.monotonic() + 15.0
        while self.running and time.monotonic() < t_end and time.monotonic() < self._speaking_until + 0.6:
            await asyncio.sleep(0.2)
        if not self.running or answer_said(answer, self._said_all[start:]):
            return
        logs.info("live", "LIVE_OWN_RESULT", "handing the Core-started backend result to GPT-Live", chars=len(answer))
        await self.announce(REDELEGATE_RESULT_NOTE.format(answer=answer[:600]), max_wait_s=15.0, tag="redelegate")

    def _superseded(self, delegation_id) -> bool:
        """CV-09: the user moved on after this delegation (a NEW utterance well after it) and JARVES already spoke
        to that new turn, e.g. a barge-in "2+2" answered directly while an older story lookup still runs."""
        t = self._deleg_turn.get(str(delegation_id or ""))
        return (t is not None and self._user_turn_n > t[0] and self._turn_t0 > t[1] + 1.5
                and self._last_speech_t > self._turn_t0 + 0.3)

    async def _match_language(self) -> None:
        """CV-05: name this turn's language for GPT-Live (developer instruction) whenever it changes."""
        if getattr(self, "_lang_off", False) or self._lang_turn == self._user_turn_n or self.conn is None:
            return
        lang = text_language(self._in_text)
        if not lang:
            return
        self._lang_turn = self._user_turn_n
        if lang == self._reply_lang:
            return
        self._reply_lang = lang
        name = "English" if lang == "en" else "Hebrew"
        try:
            if lang == "he":
                # live runs 20 Oct (3/3): a developer INSTRUCTION naming Hebrew, appended while the user asked in
                # Hebrew, left that question unanswered (no audio at all); without any note the answer stayed in
                # English (2/2). Back to the default language goes as silent context instead.
                await self.conn.session.thinking.append(content=LANG_BACK_NOTE, delegation_id=None,
                                                        event_id=f"jarves_lang_{self._user_turn_n}")
            else:
                await self.conn.session.instructions.append(content=LANG_NOTE.format(lang=name), delegation_id=None,
                                                            event_id=f"jarves_lang_{self._user_turn_n}")
            self._stats["language_notes"] = self._stats.get("language_notes", 0) + 1
            logs.info("live", "LIVE_LANGUAGE", "language note sent", language=name, lang=lang)
        except Exception as e:
            logs.warn("live", "LIVE_LANGUAGE_FAILED", "language note not sent", error_type=type(e).__name__)

    async def _make_claim_true(self) -> bool:
        """The user's own words were a plain "תזכור ש..." (the deterministic parser matches them): the Core saves it
        itself, as the user's request (origin "user"), so the spoken "רשמתי" becomes true. Silent run 20 Oct: a
        correction note alone made GPT-Live say "let me verify" but not delegate until the next turn."""
        from ..brain import match_intent
        text = self._user_turns[-1] if self._user_turns else ""
        intent = match_intent(text) if text else None
        if intent is None or intent.tool != "remember" or self.registry.get("remember") is None:
            return False
        ROUTE.set(VOICE_ROUTE)
        res = await self.registry.call("remember", intent.args, origin="user")
        if not res.ok:
            return False
        self._action_times.append(time.monotonic())
        logs.info("live", "LIVE_CLAIM_FIXED", "the claimed memory was saved by the Core from the user's own words")
        return True

    async def _watch_delivery(self, answer: str, mark: tuple[int, int, int]) -> None:
        """After a completed backend answer: if GPT-Live said only fillers (or nothing) within DELIVERY_WINDOW_S, and
        the user did not move on (no new utterance or delegation), hand it the answer once."""
        start, deleg, turns = mark            # taken when the answer completed (this task starts later)
        t0 = time.monotonic()
        while self.running and time.monotonic() - t0 < DELIVERY_WINDOW_S:
            # CV-14: a dead quiet DELIVERY_QUIET_S after the answer (nothing said, nothing playing) needs no full
            # 7 s window: the WhatsApp "opened the chat" result after a spoken yes was otherwise 9 s of silence
            if (time.monotonic() - t0 >= DELIVERY_QUIET_S and time.monotonic() >= self._speaking_until + 1.5
                    and not self._backend_busy and not self._tools_running
                    and not answer_said(answer, self._said_all[start:])):
                break
            await asyncio.sleep(0.2)
        t_end = time.monotonic() + DELIVERY_MAX_WAIT_S
        while self.running and time.monotonic() < t_end and (
                time.monotonic() < self._speaking_until + 0.5 or self._tools_running or self._backend_busy):
            await asyncio.sleep(0.25)
        # CV-14 (live 20 Oct): after a spoken approval "כן", GPT-Live delegated that "כן" once more (an empty 4-token
        # backend answer) and never said "opened the chat"; only a NEW substantive answer or user turn means the
        # user moved on, not any delegation
        if not self.running or self._answers != deleg or self._user_turn_n != turns:
            return
        said = self._said_all[start:]
        if answer_said(answer, said):
            return
        self._stats["undelivered_answers"] = self._stats.get("undelivered_answers", 0) + 1
        logs.warn("live", "LIVE_ANSWER_UNDELIVERED", "the backend answer was not said; handing it to GPT-Live again",
                  window_s=DELIVERY_WINDOW_S, said_chars=len(said.strip()), answer_chars=len(answer))
        await self.announce(DELIVERY_NOTE.format(answer=answer[:600]), max_wait_s=15.0, tag="delivery")

    def _settle_one(self) -> None:
        """A delegated backend response finished: commit its reservation (FIFO)."""
        if self.budget is not None and not self._budget_settled:
            if self._pending:
                self._pending.pop(0).commit()
            else:                               # a backend response we never saw start: count it anyway
                self._stats["budget_overrun"] += 1
                self.budget.record("ai_requests", 1)

    async def _run_tool(self, name: str, call_id: str, arguments: str, state: dict) -> None:
        ROUTE.set(VOICE_ROUTE)                  # own task: the untrusted-content taint is this conversation's
        await self._tool_activity(name, None)
        res = await self.registry.call(name, arguments)
        if res.ok and getattr(self.registry.get(name), "permission", "read") in ("action", "destructive"):
            self._action_times.append(time.monotonic())     # real side effects (false-claim check)
        await self._tool_activity(name, res)
        await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                   "output": res.to_model()})
        state["sent"] = True
        await self.conn.response.create()

    def _tool_finished(self, task: asyncio.Task, nxt, state: dict) -> None:
        """Runs even when the task was cancelled before it started: settle the reserved continuation."""
        self._tools_running = max(0, self._tools_running - 1)
        self.note_activity()
        failed = task.cancelled() or task.exception() is not None
        if failed and nxt is not None and nxt in self._pending:
            self._pending.remove(nxt)
            nxt.commit() if state["sent"] else nxt.release()   # outcome unknown -> counted; never sent -> freed

    async def confirm_pending(self, prompt: str, voice: bool = False) -> None:
        """The Core is asking the user to approve a destructive tool on the screen. If it is still unanswered after
        CONFIRM_NOTE_AFTER_S, JARVES says so (once). No progress notes meanwhile. QA 19 Oct: a note sent at once was
        spoken INSTEAD of the result when the user approved quickly, so a fast answer gets no note at all."""
        self._confirm_pending = True
        self._confirm_gen = getattr(self, "_confirm_gen", 0) + 1
        self._confirm_voice = bool(voice)
        self._confirm_prompt = prompt
        if self.running and self.conn is not None:
            self._spawn(self._confirm_note_later(self._confirm_gen), "confirm-note")

    async def _confirm_note_later(self, gen: int) -> None:
        await asyncio.sleep(CONFIRM_NOTE_AFTER_S)
        if not (self.running and self._confirm_pending and gen == getattr(self, "_confirm_gen", 0)):
            return
        try:
            note = CONFIRM_NOTE_VOICE if getattr(self, "_confirm_voice", False) else CONFIRM_NOTE
            prompt = str(getattr(self, "_confirm_prompt", "") or "").strip()
            if prompt:
                # live probe 20 Oct: for a Core-started request GPT-Live did not know what was being approved and
                # asked "to check the time, yes or no?"; the approval's own read-back names it (CV-14 too)
                note += (f"\nThe action waiting for approval (name only this kind of action, in the user's language; "
                         f"never read out message text, commands, addresses or any other detail): {prompt[:300]}")
            await self.conn.session.commentary.append(content=note, delegation_id=None,
                                                      event_id="jarves_progress_confirm")
            logs.info("live", "LIVE_CONFIRM_NOTE", "asked the user to approve on the screen")
        except Exception as e:
            logs.warn("live", "LIVE_PROGRESS_FAILED", "confirmation note not sent", error_type=type(e).__name__)

    def confirm_done(self) -> None:
        self._confirm_pending = False
        self.note_activity()
