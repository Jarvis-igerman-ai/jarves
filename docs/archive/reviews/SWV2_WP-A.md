# SW-V2 WP-A: conversation quality (#17)

The full findings are in `docs/reviews/CONVERSATION_LOG_REVIEW_19OCT.md`.

## Changed
- `core/src/jarves/voice/live.py`:
  - `FRONT_INSTRUCTIONS` is reworked. Actions are confirmed once, with the real value or name, and never with a bare ack word. Quick actions get nothing before the result. The name is always said as the Hebrew spoken name. The persona has more warmth, humour, empathy and tone words, and is told to report exact values. A misheard question gets a light "say again?".
  - New `front_instructions(name, spoken)`. New `LiveVoice(spoken_name=...)`. The greeting message now says the Hebrew name, warmly.
- `core/src/jarves/config.py`: `assistant_name_spoken = "ג'ארוויס"` and `voice.greeting_delay_s = 0.5`.
- `core/src/jarves/app.py`:
  - `hello_text()` gives one spoken hello with no Latin letters.
  - `_present_after_delay()` releases the held live greeting 0.5 s after the face connects.
  - `greet()` (the TTS/cached path) waits the same beat.
- `core/src/jarves/emotion.py`: warmer JARVES cues (love: "שמח בשבילך", "מזל טוב"; mild sad: "אוי לא", "אני כאן בשבילך").
- Tests:
  - New `core/tests/test_conversation_quality.py`: the ack-word regression scan over the prompt, the notes, the greeting and every Hebrew literal in live/app/brain/chatgpt_brain/music/builtin; the name; the delay; the emotion cues.
  - `conftest.py` sets `greeting_delay_s = 0` for other tests.
  - Two greeting assertions were updated for the Hebrew name.

## Tests and cost
- `python -m pytest core/tests -q`: 318 passed, 1 skipped.
- Silent harness: real GPT-Live, virtual audio and a fake music player. Two runs, 14 turns, about 3.5 min of voice, about $0.20. There was no סגור, all confirmations carried the real values, and the emotion and face cues fit.

## Integration hooks (other owners)
- **E (music.py)**:
  - `music_status` must include the current volume, and `music_volume` should accept relative up/down. Without it, "how much volume?" made the model call `music_volume` in the live session. That changes volume to answer a question.
  - mute vs pause: "תעשה מיוט" paused, but the reply said "השתקתי".
  - music_play took 8.5 s on Pear.
- **C (progress/backend in live.py)**:
  - `PROGRESS_NOTE`'s example "עוד שנייה, כמעט שם" is a false claim, and the model repeats it ("ממש שם"). Use a neutral "still checking" instead.
  - The web-search stall (Intel, about 90 s, nothing logged) needs logging, a timeout and an honest failure.
- **B**: the session/dashboard view should show local time. The log is UTC, so "17:13" looked wrong next to 14:13.
- Shared files touched (merge note): `config.py` gets two fields, and `conftest.py` gets one line.

## Open questions
- Personality is the user's choice. Is the warmer, playful-teasing persona right? Should the greeting stay "שלום! אני ג'ארוויס. איך אפשר לעזור?" (USER DECISION REQUIRED, needs a listen).
- A 1-word lead-in ("שניה…") is still said while quick actions run. Is that acceptable, or should it be fully silent (which means 1-2 s of dead air)?
- `docs/HANDOFF.md` was not edited, to avoid conflicts between packages. The lead should add these items there.
