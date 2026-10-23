"""The ONE reviewed list of spoken filler / acknowledgement words (issue #17).

Real PC evidence (`jarvis diagnose`, Hebrew voice user): a radio-style acknowledgement (heard as "רוגס", i.e. "Roger")
was said 4 times and the user asked what it meant. Rules, enforced by tests/test_voice_behaviour.py:

* JARGON_ACKS: words JARVES must never say as an acknowledgement (radio / military / slang that a user may not
  understand). They go into the voice instructions as a ban list and into `jarvis diagnose` as "confusing filler".
* LEAD_IN_STEMS: plain waiting words ("רגע", "בודק"). Allowed only in the same moment as a delegation, never alone.
* Anything else (a real result sentence) is neither: it is an answer.

Both live.py (prompt, answer detection, undelegated watch) and diagnose.py (filler issue) import from here, so the
list is reviewed in one place.
"""
from __future__ import annotations

# spoken form -> canonical display word. Same-root variants and ASR spellings of the same word count together.
JARGON_ACKS: dict[str, str] = {
    "רוגס": "רוג'ר", "רוגר": "רוג'ר", "רוג'ר": "רוג'ר", "רוג'ס": "רוג'ר", "roger": "roger", "copy": "copy",
    "wilco": "wilco", "affirmative": "affirmative", "ווילקו": "wilco", "קופי": "copy", "אפירמטיב": "affirmative",
    "סגור": "סגור", "סוגר": "סגור", "בטח": "בטח",
}
# bare acknowledgements that carry no result (diagnose counts them as "confusing filler" when repeated)
PLAIN_ACKS: dict[str, str] = {"אוקיי": "אוקיי", "אוקי": "אוקיי", "ok": "אוקיי", "okay": "אוקיי", "בסדר": "בסדר",
                              "מעולה": "מעולה", "יופי": "יופי", "sure": "sure", "done": "done"}
ACK_WORDS: dict[str, str] = {**JARGON_ACKS, **PLAIN_ACKS}

# waiting words: allowed only together with the delegation (stems, matched inside words)
LEAD_IN_STEMS: tuple[str, ...] = ("בודק", "בודקת", "מחפש", "מחפשת", "רגע", "שנייה", "שניה", "כמעט", "לוקח", "ממתין",
                                  "מתעכב", "checking", "moment", "second", "almost")
WAIT_ONLY_EXTRA: tuple[str, ...] = ("מחשב", "מחשבת", "מעביר", "מוחק", "מוחקת", "שומר", "פותח", "אממ")
BARE_REPLIES = frozenset({"כן", "לא", "בסדר", "אוקיי", "אוקי", "ok", "okay", "yes", "no", "done"}
                         | {w.lower() for w in JARGON_ACKS})


def banned_list_for_prompt() -> str:
    """The jargon words as one quoted list for the voice instructions."""
    seen: list[str] = []
    for w in ("רוג'ר", "roger", "copy", "wilco", "סגור", "בטח"):
        if w not in seen:
            seen.append(w)
    return ", ".join(f'"{w}"' for w in seen)
