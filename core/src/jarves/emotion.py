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
    "user": ["עצוב לי", "אני עצוב", "אני עצובה", "בוכה", "בכיתי", "דמעות", "בודד", "בודדה", "בדידות", "דיכאון",
             "נפטר", "נפטרה", "הלוויה", "כואב לי", "שבור לב", "i'm sad", "i am sad", "i'm crying", "feel lonely",
             "passed away", "funeral", "heartbroken", "depressed"],
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
    "jarves": ["אוהב אותך", "נעים לי", "איזה כיף", "you're so sweet", "aww"],
}
_TABLE = {"laugh": _LAUGH, "cry": _SAD, "surprised": _SURPRISED, "love": _LOVE}
# when several match in one sentence: sad news wins over a nervous laugh, laughter over the rest
_PRIORITY = ("cry", "laugh", "love", "surprised")
DURATION_MS = {"laugh": 3500, "cry": 4500, "surprised": 2200, "love": 3200}
_norm = re.compile(r"[֑-ׇ]")          # Hebrew niqqud


def detect_emotion(text: str, role: str = "user") -> str | None:
    """Return "laugh" | "cry" | "surprised" | "love" | None for one utterance (role: "user" or "jarves")."""
    t = _norm.sub("", (text or "").casefold())
    if not t.strip():
        return None
    for emo in _PRIORITY:
        cues = _TABLE[emo]
        if any(c in t for c in cues["both"] + cues.get(role, [])):
            return emo
    return None
