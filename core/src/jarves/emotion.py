"""Lightweight, offline emotion cues from the conversation text (Hebrew + English), so the face can react:
laugh at a joke, cry at sad news, show surprise or affection. No model call, no cost, instant.

Conservative on purpose: common words ("אבל" = but, "sorry", "מצטער") would fire constantly, so only unmistakable
cues count. The Live model is also told to laugh ("חה חה") and sound sad in its own voice, which shows up here.
"""
from __future__ import annotations

import re

# role-specific cues: what the USER says vs what JARVES says
_LAUGH = {
    "both": ["חחח", "חה חה", "חהחה", "הההה", "ahaha", "hahaha", "haha", "ha ha", "lol", "lmao", "😂", "🤣", "😆"],
    "user": ["מצחיק", "צוחק", "מתפקע", "בדיחה טובה"],
    "jarves": ["זה מצחיק", "מצחיק אותי", "אני צוחק", "בדיחה טובה", "funny one"],
}
_SAD = {
    "both": ["😢", "😭", "🥺"],
    "user": ["בוכה", "בכיתי", "דמעות", "נפטר", "נפטרה", "הלוויה", "כואב לי", "שבור לב", "i'm crying", "passed away",
             "funeral", "heartbroken"],
    "jarves": ["עצוב לי", "זה עצוב", "מצטער לשמוע", "מצטערת לשמוע", "זה נורא", "כואב לי לשמוע", "אני בוכה",
               "so sorry to hear", "that's so sad", "that is sad", "i'm sorry for your loss", "my condolences"],
}
_SURPRISED = {
    "both": ["וואו", "וואלה", "לא ייאמן", "לא יאומן", "בחיים לא", "wow", "no way", "omg", "unbelievable", "😮", "😲"],
    "user": [],
    "jarves": ["מדהים", "amazing", "incredible"],
}
_LOVE = {
    "both": ["❤", "😍", "🥰"],
    "user": ["אוהב אותך", "אני אוהבת אותך", "love you", "אתה מתוק", "אתה חמוד", "you're sweet", "תודה רבה"],
    # 19 Oct persona: the warmer tone words the Live prompt now asks for ("שמח בשבילך", "מזל טוב")
    "jarves": ["אוהב אותך", "נעים לי", "איזה כיף", "שמח בשבילך", "מזל טוב", "כל הכבוד לך", "you're so sweet",
               "aww", "so happy for you", "congratulations"],
}
# Mild sadness ("I'm a bit sad", "that's not simple, I'm here with you") -> the face's gentle "sad" look; loss, illness,
# death or crying -> full "cry". Regexes allow words between ("אני קצת עצוב").
_MILD_SAD = {
    "both": [],
    "user": [r"אני\s+(?:\S+\s+){0,2}(?:עצוב|עצובה|מדוכא|מדוכאת|בודד|בודדה|שבור|שבורה|מתוסכל|מתוסכלת)",
             r"(?:עצוב|עצובה)\s+לי", r"בדידות|דיכאון|(?:feel|feeling)\s+lonely",
             r"(?:i'?m|i am|i feel)\s+(?:\w+\s+){0,2}(?:sad|lonely|depressed|down|upset)"],
    "jarves": [r"אני\s+כאן\s+איתך", r"אני\s+כאן\s+בשבילך", r"זה\s+(?:באמת\s+)?לא\s+פשוט", r"^אוי[,.!\s]+לא\b",
               r"i'?m\s+here\s+(?:with|for)\s+you"],
}
_CRY_RE = {
    "both": [],
    "user": [r"(?:שלי|שלנו)\s+(?:\S+\s+)?(?:חולה|חולים|מת|מתה|נפטר|נפטרה|נפטרו|נהרג|נהרגה)",
             r"(?:my|our)\s+(?:\w+\s+){0,2}(?:is|are|was)\s+(?:sick|dying|dead|gone)"],
    "jarves": [],
}
_TABLE = {"laugh": _LAUGH, "cry": _SAD, "surprised": _SURPRISED, "love": _LOVE}
_REGEX = {"cry": _CRY_RE, "sad": _MILD_SAD}
# when several match in one sentence: loss beats mild sadness, sadness beats a nervous laugh, laughter the rest
_PRIORITY = ("cry", "sad", "laugh", "love", "surprised")
DURATION_MS = {"laugh": 3500, "cry": 4500, "sad": 3500, "surprised": 2200, "love": 3200}
_norm = re.compile(r"[֑-ׇ]")          # Hebrew niqqud


def detect_emotion(text: str, role: str = "user") -> str | None:
    """Return "laugh" | "cry" | "surprised" | "love" | None for one utterance (role: "user" or "jarves")."""
    t = _norm.sub("", (text or "").casefold())
    if not t.strip():
        return None
    for emo in _PRIORITY:
        cues = _TABLE.get(emo)
        if cues and any(c in t for c in cues["both"] + cues.get(role, [])):
            return emo
        rx = _REGEX.get(emo)
        if rx and any(re.search(r, t) for r in rx["both"] + rx.get(role, [])):
            return emo
    return None
